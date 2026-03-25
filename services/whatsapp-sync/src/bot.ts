import makeWASocket, {
  DisconnectReason,
  useMultiFileAuthState,
  downloadMediaMessage,
  WAMessage,
} from '@whiskeysockets/baileys';
import { Boom } from '@hapi/boom';
import * as fs from 'fs';
import * as path from 'path';
import axios from 'axios';
import { convertOggToWav } from './audio';

const API_BASE_URL = process.env.API_BASE_URL || 'http://localhost:8000';
const CONTACTS_DIR = process.env.CONTACTS_DIR || './contacts';

// Track active persona sessions per user JID
const activeSessions = new Map<string, string>(); // userJid -> contactName

export async function runBot(): Promise<void> {
  const { state, saveCreds } = await useMultiFileAuthState('./auth_state');

  const sock = makeWASocket({
    auth: state,
    printQRInTerminal: true,
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect } = update;
    if (connection === 'close') {
      const shouldReconnect =
        (lastDisconnect?.error as Boom)?.output?.statusCode !== DisconnectReason.loggedOut;
      if (shouldReconnect) {
        console.log('[bot] Reconnecting...');
        runBot();
      }
    } else if (connection === 'open') {
      console.log('[bot] After-Life bot is live and listening.');
    }
  });

  sock.ev.on('messages.upsert', async ({ messages }) => {
    for (const msg of messages) {
      if (!msg.message || msg.key.fromMe) continue;

      const userJid = msg.key.remoteJid!;
      await handleMessage(sock, userJid, msg);
    }
  });
}

async function handleMessage(
  sock: ReturnType<typeof makeWASocket>,
  userJid: string,
  msg: WAMessage
): Promise<void> {
  const message = msg.message!;
  const activeContact = activeSessions.get(userJid);

  // Parse input
  let userText = '';
  let audioBuffer: Buffer | null = null;

  if (message.conversation) {
    userText = message.conversation as string;
  } else if (message.extendedTextMessage) {
    userText = (message.extendedTextMessage as { text?: string }).text || '';
  } else if (message.audioMessage) {
    const tmpOgg = `/tmp/input_${Date.now()}.ogg`;
    const tmpWav = tmpOgg.replace('.ogg', '.wav');
    try {
      const buffer = await downloadMediaMessage(msg as Parameters<typeof downloadMediaMessage>[0], 'buffer', {});
      fs.writeFileSync(tmpOgg, buffer as Buffer);
      await convertOggToWav(tmpOgg, tmpWav);
      audioBuffer = fs.readFileSync(tmpWav);
      userText = '[voice note]';
    } catch (err) {
      console.warn('[bot] Failed to process incoming voice note:', err);
      await sendText(sock, userJid, "Sorry, I couldn't process that voice note. Try sending a text message.");
      return;
    }
  }

  if (!userText && !audioBuffer) return;

  // If no active session, prompt for contact selection
  if (!activeContact) {
    const intent = parseContactIntent(userText);
    if (intent) {
      const available = getAvailableContacts();
      const matched = available.find((c) =>
        c.toLowerCase().includes(intent.toLowerCase())
      );
      if (matched) {
        activeSessions.set(userJid, matched);
        await sendText(sock, userJid, `Connecting you with ${matched}...`);
        // Initial greeting from the persona
        await converseAndRespond(sock, userJid, matched, 'Hello', null);
      } else {
        const contactList = available.join(', ') || 'no contacts synced yet';
        await sendText(sock, userJid, `I couldn't find that contact. Available: ${contactList}`);
      }
    } else {
      const available = getAvailableContacts().join(', ') || 'none yet — run sync first';
      await sendText(sock, userJid, `Hey! Who would you like to connect with today?\n\nAvailable contacts: ${available}`);
    }
    return;
  }

  // Check for "end session" command
  if (/^(end|stop|exit|bye|goodbye)/i.test(userText)) {
    activeSessions.delete(userJid);
    await sendText(sock, userJid, `Ended your session with ${activeContact}. Who would you like to talk to next?`);
    return;
  }

  // Active session: route to persona
  await converseAndRespond(sock, userJid, activeContact, userText, audioBuffer);
}

function parseContactIntent(text: string): string | null {
  const patterns = [
    /(?:talk to|call|connect with|speak to|i want)\s+(.+)/i,
    /^(.+)$/i, // fallback: treat whole message as name
  ];
  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match) return match[1].trim();
  }
  return null;
}

function getAvailableContacts(): string[] {
  if (!fs.existsSync(CONTACTS_DIR)) return [];
  return fs.readdirSync(CONTACTS_DIR).filter((f) => {
    const metaPath = path.join(CONTACTS_DIR, f, 'metadata.json');
    return fs.existsSync(metaPath);
  });
}

async function converseAndRespond(
  sock: ReturnType<typeof makeWASocket>,
  userJid: string,
  contact: string,
  text: string,
  audio: Buffer | null
): Promise<void> {
  try {
    const formData = new FormData();
    formData.append('text', text);
    if (audio) {
      const blob = new Blob([audio], { type: 'audio/wav' });
      formData.append('audio', blob, 'input.wav');
    }

    const response = await axios.post(
      `${API_BASE_URL}/converse/${contact}`,
      formData,
      { responseType: 'arraybuffer' }
    );

    const responseText = response.headers['x-response-text'] as string || '';
    const audioData = Buffer.from(response.data as ArrayBuffer);

    if (audioData.length > 0) {
      await sock.sendMessage(userJid, {
        audio: audioData,
        mimetype: 'audio/ogg; codecs=opus',
        ptt: true,
      });
    } else if (responseText) {
      await sendText(sock, userJid, responseText);
    }
  } catch (err) {
    console.error('[bot] Conversation error:', err);
    await sendText(sock, userJid, "Something went wrong. Please try again.");
  }
}

async function sendText(
  sock: ReturnType<typeof makeWASocket>,
  jid: string,
  text: string
): Promise<void> {
  await sock.sendMessage(jid, { text });
}
