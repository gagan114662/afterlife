"""
After-Life Conversation API

Endpoints:
  GET  /health             — service health check
  POST /conversation/start — start a new conversation session with a persona
  POST /conversation/message — send a message, get text + audio response
  POST /biography/update   — update contact biography

Run locally:
  uvicorn services.api.main:app --reload --port 8000

Environment variables:
  ANTHROPIC_API_KEY    — Claude API key (required)
  ELEVENLABS_API_KEY   — ElevenLabs API key (required)
  MONGODB_URI          — MongoDB connection string (required)
  MONGODB_DB           — MongoDB database name (default: afterlife)
  PINECONE_API_KEY     — Pinecone API key (optional)
  PINECONE_INDEX       — Pinecone index name (default: afterlife-memories)
"""

import base64
import os
import uuid
from typing import Optional

import structlog
from fastapi import FastAPI, HTTPException, Request, Response
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, field_validator

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
    # Fail fast on missing required env vars.
    _require_env("ANTHROPIC_API_KEY")
    _require_env("ELEVENLABS_API_KEY")
    _require_env("MONGODB_URI")

    mongodb_uri = os.environ["MONGODB_URI"]
    db_name = os.environ.get("MONGODB_DB", "afterlife")
    client = AsyncIOMotorClient(mongodb_uri)
    app.state.db = client[db_name]
    await ensure_indexes(app.state.db)
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
    Loads the contact's biography from MongoDB, generates an opening greeting,
    and returns the session ID for subsequent messages.
    """
    db = request.app.state.db
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

    audio_b64 = _tts_to_b64(greeting, profile.get("voice_id", ""))
    return StartResponse(
        session_id=session_id,
        greeting_text=greeting,
        greeting_audio_b64=audio_b64,
    )


@app.post("/conversation/message", response_model=MessageResponse)
async def send_message(req: MessageRequest, request: Request) -> MessageResponse:
    """
    Send a message to the persona and receive a text (+ optional audio) reply.
    """
    db = request.app.state.db
    session = await get_session(db, req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    contact_name = session["contact_name"]
    user_name = session["user_name"]
    history = session["history"]
    voice_id = session.get("voice_id", "")

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


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _tts_to_b64(text: str, voice_id: str) -> Optional[str]:
    """Convert text to speech and base64-encode the result. Returns None on failure."""
    audio_bytes = text_to_speech(text, voice_id)
    if audio_bytes:
        return base64.b64encode(audio_bytes).decode("utf-8")
    return None
