# Staging Environment — Assumptions and Verification

This document describes the assumptions made for staging deployments and how to
verify them.

## Topology

Staging mirrors production topology with smaller compute:

| Service            | Staging host assumption               | Port  |
|--------------------|---------------------------------------|-------|
| MongoDB            | Managed Atlas cluster (or single VM)  | 27017 |
| Ollama             | Dedicated GPU instance or CPU VM      | 11434 |
| Conversation API   | Single container (2 replicas in prod) | 8000  |
| WhatsApp bot       | Single container (must not scale > 1) | —     |
| Personal sync      | Single container per user session     | —     |

> **WhatsApp bot and personal-sync MUST NOT be scaled horizontally.** Baileys
> maintains a persistent WebSocket session per WhatsApp account. Running two
> instances on the same phone number causes immediate disconnection.

## Required Secrets

Set these as environment variables (or in your secrets manager):

```
MONGODB_URI        # Atlas connection string with credentials
MONGODB_DB         # e.g. afterlife_staging
OLLAMA_HOST        # http://<ollama-vm>:11434
OLLAMA_MODEL       # llama3.2:3b (or the model pulled on the Ollama host)
API_BASE_URL       # https://api.afterlife.staging.<domain>
ADMIN_JID          # WhatsApp JID of the bot operator
ELEVENLABS_API_KEY # Optional — omit to use Coqui TTS
```

## Ollama Model Pre-pull

The Ollama model must be pulled before the API starts:

```bash
ssh <ollama-host> "ollama pull llama3.2:3b"
```

The API startup sequence warns if the model is missing but does not crash —
first requests will fail with a 502 until the model is available.

## Baileys Auth State Persistence

The WhatsApp bot stores its session in a directory (`baileys_auth_info/`).

- **Staging**: Mount a persistent volume or bind-mount to a path that survives
  container restarts. Loss of auth state forces a new QR scan.
- **First deploy**: After the bot starts, check its logs for the QR code and scan
  it with the staging bot phone number. Auth state is then saved and reused.

## MongoDB Indexes

Indexes are created automatically on API startup via `ensure_indexes()`. Verify
with:

```javascript
// mongosh
use afterlife_staging
db.sessions.getIndexes()
// Should include TTL index on updated_at (24h expiry)
```

## Health Check Endpoints

All services expose `/health`. Use these to gate deploys:

```bash
curl -sf http://<api-host>:8000/health | jq .
# Expected: {"status": "ok", "service": "conversation-api"}
```

## Smoke Tests

After deploying, run the smoke suite against staging:

```bash
API_BASE_URL=https://api.afterlife.staging.<domain> \
  python -m pytest tests/smoke/ -v
```

The smoke suite calls `/health`, starts a test conversation, and verifies the
response shape without requiring real WhatsApp connectivity.

## Fixture Replay (no live WhatsApp)

For CI and staging pipelines that cannot link a real WhatsApp account:

1. Place pre-recorded contact exports in `data/fixtures/contacts/`:
   ```
   data/fixtures/contacts/
     test_contact/
       messages.json
       voice_notes/
       metadata.json
   ```
2. Set `CONTACTS_DIR=/data/fixtures/contacts` in your environment.
3. Start only `mongodb + ollama + api`:
   ```bash
   docker compose up -d mongodb ollama api
   ```
4. Call the API directly:
   ```bash
   curl -X POST http://localhost:8000/conversation/start \
     -H 'Content-Type: application/json' \
     -d '{"contact_name": "test_contact", "user_name": "tester"}'
   ```

The WhatsApp bot services are not required for this path.

## Rollback

To roll back to the previous image:

```bash
docker compose pull --no-parallel
docker compose up -d --no-recreate  # or pin image tags in compose file
```

For MongoDB: Atlas supports point-in-time restore. Do not drop the database
to rollback — restore from a snapshot.
