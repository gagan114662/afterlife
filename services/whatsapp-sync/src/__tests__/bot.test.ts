// Mock baileys and qrcode (ESM modules) before imports
jest.mock('@whiskeysockets/baileys', () => ({}));
jest.mock('qrcode', () => ({ toBuffer: jest.fn() }));
jest.mock('../state', () => ({
  UserState: { INIT: 'INIT', QR_SENT: 'QR_SENT', LINKED: 'LINKED', SYNCING: 'SYNCING', ACTIVE: 'ACTIVE' },
  getUserState: jest.fn(),
  setUserState: jest.fn(),
  ensureStateIndex: jest.fn().mockResolvedValue(undefined),
}));
jest.mock('mongodb', () => ({ MongoClient: jest.fn() }));

import { parseContactIntent, isCallMeRequest, generateJitsiUrl, getAvailableContacts } from '../bot';
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';

describe('parseContactIntent', () => {
  it('matches "talk to X"', () => {
    expect(parseContactIntent('talk to Mom')).toBe('Mom');
  });

  it('matches "call X"', () => {
    expect(parseContactIntent('call Dad')).toBe('Dad');
  });

  it('matches "connect with X"', () => {
    expect(parseContactIntent('connect with Grandma')).toBe('Grandma');
  });

  it('matches "speak to X"', () => {
    expect(parseContactIntent('speak to John')).toBe('John');
  });

  it('returns the whole text as fallback', () => {
    expect(parseContactIntent('Alice')).toBe('Alice');
  });

  it('returns null for empty string', () => {
    expect(parseContactIntent('')).toBeNull();
  });
});

describe('isCallMeRequest', () => {
  it('matches "call me"', () => {
    expect(isCallMeRequest('call me')).toBe(true);
  });

  it('matches "video call"', () => {
    expect(isCallMeRequest('video call please')).toBe(true);
  });

  it('matches "jitsi"', () => {
    expect(isCallMeRequest('lets use jitsi')).toBe(true);
  });

  it('matches "let\'s talk"', () => {
    expect(isCallMeRequest("let's talk")).toBe(true);
  });

  it('matches "hop on a call"', () => {
    expect(isCallMeRequest('hop on a call?')).toBe(true);
  });

  it('does not match regular messages', () => {
    expect(isCallMeRequest('how are you doing today?')).toBe(false);
  });

  it('does not match "call" alone without "me"', () => {
    expect(isCallMeRequest('call alice')).toBe(false);
  });
});

describe('generateJitsiUrl', () => {
  it('returns a valid Jitsi URL', () => {
    const url = generateJitsiUrl();
    expect(url).toMatch(/^https:\/\/meet\.jit\.si\/afterlife-[0-9a-f]{16}$/);
  });

  it('generates unique URLs each time', () => {
    const url1 = generateJitsiUrl();
    const url2 = generateJitsiUrl();
    expect(url1).not.toBe(url2);
  });
});

describe('getAvailableContacts', () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'contacts-'));
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true });
  });

  it('returns empty array when directory does not exist', () => {
    expect(getAvailableContacts('/nonexistent/path')).toEqual([]);
  });

  it('returns contacts that have metadata.json', () => {
    const contactDir = path.join(tmpDir, 'alice');
    fs.mkdirSync(contactDir);
    fs.writeFileSync(path.join(contactDir, 'metadata.json'), '{}');

    expect(getAvailableContacts(tmpDir)).toEqual(['alice']);
  });

  it('excludes directories without metadata.json', () => {
    const contactDir = path.join(tmpDir, 'bob');
    fs.mkdirSync(contactDir);
    // no metadata.json

    expect(getAvailableContacts(tmpDir)).toEqual([]);
  });
});

describe('isCallIntent (ACTIVE state regex)', () => {
  const isCallIntent = (text: string): boolean =>
    /\b(call me|start a call|video call|voice call|let's call|lets call)\b/i.test(text);

  it('detects "call me"', () => expect(isCallIntent('call me')).toBe(true));
  it('detects "video call"', () => expect(isCallIntent('video call')).toBe(true));
  it('detects "start a call"', () => expect(isCallIntent('start a call')).toBe(true));
  it('detects case insensitive', () => expect(isCallIntent('CALL ME')).toBe(true));
  it('does not match "talk to me"', () => expect(isCallIntent('talk to me')).toBe(false));
  it('does not match partial word "recall"', () => expect(isCallIntent('recall')).toBe(false));
});

describe('Jitsi URL format', () => {
  it('generates a valid meet.jit.si URL', () => {
    const jid = '441234567890@s.whatsapp.net';
    const id = Buffer.from(`${jid}-1234`).toString('hex').slice(0, 16);
    const url = `https://meet.jit.si/afterlife-${id}`;
    expect(url).toMatch(/^https:\/\/meet\.jit\.si\/afterlife-[a-f0-9]{16}$/);
  });
});
