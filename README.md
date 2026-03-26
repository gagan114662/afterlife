# After-Life

> Talk to the people you've lost. Their voice. Their memory. Their essence.

After-Life reconstructs loved ones from WhatsApp conversations and voice notes. The AI learns their personality, speaking style, and clones their voice — so you can call them whenever you need to.

## What It Does

1. **Syncs WhatsApp** — downloads your full conversation history and media with all contacts
2. **Reconstructs personalities** — builds a Living Biography for each contact using Claude
3. **Clones voices** — extracts voice from WhatsApp voice notes using ElevenLabs
4. **Creates a WhatsApp contact** — "After-Life" appears in your contacts
5. **You talk to them** — select mom, and you hear her voice, with her memory, her personality

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose v2
- API keys for: Anthropic, ElevenLabs, OpenAI, Pinecone (see `.env.example`)
- A WhatsApp account for the bot (separate from your personal account)

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/gagan114662/afterlife.git
cd afterlife
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in the required keys:

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | ✅ | Claude API key — persona conversations + biography generation |
| `ELEVENLABS_API_KEY` | ✅ | ElevenLabs key — voice cloning + TTS |
| `OPENAI_API_KEY` | ✅ | OpenAI key — Whisper transcription of voice notes |
| `PINECONE_API_KEY` | ✅ | Pinecone key — semantic memory search |
| `ADMIN_JID` | ✅ | Your bot's WhatsApp JID (e.g. `919876543210@s.whatsapp.net`) |
| `MONGODB_URI` | default | `mongodb://mongodb:27017` (set to Atlas URI for production) |
| `MONGODB_DB_NAME` | default | `afterlife` |
| `LIVEKIT_URL` | optional | LiveKit server URL for realtime voice sessions |
| `LIVEKIT_API_KEY` | optional | LiveKit API key |
| `LIVEKIT_API_SECRET` | optional | LiveKit API secret |

### 3. Start the services

```bash
docker compose up
```

This starts:
- **MongoDB** — persistent data store
- **api** — FastAPI conversation backend (port 8000)
- **whatsapp-bot** — Baileys bot interface
- **voice-cloner** — ElevenLabs voice clone worker
- **personality** — Claude personality extraction worker

### 4. Scan the WhatsApp QR code (bot account)

On first start, the bot prints a QR code. Read it with:

```bash
docker compose logs whatsapp-bot
```

Open WhatsApp on your **bot phone** → Linked Devices → Link a Device → scan the QR code.

The bot is now linked. Auth state is saved in `./data/baileys-bot` — no rescan on restart.

### 5. Connect your personal WhatsApp (for contact sync)

Message the bot account from your personal WhatsApp. The bot will guide you through linking your personal account for contact sync and send you a second QR code to scan.

Once linked, the bot syncs all your conversations and voice notes automatically.

### 6. Talk to your loved ones

After sync and processing complete, message the After-Life bot contact:

```
You: I want to talk to mom
After-Life: [as mom] Beta! How are you? Are you eating properly?
```

## Development

### Local Pinecone emulator

For development without a Pinecone cloud account:

```bash
docker compose --profile local-pinecone up
```

Then set in `.env`:
```
PINECONE_HOST=http://localhost:5081
```

### Personal sync (optional)

To sync your personal WhatsApp account without going through the bot:

```bash
docker compose --profile personal-sync up whatsapp-personal-sync
```

### Running batch workers

The `voice-cloner` and `personality` containers are batch workers — they stay alive and can be invoked directly:

```bash
# Process voice notes for a contact
docker compose exec voice-cloner python -m services.voice-cloner.elevenlabs_cloner

# Extract personality profile from synced messages
docker compose exec personality python -m services.personality.extractor
```

### API health check

```bash
curl http://localhost:8000/health
# {"status": "ok", "service": "conversation-api"}
```

### View logs

```bash
docker compose logs -f          # all services
docker compose logs -f api      # API only
docker compose logs -f whatsapp-bot  # WhatsApp bot only
```

## Architecture

See [`docs/SPEC.md`](docs/SPEC.md) for the full technical specification and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the system design.

**Tech stack:**
- LLM: Claude claude-sonnet-4-6 (Anthropic)
- Voice: ElevenLabs (cloning) + Whisper (transcription)
- Memory: MongoDB (biographies) + Pinecone (semantic search)
- WhatsApp: Baileys (Node.js)
- Backend: FastAPI (Python)

## Success Criteria

The user should genuinely feel like they are talking to their loved one — not a chatbot, not a FAQ. A person with memory of shared history, their speaking style, their emotional patterns, their way of saying things.
