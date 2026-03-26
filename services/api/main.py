"""
After-Life Conversation API

Endpoints:
  GET  /health             — service health check
  POST /conversation/start — start a new conversation session with a persona
  POST /conversation/message — send a message, get text + audio response
  POST /biography/update   — update contact biography
  POST /consent/grant      — grant consent for a contact twin
  POST /consent/revoke     — revoke consent (disables future sessions)
  GET  /consent/status     — check consent status for a contact

Run locally:
  uvicorn services.api.main:app --reload --port 8000

Environment variables:
  MONGODB_URI          — MongoDB connection string (required)
  MONGODB_DB           — MongoDB database name (default: afterlife)
  OLLAMA_HOST          — Ollama server URL (default: http://localhost:11434)
  OLLAMA_MODEL         — Ollama model name (default: llama3.2:3b)
  CHROMA_PATH          — Chroma persistence path (default: ./data/chroma)
"""

import base64
import os
import uuid
from typing import Optional

import structlog
from fastapi import FastAPI, HTTPException, Request, Response
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, field_validator

from services.api.consent import (
    ConsentNotFoundError,
    ConsentNotGrantedError,
    ConsentRevokedError,
    ConsentStatus,
    VoiceConsentError,
    check_twin_eligibility,
    check_voice_eligibility,
    ensure_consent_indexes,
    get_consent,
    grant_consent,
    revoke_consent,
)
from services.api.conversation import reply_as_persona, text_to_speech
from services.api.logging_config import configure_logging
from services.api.memory import load_contact_profile, update_biography
from services.api.sanitize import sanitize_name
from services.api.sessions import (
    append_message,
    create_session,
    ensure_indexes,
    get_session,
)

configure_logging()
logger = structlog.get_logger(__name__)


def _require_env(name: str) -> str:
    """Raise at startup if a required environment variable is missing."""
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Required environment variable {name!r} is not set")
    return val


app = FastAPI(
    title="After-Life Conversation API",
    description="Talk to your loved ones through AI personas.",
    version="1.0.0",
)


@app.on_event("startup")
async def startup() -> None:
    # Only MongoDB is required. Ollama runs locally.
    _require_env("MONGODB_URI")

    # Verify Ollama is reachable
    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    try:
        import httpx as _httpx
        with _httpx.Client(timeout=5) as c:
            resp = c.get(f"{ollama_host}/api/tags")
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
            ollama_model = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
            if not any(ollama_model in m for m in models):
                logger.warning(
                    "ollama_model_not_found",
                    model=ollama_model,
                    available=models,
                )
    except Exception as exc:
        logger.warning("ollama_health_check_failed", error=str(exc))
        # Don't crash — let first request fail with clear error

    mongodb_uri = os.environ["MONGODB_URI"]
    db_name = os.environ.get("MONGODB_DB", "afterlife")
    client = AsyncIOMotorClient(mongodb_uri)
    app.state.db = client[db_name]
    await ensure_indexes(app.state.db)
    await ensure_consent_indexes(app.state.db)
    logger.info("startup_complete", service="conversation-api")


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next) -> Response:
    correlation_id = str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    structlog.contextvars.clear_contextvars()
    return response


# ─── Request / Response Models ────────────────────────────────────────────────


class StartRequest(BaseModel):
    contact_name: str = Field(..., min_length=1, max_length=100)
    user_name: str = Field(..., min_length=1, max_length=100)

    @field_validator("contact_name", "user_name")
    @classmethod
    def sanitize(cls, v: str) -> str:
        return sanitize_name(v)


class StartResponse(BaseModel):
    session_id: str
    greeting_text: str
    greeting_audio_b64: Optional[str] = None


class MessageRequest(BaseModel):
    session_id: str = Field(..., min_length=36, max_length=36)
    message: str = Field(..., min_length=1, max_length=2000)


class MessageResponse(BaseModel):
    reply_text: str
    reply_audio_b64: Optional[str] = None


class BiographyUpdateRequest(BaseModel):
    contact_name: str = Field(..., min_length=1, max_length=100)
    new_biography: str = Field(..., min_length=1, max_length=10000)


class ConsentGrantRequest(BaseModel):
    contact_name: str = Field(..., min_length=1, max_length=100)
    user_name: str = Field(..., min_length=1, max_length=100)
    voice_rights: bool = Field(default=False)

    @field_validator("contact_name", "user_name")
    @classmethod
    def sanitize(cls, v: str) -> str:
        return sanitize_name(v)


class ConsentRevokeRequest(BaseModel):
    contact_name: str = Field(..., min_length=1, max_length=100)
    user_name: str = Field(..., min_length=1, max_length=100)
    reason: Optional[str] = Field(default=None, max_length=500)

    @field_validator("contact_name", "user_name")
    @classmethod
    def sanitize(cls, v: str) -> str:
        return sanitize_name(v)


class ConsentStatusResponse(BaseModel):
    contact_name: str
    user_name: str
    status: str
    approved: bool
    voice_rights: bool
    approved_at: Optional[str] = None
    revoked_at: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    service: str


# ─── Endpoints ────────────────────────────────────────────────────────────────


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", service="conversation-api")


@app.post("/conversation/start", response_model=StartResponse)
async def start_conversation(req: StartRequest, request: Request) -> StartResponse:
    """
    Start a new conversation session with a persona.
    Requires active consent for the contact. Voice audio requires voice-rights.
    Loads the contact's biography from MongoDB, generates an opening greeting,
    and returns the session ID for subsequent messages.
    """
    db = request.app.state.db

    # ── Consent gate ──────────────────────────────────────────────────────────
    try:
        await check_twin_eligibility(db, req.contact_name, req.user_name)
    except ConsentNotFoundError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ConsentRevokedError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ConsentNotGrantedError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    try:
        profile = load_contact_profile(req.contact_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    session_id = await create_session(
        db,
        contact_name=req.contact_name,
        user_name=req.user_name,
        voice_id=profile.get("voice_id", ""),
    )

    opening_prompt = (
        f"You are starting a fresh conversation. "
        f"Greet {req.user_name} warmly, as you naturally would."
    )
    try:
        greeting = reply_as_persona(
            contact_name=req.contact_name,
            user_name=req.user_name,
            history=[],
            user_message=opening_prompt,
        )
    except Exception as exc:
        logger.error("greeting_generation_failed", error=str(exc), exc_info=True)
        raise HTTPException(
            status_code=502,
            detail="Service temporarily unavailable. Please try again.",
        )

    await append_message(db, session_id, "assistant", greeting)

    # ── Voice gate: only use cloned voice if voice-rights approved ────────────
    voice_id = profile.get("voice_id", "")
    if voice_id:
        try:
            await check_voice_eligibility(db, req.contact_name, req.user_name)
        except (ConsentNotFoundError, ConsentRevokedError, VoiceConsentError):
            voice_id = ""  # Fall back to no voice audio; don't error the session

    audio_b64 = _tts_to_b64(greeting, voice_id)
    return StartResponse(
        session_id=session_id,
        greeting_text=greeting,
        greeting_audio_b64=audio_b64,
    )


@app.post("/conversation/message", response_model=MessageResponse)
async def send_message(req: MessageRequest, request: Request) -> MessageResponse:
    """
    Send a message to the persona and receive a text (+ optional audio) reply.
    Consent is re-checked on every message so revocation takes effect immediately.
    """
    db = request.app.state.db
    session = await get_session(db, req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    contact_name = session["contact_name"]
    user_name = session["user_name"]
    history = session["history"]
    voice_id = session.get("voice_id", "")

    # ── Consent re-check: revocation must disable active sessions ─────────────
    try:
        await check_twin_eligibility(db, contact_name, user_name)
    except ConsentNotFoundError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ConsentRevokedError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ConsentNotGrantedError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    await append_message(db, req.session_id, "user", req.message)

    try:
        reply = reply_as_persona(
            contact_name=contact_name,
            user_name=user_name,
            history=history,
            user_message=req.message,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.error("claude_call_failed", error=str(exc), exc_info=True)
        raise HTTPException(
            status_code=502,
            detail="Service temporarily unavailable. Please try again.",
        )

    await append_message(db, req.session_id, "assistant", reply)

    audio_b64 = _tts_to_b64(reply, voice_id)
    return MessageResponse(reply_text=reply, reply_audio_b64=audio_b64)


@app.post("/biography/update", status_code=204)
async def update_biography_endpoint(req: BiographyUpdateRequest) -> None:
    """
    Update the living biography for a contact (called by the Biographer Agent
    at the end of each session).
    """
    try:
        update_biography(req.contact_name, req.new_biography)
    except Exception as exc:
        logger.error("biography_update_failed", error=str(exc), exc_info=True)
        raise HTTPException(
            status_code=502,
            detail="Service temporarily unavailable. Please try again.",
        )


# ─── Consent Endpoints ───────────────────────────────────────────────────────


@app.post("/consent/grant", status_code=204)
async def grant_consent_endpoint(
    req: ConsentGrantRequest, request: Request
) -> None:
    """
    Grant consent for a contact twin.
    Set voice_rights=true to also permit voice cloning for this contact.
    """
    db = request.app.state.db
    try:
        await grant_consent(
            db,
            contact_name=req.contact_name,
            owner_user_id=req.user_name,
            voice_rights=req.voice_rights,
        )
    except Exception as exc:
        logger.error("consent_grant_failed", error=str(exc), exc_info=True)
        raise HTTPException(
            status_code=502,
            detail="Service temporarily unavailable. Please try again.",
        )


@app.post("/consent/revoke", status_code=204)
async def revoke_consent_endpoint(
    req: ConsentRevokeRequest, request: Request
) -> None:
    """
    Revoke consent for a contact twin.
    Immediately blocks all future sessions and voice cloning for this contact.
    """
    db = request.app.state.db
    try:
        await revoke_consent(
            db,
            contact_name=req.contact_name,
            owner_user_id=req.user_name,
            reason=req.reason,
        )
    except ConsentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("consent_revoke_failed", error=str(exc), exc_info=True)
        raise HTTPException(
            status_code=502,
            detail="Service temporarily unavailable. Please try again.",
        )


@app.get("/consent/status", response_model=ConsentStatusResponse)
async def consent_status_endpoint(
    contact_name: str,
    user_name: str,
    request: Request,
) -> ConsentStatusResponse:
    """Return the current consent status for a contact/user pair."""
    db = request.app.state.db
    record = await get_consent(db, sanitize_name(contact_name), sanitize_name(user_name))
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"No consent record found for contact '{contact_name}'",
        )
    return ConsentStatusResponse(
        contact_name=record["contact_name"],
        user_name=record["owner_user_id"],
        status=record.get("status", ConsentStatus.PENDING),
        approved=record.get("approved", False),
        voice_rights=record.get("voice_rights", False),
        approved_at=(
            record["approved_at"].isoformat() if record.get("approved_at") else None
        ),
        revoked_at=(
            record["revoked_at"].isoformat() if record.get("revoked_at") else None
        ),
    )


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _tts_to_b64(text: str, voice_id: str) -> Optional[str]:
    """Convert text to speech and base64-encode the result. Returns None on failure."""
    audio_bytes = text_to_speech(text, voice_id)
    if audio_bytes:
        return base64.b64encode(audio_bytes).decode("utf-8")
    return None
