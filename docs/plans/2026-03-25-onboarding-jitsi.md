# Branch 3: Onboarding State Machine + Jitsi Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the current ad-hoc bot flow with a proper 5-state onboarding machine so every new user automatically receives a QR code, their contacts sync in the background, and typing "call me" sends a Jitsi room link.

**Architecture:** User state lives in MongoDB `user_state` collection (persisted across restarts). The bot's `handleMessage` function dispatches on current state. State transitions: INIT → QR_SENT → LINKED → SYNCING → ACTIVE. All state reads/writes go through a `StateManager` class. Background sync runs as a separate async process that updates state to ACTIVE when done and notifies the user.

**Tech Stack:** @whiskeysockets/baileys, mongodb (npm), TypeScript 5

---

### Task 1: Create StateManager TypeScript module

**Files:**
- Create: `services/whatsapp-sync/src/state.ts`

**Step 1: Write state.ts**

```typescript
import { Collection } from 'mongodb';
import { getCollection } from './db';

export enum UserState {
  INIT = 'INIT',         // New user, never seen before
  QR_SENT = 'QR_SENT',  // QR code sent, waiting for scan
  LINKED = 'LINKED',    // Personal WhatsApp linked, triggering sync
  SYNCING = 'SYNCING',  // Background sync in progress
  ACTIVE = 'ACTIVE',    // Sync complete, ready for conversations
}

export interface UserStateDoc {
  jid: string;
  state: UserState;
  contact_count: number;
  selected_contact: string | null;
  session_id: string | null;
  updated_at: string;
}

async function getStateCollection(): Promise<Collection<UserStateDoc>> {
  return getCollection<UserStateDoc>('user_state');
}

export async function getUserState(jid: string): Promise<UserStateDoc> {
  const col = await getStateCollection();
  const doc = await col.findOne({ jid });
  if (!doc) {
    return {
      jid,
      state: UserState.INIT,
      contact_count: 0,
      selected_contact: null,
      session_id: null,
      updated_at: new Date().toISOString(),
    };
  }
  return doc;
}

export async function setUserState(
  jid: string,
  updates: Partial<Omit<UserStateDoc, 'jid'>>
): Promise<void> {
  const col = await getStateCollection();
  await col.updateOne(
    { jid },
    {
      $set: {
        ...updates,
        updated_at: new Date().toISOString(),
      },
    },
    { upsert: true }
  );
}

export async function ensureStateIndex(): Promise<void> {
  const col = await getStateCollection();
  await col.createIndex({ jid: 1 }, { unique: true });
}
```

**Step 2: Verify TypeScript compiles**

```bash
cd services/whatsapp-sync && npx tsc --noEmit
```

Expected: zero errors.

**Step 3: Commit**

```bash
git add services/whatsapp-sync/src/state.ts
git commit -m "feat: add UserState enum and StateManager for MongoDB-backed user state"
```

---

### Task 2: Write unit tests for StateManager

**Files:**
- Create: `services/whatsapp-sync/src/state.test.ts`

**Step 1: Write state.test.ts**

```typescript
import { UserState } from './state';

// Pure enum tests — no DB needed
describe('UserState enum', () => {
  it('has the 5 required states', () => {
    expect(UserState.INIT).toBe('INIT');
    expect(UserState.QR_SENT).toBe('QR_SENT');
    expect(UserState.LINKED).toBe('LINKED');
    expect(UserState.SYNCING).toBe('SYNCING');
    expect(UserState.ACTIVE).toBe('ACTIVE');
  });

  it('state values are strings (safe to store in MongoDB)', () => {
    for (const value of Object.values(UserState)) {
      expect(typeof value).toBe('string');
    }
  });
});

// State transition logic
function nextState(current: UserState, event: string): UserState {
  switch (current) {
    case UserState.INIT:
      return UserState.QR_SENT;
    case UserState.QR_SENT:
      if (event === 'qr_scanned') return UserState.LINKED;
      return UserState.QR_SENT;
    case UserState.LINKED:
      return UserState.SYNCING;
    case UserState.SYNCING:
      if (event === 'sync_complete') return UserState.ACTIVE;
      return UserState.SYNCING;
    case UserState.ACTIVE:
      return UserState.ACTIVE;
  }
}

describe('state transitions', () => {
  it('INIT -> QR_SENT on first message', () => {
    expect(nextState(UserState.INIT, 'hi')).toBe(UserState.QR_SENT);
  });

  it('QR_SENT -> LINKED on qr_scanned', () => {
    expect(nextState(UserState.QR_SENT, 'qr_scanned')).toBe(UserState.LINKED);
  });

  it('stays QR_SENT on other events', () => {
    expect(nextState(UserState.QR_SENT, 'some text')).toBe(UserState.QR_SENT);
  });

  it('LINKED -> SYNCING immediately', () => {
    expect(nextState(UserState.LINKED, 'any')).toBe(UserState.SYNCING);
  });

  it('SYNCING -> ACTIVE on sync_complete', () => {
    expect(nextState(UserState.SYNCING, 'sync_complete')).toBe(UserState.ACTIVE);
  });

  it('ACTIVE stays ACTIVE', () => {
    expect(nextState(UserState.ACTIVE, 'anything')).toBe(UserState.ACTIVE);
  });
});
```

**Step 2: Run tests**

```bash
cd services/whatsapp-sync && npm test
```

Expected: all tests pass.

**Step 3: Commit**

```bash
git add services/whatsapp-sync/src/state.test.ts
git commit -m "test: state machine transition tests"
```

---

### Task 3: Rewrite bot.ts with state machine dispatch

**Files:**
- Modify: `services/whatsapp-sync/src/bot.ts`

This is the main change. The new `handleMessage` replaces the current ad-hoc flow with explicit state dispatch.

**Step 1: Replace handleMessage in bot.ts**

Remove the current `handleMessage` function and replace it with this state-machine version.

First, update imports at top of bot.ts:

```typescript
import { getUserState, setUserState, UserState, ensureStateIndex } from './state';
```

Remove `const activeSessions` and `const pendingQR` (state is now in MongoDB).

Replace `handleMessage` with:

```typescript
async function handleMessage(
  sock: ReturnType<typeof makeWASocket>,
  userJid: string,
  msg: WAMessage
): Promise<void> {
  const message = msg.message!;

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
      const buffer = await downloadMediaMessage(
        msg as Parameters<typeof downloadMediaMessage>[0],
        'buffer',
        {}
      );
      fs.writeFileSync(tmpOgg, buffer as Buffer);
      await convertOggToWav(tmpOgg, tmpWav);
      audioBuffer = fs.readFileSync(tmpWav);
      userText = '[voice note]';
    } catch (err) {
      console.warn('[bot] Failed to process voice note:', err);
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
```

**Step 2: Add handleStateInit function**

```typescript
async function handleStateInit(
  sock: ReturnType<typeof makeWASocket>,
  userJid: string
): Promise<void> {
  await setUserState(userJid, { state: UserState.QR_SENT });
  // The personal sync process will fire QR via the polling file mechanism
  // Bot polls /tmp/afterlife_personal_qr.txt and sends the image
  await sendText(
    sock,
    userJid,
    "Hi! I'm After-Life. I'll sync your WhatsApp contacts so you can speak with them.\n\nSending you a QR code now — scan it with: WhatsApp → Settings → Linked Devices → Link a Device"
  );
  // Mark pending so the QR polling interval picks it up
  pendingQR.set(userJid, 'waiting');
}
```

Note: re-add `const pendingQR = new Map<string, string>()` since it's used by handleStateInit.

**Step 3: Add handleStateActive function**

```typescript
async function handleStateActive(
  sock: ReturnType<typeof makeWASocket>,
  userJid: string,
  userText: string,
  audioBuffer: Buffer | null,
  userStateDoc: import('./state').UserStateDoc
): Promise<void> {
  // Jitsi call trigger
  if (/\b(call me|start a call|video call|voice call|let's call|lets call)\b/i.test(userText)) {
    const sessionId = Buffer.from(`${userJid}-${Date.now()}`).toString('hex').slice(0, 16);
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
    // User hasn't picked a contact yet
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
```

**Step 4: Update converseAndRespond to persist session_id to state**

Update signature and save session_id:

```typescript
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
      const audioB64 = startResp.data.greeting_audio_b64;
      if (audioB64) {
        const audioBytes = Buffer.from(audioB64, 'base64');
        await sock.sendMessage(userJid, { audio: audioBytes, mimetype: 'audio/mpeg', ptt: true });
      } else {
        await sendText(sock, userJid, greeting);
      }
      if (userText === 'Hello') return;
    }

    const msgResp = await axios.post<{ reply_text: string; reply_audio_b64?: string }>(
      `${API_BASE_URL}/conversation/message`,
      { session_id: sessionId, message: userText },
      { headers: { 'Content-Type': 'application/json' } }
    );

    const replyText = msgResp.data.reply_text;
    const audioB64 = msgResp.data.reply_audio_b64;
    if (audioB64) {
      const audioBytes = Buffer.from(audioB64, 'base64');
      await sock.sendMessage(userJid, { audio: audioBytes, mimetype: 'audio/mpeg', ptt: true });
    } else {
      await sendText(sock, userJid, replyText);
    }
  } catch (err) {
    console.error('[bot] Conversation error:', err);
    await sendText(sock, userJid, 'Something went wrong. Please try again.');
  }
}
```

**Step 5: Call ensureStateIndex in runBot()**

At the start of `runBot()`, add:

```typescript
await ensureStateIndex();
```

**Step 6: Verify TypeScript compiles**

```bash
cd services/whatsapp-sync && npx tsc --noEmit
```

Expected: zero errors.

**Step 7: Commit**

```bash
git add services/whatsapp-sync/src/bot.ts
git commit -m "feat: replace ad-hoc bot flow with 5-state onboarding machine (INIT→QR_SENT→LINKED→SYNCING→ACTIVE)"
```

---

### Task 4: Update personal sync to advance user state on completion

When personal sync finishes, update all SYNCING users to ACTIVE and notify them.

**Files:**
- Modify: `services/whatsapp-sync/src/personal.ts`
- Modify: `services/whatsapp-sync/src/index.ts`

**Step 1: Update personal.ts onProgress callback to accept jid list**

The `runPersonalSync` in `index.ts` needs to know which user triggered the sync so it can update their state. Pass the triggerJid into the function:

In personal.ts, update the function signature:

```typescript
export async function runPersonalSync(
  onQR: (qr: string) => void,
  onProgress: SyncProgressCallback,
  onComplete?: (total: number) => void
): Promise<void>
```

Call `onComplete?.(total)` after `syncAllContacts` finishes.

**Step 2: Update index.ts to update state on sync complete**

In the `runPersonalSync` call in index.ts, replace the `sync_complete` file-writing with a MongoDB state update:

```typescript
import { setUserState, UserState, getUserState } from './state';
import { getDb } from './db';

// In the onComplete callback:
async (total: number) => {
  console.log(`[personal] Sync complete: ${total} contacts`);
  // Find all users in SYNCING state and advance them to ACTIVE
  const db = await getDb();
  const userStateCol = db.collection('user_state');
  const syncingUsers = await userStateCol.find({ state: UserState.SYNCING }).toArray();
  for (const u of syncingUsers) {
    await setUserState(u.jid, { state: UserState.ACTIVE, contact_count: total });
  }
  // Write completion file for bot's polling interval to pick up
  const fs = require('fs');
  fs.writeFileSync('/tmp/afterlife_sync_done.txt', String(total));
}
```

**Step 3: Verify TypeScript compiles**

```bash
cd services/whatsapp-sync && npx tsc --noEmit
```

Expected: zero errors.

**Step 4: Run all tests**

```bash
npm test
```

Expected: all pass.

**Step 5: Commit**

```bash
git add services/whatsapp-sync/src/personal.ts services/whatsapp-sync/src/index.ts
git commit -m "feat: advance user state SYNCING→ACTIVE when personal sync completes"
```

---

### Task 5: Update bot.ts QR polling to transition QR_SENT → LINKED

When the personal QR is consumed, mark the user as LINKED so sync starts.

**Files:**
- Modify: `services/whatsapp-sync/src/bot.ts`

**Step 1: Update QR polling interval**

In the QR polling interval inside `runBot()`, after consuming the QR file and sending images, update user state:

```typescript
// After sending QR images to pending users:
for (const [jid] of pendingQR) {
  await sendQRImage(sock, jid, qrData);
  // Transition: QR_SENT stays QR_SENT until personal instance confirms linked
  // The personal instance fires connection.update → 'open' which signals linked
}
```

When the personal sync calls `onQR`, also write a "linked" file when the personal socket opens. In personal.ts, update the `connection.update` handler to write `/tmp/afterlife_personal_linked.txt` on first successful open:

```typescript
} else if (connection === 'open') {
  const fs = require('fs');
  fs.writeFileSync('/tmp/afterlife_personal_linked.txt', 'linked');
  console.log('[personal] Connected. Starting contact sync...');
  await syncAllContacts(sock, onProgress);
  onComplete?.(/* total from syncAllContacts */);
  resolve();
}
```

In bot.ts polling interval, also check for this linked file:

```typescript
const linkedFile = '/tmp/afterlife_personal_linked.txt';
if (fs.existsSync(linkedFile)) {
  fs.unlinkSync(linkedFile);
  // Advance all QR_SENT users to LINKED → SYNCING
  const { getUserState, setUserState, UserState } = await import('./state');
  for (const [jid] of pendingQR) {
    const doc = await getUserState(jid);
    if (doc.state === UserState.QR_SENT) {
      await setUserState(jid, { state: UserState.SYNCING });
      await sendText(sock, jid, 'Linked! Syncing your contacts in the background...');
    }
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
git add services/whatsapp-sync/src/bot.ts services/whatsapp-sync/src/personal.ts
git commit -m "feat: transition QR_SENT→LINKED→SYNCING when personal WhatsApp links and starts sync"
```

---

### Task 6: Add Jitsi URL unit test

**Files:**
- Modify: `services/whatsapp-sync/src/bot.test.ts`

**Step 1: Add Jitsi detection tests**

Add to bot.test.ts:

```typescript
describe('isCallIntent', () => {
  const isCallIntent = (text: string): boolean =>
    /\b(call me|start a call|video call|voice call|let's call|lets call)\b/i.test(text);

  it('detects "call me"', () => expect(isCallIntent('call me')).toBe(true));
  it('detects "video call"', () => expect(isCallIntent('video call')).toBe(true));
  it('detects case insensitive', () => expect(isCallIntent('CALL ME')).toBe(true));
  it('does not match "talk to me"', () => expect(isCallIntent('talk to me')).toBe(false));
  it('does not match partial word "recall"', () => expect(isCallIntent('recall')).toBe(false));
});

describe('Jitsi URL generation', () => {
  it('generates a valid meet.jit.si URL', () => {
    const jid = '441234567890@s.whatsapp.net';
    const id = Buffer.from(`${jid}-1234`).toString('hex').slice(0, 16);
    const url = `https://meet.jit.si/afterlife-${id}`;
    expect(url).toMatch(/^https:\/\/meet\.jit\.si\/afterlife-[a-f0-9]{16}$/);
  });
});
```

**Step 2: Run tests**

```bash
cd services/whatsapp-sync && npm test
```

Expected: all pass.

**Step 3: Commit**

```bash
git add services/whatsapp-sync/src/bot.test.ts
git commit -m "test: add Jitsi URL generation and call intent detection tests"
```

---

### Final CI Verification

Before opening MR, run this full checklist. Every command must exit 0:

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

If any command fails, fix it before creating the MR. Do not skip.
