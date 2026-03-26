# Operations Guide

Monitoring, incident response, and rollback procedures for the Afterlife platform.

---

## Services

| Service | Language | Port | Health endpoint |
|---------|----------|------|-----------------|
| Conversation API | Python / FastAPI | 8000 | `GET /health` |
| Personality service | Python (library) | — | import check |
| Voice Cloner | Python | — | import check |
| WhatsApp Sync | TypeScript / Node | — | — |

---

## Critical Paths

The following flows are production-critical and must remain green at all times.
CI enforces integration test coverage for each service that touches them.

| Path | Entry point | Key modules |
|------|-------------|-------------|
| **Consented contact ingest** | `POST /consent/grant` | `services/api/consent.py` |
| **Consent revoke** | `POST /consent/revoke` | `services/api/consent.py` |
| **Live voice session start** | `POST /conversation/start` | `services/api/sessions.py`, `services/api/consent.py` |
| **Grounded text reply** | `POST /conversation/message` | `services/api/conversation.py`, `services/api/memory.py` |
| **Grounded voice reply** | `POST /conversation/message` (with TTS) | `services/api/conversation.py`, `services/voice-cloner/` |
| **Media backfill** | `POST /biography/update` | `services/api/memory.py`, `services/personality/` |

---

## Health Checks

### Conversation API

```bash
curl http://localhost:8000/health
# Expected: {"status":"ok","service":"conversation-api","version":"..."}
```

A non-200 response or `"status" != "ok"` indicates the service is unhealthy.

### Consent status

```bash
curl "http://localhost:8000/consent/status?contact_name=Alice&owner_user_id=user123"
# Expected: {"status":"active",...} or {"status":"pending",...}
```

### Full smoke (no running server required)

```bash
bash scripts/ci/smoke.sh
```

Validates all critical service modules are importable and the health endpoint
responds correctly over ASGI transport without any external dependencies.

---

## Monitoring Checklist

Check the following after every deployment:

1. **Health endpoint** — `GET /health` returns `200 ok` within 500 ms.
2. **Consent grant flow** — create a test consent record; verify it appears in
   MongoDB with `status=active`.
3. **Session start** — call `POST /conversation/start` with a consented contact;
   verify a session document is created in MongoDB.
4. **Message round-trip** — send a message to the test session; verify a reply
   is returned within the configured timeout.
5. **Consent revoke** — revoke the test consent; verify subsequent `start`
   calls are rejected with `403`.
6. **Log sampling** — tail the structured log output and confirm no
   `ERROR`-level entries for normal requests.

---

## Rollback Procedure

### 1. Identify the bad commit

```bash
git log --oneline main | head -20
```

Find the commit hash that introduced the regression.

### 2. Revert the bad commit (preferred — creates a new commit, preserves history)

```bash
git revert <bad-commit-sha>
git push origin main
```

CI runs automatically on push. Verify the health endpoint after deploy.

### 3. Hard rollback (use only if revert is blocked by conflicts)

```bash
# Create a rollback branch from the last known-good commit
git checkout -b rollback/<incident-id> <last-good-sha>
git push origin rollback/<incident-id>
# Open a PR or route through the merge queue
```

Do **not** force-push to `main`. Route all rollbacks through CI.

### 4. Database rollback

MongoDB state (consent records, sessions, personality profiles) is **not**
automatically rolled back with a git revert. If the bad deploy wrote corrupt
documents:

1. Stop the Conversation API to prevent further writes.
2. Connect to MongoDB and inspect the `consent`, `sessions`, and `profiles`
   collections for records created during the incident window.
3. Restore from a point-in-time backup taken before the bad deploy, or
   manually correct the affected documents.
4. Restart the API once the data is clean.

---

## CI Gate Reference

| Script | When to run | What it checks |
|--------|-------------|----------------|
| `scripts/ci/smoke.sh` | Before every deploy | Module imports + `/health` via ASGI |
| `scripts/ci/verify.sh` | Every commit / PR | ruff lint + pytest core suite |
| `scripts/ci/verify.sh --changed` | PRs with service changes | As above + targeted integration paths |
| `scripts/ci/release-check.sh` | Before cutting a release | smoke + verify --changed + bandit + Docker build |

Run locally before pushing:

```bash
bash scripts/ci/smoke.sh          # fast (< 5 s)
bash scripts/ci/verify.sh         # full suite
bash scripts/ci/release-check.sh  # pre-release (bandit + Docker optional)
```

---

## Incident Severity Levels

| Level | Examples | Response |
|-------|---------|----------|
| **P0** | Consent revoke broken; sessions created without consent | Page on-call immediately; rollback within 30 min |
| **P1** | Voice reply fails; media backfill stuck | Fix within 4 h; no rollback required unless data corrupt |
| **P2** | Single endpoint slow; non-critical test failing | Fix in next sprint |

For P0 incidents, escalate immediately and initiate rollback procedure above.
