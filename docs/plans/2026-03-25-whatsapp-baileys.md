# Branch 1: WhatsApp Baileys Integration Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Wire up dual Baileys instances (bot + personal sync) so a user texts "hi" to the bot number, gets a QR code image, scans it to link their personal WhatsApp, and all contacts/voice notes auto-sync to MongoDB.

**Architecture:** Two independent Baileys sockets — Instance 1 (bot, dedicated SIM) handles user-facing conversation; Instance 2 (personal, user's own phone) authenticates via QR scan then pulls full message history and voice notes. All synced data lands in MongoDB `contacts` collection and `data/voice_samples/<contact>/` on disk. The bot delegates text replies to the existing FastAPI `/conversation/start` and `/conversation/message` endpoints.

**Tech Stack:** @whiskeysockets/baileys (Node.js WhatsApp Web protocol), mongodb (npm), axios (API calls), TypeScript 5, fastapi (Python), motor + pymongo (MongoDB)

---

### Task 1: Add mongodb dependency to whatsapp-sync

**Files:**
- Modify: `services/whatsapp-sync/package.json`

**Step 1: Add mongodb to dependencies**

Edit `package.json`, add `"mongodb": "^6.0.0"` to the `dependencies` object.

```json
{
  "dependencies": {
    "@whiskeysockets/baileys": "^6.7.0",
    "axios": "^1.6.0",
    "commander": "^12.0.0",
    "fluent-ffmpeg": "^2.1.2",
    "ffmpeg-static": "^5.2.0",
    "mongodb": "^6.0.0",
    "pino": "^8.17.0",
    "qrcode": "^1.5.4",
    "qrcode-terminal": "^0.12.0"
  }
}
```

Note: also add `"qrcode": "^1.5.4"` — needed in Task 3 to render QR as a PNG buffer.

**Step 2: Add @types/qrcode to devDependencies**

```json
{
  "devDependencies": {
    "@types/fluent-ffmpeg": "^2.1.24",
    "@types/node": "^20.0.0",
    "@types/qrcode": "^1.5.5",
    "ts-node": "^10.9.2",
    "typescript": "^5.3.0"
  }
}
```

**Step 3: Install**

```bash
cd services/whatsapp-sync && npm install
```

Expected: `node_modules/mongodb` and `node_modules/qrcode` appear.

**Step 4: Verify TypeScript compiles**

```bash
npx tsc --noEmit
```

Expected: zero errors.

**Step 5: Commit**

```bash
git add services/whatsapp-sync/package.json services/whatsapp-sync/package-lock.json
git commit -m "chore: add mongodb and qrcode deps to whatsapp-sync"
```

---

### Task 2: Create MongoDB helper module

**Files:**
- Create: `services/whatsapp-sync/src/db.ts`
- Test: `services/whatsapp-sync/src/db.ts` (TypeScript compile = test here, integration tested manually)

**Step 1: Write db.ts**

```typescript
import { MongoClient, Collection, Db } from 'mongodb';

const MONGODB_URI = process.env.MONGODB_URI || 'mongodb://localhost:27017';
const MONGODB_DB = process.env.MONGODB_DB || 'afterlife';

let _client: MongoClient | null = null;
let _db: Db | null = null;

export async function getDb(): Promise<Db> {
  if (!_db) {
    _client = new MongoClient(MONGODB_URI);
    await _client.connect();
    _db = _client.db(MONGODB_DB);
  }
  return _db;
}

export async function getCollection<T extends object>(name: string): Promise<Collection<T>> {
  const db = await getDb();
  return db.collection<T>(name);
}

export async function closeDb(): Promise<void> {
  if (_client) {
    await _client.close();
    _client = null;
    _db = null;
  }
}
```

**Step 2: Verify TypeScript compiles**

```bash
cd services/whatsapp-sync && npx tsc --noEmit
```

Expected: zero errors.

**Step 3: Commit**

```bash
git add services/whatsapp-sync/src/db.ts
git commit -m "feat: add MongoDB connection helper for whatsapp-sync"
```

---

### Task 3: Send QR code as image (Instance 1 — bot)

The current `bot.ts` prints QR to the terminal. Users need to receive it as a WhatsApp image so they know what to do.

**Files:**
- Modify: `services/whatsapp-sync/src/bot.ts`

**Step 1: Import qrcode at top of bot.ts**

Add this import after the existing imports:
```typescript
import QRCode from 'qrcode';
```

**Step 2: Add a helper to send QR as image**

Add this function before `runBot()`:

```typescript
async function sendQRImage(
  sock: ReturnType<typeof makeWASocket>,
  jid: string,
  qrData: string
): Promise<void> {
  try {
    const pngBuffer = await QRCode.toBuffer(qrData, { type: 'png', width: 400 });
    await sock.sendMessage(jid, {
      image: pngBuffer,
      caption: 'Scan this QR code: WhatsApp → Settings → Linked Devices → Link a Device',
    });
  } catch (err) {
    console.warn('[bot] Failed to send QR image:', err);
  }
}
```

**Step 3: Track pending QR requests**

Add this near the top of bot.ts, alongside `activeSessions`:

```typescript
const pendingQR = new Map<string, string>(); // userJid -> latest QR string
```

**Step 4: Store QR when it arrives**

In `runBot()`, find the `connection.update` handler. Add QR capture BEFORE the existing `if (connection === 'close')` block:

```typescript
sock.ev.on('connection.update', (update) => {
  const { connection, lastDisconnect, qr } = update;
  if (qr) {
    // Store latest QR for any pending user requests
    for (const [jid] of pendingQR) {
      sendQRImage(sock, jid, qr);
    }
    pendingQR.clear();
  }
  if (connection === 'close') {
    // ... existing reconnect logic unchanged ...
  }
});
```

**Step 5: Expose sendQRImage and pendingQR to handleMessage**

In `handleMessage`, when the user sends "hi" or any first message and no session is active AND no contacts are available yet, queue them for QR:

```typescript
// At the top of handleMessage, after checking !activeContact:
if (!activeContact) {
  const available = getAvailableContacts();
  if (available.length === 0) {
    // No contacts yet — user needs to link personal WhatsApp
    pendingQR.set(userJid, 'waiting');
    // QR will be sent by connection.update handler next time QR arrives
    // If personal sock is already connected and sync is in progress, say so
    await sendText(sock, userJid, 'Syncing your contacts... you\'ll get a QR code to link your WhatsApp.');
    return;
  }
  // ... rest of existing contact-selection logic ...
}
```

**Step 6: Verify TypeScript compiles**

```bash
cd services/whatsapp-sync && npx tsc --noEmit
```

Expected: zero errors.

**Step 7: Commit**

```bash
git add services/whatsapp-sync/src/bot.ts
git commit -m "feat: send QR code as WhatsApp image in bot (Instance 1)"
```

---

### Task 4: Create personal sync module (Instance 2)

This is the new Baileys socket that runs on the user's personal phone number.

**Files:**
- Create: `services/whatsapp-sync/src/personal.ts`

**Step 1: Write personal.ts**

```typescript
import makeWASocket, {
  DisconnectReason,
  useMultiFileAuthState,
  downloadMediaMessage,
  proto,
} from '@whiskeysockets/baileys';
import { Boom } from '@hapi/boom';
import * as fs from 'fs';
import * as path from 'path';
import { convertOggToWav } from './audio';
import { getCollection } from './db';

const DATA_DIR = process.env.DATA_DIR || './data';
const VOICE_SAMPLES_DIR = path.join(DATA_DIR, 'voice_samples');

export type SyncProgressCallback = (synced: number, total: number) => void;

function ensureDir(p: string): void {
  if (!fs.existsSync(p)) fs.mkdirSync(p, { recursive: true });
}

function sanitizeName(name: string): string {
  return name.replace(/[^a-zA-Z0-9_-]/g, '_').toLowerCase();
}

/**
 * Start the personal WhatsApp sync instance.
 * Emits progress via onProgress(synced, total).
 * Returns a Promise that resolves when initial sync is complete.
 */
export async function runPersonalSync(
  onQR: (qr: string) => void,
  onProgress: SyncProgressCallback
): Promise<void> {
  const { state, saveCreds } = await useMultiFileAuthState('./auth_state_personal');

  const sock = makeWASocket({
    auth: state,
    printQRInTerminal: false,
  });

  sock.ev.on('creds.update', saveCreds);

  return new Promise((resolve) => {
    sock.ev.on('connection.update', async (update) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        onQR(qr);
      }

      if (connection === 'close') {
        const shouldReconnect =
          (lastDisconnect?.error as Boom)?.output?.statusCode !== DisconnectReason.loggedOut;
        if (shouldReconnect) {
          console.log('[personal] Reconnecting...');
          runPersonalSync(onQR, onProgress).then(resolve);
        } else {
          console.log('[personal] Logged out.');
          resolve();
        }
      } else if (connection === 'open') {
        console.log('[personal] Connected. Starting contact sync...');
        await syncAllContacts(sock, onProgress);
        resolve();
      }
    });
  });
}

async function syncAllContacts(
  sock: ReturnType<typeof makeWASocket>,
  onProgress: SyncProgressCallback
): Promise<void> {
  const store = (sock as unknown as {
    store?: { contacts?: Record<string, { name?: string; notify?: string }> }
  }).store;
  const contacts = store?.contacts ?? {};
  const personalJids = Object.entries(contacts).filter(([jid]) =>
    jid.endsWith('@s.whatsapp.net')
  );

  let synced = 0;
  const total = personalJids.length;
  onProgress(0, total);

  for (const [jid, contact] of personalJids) {
    const name = contact.name || contact.notify || jid.split('@')[0];
    await syncContact(sock, jid, name);
    synced++;
    onProgress(synced, total);
  }

  console.log(`[personal] Sync complete: ${synced} contacts.`);
}

async function syncContact(
  sock: ReturnType<typeof makeWASocket>,
  jid: string,
  displayName: string
): Promise<void> {
  const safeName = sanitizeName(displayName);
  const voiceDir = path.join(VOICE_SAMPLES_DIR, safeName);
  ensureDir(voiceDir);

  const store = (sock as unknown as {
    store?: { messages?: Record<string, { array?: proto.IWebMessageInfo[] }> }
  }).store;
  const rawMessages = store?.messages?.[jid]?.array ?? [];

  const textMessages: string[] = [];
  let voiceCount = 0;

  for (const msg of rawMessages) {
    if (!msg.message) continue;
    if (!msg.key.fromMe) continue; // only THEIR messages for voice cloning

    if (msg.message.conversation) {
      textMessages.push(msg.message.conversation);
    } else if (msg.message.extendedTextMessage?.text) {
      textMessages.push(msg.message.extendedTextMessage.text);
    } else if (msg.message.audioMessage) {
      const ts = typeof msg.messageTimestamp === 'number'
        ? msg.messageTimestamp
        : Number(msg.messageTimestamp);
      const oggPath = path.join(voiceDir, `${voiceCount}_${ts}.ogg`);
      try {
        const buf = await downloadMediaMessage(msg, 'buffer', {});
        fs.writeFileSync(oggPath, buf as Buffer);
        const wavPath = oggPath.replace('.ogg', '.wav');
        await convertOggToWav(oggPath, wavPath);
        voiceCount++;
      } catch {
        // skip failed downloads
      }
    }
  }

  // Upsert contact in MongoDB
  const contacts = await getCollection<Record<string, unknown>>('contacts');
  await contacts.updateOne(
    { name: displayName },
    {
      $set: {
        name: displayName,
        phone: jid.split('@')[0],
        voice_samples_dir: voiceDir,
        message_count: textMessages.length,
        voice_note_count: voiceCount,
        last_synced: new Date().toISOString(),
      },
      $setOnInsert: {
        biography: '',
        personality_profile: '',
        common_phrases: '',
        voice_id: '',
      },
    },
    { upsert: true }
  );

  console.log(`[personal] ${displayName}: ${textMessages.length} texts, ${voiceCount} voice notes`);
}
```

**Step 2: Verify TypeScript compiles**

```bash
cd services/whatsapp-sync && npx tsc --noEmit
```

Expected: zero errors.

**Step 3: Commit**

```bash
git add services/whatsapp-sync/src/personal.ts
git commit -m "feat: personal WhatsApp sync instance (Baileys Instance 2)"
```

---

### Task 5: Wire personal sync into index.ts + forward QR to bot

**Files:**
- Modify: `services/whatsapp-sync/src/index.ts`
- Modify: `services/whatsapp-sync/src/bot.ts`

**Step 1: Export `pendingQR` and `notifyContactsSynced` from bot.ts**

At module level in bot.ts, add an export for pendingQR and a new function:

```typescript
// Already defined above — make it exported:
export const pendingQR = new Map<string, string>();

/** Called by personal sync when all contacts are ready. */
export function notifyContactsSynced(count: number, botSock: ReturnType<typeof makeWASocket>): void {
  for (const [jid] of activeSessions) {
    sendText(botSock, jid, `✓ ${count} contacts synced. Who would you like to talk to?`);
  }
  // Also notify any user who triggered the sync (stored in pendingQR keys)
  for (const [jid] of pendingQR) {
    sendText(botSock, jid, `✓ ${count} contacts synced. Who would you like to talk to?`);
  }
  pendingQR.clear();
}
```

**Step 2: Update index.ts to start both instances**

Replace the contents of `services/whatsapp-sync/src/index.ts`:

```typescript
import { program } from 'commander';
import { runSync } from './sync';
import { runBot, pendingQR, notifyContactsSynced } from './bot';
import { runPersonalSync } from './personal';

program
  .option('--mode <mode>', 'Run mode: sync | bot | all', 'all')
  .parse(process.argv);

const opts = program.opts();

async function main(): Promise<void> {
  const mode = opts.mode as string;

  if (mode === 'sync') {
    console.log('[afterlife] Starting WhatsApp sync...');
    await runSync();
  } else if (mode === 'bot') {
    console.log('[afterlife] Starting WhatsApp bot only...');
    await runBot();
  } else if (mode === 'all') {
    console.log('[afterlife] Starting bot + personal sync...');

    // Start bot first so it can receive messages
    const botPromise = runBot();

    // Start personal sync — passes QR back to bot users
    // We need the bot socket to send messages; bot.ts exposes it via callback
    let botSock: ReturnType<typeof import('./bot').runBot> extends Promise<infer T> ? T : never;
    // For simplicity: personal sync runs concurrently, bot handles QR distribution
    runPersonalSync(
      (qr) => {
        // Forward QR to all pending users
        // Bot's pendingQR map tracks who needs it; sendQRImage is internal to bot.ts
        // We store the QR in an env var that bot.ts polls — simpler: use a shared file
        const fs = require('fs');
        fs.writeFileSync('/tmp/afterlife_personal_qr.txt', qr);
        console.log('[personal] QR ready at /tmp/afterlife_personal_qr.txt');
      },
      (synced, total) => {
        console.log(`[personal] Progress: ${synced}/${total}`);
        if (synced === total && total > 0) {
          console.log(`[personal] All ${total} contacts synced.`);
          // Write completion marker
          const fs = require('fs');
          fs.writeFileSync('/tmp/afterlife_sync_done.txt', String(total));
        }
      }
    ).catch((err) => console.error('[personal] Sync failed:', err));

    await botPromise;
  } else {
    console.error(`Unknown mode: ${mode}. Use --mode sync|bot|all`);
    process.exit(1);
  }
}

main().catch((err) => {
  console.error('[afterlife] Fatal error:', err);
  process.exit(1);
});
```

**Step 3: Add QR polling to bot.ts**

In bot.ts `runBot()`, add a polling interval after the socket is created that watches `/tmp/afterlife_personal_qr.txt`:

```typescript
// Poll for personal QR file and forward to pending users
const qrPollInterval = setInterval(async () => {
  const qrFile = '/tmp/afterlife_personal_qr.txt';
  if (fs.existsSync(qrFile) && pendingQR.size > 0) {
    const qrData = fs.readFileSync(qrFile, 'utf-8').trim();
    fs.unlinkSync(qrFile); // consume it
    for (const [jid] of pendingQR) {
      await sendQRImage(sock, jid, qrData);
    }
    pendingQR.clear();
  }
  // Check for sync completion
  const doneFile = '/tmp/afterlife_sync_done.txt';
  if (fs.existsSync(doneFile)) {
    const count = parseInt(fs.readFileSync(doneFile, 'utf-8').trim(), 10);
    fs.unlinkSync(doneFile);
    // Notify all active users
    for (const [jid] of activeSessions) {
      await sendText(sock, jid, `✓ ${count} contacts synced. Who would you like to talk to?`);
    }
  }
}, 2000);

// Clean up on close
sock.ev.on('connection.update', (update) => {
  if (update.connection === 'close') {
    clearInterval(qrPollInterval);
    // ... existing reconnect logic ...
  }
});
```

**Step 4: Verify TypeScript compiles**

```bash
cd services/whatsapp-sync && npx tsc --noEmit
```

Expected: zero errors.

**Step 5: Run npm test**

```bash
npm test
```

Expected: `No tests yet` + exit 0.

**Step 6: Commit**

```bash
git add services/whatsapp-sync/src/index.ts services/whatsapp-sync/src/bot.ts
git commit -m "feat: wire dual Baileys instances — bot + personal sync run together"
```

---

### Task 6: Connect bot message handler to conversation API

The current `bot.ts` calls `/converse/<contact>` which doesn't exist. Wire it to the real `/conversation/start` and `/conversation/message` endpoints.

**Files:**
- Modify: `services/whatsapp-sync/src/bot.ts`

**Step 1: Replace converseAndRespond with proper API calls**

Replace the existing `converseAndRespond` function entirely:

```typescript
// Track session IDs per user+contact pair
const apiSessions = new Map<string, string>(); // `${userJid}:${contactName}` -> sessionId

async function converseAndRespond(
  sock: ReturnType<typeof makeWASocket>,
  userJid: string,
  contactName: string,
  userText: string,
  _audio: Buffer | null
): Promise<void> {
  try {
    const sessionKey = `${userJid}:${contactName}`;
    let sessionId = apiSessions.get(sessionKey);

    if (!sessionId) {
      // Start new session
      const startResp = await axios.post<{ session_id: string; greeting_text: string; greeting_audio_b64?: string }>(
        `${API_BASE_URL}/conversation/start`,
        { contact_name: contactName, user_name: userJid.split('@')[0] },
        { headers: { 'Content-Type': 'application/json' } }
      );
      sessionId = startResp.data.session_id;
      apiSessions.set(sessionKey, sessionId);

      // Send greeting
      const greeting = startResp.data.greeting_text;
      const audioB64 = startResp.data.greeting_audio_b64;
      if (audioB64) {
        const audioBytes = Buffer.from(audioB64, 'base64');
        await sock.sendMessage(userJid, {
          audio: audioBytes,
          mimetype: 'audio/mpeg',
          ptt: true,
        });
      } else {
        await sendText(sock, userJid, greeting);
      }

      // If the original trigger was "Hello" (initial greeting), don't also send the userText
      if (userText === 'Hello') return;
    }

    // Send the actual message
    const msgResp = await axios.post<{ reply_text: string; reply_audio_b64?: string }>(
      `${API_BASE_URL}/conversation/message`,
      { session_id: sessionId, message: userText },
      { headers: { 'Content-Type': 'application/json' } }
    );

    const replyText = msgResp.data.reply_text;
    const audioB64 = msgResp.data.reply_audio_b64;

    if (audioB64) {
      const audioBytes = Buffer.from(audioB64, 'base64');
      await sock.sendMessage(userJid, {
        audio: audioBytes,
        mimetype: 'audio/mpeg',
        ptt: true,
      });
    } else {
      await sendText(sock, userJid, replyText);
    }
  } catch (err) {
    console.error('[bot] Conversation API error:', err);
    await sendText(sock, userJid, 'Something went wrong. Please try again.');
  }
}
```

**Step 2: Clean up unused import**

Remove `downloadMediaMessage` from bot.ts imports if it's no longer used, or keep it if audio handling still references it.

Check: `downloadMediaMessage` is used in the audio handling block. Keep it.

**Step 3: Verify TypeScript compiles**

```bash
cd services/whatsapp-sync && npx tsc --noEmit
```

Expected: zero errors.

**Step 4: Commit**

```bash
git add services/whatsapp-sync/src/bot.ts
git commit -m "feat: wire bot to /conversation/start and /conversation/message API endpoints"
```

---

### Task 7: Add "call me" Jitsi handler

**Files:**
- Modify: `services/whatsapp-sync/src/bot.ts`

**Step 1: Add call detection in handleMessage**

In `handleMessage`, after the "end session" check and before routing to `converseAndRespond`, add:

```typescript
// Jitsi call trigger
if (/\b(call me|start a call|video call|voice call|let's call|lets call)\b/i.test(userText)) {
  const sessionId = Buffer.from(`${userJid}-${Date.now()}`).toString('hex').slice(0, 16);
  const jitsiUrl = `https://meet.jit.si/afterlife-${sessionId}`;
  await sendText(sock, userJid, `Tap to join the call: ${jitsiUrl}`);
  return;
}
```

**Step 2: Verify TypeScript compiles**

```bash
cd services/whatsapp-sync && npx tsc --noEmit
```

Expected: zero errors.

**Step 3: Run CI checklist**

```bash
cd services/whatsapp-sync && npm install && npx tsc --noEmit && npm test
cd ../.. && ruff check services/ tests/ && python -m pytest tests/ -x -q
```

Expected: all pass with exit 0.

**Step 4: Commit**

```bash
git add services/whatsapp-sync/src/bot.ts
git commit -m "feat: detect 'call me' and send Jitsi URL to user"
```

---

### Task 8: Write TypeScript unit tests

**Files:**
- Create: `services/whatsapp-sync/src/bot.test.ts`
- Modify: `services/whatsapp-sync/package.json`

**Step 1: Add jest to devDependencies**

```json
{
  "devDependencies": {
    "@types/jest": "^29.5.0",
    "@types/node": "^20.0.0",
    "@types/qrcode": "^1.5.5",
    "@types/fluent-ffmpeg": "^2.1.24",
    "jest": "^29.7.0",
    "ts-jest": "^29.1.0",
    "ts-node": "^10.9.2",
    "typescript": "^5.3.0"
  },
  "scripts": {
    "test": "jest"
  }
}
```

Add jest config at bottom of package.json:
```json
{
  "jest": {
    "preset": "ts-jest",
    "testEnvironment": "node",
    "testMatch": ["**/*.test.ts"]
  }
}
```

**Step 2: Write bot.test.ts**

```typescript
// Test pure logic only — no Baileys socket needed.

// parseContactIntent
function parseContactIntent(text: string): string | null {
  const patterns = [
    /(?:talk to|call|connect with|speak to|i want)\s+(.+)/i,
    /^(.+)$/i,
  ];
  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match) return match[1].trim();
  }
  return null;
}

function isCallIntent(text: string): boolean {
  return /\b(call me|start a call|video call|voice call|let's call|lets call)\b/i.test(text);
}

function makeJitsiUrl(userJid: string): string {
  const sessionId = Buffer.from(`${userJid}-test`).toString('hex').slice(0, 16);
  return `https://meet.jit.si/afterlife-${sessionId}`;
}

describe('parseContactIntent', () => {
  it('extracts contact from "talk to mom"', () => {
    expect(parseContactIntent('talk to mom')).toBe('mom');
  });
  it('extracts contact from "i want dad"', () => {
    expect(parseContactIntent('i want dad')).toBe('dad');
  });
  it('treats plain text as contact name', () => {
    expect(parseContactIntent('grandma')).toBe('grandma');
  });
  it('returns null for empty string', () => {
    expect(parseContactIntent('')).toBeNull();
  });
});

describe('isCallIntent', () => {
  it('detects "call me"', () => {
    expect(isCallIntent('call me')).toBe(true);
  });
  it('detects "lets call"', () => {
    expect(isCallIntent("let's call")).toBe(true);
  });
  it('does not match regular text', () => {
    expect(isCallIntent('how are you')).toBe(false);
  });
});

describe('makeJitsiUrl', () => {
  it('returns a valid meet.jit.si URL', () => {
    const url = makeJitsiUrl('1234567890@s.whatsapp.net');
    expect(url).toMatch(/^https:\/\/meet\.jit\.si\/afterlife-[a-f0-9]+$/);
  });
});
```

**Step 3: Run tests**

```bash
cd services/whatsapp-sync && npm install && npm test
```

Expected: all 8 tests PASS.

**Step 4: Verify full CI checklist passes**

```bash
cd services/whatsapp-sync && npx tsc --noEmit && npm test
cd ../../.. && ruff check services/ tests/ && python -m pytest tests/ -x -q
```

Expected: all exit 0.

**Step 5: Commit**

```bash
git add services/whatsapp-sync/package.json services/whatsapp-sync/package-lock.json services/whatsapp-sync/src/bot.test.ts
git commit -m "test: add TypeScript unit tests for bot logic (contact intent, call detection, Jitsi URL)"
```

---

### Final CI Verification

Before opening MR, run this full checklist and confirm every line exits 0:

```bash
cd ~/gt/afterlife/refinery/rig

# Python
ruff check services/ tests/ --fix
ruff check services/ tests/
python -m pytest tests/ -x -q

# TypeScript
cd services/whatsapp-sync
npm install
npx tsc --noEmit
npm test
```
