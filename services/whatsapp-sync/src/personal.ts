import makeWASocket, {
  DisconnectReason,
  useMultiFileAuthState,
  WAMessage,
} from '@whiskeysockets/baileys';
import { Boom } from '@hapi/boom';
import { upsertMessage, SyncedMessage } from './db';

export function classifyMessage(msg: WAMessage): Pick<SyncedMessage, 'type' | 'content'> | null {
  const message = msg.message;
  if (!message) return null;

  if (message.conversation) {
    return { type: 'text', content: message.conversation };
  }
  if (message.extendedTextMessage?.text) {
    return { type: 'text', content: message.extendedTextMessage.text };
  }
  if (message.audioMessage) {
    return { type: 'voice_note', content: '[voice note]' };
  }
  if (message.imageMessage) {
    return { type: 'photo', content: message.imageMessage.caption || '[photo]' };
  }
  return null;
}

export async function runPersonalSync(): Promise<void> {
  const { state, saveCreds } = await useMultiFileAuthState('./personal_auth_state');

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
        console.log('[personal] Reconnecting...');
        runPersonalSync();
      } else {
        console.log('[personal] Logged out.');
      }
    } else if (connection === 'open') {
      console.log('[personal] Personal sync instance connected. Syncing to MongoDB...');
    }
  });

  sock.ev.on('messages.upsert', async ({ messages }) => {
    for (const msg of messages) {
      const jid = msg.key.remoteJid;
      if (!jid || !jid.endsWith('@s.whatsapp.net')) continue;

      const classified = classifyMessage(msg);
      if (!classified) continue;

      const syncedMsg: SyncedMessage = {
        jid,
        contact: jid.split('@')[0],
        messageId: msg.key.id || '',
        timestamp: typeof msg.messageTimestamp === 'number'
          ? msg.messageTimestamp
          : Number(msg.messageTimestamp ?? 0),
        from: msg.key.fromMe ? 'me' : 'them',
        type: classified.type,
        content: classified.content,
        syncedAt: new Date(),
      };

      try {
        await upsertMessage(syncedMsg);
      } catch (err) {
        console.warn('[personal] Failed to sync message to MongoDB:', err);
      }
    }
  });
}
