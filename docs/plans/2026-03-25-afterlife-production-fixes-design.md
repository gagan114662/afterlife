# Afterlife Production Fixes Design
**Date:** 2026-03-25
**Status:** Approved

## Problem

The initial agent-built codebase has 33 production readiness issues identified via code review. Critical failures include: in-memory sessions lost on restart, non-functional memory system (zero-vector embeddings), no input validation, silent error swallowing, duplicate code across polecats, and zero CI/CD.

## Scope

Fix the most critical issues in two phases. Auth, rate limiting, encryption, and GDPR compliance are explicitly out of scope for this iteration.

## Approach

Targeted fix beads slinging to polecats in parallel, gated by standards and refinery checks.

---

## Phase 1 — Standards & Gates (blocking)

**Bead:** `af-fix-standards`

### CLAUDE.md (written to afterlife repo root)
Rules all agents must follow:
- No bare `except` clauses — always log with context and re-raise or return typed errors
- All Pydantic models use `Field(..., min_length=1, max_length=N)` on string inputs
- No in-memory state for data that must survive restart — use MongoDB
- All required env vars validated at startup; fail fast with clear error messages
- No hardcoded model names, URLs, or API keys — env vars with explicit defaults only
- Structured logging via `structlog` — every request tagged with correlation ID
- Every service exposes a `/health` endpoint returning `{"status": "ok"}`
- Integration tests must hit real services (mock only external paid APIs in unit tests)

### Refinery Gates
Configure in `~/gt/afterlife/settings/config.json`:
- `ruff check .` — Python linting (catches bare excepts, unused imports, style)
- `python -m pytest` — all tests must pass
- `tsc --noEmit` — TypeScript type checking, zero errors allowed

---

## Phase 2 — Parallel Fix Beads

All 7 beads sling simultaneously once Phase 1 is merged.

### af-fix-sessions
**Problem:** `_sessions: dict = {}` loses all state on restart, grows unbounded
**Fix:** Replace with MongoDB collection `sessions` with TTL index (24h expiry). Use `motor` for async MongoDB access. Session schema: `{session_id, contact_name, user_name, history: [], created_at, updated_at}`.

### af-fix-embeddings
**Problem:** `vector=[0.0] * 1536` — all memory queries return identical results
**Fix:** Replace with real Anthropic embeddings via `anthropic.embeddings.create()`. Store embeddings in MongoDB alongside messages. For Pinecone queries, generate embedding from user message first.

### af-fix-validation
**Problem:** No input length limits, user input injected raw into prompts
**Fix:**
- Add `Field(..., min_length=1, max_length=100)` to all Pydantic string fields
- Sanitize `contact_name` and `user_name` before injecting into prompts (strip special chars, limit to alphanumeric + spaces)
- Add `message` max length of 2000 chars
- Validate `session_id` is valid UUID format before lookup

### af-fix-errors
**Problem:** Bare `except` clauses, `json.loads()` without try/except, full exception details in HTTP responses
**Fix:**
- Wrap all `json.loads()` in try/except with fallback and logging
- Replace bare `except` / `except { /* ignore */ }` with specific exception types + logging
- Replace `raise HTTPException(detail=str(exc))` with generic client messages; log full exception server-side
- Add retry logic (3 attempts, exponential backoff) for Pinecone and ElevenLabs calls

### af-fix-dedup
**Problem:** voice-cloner identical in opal + jasper; whatsapp-sync duplicated in obsidian + quartz
**Fix:** Establish single canonical location in repo:
- `services/voice-cloner/` — one copy, remove duplicates
- `services/whatsapp-sync/` — one copy, merge best parts of obsidian + quartz versions
- `services/personality/` — one copy from onyx
- `services/api/` — one copy from opal

### af-fix-logging
**Problem:** Basic `logging.basicConfig()`, no correlation IDs, no startup validation
**Fix:**
- Replace all `logging.getLogger()` with `structlog` configured for JSON output
- Add request middleware that generates UUID correlation ID, attaches to all log lines
- Add startup validation function that checks all required env vars exist before accepting traffic
- Add `/health` endpoint to all FastAPI services

### af-fix-ci
**Problem:** Zero CI, no branch protection, code merges with no checks
**Fix:**
- Create `.github/workflows/ci.yml` running on push + PR to main:
  - `ruff check .` on Python code
  - `python -m pytest`
  - `tsc --noEmit` on TypeScript
  - `npm test` on TypeScript
- Configure branch protection on `main` via `gh api`:
  - Require CI to pass before merge
  - Require at least 1 approving review (can be waived for bot PRs)
  - Disallow force pushes

---

## Success Criteria

- All 7 fix beads merged to main
- CI passing on main branch
- Branch protection active on GitHub
- `pytest` runs without failures
- No `ruff` violations
- `tsc --noEmit` exits 0
- Sessions persist across service restart (manual test)
- Memory queries return different results for different messages (not all identical)

## Out of Scope

- Authentication / API keys
- Rate limiting
- Encryption at rest
- GDPR / data retention
- Load testing
- Monitoring / alerting
