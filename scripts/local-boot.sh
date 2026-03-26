#!/usr/bin/env bash
# scripts/local-boot.sh — boot After-Life locally via Docker Compose
#
# Usage:
#   ./scripts/local-boot.sh            # start all services
#   ./scripts/local-boot.sh --api-only # start API + MongoDB + Ollama only
#   ./scripts/local-boot.sh --down     # stop and remove containers
#   ./scripts/local-boot.sh --reset    # stop, delete volumes, restart fresh
#
# Prerequisites:
#   - Docker and Docker Compose v2 installed
#   - ADMIN_JID set in .env (your bot's WhatsApp JID)
#
# First time:
#   cp .env.example .env && $EDITOR .env   # fill in ADMIN_JID
#   ./scripts/local-boot.sh               # boots everything
#   ./scripts/local-boot.sh --pull-model  # pull Ollama model (run once)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# ─── Helpers ──────────────────────────────────────────────────────────────────

info()  { echo "[local-boot] $*"; }
warn()  { echo "[local-boot] WARNING: $*" >&2; }
error() { echo "[local-boot] ERROR: $*" >&2; exit 1; }

check_deps() {
    command -v docker >/dev/null 2>&1 || error "docker is not installed"
    docker compose version >/dev/null 2>&1 || error "docker compose (v2) is not installed"
}

check_env() {
    if [[ ! -f ".env" ]]; then
        warn ".env not found — copying from .env.example"
        cp .env.example .env
        warn "Please edit .env and set ADMIN_JID, then re-run this script"
        exit 0
    fi

    # Warn if ADMIN_JID is blank
    ADMIN_JID_VAL="$(grep -E '^ADMIN_JID=' .env | cut -d= -f2- | tr -d ' ' || true)"
    if [[ -z "${ADMIN_JID_VAL}" ]]; then
        warn "ADMIN_JID is not set in .env — the WhatsApp bot will not know who its admin is"
        warn "Set ADMIN_JID=<your-number>@s.whatsapp.net in .env and restart"
    fi
}

create_data_dirs() {
    # Baileys auth state directories must exist before mounting
    mkdir -p data/baileys-bot
    mkdir -p data/baileys-personal
    mkdir -p data/fixtures/contacts
}

pull_ollama_model() {
    MODEL="${OLLAMA_MODEL:-llama3.2:3b}"
    info "Pulling Ollama model: ${MODEL}"
    info "This may take several minutes the first time..."
    docker compose exec ollama ollama pull "${MODEL}"
    info "Model ready: ${MODEL}"
}

show_qr_instructions() {
    info ""
    info "─────────────────────────────────────────────────────────"
    info "  QR PAIRING"
    info ""
    info "  1. Watch the whatsapp-bot logs for the QR code:"
    info "       docker compose logs -f whatsapp-bot"
    info ""
    info "  2. Scan the QR with the WhatsApp account you want"
    info "     to use as the 'After-Life' bot number."
    info ""
    info "  3. The auth state is persisted in ./data/baileys-bot/"
    info "     — you only need to scan once."
    info "─────────────────────────────────────────────────────────"
    info ""
}

# ─── Argument parsing ─────────────────────────────────────────────────────────

API_ONLY=false
PULL_MODEL=false
RESET=false
DOWN=false

for arg in "$@"; do
    case "${arg}" in
        --api-only)    API_ONLY=true ;;
        --pull-model)  PULL_MODEL=true ;;
        --reset)       RESET=true ;;
        --down)        DOWN=true ;;
        --help|-h)
            sed -n '2,20p' "$0"
            exit 0
            ;;
        *)
            error "Unknown argument: ${arg}. Use --help for usage."
            ;;
    esac
done

# ─── Main ─────────────────────────────────────────────────────────────────────

check_deps
check_env
create_data_dirs

if [[ "${DOWN}" == "true" ]]; then
    info "Stopping After-Life services..."
    docker compose down
    exit 0
fi

if [[ "${RESET}" == "true" ]]; then
    info "Resetting After-Life (removing containers and volumes)..."
    docker compose down -v
    info "Volumes cleared. Restart with: ./scripts/local-boot.sh"
    exit 0
fi

if [[ "${API_ONLY}" == "true" ]]; then
    info "Starting API-only stack (mongodb + ollama + api)..."
    docker compose up -d mongodb ollama api
    info "Waiting for services to be healthy..."
    sleep 5
    docker compose ps
    info ""
    info "API available at: http://localhost:${PORT:-8000}"
    info "Health check:     curl http://localhost:${PORT:-8000}/health"
else
    info "Starting full After-Life stack..."
    docker compose up -d
    info "Waiting for services to be healthy..."
    sleep 5
    docker compose ps
    show_qr_instructions
    info "API available at: http://localhost:${PORT:-8000}"
    info "Health check:     curl http://localhost:${PORT:-8000}/health"
    info "All logs:         docker compose logs -f"
fi

if [[ "${PULL_MODEL}" == "true" ]]; then
    info "Waiting for Ollama to be ready..."
    sleep 10
    pull_ollama_model
fi

info ""
info "After-Life is running. Use 'docker compose logs -f' to watch logs."
