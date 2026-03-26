// backfill.test.ts — fixture-driven tests for historical ingest + media normalisation
//
// All external I/O is mocked:
//   - MongoDB upsertMessage       → jest mock (in-memory call recorder)
//   - downloadMediaMessage        → returns a fake Buffer
//   - convertOggToWav             → no-op (writes empty wav)
//   - fs.*                        → real filesystem via tmp directory
//
// Tests verify:
//   1. Text messages are classified and persisted correctly
//   2. Voice notes trigger download + OGG→WAV conversion
//   3. Photos and videos are downloaded to stable paths
//   4. Idempotency: a second backfill call upserts the same records (no duplicates)
//   5. Unrecognised message types are skipped

import * as os from 'os';
import * as fs from 'fs';
import * as path from 'path';
import type { WAMessage } from '@whiskeysockets/baileys';

// ── Mocks (must be hoisted before any import that touches these modules) ──────

jest.mock('@whiskeysockets/baileys', () => ({
  downloadMediaMessage: jest.fn(),
}));

jest.mock('../audio', () => ({
  convertOggToWav: jest.fn().mockImplementation((_src: string, dest: string) => {
    // Write an empty WAV so the file exists
    fs.writeFileSync(dest, Buffer.alloc(0));
    return Promise.resolve();
  }),
}));

jest.mock('../db', () => ({
  upsertMessage: jest.fn().mockResolvedValue(undefined),
}));

// ── Imports after mocks ───────────────────────────────────────────────────────

import { backfillContact } from '../backfill';
import { stableMediaPath } from '../normalizer';
import { upsertMessage } from '../db';
import { downloadMediaMessage } from '@whiskeysockets/baileys';

const mockUpsert = upsertMessage as jest.MockedFunction<typeof upsertMessage>;
const mockDownload = downloadMediaMessage as jest.MockedFunction<typeof downloadMediaMessage>;

// ── Fixture helpers ───────────────────────────────────────────────────────────

function makeTextMsg(id: string, text: string, fromMe = false): WAMessage {
  return {
    key: { remoteJid: '15550001234@s.whatsapp.net', fromMe, id },
    messageTimestamp: 1700000000 + parseInt(id, 36) % 10000,
    message: { conversation: text },
  } as unknown as WAMessage;
}

function makeVoiceNoteMsg(id: string): WAMessage {
  return {
    key: { remoteJid: '15550001234@s.whatsapp.net', fromMe: false, id },
    messageTimestamp: 1700010000,
    message: { audioMessage: { url: 'https://example.com/audio.ogg' } },
  } as unknown as WAMessage;
}

function makePhotoMsg(id: string, caption = ''): WAMessage {
  return {
    key: { remoteJid: '15550001234@s.whatsapp.net', fromMe: true, id },
    messageTimestamp: 1700020000,
    message: { imageMessage: { caption, url: 'https://example.com/photo.jpg' } },
  } as unknown as WAMessage;
}

function makeVideoMsg(id: string): WAMessage {
  return {
    key: { remoteJid: '15550001234@s.whatsapp.net', fromMe: false, id },
    messageTimestamp: 1700030000,
    message: { videoMessage: { caption: '', url: 'https://example.com/clip.mp4' } },
  } as unknown as WAMessage;
}

function makeStickerMsg(id: string): WAMessage {
  return {
    key: { remoteJid: '15550001234@s.whatsapp.net', fromMe: false, id },
    messageTimestamp: 1700040000,
    message: { stickerMessage: {} },
  } as unknown as WAMessage;
}

// ── Test suite ────────────────────────────────────────────────────────────────

describe('backfillContact', () => {
  const JID = '15550001234@s.whatsapp.net';
  const CONTACT = 'alice';
  let mediaDir: string;

  beforeEach(() => {
    mediaDir = fs.mkdtempSync(path.join(os.tmpdir(), 'backfill-'));
    mockUpsert.mockClear();
    mockDownload.mockClear();
    // Default: download returns a fake buffer
    mockDownload.mockResolvedValue(Buffer.from('fake-media') as unknown as ReturnType<typeof downloadMediaMessage>);
  });

  afterEach(() => {
    fs.rmSync(mediaDir, { recursive: true, force: true });
  });

  // ── 1. Text messages ─────────────────────────────────────────────────────

  it('persists a text message with correct fields', async () => {
    const msg = makeTextMsg('msg001', 'Hello world', false);
    const result = await backfillContact({
      jid: JID,
      contactName: CONTACT,
      messages: [msg],
      mediaDir,
    });

    expect(result).toEqual({ processed: 1, skipped: 0 });
    expect(mockUpsert).toHaveBeenCalledTimes(1);

    const saved = mockUpsert.mock.calls[0][0];
    expect(saved.jid).toBe(JID);
    expect(saved.contact).toBe(CONTACT);
    expect(saved.messageId).toBe('msg001');
    expect(saved.type).toBe('text');
    expect(saved.content).toBe('Hello world');
    expect(saved.from).toBe('them');
    expect(saved.media_path).toBeNull();
    expect(typeof saved.timestamp).toBe('number');
  });

  it('sets from="me" for outgoing messages', async () => {
    const msg = makeTextMsg('msg002', 'Hi back', true);
    await backfillContact({ jid: JID, contactName: CONTACT, messages: [msg], mediaDir });

    expect(mockUpsert.mock.calls[0][0].from).toBe('me');
  });

  it('processes multiple text messages in one call', async () => {
    const messages = [
      makeTextMsg('t1', 'First'),
      makeTextMsg('t2', 'Second'),
      makeTextMsg('t3', 'Third'),
    ];
    const result = await backfillContact({ jid: JID, contactName: CONTACT, messages, mediaDir });

    expect(result.processed).toBe(3);
    expect(mockUpsert).toHaveBeenCalledTimes(3);
  });

  // ── 2. Voice notes ───────────────────────────────────────────────────────

  it('downloads voice note, converts to WAV, stores stable path', async () => {
    const msg = makeVoiceNoteMsg('voice001');
    const result = await backfillContact({ jid: JID, contactName: CONTACT, messages: [msg], mediaDir });

    expect(result).toEqual({ processed: 1, skipped: 0 });

    const expectedWav = stableMediaPath(mediaDir, CONTACT, 'voice001', 'wav');
    const saved = mockUpsert.mock.calls[0][0];
    expect(saved.type).toBe('voice_note');
    expect(saved.media_path).toBe(expectedWav);
    expect(fs.existsSync(expectedWav)).toBe(true);
  });

  it('does not re-download a voice note that already exists (idempotent)', async () => {
    // Pre-create the WAV file
    const wavPath = stableMediaPath(mediaDir, CONTACT, 'voice002', 'wav');
    fs.mkdirSync(path.dirname(wavPath), { recursive: true });
    fs.writeFileSync(wavPath, Buffer.from('existing-wav'));

    const msg = makeVoiceNoteMsg('voice002');
    await backfillContact({ jid: JID, contactName: CONTACT, messages: [msg], mediaDir });

    // Download should NOT be called because file already exists
    expect(mockDownload).not.toHaveBeenCalled();

    const saved = mockUpsert.mock.calls[0][0];
    expect(saved.media_path).toBe(wavPath);
  });

  // ── 3. Photos ────────────────────────────────────────────────────────────

  it('downloads photo and stores at stable JPG path', async () => {
    const msg = makePhotoMsg('photo001', 'Nice sunset');
    await backfillContact({ jid: JID, contactName: CONTACT, messages: [msg], mediaDir });

    const expectedJpg = stableMediaPath(mediaDir, CONTACT, 'photo001', 'jpg');
    const saved = mockUpsert.mock.calls[0][0];
    expect(saved.type).toBe('photo');
    expect(saved.content).toBe('Nice sunset');
    expect(saved.media_path).toBe(expectedJpg);
    expect(fs.existsSync(expectedJpg)).toBe(true);
  });

  it('uses [photo] content when caption is absent', async () => {
    const msg = makePhotoMsg('photo002');
    await backfillContact({ jid: JID, contactName: CONTACT, messages: [msg], mediaDir });
    expect(mockUpsert.mock.calls[0][0].content).toBe('[photo]');
  });

  it('does not re-download a photo that already exists (idempotent)', async () => {
    const jpgPath = stableMediaPath(mediaDir, CONTACT, 'photo003', 'jpg');
    fs.mkdirSync(path.dirname(jpgPath), { recursive: true });
    fs.writeFileSync(jpgPath, Buffer.from('existing-jpg'));

    const msg = makePhotoMsg('photo003');
    await backfillContact({ jid: JID, contactName: CONTACT, messages: [msg], mediaDir });

    expect(mockDownload).not.toHaveBeenCalled();
    expect(mockUpsert.mock.calls[0][0].media_path).toBe(jpgPath);
  });

  // ── 4. Videos ────────────────────────────────────────────────────────────

  it('downloads video and stores at stable MP4 path', async () => {
    const msg = makeVideoMsg('vid001');
    await backfillContact({ jid: JID, contactName: CONTACT, messages: [msg], mediaDir });

    const expectedMp4 = stableMediaPath(mediaDir, CONTACT, 'vid001', 'mp4');
    const saved = mockUpsert.mock.calls[0][0];
    expect(saved.type).toBe('video');
    expect(saved.media_path).toBe(expectedMp4);
    expect(fs.existsSync(expectedMp4)).toBe(true);
  });

  // ── 5. Unrecognised types ─────────────────────────────────────────────────

  it('skips sticker and other unrecognised messages', async () => {
    const msg = makeStickerMsg('sticker001');
    const result = await backfillContact({
      jid: JID,
      contactName: CONTACT,
      messages: [msg],
      mediaDir,
    });

    expect(result).toEqual({ processed: 0, skipped: 1 });
    expect(mockUpsert).not.toHaveBeenCalled();
  });

  it('handles mixed message types and skips correctly', async () => {
    const messages = [
      makeTextMsg('t1', 'Hi'),
      makeStickerMsg('s1'),
      makePhotoMsg('p1'),
      makeStickerMsg('s2'),
      makeVoiceNoteMsg('v1'),
    ];
    const result = await backfillContact({ jid: JID, contactName: CONTACT, messages, mediaDir });

    expect(result.processed).toBe(3);  // text + photo + voice_note
    expect(result.skipped).toBe(2);    // 2 stickers
  });

  // ── 6. Full idempotency ───────────────────────────────────────────────────

  it('calling backfill twice with same messages upserts same records both times', async () => {
    const messages = [
      makeTextMsg('dup1', 'Hello'),
      makePhotoMsg('dup2', 'A photo'),
    ];

    const opts = { jid: JID, contactName: CONTACT, messages, mediaDir };
    const r1 = await backfillContact(opts);
    const calls1 = mockUpsert.mock.calls.map((c) => c[0].messageId);

    mockUpsert.mockClear();
    mockDownload.mockClear();

    const r2 = await backfillContact(opts);
    const calls2 = mockUpsert.mock.calls.map((c) => c[0].messageId);

    // Same number processed both times
    expect(r1.processed).toBe(r2.processed);

    // Same message IDs upserted both times (order may differ)
    expect(calls1.sort()).toEqual(calls2.sort());

    // No media re-download on second pass
    expect(mockDownload).not.toHaveBeenCalled();
  });

  // ── 7. Empty message list ─────────────────────────────────────────────────

  it('returns zero counts for empty message list', async () => {
    const result = await backfillContact({ jid: JID, contactName: CONTACT, messages: [], mediaDir });
    expect(result).toEqual({ processed: 0, skipped: 0 });
    expect(mockUpsert).not.toHaveBeenCalled();
  });
});

// ── stableMediaPath unit tests ────────────────────────────────────────────────

describe('stableMediaPath', () => {
  it('builds correct path for voice note', () => {
    expect(stableMediaPath('/media', 'alice', 'abc123', 'wav')).toBe('/media/alice/abc123.wav');
  });

  it('builds correct path for photo', () => {
    expect(stableMediaPath('/media', 'bob', 'xyz789', 'jpg')).toBe('/media/bob/xyz789.jpg');
  });

  it('builds correct path for video', () => {
    expect(stableMediaPath('/tmp/media', 'carol', 'vid001', 'mp4')).toBe('/tmp/media/carol/vid001.mp4');
  });

  it('is deterministic — same inputs always produce same path', () => {
    const p1 = stableMediaPath('/m', 'dave', 'id1', 'wav');
    const p2 = stableMediaPath('/m', 'dave', 'id1', 'wav');
    expect(p1).toBe(p2);
  });
});
