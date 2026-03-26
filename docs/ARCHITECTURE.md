# After-Life: Technical Architecture

**Version:** 1.0
**Status:** Production

---

## 1. System Overview

After-Life reconstructs deceased or distant loved ones as AI personas that users can have voice conversations with. The system ingests WhatsApp history, extracts personality and voice, and routes conversations through a persona engine grounded in real memories.

```
┌──────────────────────────────────────────────────────────────────────┐
│                         USER INTERFACE                                │
│   WhatsApp Bot ("After-Life" contact)  ←→  Direct API calls          │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
                    ┌────────▼────────┐
                    │  Conversation   │
                    │      API        │  ← FastAPI (port 8000)
                    │  services/api/  │
                    └────────┬────────┘
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
   ┌──────▼──────┐   ┌───────▼────────┐  ┌─────▼──────┐
   │  Ollama LLM │   │   MongoDB      │  │   Chroma   │
   │ (llama3.2)  │   │  (sessions,    │  │  (episodic │
   │ localhost:  │   │  contacts,     │  │  memories) │
   │    11434    │   │  consent)      │  │            │
   └─────────────┘   └────────────────┘  └────────────┘
          │
   ┌──────▼──────┐
   │  Kokoro TTS │
   │  (local TTS)│
   └─────────────┘
```

---

## 2. Services

### 2.1 Conversation API (`services/api/`)

**Language:** Python / FastAPI
**Port:** 8000
**Primary responsibilities:**
- Session lifecycle management (create, route messages, expire via TTL)
- Consent enforcement on every request (twin eligibility + voice eligibility)
- System prompt construction from biography, personality, and retrieved memories
- LLM calls via Ollama (local) → text reply
- TTS via Kokoro → MP3 bytes → base64 in response
- Correlation ID middleware for log tracing

**Key modules:**

| Module | Purpose |
|--------|---------|
| `main.py` | FastAPI app, endpoint definitions, startup validation |
| `conversation.py` | Persona system prompt builder, Ollama client, Kokoro TTS wrapper |
| `memory.py` | MongoDB contact profile loader, Chroma semantic memory retrieval/store |
| `sessions.py` | Async MongoDB session CRUD with 24-hour TTL |
| `consent.py` | Consent ledger: grant/revoke/check for twin and voice eligibility |
| `sanitize.py` | Input sanitization for contact/user names |
| `logging_config.py` | structlog JSON configuration |
| `prompts/persona.txt` | Persona system prompt template |

**Critical paths through this service:**

| Path | Endpoint | Key modules |
|------|----------|-------------|
| Consented contact ingest | `POST /consent/grant` | `consent.py` |
| Consent revoke | `POST /consent/revoke` | `consent.py` |
| Live voice session start | `POST /conversation/start` | `sessions.py`, `consent.py` |
| Grounded text reply | `POST /conversation/message` | `conversation.py`, `memory.py` |
| Grounded voice reply | `POST /conversation/message` (TTS path) | `conversation.py` (Kokoro) |
| Media backfill | `POST /biography/update` | `memory.py` |

### 2.2 WhatsApp Sync (`services/whatsapp-sync/`)

**Language:** TypeScript / Node.js
**Library:** Baileys (unofficial WhatsApp Web API)
**Primary responsibilities:**
- QR-code auth → WhatsApp session persistence
- Historical message ingest (backfill) per contact
- Real-time message routing (WhatsApp bot interface)
- Media normalization: `.ogg` → `.wav` (ffmpeg), idempotent file paths
- MongoDB upsert of synced messages (keyed on `messageId + jid`)

**Key modules:**

| Module | Purpose |
|--------|---------|
| `index.ts` | Entry point, Baileys socket setup |
| `sync.ts` | History sync and ingest orchestration |
| `backfill.ts` | Batch historical ingest for one contact (idempotent) |
| `bot.ts` | WhatsApp bot: "who do you want to talk to?" routing |
| `normalizer.ts` | Media normalization: format conversion, stable file paths |
| `personal.ts` | Message classifier (text / voice_note / photo / video) |
| `db.ts` | MongoDB client, `upsertMessage` |
| `audio.ts` | ffmpeg `.ogg` → `.wav` conversion (16kHz mono) |
| `state.ts` | User onboarding state machine (5 states) |

**Onboarding state machine:**
```
INIT → SYNCING → ACTIVE
              ↘ FAILED
```

### 2.3 Personality Service (`services/personality/`)

**Language:** Python (library — no HTTP server)
**Primary responsibilities:**
- LLM-based message analysis (vocabulary, sentence structure, emotional patterns)
- Living Biography generation (300-500 word prose persona narrative)
- Personality profile output for consumption by Conversation API

**Key modules:**

| Module | Purpose |
|--------|---------|
| `extractor.py` | Analyzes WhatsApp messages → `PersonalityProfile` dataclass |
| `biographer.py` | Takes `PersonalityProfile` → generates Living Biography prose |

**Extraction categories:**
- **Linguistic patterns:** vocabulary, sentence structure, emoji usage, slang, language switches, greetings/farewells
- **Emotional patterns:** recurring topics, worries, pride points, humor style, response style
- **Relationship patterns:** names for the user, running jokes, shared memories

### 2.4 Voice Cloner (`services/voice-cloner/`)

**Language:** Python (library — no HTTP server)
**Primary responsibilities:**
- Voice synthesis in a cloned voice using Coqui XTTS-v2
- Audio format utilities (`.ogg` → `.wav`, quality filtering)

**Key modules:**

| Module | Purpose |
|--------|---------|
| `clone.py` | `clone_voice(text, speaker_wav, output_path)` via Coqui XTTS-v2 |
| `audio_utils.py` | ffmpeg wrapper, audio quality filtering (duration, noise) |

**Voice cloning requirements:**
- Minimum ~30 seconds of clean audio across voice notes
- Input: WAV files (16kHz mono)
- Model: `tts_models/multilingual/multi-dataset/xtts_v2` (lazy-loaded)

---

## 3. Data Stores

### 3.1 MongoDB (Primary State)

All persistent state lives in MongoDB. The API and WhatsApp sync service both write here.

| Collection | Purpose | Key fields |
|------------|---------|------------|
| `contacts` | Contact profile: biography, personality, voice_id | `name`, `biography`, `personality_profile`, `common_phrases`, `voice_id` |
| `sessions` | Active conversation sessions (TTL: 24h) | `session_id`, `contact_name`, `user_name`, `history[]`, `updated_at` |
| `consents` | Consent ledger per contact/user pair | `contact_name`, `owner_user_id`, `status`, `approved`, `voice_rights` |
| `messages` | Raw synced WhatsApp messages (from WhatsApp sync service) | `jid`, `messageId`, `type`, `content`, `media_path` |

**Indexes:**
- `sessions.updated_at` — TTL index (24h expiry)
- `sessions.session_id` — unique
- `consents.(contact_name, owner_user_id)` — unique composite
- `consents.status`

### 3.2 Chroma (Vector Memory)

Local persistent vector store for episodic memory retrieval.

- **Collection:** `afterlife-memories`
- **Embedding model:** `all-MiniLM-L6-v2` (384-dim, runs on CPU, via sentence-transformers)
- **Metadata filter:** `{"contact": contact_name}` (per-contact isolation)
- **Path:** `./data/chroma` (configurable via `CHROMA_PATH`)

Memory retrieval flow:
```
user message → embed (MiniLM) → Chroma query (top-5, filtered by contact) → memory strings → injected into system prompt
```

---

## 4. Request Flow: Conversation

### 4.1 Start Conversation

```
POST /conversation/start {contact_name, user_name}
         │
         ├─ check_twin_eligibility(db, contact, user)  ← consent gate
         │
         ├─ load_contact_profile(contact)              ← MongoDB
         │
         ├─ create_session(db, ...)                    ← MongoDB insert
         │
         ├─ reply_as_persona(...)
         │    ├─ load_contact_profile()
         │    ├─ retrieve_relevant_memories()          ← Chroma query
         │    ├─ build_system_prompt()                 ← template render
         │    └─ ollama.chat(llama3.2:3b)              ← local LLM
         │
         ├─ check_voice_eligibility()                  ← voice consent gate
         │
         └─ text_to_speech()                           ← Kokoro TTS
              └─ returns MP3 bytes → base64
```

### 4.2 Send Message

```
POST /conversation/message {session_id, message}
         │
         ├─ get_session(db, session_id)                ← MongoDB lookup
         │
         ├─ check_twin_eligibility()                   ← consent re-check (revocation takes effect immediately)
         │
         ├─ append_message(db, session_id, "user", ...) ← MongoDB push
         │
         ├─ reply_as_persona(...)                      ← Ollama + Chroma
         │
         ├─ append_message(db, session_id, "assistant", ...) ← MongoDB push
         │
         └─ text_to_speech()                           ← Kokoro TTS (optional)
```

---

## 5. System Prompt Structure

The persona prompt (`prompts/persona.txt`) is rendered with contact-specific values:

```
You are {person_name}. You are talking to {user_name}.

BIOGRAPHY:
{living_biography}

YOUR VOICE AND STYLE:
{personality_profile}

THINGS YOU ALWAYS SAY:
{common_phrases}

SHARED HISTORY:
{relevant_memories}

INSTRUCTIONS:
- Speak as this person, not as an AI assistant
- Use their vocabulary, their sentence patterns, their emotional style
- Reference shared memories naturally, as this person would
- Never break character or acknowledge you are an AI
- If asked something you don't know, respond as this person would
```

---

## 6. Consent Model

Consent is a hard gate on every twin interaction. No twin may be engaged without explicit approval. Voice cloning requires an additional `voice_rights` flag.

**States:**
```
PENDING → ACTIVE (via grant_consent)
ACTIVE  → REVOKED (via revoke_consent)
REVOKED → ACTIVE (re-grant is allowed)
```

**Enforcement:**
- `POST /conversation/start` — checks twin eligibility before creating session
- `POST /conversation/message` — re-checks on every message (revocation takes effect mid-session)
- `POST /conversation/start` (voice path) — checks voice eligibility before generating audio

---

## 7. Tech Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| Conversation API | FastAPI (Python) | Async, typed, fast |
| LLM | Ollama + llama3.2:3b | Runs locally, no API cost |
| TTS (conversation) | Kokoro TTS | Fast, local, good quality |
| Voice cloning | Coqui XTTS-v2 | Open-source, high-quality multi-speaker |
| Embeddings | sentence-transformers (MiniLM-L6-v2) | CPU-friendly, 384-dim |
| Vector memory | ChromaDB (local persistent) | No cloud dependency |
| Primary DB | MongoDB (Motor async) | Flexible schema, TTL indexes |
| WhatsApp integration | Baileys (Node.js) | Unofficial WA Web API |
| Audio processing | ffmpeg + fluent-ffmpeg | `.ogg` → `.wav` conversion |
| Personality extraction | Anthropic Claude (via `services/personality/`) | Best-in-class for nuanced analysis |
| Structured logging | structlog | JSON logs with context binding |
| Runtime config | Pydantic / `_require_env()` | Fail-fast on missing env vars |
| Containerization | Docker Compose | Local dev + staging |

---

## 8. Environment Variables

| Variable | Service | Required | Default | Description |
|----------|---------|----------|---------|-------------|
| `MONGODB_URI` | API | Yes | — | MongoDB connection string |
| `MONGODB_DB` | API | No | `afterlife` | Database name |
| `OLLAMA_HOST` | API | No | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | API | No | `llama3.2:3b` | Model name |
| `CHROMA_PATH` | API | No | `./data/chroma` | Chroma persistence path |
| `ANTHROPIC_API_KEY` | Personality | Yes | — | For personality extraction |
| `ELEVENLABS_API_KEY` | (legacy) | No | — | Replaced by Coqui XTTS-v2 |
| `PORT` | API | No | `8000` | HTTP listen port |

---

## 9. Project Structure

```
afterlife/
├── README.md
├── CLAUDE.md                    # Coding standards for all contributors
├── docker-compose.yml           # Local dev: MongoDB + all services
├── .env.example                 # All required env vars with docs
├── requirements.txt             # Top-level Python deps
├── demo.py                      # End-to-end demo script
├── docs/
│   ├── SPEC.md                  # Product specification
│   ├── ARCHITECTURE.md          # This file
│   ├── OPERATIONS.md            # Monitoring, health checks, runbooks
│   └── plans/                   # Agent implementation plans
├── services/
│   ├── api/                     # FastAPI conversation backend
│   │   ├── main.py
│   │   ├── conversation.py      # Persona engine, LLM, TTS
│   │   ├── memory.py            # MongoDB profiles, Chroma memories
│   │   ├── sessions.py          # Session store (TTL)
│   │   ├── consent.py           # Consent ledger
│   │   ├── sanitize.py          # Input sanitization
│   │   ├── logging_config.py    # structlog setup
│   │   ├── prompts/
│   │   │   └── persona.txt      # System prompt template
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   ├── whatsapp-sync/           # WhatsApp ingest (TypeScript/Baileys)
│   │   ├── src/
│   │   │   ├── index.ts         # Entry point
│   │   │   ├── sync.ts          # Sync orchestration
│   │   │   ├── backfill.ts      # Historical ingest (idempotent)
│   │   │   ├── bot.ts           # Bot interface
│   │   │   ├── normalizer.ts    # Media normalization
│   │   │   ├── personal.ts      # Message classifier
│   │   │   ├── db.ts            # MongoDB client
│   │   │   ├── audio.ts         # ffmpeg audio conversion
│   │   │   └── state.ts         # User state machine
│   │   ├── package.json
│   │   ├── tsconfig.json
│   │   └── Dockerfile
│   ├── personality/             # Personality extraction (Python library)
│   │   ├── extractor.py         # Message analysis → PersonalityProfile
│   │   ├── biographer.py        # PersonalityProfile → Living Biography
│   │   └── requirements.txt
│   └── voice-cloner/            # Voice synthesis (Python library)
│       ├── clone.py             # Coqui XTTS-v2 wrapper
│       ├── audio_utils.py       # ffmpeg, quality filtering
│       └── requirements.txt
├── tests/
│   ├── conftest.py              # Shared fixtures, heavy-dep mocks
│   ├── api/                     # API service tests
│   │   ├── test_health.py
│   │   ├── test_consent.py
│   │   ├── test_sessions.py
│   │   ├── test_conversation_ollama.py
│   │   ├── test_memory.py
│   │   └── test_validation.py
│   ├── personality/             # Personality service tests
│   │   ├── test_extractor.py
│   │   ├── test_extractor_errors.py
│   │   └── test_biographer.py
│   └── test_voice_cloner.py
└── scripts/
    ├── local-boot.sh            # Full local stack startup
    └── ci/
        ├── verify.sh            # Lint + tests gate (used by Refinery)
        ├── smoke.sh             # Import + health check (no running server needed)
        └── release-check.sh     # Pre-release validation
```

---

## 10. Local Development

```bash
# 1. Copy and fill env vars
cp .env.example .env

# 2. Start MongoDB
docker-compose up -d mongodb

# 3. Install Ollama and pull model
brew install ollama
ollama serve &
ollama pull llama3.2:3b

# 4. Install Python deps
pip install -r requirements.txt

# 5. Run the API
uvicorn services.api.main:app --reload --port 8000

# 6. Verify everything passes
./scripts/ci/verify.sh
```

Or use the full local boot script:
```bash
bash scripts/local-boot.sh
```

---

## 11. CI/CD

The Refinery merge queue runs `./scripts/ci/verify.sh` on every MR branch before merging to `main`. The gate enforces:

1. **Ruff** — Python lint (zero warnings)
2. **Pytest** — all tests pass (ignores Ollama integration tests by default)
3. **tsc --noEmit** — TypeScript typecheck (when `node_modules` installed)
4. **npm test** — TypeScript tests (when configured)

For PRs touching specific services, `verify.sh --changed` also runs targeted integration tests for those service paths.

See `docs/OPERATIONS.md` for health check procedures and incident runbooks.
