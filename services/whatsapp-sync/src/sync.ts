import makeWASocket, {
  DisconnectReason,
  useMultiFileAuthState,
  downloadMediaMessage,
  proto,
} from '@whiskeysockets/baileys';
import { Boom } from '@hapi/boom';
import * as fs from 'fs';
import * as path from 'path';
import axios from 'axios';
import { convertOggToWav } from './audio';

const CONTACTS_DIR = process.env.CONTACTS_DIR || './contacts';
const API_BASE_URL = process.env.API_BASE_URL || 'http://localhost:8000';

interface Message {
  id: string;
  timestamp: number;
  from: 'me' | 'them';
  type: 'text' | 'voice_note' | 'photo' | 'other';
  content: string;
  media_path: string | null;
}

interface ContactData {
  contact: string;
  phone: string;
  messages: Message[];
  stats: {
    total_messages: number;
    voice_notes_count: number;
    date_range: { first: string; last: string };
  };
}

function sanitizeName(name: string): string {
  return name.replace(/[^a-zA-Z0-9_-]/g, '_').toLowerCase();
}

function ensureDir(dirPath: string): void {
  if (!fs.existsSync(dirPath)) {
    fs.mkdirSync(dirPath, { recursive: true });
  }
}

export async function runSync(): Promise<void> {
  const { state, saveCreds } = await useMultiFileAuthState('./auth_state');

  const sock = makeWASocket({
    auth: state,
    printQRInTerminal: true,
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect } = update;

    if (connection === 'close') {
      const shouldReconnect =
        (lastDisconnect?.error as Boom)?.output?.statusCode !== DisconnectReason.loggedOut;
      if (shouldReconnect) {
        console.log('[sync] Connection closed, reconnecting...');
        await runSync();
      } else {
        console.log('[sync] Logged out.');
      }
    } else if (connection === 'open') {
      console.log('[sync] Connected to WhatsApp. Starting data extraction...');
      await extractAllContacts(sock);
    }
  });
}

async function extractAllContacts(sock: ReturnType<typeof makeWASocket>): Promise<void> {
  console.log('[sync] Fetching chat list...');

  // Get all chats
  const chats = await sock.groupFetchAllParticipating();
  const contacts = (sock as unknown as { store?: { contacts?: Record<string, unknown> } }).store?.contacts || {};

  for (const [jid, contact] of Object.entries(contacts)) {
    if (jid.endsWith('@s.whatsapp.net')) {
      const name = (contact as { name?: string; notify?: string }).name
        || (contact as { name?: string; notify?: string }).notify
        || jid.split('@')[0];
      console.log(`[sync] Extracting contact: ${name}`);
      await extractContact(sock, jid, name);
    }
  }

  console.log('[sync] Extraction complete. Triggering ingestion pipeline...');
  await triggerIngestion();
}

async function extractContact(
  sock: ReturnType<typeof makeWASocket>,
  jid: string,
  displayName: string
): Promise<void> {
  const safeName = sanitizeName(displayName);
  const contactDir = path.join(CONTACTS_DIR, safeName);
  const voiceNotesDir = path.join(contactDir, 'voice_notes');
  const photosDir = path.join(contactDir, 'photos');

  ensureDir(contactDir);
  ensureDir(voiceNotesDir);
  ensureDir(photosDir);

  const messages: Message[] = [];
  let voiceNoteCount = 0;

  // Load message history from Baileys store (best-effort; empty if store unavailable)
  const store = (sock as unknown as { store?: { messages?: Record<string, { array?: proto.IWebMessageInfo[] }> } }).store;
  const rawMessages = store?.messages?.[jid]?.array ?? [];

  for (const msg of rawMessages) {
    if (!msg.message) continue;

    const isFromMe = msg.key.fromMe ?? false;
    const timestamp = typeof msg.messageTimestamp === 'number'
      ? msg.messageTimestamp
      : Number(msg.messageTimestamp);

    let type: Message['type'] = 'other';
    let content = '';
    let mediaPath: string | null = null;

    if (msg.message.conversation) {
      type = 'text';
      content = msg.message.conversation;
    } else if (msg.message.extendedTextMessage) {
      type = 'text';
      content = msg.message.extendedTextMessage.text || '';
    } else if (msg.message.audioMessage) {
      type = 'voice_note';
      const filename = `${voiceNoteCount++}_${timestamp}.ogg`;
      const oggPath = path.join(voiceNotesDir, filename);

      try {
        const buffer = await downloadMediaMessage(msg, 'buffer', {});
        fs.writeFileSync(oggPath, buffer as Buffer);

        const wavPath = oggPath.replace('.ogg', '.wav');
        await convertOggToWav(oggPath, wavPath);
        mediaPath = path.relative(contactDir, wavPath);
        content = '[voice note]';
      } catch (err) {
        console.warn(`[sync] Failed to download voice note: ${err}`);
      }
    } else if (msg.message.imageMessage) {
      type = 'photo';
      content = msg.message.imageMessage.caption || '[photo]';
    }

    if (type !== 'other') {
      messages.push({
        id: msg.key.id || '',
        timestamp,
        from: isFromMe ? 'me' : 'them',
        type,
        content,
        media_path: mediaPath,
      });
    }
  }

  const timestamps = messages.map((m) => m.timestamp).sort();
  const contactData: ContactData = {
    contact: displayName,
    phone: jid.split('@')[0],
    messages,
    stats: {
      total_messages: messages.length,
      voice_notes_count: voiceNoteCount,
      date_range: {
        first: timestamps.length > 0
          ? new Date(timestamps[0] * 1000).toISOString().split('T')[0]
          : '',
        last: timestamps.length > 0
          ? new Date(timestamps[timestamps.length - 1] * 1000).toISOString().split('T')[0]
          : '',
      },
    },
  };

  fs.writeFileSync(
    path.join(contactDir, 'messages.json'),
    JSON.stringify(contactData, null, 2)
  );

  const metadata = {
    display_name: displayName,
    phone: jid.split('@')[0],
    ...contactData.stats,
  };
  fs.writeFileSync(
    path.join(contactDir, 'metadata.json'),
    JSON.stringify(metadata, null, 2)
  );

  console.log(`[sync] Saved ${messages.length} messages, ${voiceNoteCount} voice notes for ${displayName}`);
}

async function triggerIngestion(): Promise<void> {
  const contacts = fs.readdirSync(CONTACTS_DIR).filter((f) => {
    return fs.statSync(path.join(CONTACTS_DIR, f)).isDirectory();
  });

  for (const contact of contacts) {
    try {
      await axios.post(`${API_BASE_URL}/ingest/${contact}`);
      console.log(`[sync] Triggered ingestion for ${contact}`);
    } catch (err) {
      console.warn(`[sync] Failed to trigger ingestion for ${contact}: ${err}`);
    }
  }
}
