#!/usr/bin/env bash
# scripts/ci/verify.sh — local and CI verification gate
#
# Mirrors the checks in .github/workflows/ci.yml.
# Exit 0 = all gates pass. Non-zero = at least one gate failed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

FAILED=0

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

# ─── Python: pytest ───────────────────────────────────────────────────────────
echo "[verify] pytest tests/"
PY=python3
if command -v python >/dev/null 2>&1; then PY=python; fi

if $PY -m pytest --version >/dev/null 2>&1; then
    if ! $PY -m pytest tests/ -q; then
        echo "[verify] FAILED: pytest"
        FAILED=1
    fi
else
    echo "[verify] SKIP: pytest not installed"
fi

# ─── TypeScript: tsc + npm test ───────────────────────────────────────────────
for tsdir in $(find services -name "tsconfig.json" -not -path "*/node_modules/*" -exec dirname {} \;); do
    echo "[verify] tsc --noEmit in ${tsdir}"
    if ! (cd "${tsdir}" && npx --no-install tsc --noEmit 2>/dev/null); then
        echo "[verify] FAILED: tsc in ${tsdir}"
        FAILED=1
    fi
    echo "[verify] npm test in ${tsdir}"
    if ! (cd "${tsdir}" && npm test --if-present 2>/dev/null); then
        echo "[verify] FAILED: npm test in ${tsdir}"
        FAILED=1
    fi
done

# ─── Result ───────────────────────────────────────────────────────────────────
if [[ "${FAILED}" -eq 0 ]]; then
    echo "[verify] All gates passed."
else
    echo "[verify] One or more gates failed."
    exit 1
fi
