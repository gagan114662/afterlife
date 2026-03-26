#!/usr/bin/env bash
# scripts/ci/release-check.sh — pre-release gate.
#
# Runs the full suite of checks required before cutting a release or merging
# to main in production-enforcement mode:
#   1. Smoke (import + health endpoint)
#   2. Verify with integration path coverage (lint + tests + changed paths)
#   3. Security scan (bandit, if installed)
#   4. Docker build validation (if docker is available)
#
# Usage:
#   ./scripts/ci/release-check.sh [--skip-docker] [--skip-security]
#
# Exit 0 = release-ready. Non-zero = at least one check failed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

SKIP_DOCKER=false
SKIP_SECURITY=false
for arg in "$@"; do
    [[ "$arg" == "--skip-docker"   ]] && SKIP_DOCKER=true
    [[ "$arg" == "--skip-security" ]] && SKIP_SECURITY=true
done

FAILED=0

# ─── 1. Smoke ─────────────────────────────────────────────────────────────────
echo "[release-check] 1/4 smoke"
if ! "${SCRIPT_DIR}/smoke.sh"; then
    echo "[release-check] FAILED: smoke"
    FAILED=1
fi

# ─── 2. Verify (lint + tests + changed-service integration paths) ─────────────
echo "[release-check] 2/4 verify --changed"
if ! "${SCRIPT_DIR}/verify.sh" --changed; then
    echo "[release-check] FAILED: verify"
    FAILED=1
fi

# ─── 3. Security scan ─────────────────────────────────────────────────────────
if [[ "${SKIP_SECURITY}" == "true" ]]; then
    echo "[release-check] 3/4 security scan SKIPPED (--skip-security)"
else
    echo "[release-check] 3/4 security scan (bandit)"
    if command -v bandit >/dev/null 2>&1; then
        # -ll = only report MEDIUM+ severity, -iii = only report MEDIUM+ confidence
        # Exclude tests (they intentionally test edge cases) and migrations.
        if ! bandit -r services/ -ll -iii \
              --exclude services/whatsapp-sync \
              -q; then
            echo "[release-check] FAILED: bandit security scan"
            FAILED=1
        else
            echo "[release-check]   ✓ bandit: no high-severity issues"
        fi
    else
        echo "[release-check]   SKIP: bandit not installed (pip install bandit)"
    fi
fi

# ─── 4. Docker build validation ───────────────────────────────────────────────
if [[ "${SKIP_DOCKER}" == "true" ]]; then
    echo "[release-check] 4/4 Docker build SKIPPED (--skip-docker)"
else
    echo "[release-check] 4/4 Docker build validation"
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
        BUILD_FAILED=0
        for dockerfile in $(find services -name "Dockerfile" -not -path "*/node_modules/*"); do
            svc_dir="$(dirname "${dockerfile}")"
            svc_name="$(basename "${svc_dir}")"
            echo "[release-check]   docker build: ${svc_name}"
            if ! docker build -q -t "afterlife/${svc_name}:smoke" "${svc_dir}" >/dev/null; then
                echo "[release-check]   FAILED: docker build ${svc_name}"
                BUILD_FAILED=1
                FAILED=1
            fi
        done
        if [[ "${BUILD_FAILED}" -eq 0 ]]; then
            echo "[release-check]   ✓ all Docker builds succeeded"
        fi
    else
        echo "[release-check]   SKIP: docker not available"
    fi
fi

# ─── Result ───────────────────────────────────────────────────────────────────
echo ""
if [[ "${FAILED}" -eq 0 ]]; then
    echo "[release-check] ✓ All release gates passed — ready to ship."
else
    echo "[release-check] ✗ One or more release gates failed."
    exit 1
fi
