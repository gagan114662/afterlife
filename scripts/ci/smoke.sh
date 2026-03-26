#!/usr/bin/env bash
# scripts/ci/smoke.sh — fast smoke checks that run without external services.
#
# Validates that critical service modules are importable and that the API
# health endpoint responds correctly — no MongoDB, Ollama, or ElevenLabs
# required.
#
# Usage:
#   ./scripts/ci/smoke.sh
#
# Exit 0 = smoke passed. Non-zero = something is broken at import/startup time.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

PY=python3

FAILED=0

# ─── Import smoke: critical Python modules ────────────────────────────────────
# These imports must succeed with only the packages in requirements.txt.
# ML heavy-deps (chromadb, kokoro, sentence_transformers) are stubbed via
# tests/conftest.py; smoke reproduces that stub inline so it needs no pytest.

echo "[smoke] import check: critical service modules"

IMPORT_SMOKE=$(cat <<'PYEOF'
import sys
from unittest.mock import MagicMock

_heavy = ["kokoro", "chromadb", "sentence_transformers", "TTS", "TTS.api"]
for mod in _heavy:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

import os
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")
os.environ.setdefault("MONGODB_DB", "afterlife")

# Critical path: consent (consent revoke, contact ingest, session start)
from services.api.consent import (
    ConsentStatus,
    ConsentRecord,
    ConsentNotFoundError,
    ConsentRevokedError,
    ConsentNotGrantedError,
    check_twin_eligibility,
    check_voice_eligibility,
    grant_consent,
    revoke_consent,
)

# Critical path: sessions (live voice session start)
from services.api.sessions import create_session, get_session, append_message

# Critical path: grounded text/voice reply
from services.api.conversation import reply_as_persona

# Critical path: biography / media backfill
from services.api.memory import load_contact_profile, update_biography

# Critical path: FastAPI app
from services.api.main import app

# Personality service
from services.personality.extractor import PersonalityExtractor
from services.personality.biographer import BiographerAgent

print("import smoke: OK")
PYEOF
)

if ! $PY -c "${IMPORT_SMOKE}"; then
    echo "[smoke] FAILED: import check"
    FAILED=1
else
    echo "[smoke]   ✓ all critical modules importable"
fi

# ─── Health endpoint smoke ────────────────────────────────────────────────────
# Calls /health via ASGI transport — no live server or DB needed.

echo "[smoke] /health endpoint"

HEALTH_SMOKE=$(cat <<'PYEOF'
import sys, asyncio, os
from unittest.mock import MagicMock

for mod in ["kokoro", "chromadb", "sentence_transformers", "TTS", "TTS.api"]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")
os.environ.setdefault("MONGODB_DB", "afterlife")

from httpx import AsyncClient, ASGITransport
from services.api.main import app

async def check():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/health")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    data = r.json()
    assert data.get("status") == "ok", f"Unexpected health payload: {data}"
    print(f"health smoke: OK (status={data['status']}, service={data.get('service')})")

asyncio.run(check())
PYEOF
)

if ! $PY -c "${HEALTH_SMOKE}"; then
    echo "[smoke] FAILED: /health endpoint"
    FAILED=1
else
    echo "[smoke]   ✓ /health returned ok"
fi

# ─── Result ───────────────────────────────────────────────────────────────────
if [[ "${FAILED}" -eq 0 ]]; then
    echo "[smoke] All smoke checks passed."
else
    echo "[smoke] One or more smoke checks failed."
    exit 1
fi
