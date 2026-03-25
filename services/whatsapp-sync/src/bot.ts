import makeWASocket, {
  DisconnectReason,
  useMultiFileAuthState,
  downloadMediaMessage,
  WAMessage,
} from '@whiskeysockets/baileys';
import { Boom } from '@hapi/boom';
import * as fs from 'fs';
import * as path from 'path';
import * as crypto from 'crypto';
import axios from 'axios';
import QRCode from 'qrcode';
import { convertOggToWav } from './audio';

const API_BASE_URL = process.env.API_BASE_URL || 'http://localhost:8000';
const CONTACTS_DIR = process.env.CONTACTS_DIR || './contacts';
const ADMIN_JID = process.env.ADMIN_JID || '';

interface ActiveSession {
  contactName: string;
  sessionId: string;
}

// Track active persona sessions per user JID
const activeSessions = new Map<string, ActiveSession>();

export function generateJitsiUrl(): string {
  const roomId = crypto.randomBytes(8).toString('hex');
  return `https://meet.jit.si/afterlife-${roomId}`;
}

export function isCallMeRequest(text: string): boolean {
  return /\b(call me|video call|jitsi|let'?s? talk|hop on a call)\b/i.test(text);
}

export function parseContactIntent(text: string): string | null {
  const patterns = [
    /(?:talk to|call|connect with|speak to|i want to talk to)\s+(.+)/i,
    /^(.+)$/i, // fallback: treat whole message as name
  ];
  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match) return match[1].trim();
  }
  return null;
}

export function getAvailableContacts(contactsDir: string = CONTACTS_DIR): string[] {
  if (!fs.existsSync(contactsDir)) return [];
  return fs.readdirSync(contactsDir).filter((f) => {
    const metaPath = path.join(contactsDir, f, 'metadata.json');
    return fs.existsSync(metaPath);
  });
}

export async function runBot(): Promise<void> {
  const { state, saveCreds } = await useMultiFileAuthState('./auth_state');

  const sock = makeWASocket({
    auth: state,
    printQRInTerminal: false,
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      await handleQrCode(sock, qr);
    }

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

async function handleQrCode(
  sock: ReturnType<typeof makeWASocket>,
  qr: string
): Promise<void> {
  console.log('[bot] QR code received. Generating image...');
  try {
    const qrBuffer = await QRCode.toBuffer(qr, { type: 'png', width: 512 });
    if (ADMIN_JID) {
      await sock.sendMessage(ADMIN_JID, {
        image: qrBuffer,
        caption: 'Scan this QR code to connect After-Life WhatsApp bot.',
      });
      console.log(`[bot] QR code sent to admin: ${ADMIN_JID}`);
    } else {
      const qrPath = '/tmp/afterlife_qr.png';
      fs.writeFileSync(qrPath, qrBuffer);
      console.log(`[bot] QR code saved to ${qrPath} (set ADMIN_JID to send via WhatsApp)`);
    }
  } catch (err) {
    console.warn('[bot] Failed to generate/send QR code image, falling back to terminal:', err);
    // Fallback: print ASCII QR to terminal
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const qrcodeTerminal = require('qrcode-terminal') as { generate: (qr: string, opts: object) => void };
    qrcodeTerminal.generate(qr, { small: true });
  }
}

async function handleMessage(
  sock: ReturnType<typeof makeWASocket>,
  userJid: string,
  msg: WAMessage
): Promise<void> {
  const message = msg.message!;
  const activeSession = activeSessions.get(userJid);

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

  // Handle "call me" request in any state
  if (isCallMeRequest(userText)) {
    const jitsiUrl = generateJitsiUrl();
    await sendText(sock, userJid, `Here's your video call link:\n${jitsiUrl}\n\nClick to join — no account needed.`);
    return;
  }

  // If no active session, prompt for contact selection
  if (!activeSession) {
    const intent = parseContactIntent(userText);
    if (intent) {
      const available = getAvailableContacts();
      const matched = available.find((c) =>
        c.toLowerCase().includes(intent.toLowerCase())
      );
      if (matched) {
        await sendText(sock, userJid, `Connecting you with ${matched}...`);
        const session = await startConversation(matched, userJid);
        if (session) {
          activeSessions.set(userJid, { contactName: matched, sessionId: session.sessionId });
          await deliverResponse(sock, userJid, session.greetingText, session.greetingAudioB64);
        } else {
          await sendText(sock, userJid, `Sorry, I couldn't connect you with ${matched}. Please try again.`);
        }
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
    await sendText(sock, userJid, `Ended your session with ${activeSession.contactName}. Who would you like to talk to next?`);
    return;
  }

  // Active session: route to conversation API
  await sendMessageToPersona(sock, userJid, activeSession, userText, audioBuffer);
}

async function startConversation(
  contactName: string,
  userJid: string
): Promise<{ sessionId: string; greetingText: string; greetingAudioB64: string | null } | null> {
  try {
    const response = await axios.post(`${API_BASE_URL}/conversation/start`, {
      contact_name: contactName,
      user_name: userJid,
    });
    return {
      sessionId: response.data.session_id as string,
      greetingText: response.data.greeting_text as string,
      greetingAudioB64: response.data.greeting_audio_b64 as string | null,
    };
  } catch (err) {
    console.error('[bot] Failed to start conversation:', err);
    return null;
  }
}

async function sendMessageToPersona(
  sock: ReturnType<typeof makeWASocket>,
  userJid: string,
  session: ActiveSession,
  text: string,
  _audio: Buffer | null
): Promise<void> {
  try {
    const response = await axios.post(`${API_BASE_URL}/conversation/message`, {
      session_id: session.sessionId,
      message: text,
    });
    const replyText = response.data.reply_text as string;
    const replyAudioB64 = response.data.reply_audio_b64 as string | null;
    await deliverResponse(sock, userJid, replyText, replyAudioB64);
  } catch (err) {
    console.error('[bot] Conversation message error:', err);
    await sendText(sock, userJid, 'Something went wrong. Please try again.');
  }
}

async function deliverResponse(
  sock: ReturnType<typeof makeWASocket>,
  userJid: string,
  text: string,
  audioB64: string | null
): Promise<void> {
  if (audioB64) {
    const audioBuffer = Buffer.from(audioB64, 'base64');
    await sock.sendMessage(userJid, {
      audio: audioBuffer,
      mimetype: 'audio/ogg; codecs=opus',
      ptt: true,
    });
  } else if (text) {
    await sendText(sock, userJid, text);
  }
}

async function sendText(
  sock: ReturnType<typeof makeWASocket>,
  jid: string,
  text: string
): Promise<void> {
  await sock.sendMessage(jid, { text });
}
