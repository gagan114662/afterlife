# Afterlife Production Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix 33 critical production issues across the afterlife codebase so the app is reliable, testable, and maintainable.

**Architecture:** Phase 1 establishes standards and gates (CLAUDE.md + refinery config + CI). Phase 2 runs 8 fix beads in parallel, each merged through the now-gated refinery. All services consolidate into `services/` at repo root.

**Tech Stack:** Python 3.11, FastAPI, motor (async MongoDB), structlog, ruff, pytest, TypeScript, Node 20, GitHub Actions

---

## PHASE 1: af-fix-standards

### Task 1: Write CLAUDE.md

**Files:**
- Create: `CLAUDE.md`

**Step 1: Create the file**

```markdown
# Afterlife — Coding Standards

These rules apply to ALL agents working in this repo. The refinery will reject MRs that violate them.

## Python

- No bare `except` clauses. Always catch specific exceptions. Always log with context.
- All Pydantic models use `Field(..., min_length=1, max_length=N)` on every string input.
- No in-memory state for data that must survive restart. Use MongoDB.
- All required env vars validated at startup. Use this pattern:

```python
def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Required environment variable {name!r} is not set")
    return val
```

- No hardcoded model names, base URLs, or API keys. Use env vars with explicit defaults.
- Structured logging via `structlog`. Never use `print()` or bare `logging.basicConfig()`.
- Every FastAPI service exposes `GET /health` returning `{"status": "ok", "service": "<name>"}`.
- Every request gets a UUID correlation ID attached to all log lines for that request.

## Error Handling

- Wrap all `json.loads()` calls in try/except. Log the raw response on failure.
- HTTP responses to clients: generic message only. Full exception logged server-side.
- Retry transient external API calls (3 attempts, exponential backoff starting at 1s).

## TypeScript

- `tsc --noEmit` must pass with zero errors.
- No empty catch blocks. Always log with `console.error`.
- Use `?.` optional chaining before accessing fields from external messages.

## Testing

- Unit tests: mock only external paid APIs (Anthropic, ElevenLabs, Pinecone).
- Integration tests: hit real MongoDB (use test database `afterlife_test`).
- Every new function needs at least one test.
- Tests live in `tests/` mirroring `services/` structure.

## Services Structure

All services live at repo root:
```
services/
  api/           # FastAPI conversation API
  personality/   # Personality extraction + biography
  voice-cloner/  # ElevenLabs voice cloning
  whatsapp-sync/ # WhatsApp message sync (TypeScript)
tests/
  api/
  personality/
  voice-cloner/
```

## Commits

- Prefix: feat/fix/refactor/test/docs/chore
- One logical change per commit
- Never commit with failing tests
```

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add CLAUDE.md coding standards"
```

---

### Task 2: Configure Refinery Gates

**Files:**
- Modify: `~/gt/afterlife/settings/config.json`

**Step 1: Read the current config**

```bash
cat ~/gt/afterlife/settings/config.json
```

**Step 2: Add gates**

```json
{
  "type": "rig-settings",
  "version": 1,
  "merge_queue": {
    "enabled": true,
    "max_concurrent": 1,
    "gates": {
      "lint": {
        "cmd": "ruff check services/ tests/",
        "timeout": "2m"
      },
      "test": {
        "cmd": "python -m pytest tests/ -x -q",
        "timeout": "5m"
      },
      "typecheck": {
        "cmd": "cd services/whatsapp-sync && tsc --noEmit",
        "timeout": "2m"
      }
    }
  }
}
```

**Step 3: Commit**

```bash
git add ~/gt/afterlife/settings/config.json
git commit -m "chore: add refinery gates (ruff, pytest, tsc)"
```

---

### Task 3: Add GitHub Actions CI

**Files:**
- Create: `.github/workflows/ci.yml`

**Step 1: Write the workflow**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  python:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install dependencies
        run: |
          pip install ruff pytest structlog fastapi motor anthropic httpx pydantic
          if [ -f services/api/requirements.txt ]; then pip install -r services/api/requirements.txt; fi
          if [ -f services/personality/requirements.txt ]; then pip install -r services/personality/requirements.txt; fi
          if [ -f services/voice-cloner/requirements.txt ]; then pip install -r services/voice-cloner/requirements.txt; fi
      - name: Lint
        run: ruff check services/ tests/
      - name: Test
        run: python -m pytest tests/ -x -q
        env:
          MONGODB_URI: mongodb://localhost:27017
          MONGODB_DB: afterlife_test

    services:
      mongodb:
        image: mongo:7
        ports:
          - 27017:27017

  typescript:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - name: Install dependencies
        run: cd services/whatsapp-sync && npm install
      - name: Type check
        run: cd services/whatsapp-sync && npx tsc --noEmit
      - name: Test
        run: cd services/whatsapp-sync && npm test
```

**Step 2: Enable branch protection via GitHub API**

```bash
gh api repos/gagan114662/afterlife/branches/main/protection \
  --method PUT \
  --field required_status_checks='{"strict":true,"contexts":["python","typescript"]}' \
  --field enforce_admins=false \
  --field required_pull_request_reviews=null \
  --field restrictions=null
```

**Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add GitHub Actions CI with ruff, pytest, tsc + branch protection"
```

---

## PHASE 2: Parallel Fix Beads

---

## af-fix-sessions

### Task 4: Replace In-Memory Sessions with MongoDB

**Files:**
- Modify: `services/api/main.py`
- Create: `services/api/sessions.py`
- Modify: `services/api/requirements.txt`
- Create: `tests/api/test_sessions.py`

**Step 1: Write failing test**

```python
# tests/api/test_sessions.py
import pytest
from services.api.sessions import create_session, get_session, append_message

@pytest.fixture
def mongo_client():
    from motor.motor_asyncio import AsyncIOMotorClient
    return AsyncIOMotorClient("mongodb://localhost:27017")

@pytest.mark.asyncio
async def test_session_persists(mongo_client):
    db = mongo_client["afterlife_test"]
    session_id = await create_session(db, "mom", "Gagan")
    session = await get_session(db, session_id)
    assert session["contact_name"] == "mom"
    assert session["user_name"] == "Gagan"
    assert session["history"] == []

@pytest.mark.asyncio
async def test_append_message(mongo_client):
    db = mongo_client["afterlife_test"]
    session_id = await create_session(db, "dad", "Gagan")
    await append_message(db, session_id, "user", "hello")
    session = await get_session(db, session_id)
    assert len(session["history"]) == 1
    assert session["history"][0] == {"role": "user", "content": "hello"}
```

**Step 2: Run test, verify FAIL**

```bash
pytest tests/api/test_sessions.py -v
# Expected: ModuleNotFoundError: No module named 'services.api.sessions'
```

**Step 3: Implement sessions.py**

```python
# services/api/sessions.py
import uuid
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorDatabase

SESSION_TTL_SECONDS = 86400  # 24 hours


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """Call once at startup to create TTL index."""
    await db.sessions.create_index("updated_at", expireAfterSeconds=SESSION_TTL_SECONDS)
    await db.sessions.create_index("session_id", unique=True)


async def create_session(db: AsyncIOMotorDatabase, contact_name: str, user_name: str) -> str:
    session_id = str(uuid.uuid4())
    now = datetime.utcnow()
    await db.sessions.insert_one({
        "session_id": session_id,
        "contact_name": contact_name,
        "user_name": user_name,
        "history": [],
        "created_at": now,
        "updated_at": now,
    })
    return session_id


async def get_session(db: AsyncIOMotorDatabase, session_id: str) -> dict | None:
    return await db.sessions.find_one({"session_id": session_id}, {"_id": 0})


async def append_message(db: AsyncIOMotorDatabase, session_id: str, role: str, content: str) -> None:
    await db.sessions.update_one(
        {"session_id": session_id},
        {
            "$push": {"history": {"role": role, "content": content}},
            "$set": {"updated_at": datetime.utcnow()},
        },
    )
```

**Step 4: Update main.py to use sessions module**

Replace `_sessions: dict = {}` and all references. Add at startup:
```python
from services.api.sessions import ensure_indexes, create_session, get_session, append_message

@app.on_event("startup")
async def startup():
    await ensure_indexes(app.state.db)
```

**Step 5: Run tests, verify PASS**

```bash
pytest tests/api/test_sessions.py -v
# Expected: PASSED (2 tests)
```

**Step 6: Commit**

```bash
git add services/api/sessions.py services/api/main.py tests/api/test_sessions.py
git commit -m "fix: replace in-memory sessions with MongoDB TTL-backed sessions"
```

---

## af-fix-embeddings

### Task 5: Replace Zero Vectors with Real Anthropic Embeddings

**Files:**
- Modify: `services/api/memory.py`
- Create: `tests/api/test_memory.py`

**Step 1: Write failing test**

```python
# tests/api/test_memory.py
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_embeddings_not_zero():
    """Verify that embeddings are generated from actual message content."""
    from services.api.memory import get_embedding
    with patch("anthropic.Anthropic") as mock_client:
        mock_client.return_value.embeddings.create.return_value.embeddings = [
            type("E", (), {"embedding": [0.1, 0.2, 0.3]})()
        ]
        result = await get_embedding("hello world")
    assert result != [0.0] * len(result), "Embeddings must not be all zeros"
    assert len(result) > 0
```

**Step 2: Run test, verify FAIL**

```bash
pytest tests/api/test_memory.py::test_embeddings_not_zero -v
# Expected: FAIL — function returns [0.0, 0.0, ...]
```

**Step 3: Implement real embeddings**

```python
# services/api/memory.py — replace the placeholder query section

import anthropic
import os

_anthropic = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

async def get_embedding(text: str) -> list[float]:
    """Generate embedding for text using Anthropic API."""
    response = _anthropic.embeddings.create(
        model="voyage-3",
        input=[text],
    )
    return response.embeddings[0].embedding

# In retrieve_relevant_memories(), replace:
#   vector=[0.0] * 1536
# with:
#   vector=await get_embedding(query_text)
```

**Step 4: Run tests, verify PASS**

```bash
pytest tests/api/test_memory.py -v
```

**Step 5: Commit**

```bash
git add services/api/memory.py tests/api/test_memory.py
git commit -m "fix: replace zero-vector embeddings with real Anthropic embeddings"
```

---

## af-fix-validation

### Task 6: Add Input Validation and Prompt Injection Sanitization

**Files:**
- Modify: `services/api/main.py`
- Create: `services/api/sanitize.py`
- Create: `tests/api/test_validation.py`

**Step 1: Write failing tests**

```python
# tests/api/test_validation.py
import pytest
from httpx import AsyncClient
from services.api.main import app

@pytest.mark.asyncio
async def test_rejects_empty_contact_name():
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.post("/conversation/start", json={"contact_name": "", "user_name": "Gagan"})
    assert resp.status_code == 422

@pytest.mark.asyncio
async def test_rejects_oversized_message():
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.post("/conversation/message", json={
            "session_id": "abc",
            "message": "x" * 2001
        })
    assert resp.status_code == 422

def test_sanitize_strips_injection():
    from services.api.sanitize import sanitize_name
    result = sanitize_name('mom"; DROP TABLE contacts; --')
    assert "DROP" not in result
    assert '"' not in result
```

**Step 2: Run tests, verify FAIL**

```bash
pytest tests/api/test_validation.py -v
```

**Step 3: Implement sanitize.py**

```python
# services/api/sanitize.py
import re

_SAFE_NAME_PATTERN = re.compile(r"[^a-zA-Z0-9\s\-']")

def sanitize_name(name: str) -> str:
    """Strip characters unsafe for use in prompts or DB queries."""
    return _SAFE_NAME_PATTERN.sub("", name).strip()[:100]
```

**Step 4: Update Pydantic models in main.py**

```python
from pydantic import BaseModel, Field, field_validator
from services.api.sanitize import sanitize_name

class StartRequest(BaseModel):
    contact_name: str = Field(..., min_length=1, max_length=100)
    user_name: str = Field(..., min_length=1, max_length=100)

    @field_validator("contact_name", "user_name")
    @classmethod
    def sanitize(cls, v: str) -> str:
        return sanitize_name(v)

class MessageRequest(BaseModel):
    session_id: str = Field(..., min_length=36, max_length=36)  # UUID format
    message: str = Field(..., min_length=1, max_length=2000)
```

**Step 5: Run tests, verify PASS**

```bash
pytest tests/api/test_validation.py -v
```

**Step 6: Commit**

```bash
git add services/api/main.py services/api/sanitize.py tests/api/test_validation.py
git commit -m "fix: add input validation and prompt injection sanitization"
```

---

## af-fix-errors

### Task 7: Fix Error Handling — JSON Parsing, Bare Excepts, HTTP Responses

**Files:**
- Modify: `services/personality/extractor.py`
- Modify: `services/api/main.py`
- Modify: `services/whatsapp-sync/src/bot.ts`
- Create: `tests/personality/test_extractor_errors.py`

**Step 1: Write failing test**

```python
# tests/personality/test_extractor_errors.py
import pytest
from unittest.mock import MagicMock

def test_extractor_handles_invalid_json():
    """Extractor must not crash when Claude returns malformed JSON."""
    from services.personality.extractor import PersonalityExtractor
    extractor = PersonalityExtractor.__new__(PersonalityExtractor)
    extractor._client = MagicMock()
    extractor._client.messages.create.return_value.content = [
        MagicMock(text="this is not json {{{")
    ]
    # Should not raise — should return a fallback profile
    result = extractor.extract([{"role": "user", "content": "hi"}], "mom", "Gagan")
    assert result is not None
    assert result.contact_name == "mom"
```

**Step 2: Run test, verify FAIL**

```bash
pytest tests/personality/test_extractor_errors.py -v
# Expected: json.JSONDecodeError raised
```

**Step 3: Fix extractor.py**

```python
# In extract() method, replace bare json.loads():
try:
    raw = response.content[0].text.strip()
    data = json.loads(raw)
except (json.JSONDecodeError, IndexError, AttributeError) as exc:
    logger.warning(
        "claude_response_parse_failed",
        error=str(exc),
        raw_response=raw[:200] if raw else None,
        contact_name=contact_name,
    )
    data = {}  # Fall back to empty profile

return PersonalityProfile(
    contact_name=contact_name,
    user_name=user_name,
    linguistic_patterns=data.get("linguistic_patterns") or {},
    emotional_patterns=data.get("emotional_patterns") or {},
    topics=data.get("topics") or [],
    common_phrases=data.get("common_phrases") or [],
)
```

**Step 4: Fix HTTP error responses in main.py**

Replace all:
```python
raise HTTPException(status_code=502, detail=f"Failed to ...: {exc}")
```
With:
```python
logger.error("operation_failed", error=str(exc), exc_info=True)
raise HTTPException(status_code=502, detail="Service temporarily unavailable. Please try again.")
```

**Step 5: Fix TypeScript empty catch blocks in bot.ts**

Replace all:
```typescript
} catch { /* ignore */ }
```
With:
```typescript
} catch (err) {
  console.error('[afterlife] cleanup error:', err);
}
```

**Step 6: Run tests, verify PASS**

```bash
pytest tests/personality/test_extractor_errors.py -v
```

**Step 7: Commit**

```bash
git add services/personality/extractor.py services/api/main.py services/whatsapp-sync/src/bot.ts tests/personality/test_extractor_errors.py
git commit -m "fix: handle JSON parse errors and improve error responses"
```

---

## af-fix-dedup

### Task 8: Consolidate Duplicate Services

**Files:**
- Create canonical: `services/voice-cloner/` (from opal version)
- Create canonical: `services/whatsapp-sync/` (merge obsidian + quartz best parts)
- Create canonical: `services/personality/` (from onyx version)
- Create canonical: `services/api/` (from opal version)
- Delete polecat copies after consolidation

**Step 1: Identify canonical versions**

```bash
# Compare the two voice-cloner copies
diff ~/gt/afterlife/polecats/opal/afterlife/services/voice-cloner/clone.py \
     ~/gt/afterlife/polecats/jasper/afterlife/services/voice-cloner/clone.py
```

Use the version with more recent fixes. If identical, either works.

**Step 2: Copy canonical versions to repo root services/**

```bash
cp -r ~/gt/afterlife/polecats/opal/afterlife/services/api/ services/api/
cp -r ~/gt/afterlife/polecats/opal/afterlife/services/voice-cloner/ services/voice-cloner/
cp -r ~/gt/afterlife/polecats/onyx/afterlife/services/personality/ services/personality/
```

For whatsapp-sync, pick the version with better error handling (check which has fewer empty catch blocks).

**Step 3: Update all internal imports to use canonical paths**

Verify no service imports from `polecats/` paths.

**Step 4: Add requirements.txt at repo root for CI**

```
# requirements.txt
fastapi>=0.110
motor>=3.3
structlog>=24.1
anthropic>=0.25
httpx>=0.27
pydantic>=2.6
pytest>=8.1
pytest-asyncio>=0.23
ruff>=0.3
```

**Step 5: Verify tests still pass**

```bash
pytest tests/ -x -q
ruff check services/ tests/
```

**Step 6: Commit**

```bash
git add services/ requirements.txt
git commit -m "refactor: consolidate duplicate services into canonical locations"
```

---

## af-fix-logging

### Task 9: Add Structured Logging, Startup Validation, Health Endpoints

**Files:**
- Create: `services/api/logging_config.py`
- Modify: `services/api/main.py`
- Modify: `services/personality/extractor.py`
- Modify: `services/voice-cloner/clone.py`
- Create: `tests/api/test_health.py`

**Step 1: Write failing test**

```python
# tests/api/test_health.py
import pytest
from httpx import AsyncClient
from services.api.main import app

@pytest.mark.asyncio
async def test_health_endpoint():
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "conversation-api"}
```

**Step 2: Run test, verify FAIL**

```bash
pytest tests/api/test_health.py -v
# Expected: 404 Not Found
```

**Step 3: Implement logging_config.py**

```python
# services/api/logging_config.py
import structlog
import logging

def configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
    )
```

**Step 4: Implement startup validation and health in main.py**

```python
import os
import uuid
import structlog
from services.api.logging_config import configure_logging

configure_logging()
logger = structlog.get_logger()

def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Required environment variable {name!r} is not set")
    return val

@app.on_event("startup")
async def startup():
    # Fail fast if env vars missing
    _require_env("ANTHROPIC_API_KEY")
    _require_env("ELEVENLABS_API_KEY")
    _require_env("MONGODB_URI")
    logger.info("startup_complete", service="conversation-api")

@app.middleware("http")
async def correlation_id_middleware(request, call_next):
    correlation_id = str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    structlog.contextvars.clear_contextvars()
    return response

@app.get("/health")
async def health():
    return {"status": "ok", "service": "conversation-api"}
```

**Step 5: Run tests, verify PASS**

```bash
pytest tests/api/test_health.py -v
```

**Step 6: Commit**

```bash
git add services/api/logging_config.py services/api/main.py tests/api/test_health.py
git commit -m "feat: add structlog, startup env validation, correlation IDs, health endpoint"
```

---

## af-fix-ci

### Task 10: GitHub Actions CI + Branch Protection

**Files:**
- Create: `.github/workflows/ci.yml`

**Step 1: Write the workflow** (see Task 3 above — same content)

**Step 2: Enable branch protection**

```bash
gh api repos/gagan114662/afterlife/branches/main/protection \
  --method PUT \
  --input - <<'EOF'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["python", "typescript"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null
}
EOF
```

**Step 3: Verify CI triggers**

```bash
git push origin main
gh run list --repo gagan114662/afterlife --limit 5
```

**Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add GitHub Actions CI + branch protection on main"
```

---

## Execution Order

```
af-fix-standards (Task 1-3) → MERGE → then all in parallel:
  af-fix-sessions   (Task 4)
  af-fix-embeddings (Task 5)
  af-fix-validation (Task 6)
  af-fix-errors     (Task 7)
  af-fix-dedup      (Task 8)
  af-fix-logging    (Task 9)
  af-fix-ci         (Task 10)
```

## Verification Checklist

After all beads merged:
- [ ] `pytest tests/ -x -q` — all green
- [ ] `ruff check services/ tests/` — zero violations
- [ ] `tsc --noEmit` in whatsapp-sync — zero errors
- [ ] `GET /health` returns 200
- [ ] Session survives service restart (create session, restart, retrieve session)
- [ ] Memory query for "hello" returns different results than query for "what time did we meet"
- [ ] GitHub Actions CI shows green on main branch
- [ ] Branch protection active — test by opening a PR with a ruff violation
