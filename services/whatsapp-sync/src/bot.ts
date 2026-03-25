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
import { getUserState, setUserState, UserState, ensureStateIndex, UserStateDoc } from './state';

const API_BASE_URL = process.env.API_BASE_URL || 'http://localhost:8000';
const CONTACTS_DIR = process.env.CONTACTS_DIR || './contacts';
const ADMIN_JID = process.env.ADMIN_JID || '';

// Track users waiting for personal QR code to be sent
const pendingQR = new Map<string, string>();

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
  await ensureStateIndex();

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

  // Poll for personal QR and linked/sync status every 5 seconds
  setInterval(async () => {
    await checkPersonalQR(sock);
    await checkPersonalLinked(sock);
  }, 5000);
}

async function checkPersonalQR(sock: ReturnType<typeof makeWASocket>): Promise<void> {
  const qrFile = '/tmp/afterlife_personal_qr.txt';
  if (!fs.existsSync(qrFile)) return;

  try {
    const qrData = fs.readFileSync(qrFile, 'utf8').trim();
    fs.unlinkSync(qrFile);

    const qrBuffer = await QRCode.toBuffer(qrData, { type: 'png', width: 512 });

    for (const [jid, status] of pendingQR) {
      if (status === 'waiting') {
        await sock.sendMessage(jid, {
          image: qrBuffer,
          caption: 'Scan this QR code with WhatsApp → Settings → Linked Devices → Link a Device',
        });
        pendingQR.set(jid, 'sent');
      }
    }
  } catch (err) {
    console.warn('[bot] Failed to process personal QR file:', err);
  }
}

async function checkPersonalLinked(sock: ReturnType<typeof makeWASocket>): Promise<void> {
  const linkedFile = '/tmp/afterlife_personal_linked.txt';
  if (!fs.existsSync(linkedFile)) return;

  try {
    fs.unlinkSync(linkedFile);

    // Advance all QR_SENT users to SYNCING
    for (const [jid] of pendingQR) {
      const doc = await getUserState(jid);
      if (doc.state === UserState.QR_SENT) {
        await setUserState(jid, { state: UserState.SYNCING });
        await sendText(sock, jid, 'Linked! Syncing your contacts in the background...');
        pendingQR.delete(jid);
      }
    }
  } catch (err) {
    console.warn('[bot] Failed to process personal linked file:', err);
  }
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
      await sendText(sock, userJid, "Sorry, couldn't process that voice note.");
      return;
    }
  }

  if (!userText && !audioBuffer) return;

  const userStateDoc = await getUserState(userJid);

  switch (userStateDoc.state) {
    case UserState.INIT:
      await handleStateInit(sock, userJid);
      break;

    case UserState.QR_SENT:
      await sendText(sock, userJid, 'Still waiting for you to scan the QR code. Check the image above.');
      break;

    case UserState.LINKED:
    case UserState.SYNCING:
      await sendText(sock, userJid, `Still syncing your contacts (${userStateDoc.contact_count} done so far). Almost ready!`);
      break;

    case UserState.ACTIVE:
      await handleStateActive(sock, userJid, userText, audioBuffer, userStateDoc);
      break;
  }
}

async function handleStateInit(
  sock: ReturnType<typeof makeWASocket>,
  userJid: string
): Promise<void> {
  await setUserState(userJid, { state: UserState.QR_SENT });
  await sendText(
    sock,
    userJid,
    "Hi! I'm After-Life. I'll sync your WhatsApp contacts so you can speak with them.\n\nSending you a QR code now — scan it with: WhatsApp → Settings → Linked Devices → Link a Device"
  );
  pendingQR.set(userJid, 'waiting');
}

async function handleStateActive(
  sock: ReturnType<typeof makeWASocket>,
  userJid: string,
  userText: string,
  audioBuffer: Buffer | null,
  userStateDoc: UserStateDoc
): Promise<void> {
  // Jitsi call trigger
  if (/\b(call me|start a call|video call|voice call|let's call|lets call)\b/i.test(userText)) {
    const sessionId = crypto.randomBytes(8).toString('hex');
    const jitsiUrl = `https://meet.jit.si/afterlife-${sessionId}`;
    await sendText(sock, userJid, `Tap to join: ${jitsiUrl}`);
    return;
  }

  // End session
  if (/^(end|stop|exit|bye|goodbye)/i.test(userText)) {
    await setUserState(userJid, { selected_contact: null, session_id: null });
    await sendText(sock, userJid, `Session ended. Who would you like to talk to next?\n\nAvailable: ${getAvailableContacts().join(', ')}`);
    return;
  }

  const activeContact = userStateDoc.selected_contact;

  if (!activeContact) {
    const intent = parseContactIntent(userText);
    if (intent) {
      const available = getAvailableContacts();
      const matched = available.find((c) => c.toLowerCase().includes(intent.toLowerCase()));
      if (matched) {
        await setUserState(userJid, { selected_contact: matched, session_id: null });
        await converseAndRespond(sock, userJid, matched, 'Hello', null, null);
      } else {
        await sendText(sock, userJid, `Couldn't find "${intent}". Available: ${available.join(', ') || 'none yet'}`);
      }
    } else {
      const available = getAvailableContacts();
      await sendText(sock, userJid, `Who would you like to talk to?\n\nAvailable: ${available.join(', ') || 'none yet'}`);
    }
    return;
  }

  // Active contact — route to conversation API
  await converseAndRespond(sock, userJid, activeContact, userText, audioBuffer, userStateDoc.session_id);
}

async function converseAndRespond(
  sock: ReturnType<typeof makeWASocket>,
  userJid: string,
  contactName: string,
  userText: string,
  audio: Buffer | null,
  existingSessionId: string | null
): Promise<void> {
  try {
    let sessionId = existingSessionId;

    if (!sessionId) {
      const startResp = await axios.post<{ session_id: string; greeting_text: string; greeting_audio_b64?: string }>(
        `${API_BASE_URL}/conversation/start`,
        { contact_name: contactName, user_name: userJid.split('@')[0] },
        { headers: { 'Content-Type': 'application/json' } }
      );
      sessionId = startResp.data.session_id;
      await setUserState(userJid, { session_id: sessionId });

      const greeting = startResp.data.greeting_text;
      const greetingAudioB64 = startResp.data.greeting_audio_b64;
      if (greetingAudioB64) {
        const audioBytes = Buffer.from(greetingAudioB64, 'base64');
        await sock.sendMessage(userJid, { audio: audioBytes, mimetype: 'audio/mpeg', ptt: true });
      } else {
        await sendText(sock, userJid, greeting);
      }
      if (userText === 'Hello') return;
    }

    const msgResp = await axios.post<{ reply_text: string; reply_audio_b64?: string }>(
      `${API_BASE_URL}/conversation/message`,
      { session_id: sessionId, message: audio ? '[voice note]' : userText },
      { headers: { 'Content-Type': 'application/json' } }
    );

    const replyText = msgResp.data.reply_text;
    const replyAudioB64 = msgResp.data.reply_audio_b64;
    if (replyAudioB64) {
      const audioBytes = Buffer.from(replyAudioB64, 'base64');
      await sock.sendMessage(userJid, { audio: audioBytes, mimetype: 'audio/mpeg', ptt: true });
    } else {
      await sendText(sock, userJid, replyText);
    }
  } catch (err) {
    console.error('[bot] Conversation error:', err);
    await sendText(sock, userJid, 'Something went wrong. Please try again.');
  }
}

async function sendText(
  sock: ReturnType<typeof makeWASocket>,
  jid: string,
  text: string
): Promise<void> {
  await sock.sendMessage(jid, { text });
}
