#!/usr/bin/env bash
# scripts/ci/verify.sh — local and CI verification gate
#
# Usage:
#   ./scripts/ci/verify.sh              # full verify (lint + tests)
#   ./scripts/ci/verify.sh --changed    # also run targeted integration tests
#                                       # for services changed vs origin/main
#
# Exit 0 = all gates pass. Non-zero = at least one gate failed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

FAILED=0
CHANGED_MODE=false
for arg in "$@"; do
    [[ "$arg" == "--changed" ]] && CHANGED_MODE=true
done

# ─── Python: ruff lint ────────────────────────────────────────────────────────
echo "[verify] ruff check ."
if command -v ruff >/dev/null 2>&1; then
    if ! ruff check .; then
        echo "[verify] FAILED: ruff"
        FAILED=1
    fi
else
    echo "[verify] SKIP: ruff not installed"
fi

# ─── Python: pytest (core) ────────────────────────────────────────────────────
echo "[verify] pytest tests/ (core)"
PY=python3
if command -v python >/dev/null 2>&1; then PY=python; fi

if $PY -m pytest --version >/dev/null 2>&1; then
    if ! $PY -m pytest tests/ -q \
        --ignore=tests/api/test_conversation_ollama.py \
        --ignore=tests/api/test_memory.py; then
        echo "[verify] FAILED: pytest"
        FAILED=1
    fi
else
    echo "[verify] SKIP: pytest not installed"
fi

# ─── Integration path coverage for changed services ──────────────────────────
# When --changed is passed (e.g. in PR CI), detect which service directories
# were touched and run their targeted test files.  This ensures regressions in
# one service don't slip through the general suite.
#
# Critical paths covered: consented contact ingest, media backfill,
# grounded text reply, grounded voice reply, live voice session start,
# consent revoke.
if [[ "${CHANGED_MODE}" == "true" ]]; then
    echo "[verify] integration path check (changed services)"

    MERGE_BASE=$(git merge-base HEAD origin/main 2>/dev/null \
                  || git merge-base HEAD main 2>/dev/null \
                  || echo "")
    if [[ -n "${MERGE_BASE}" ]]; then
        CHANGED=$(git diff --name-only "${MERGE_BASE}"..HEAD)
    else
        CHANGED=$(git diff --name-only HEAD~1..HEAD 2>/dev/null || echo "")
    fi

    EXTRA_TESTS=()

    # Map: service directory prefix → test path
    declare -A SERVICE_TEST_MAP
    SERVICE_TEST_MAP["services/api"]="tests/api"
    SERVICE_TEST_MAP["services/personality"]="tests/personality"
    SERVICE_TEST_MAP["services/voice-cloner"]="tests/test_voice_cloner.py"

    for svc in "${!SERVICE_TEST_MAP[@]}"; do
        if echo "${CHANGED}" | grep -q "^${svc}/"; then
            test_path="${SERVICE_TEST_MAP[$svc]}"
            if [[ -e "${test_path}" ]]; then
                EXTRA_TESTS+=("${test_path}")
                echo "[verify]   + changed: ${svc} → ${test_path}"
            fi
        fi
    done

    if [[ ${#EXTRA_TESTS[@]} -gt 0 ]]; then
        readarray -t UNIQUE_TESTS < <(printf '%s\n' "${EXTRA_TESTS[@]}" | sort -u)
        if ! $PY -m pytest "${UNIQUE_TESTS[@]}" -q; then
            echo "[verify] FAILED: integration path tests"
            FAILED=1
        fi
    else
        echo "[verify]   (no tracked service changes detected)"
    fi
fi

# ─── TypeScript: tsc + npm test ───────────────────────────────────────────────
# Only run TypeScript checks when node_modules is installed.  In CI the
# workflow runs `npm ci` before calling this script; locally, run
# `npm ci` inside each TypeScript service directory first.
while IFS= read -r -d '' tsconfig; do
    tsdir="$(dirname "${tsconfig}")"
    if [[ ! -d "${tsdir}/node_modules" ]]; then
        echo "[verify] SKIP: ${tsdir} — node_modules not installed (run 'npm ci' first)"
        continue
    fi
    echo "[verify] tsc --noEmit in ${tsdir}"
    if ! (cd "${tsdir}" && npx tsc --noEmit 2>/dev/null); then
        echo "[verify] FAILED: tsc in ${tsdir}"
        FAILED=1
    fi
    echo "[verify] npm test in ${tsdir}"
    if ! (cd "${tsdir}" && npm test --if-present 2>/dev/null); then
        echo "[verify] FAILED: npm test in ${tsdir}"
        FAILED=1
    fi
done < <(find services -name "tsconfig.json" -not -path "*/node_modules/*" -print0)

# ─── Result ───────────────────────────────────────────────────────────────────
if [[ "${FAILED}" -eq 0 ]]; then
    echo "[verify] All gates passed."
else
    echo "[verify] One or more gates failed."
    exit 1
fi
