# WhatsApp Integration + Open Source Stack Design
**Date:** 2026-03-25
**Status:** Approved

## Goal

Build a fully working WhatsApp bot that lets users talk to deceased loved ones via text and voice notes, with a Jitsi live voice call option. Zero paid APIs — all open source, runs on MacBook.

## User Flow

```
1. User texts dedicated WhatsApp number: "hi"
2. Bot sends QR code image in chat
3. User scans QR: WhatsApp → Settings → Linked Devices → Link a Device
4. Bot syncs ALL user's chats + voice notes silently in background
5. Bot: "47 contacts ready. Who do you want to talk to?"
6. User: "mom"
7. Bot replies as mom — text + voice note in her cloned voice
8. User: "call me" → bot sends Jitsi room link → live voice conversation
```

## Open Source Stack

| Component | Replaces | Technology |
|-----------|----------|------------|
| LLM | Anthropic Claude | Ollama + llama3.2:3b |
| TTS | ElevenLabs | Kokoro TTS (82M params, CPU fast) |
| Voice cloning | ElevenLabs clone | Coqui XTTS-v2 |
| Vector memory | Pinecone | Chroma (local) |
| WhatsApp bot | — | Baileys (Node.js) |
| Personal sync | — | Second Baileys instance |
| Live calls | Twilio | Jitsi (meet.jit.si, free) |
| Database | — | MongoDB (unchanged) |

## Architecture

```
[User's Personal WhatsApp] ←→ [Baileys Instance 2 - personal]
                                        ↓ sync history
[User texts bot] → [Baileys Instance 1 - dedicated SIM]
                                        ↓
                           [Conversation API (FastAPI)]
                                        ↓
                    ┌───────────────────┼───────────────────┐
                    ↓                   ↓                   ↓
             [Ollama LLM]        [Kokoro TTS]         [Chroma DB]
                    ↓                   ↓
             [text reply]      [voice note MP3]
                    └───────────────────┘
                                        ↓
                           [back to user via Baileys 1]

"call me" → generate meet.jit.si/<uuid> → send as WhatsApp message
```

## Three Sequential Branches

### Branch 1: feature/whatsapp-baileys
**One polecat, one session.**

- Connect existing Baileys scaffold to conversation API end-to-end
- QR code auth: Baileys instance 1 (bot) generates QR → sends as image to user
- Baileys instance 2 (personal): authenticates via QR scan, pulls full message + voice note history
- Sync pipeline: messages → MongoDB contacts collection, voice notes → `data/voice_samples/<contact>/`
- Message handler: receives user text → calls POST /conversation/start or /conversation/message → sends reply
- "call me" handler: generates `https://meet.jit.si/afterlife-<uuid>` → sends as message

**CI requirements (must pass before MR):**
```bash
cd services/whatsapp-sync && npm install && npx tsc --noEmit && npm test
ruff check services/ tests/
python -m pytest tests/ -x -q
```

### Branch 2: feature/open-source-stack
**One polecat, rebases from main after Branch 1 merges.**

- Install Ollama + pull llama3.2:3b model
- Replace `anthropic.Anthropic` calls in `conversation.py` with `ollama.chat()`
- Replace ElevenLabs TTS in `conversation.py` with Kokoro TTS Python package
- Replace Pinecone in `memory.py` with Chroma (`chromadb` package)
- Add Coqui XTTS-v2 to `voice-cloner/clone.py` for voice cloning from WAV samples
- Update `requirements.txt` — remove anthropic, pinecone; add ollama, kokoro-tts, chromadb, TTS
- Update startup validation — remove ANTHROPIC_API_KEY, ELEVENLABS_API_KEY, PINECONE_API_KEY requirements
- Add Ollama health check at startup (verify `ollama list` shows llama3.2:3b)

**CI requirements (must pass before MR):**
```bash
ruff check services/ tests/
python -m pytest tests/ -x -q
cd services/whatsapp-sync && npx tsc --noEmit && npm test
```

### Branch 3: feature/onboarding-jitsi
**One polecat, rebases from main after Branch 2 merges.**

- Onboarding state machine in Baileys message handler:
  - State 0 (new user): send QR code image
  - State 1 (QR sent): wait for personal number link confirmation
  - State 2 (linked): trigger background sync → "Syncing your contacts..."
  - State 3 (synced): "X contacts ready. Who do you want to talk to?"
  - State 4 (active): route messages to conversation API
- "call me" trigger: detect phrase → generate Jitsi URL → send link with message "Tap to call"
- Jitsi URL format: `https://meet.jit.si/afterlife-{session_id}`
- User state persisted in MongoDB `user_state` collection
- Background sync progress tracked: update state when all contacts processed

**CI requirements (must pass before MR):**
```bash
ruff check services/ tests/
python -m pytest tests/ -x -q
cd services/whatsapp-sync && npx tsc --noEmit && npm test
```

## CI Zero-Error Policy

Every bead description must include this mandatory pre-MR checklist. The polecat MUST run all checks and fix all errors before calling `gt done`:

```bash
# Python
ruff check services/ tests/ --fix   # auto-fix what's fixable
ruff check services/ tests/          # verify zero remaining
python -m pytest tests/ -x -q       # all tests pass

# TypeScript
cd services/whatsapp-sync
npm install
npx tsc --noEmit                     # zero type errors
npm test                             # passes

# Only submit if ALL above pass with exit code 0
```

## Out of Scope

- WhatsApp voice/video calls (Jitsi link is the call mechanism)
- End-to-end encryption of synced messages
- Multiple simultaneous users
- Auth / rate limiting (deferred from Phase 1)
