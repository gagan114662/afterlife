"""
After-Life Conversation API

Endpoints:
  POST /conversation/start   — start a new conversation session with a persona
  POST /conversation/message — send a message, get text + audio response

Run locally:
  uvicorn main:app --reload --port 8000

Environment variables:
  ANTHROPIC_API_KEY    — Claude API key (required)
  ELEVENLABS_API_KEY   — ElevenLabs API key (for TTS)
  PINECONE_API_KEY     — Pinecone API key (for memory retrieval)
  PINECONE_INDEX       — Pinecone index name (default: afterlife-memories)
  MONGODB_URI          — MongoDB connection string (default: mongodb://localhost:27017)
  MONGODB_DB           — MongoDB database name (default: afterlife)
"""

import base64
import logging
import os
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from conversation import reply_as_persona, text_to_speech
from memory import load_contact_profile, update_biography

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="After-Life Conversation API",
    description="Talk to your loved ones through AI personas.",
    version="1.0.0",
)

# In-memory session store: session_id → {contact_name, user_name, history}
# For production, replace with Redis or MongoDB-backed sessions.
_sessions: dict[str, dict] = {}


# ─── Request / Response Models ────────────────────────────────────────────────


class StartRequest(BaseModel):
    contact_name: str  # e.g. "mom"
    user_name: str  # e.g. "Gagan"


class StartResponse(BaseModel):
    session_id: str
    greeting_text: str
    greeting_audio_b64: Optional[str] = None  # base64-encoded MP3, if TTS available


class MessageRequest(BaseModel):
    session_id: str
    message: str


class MessageResponse(BaseModel):
    reply_text: str
    reply_audio_b64: Optional[str] = None  # base64-encoded MP3, if TTS available


class BiographyUpdateRequest(BaseModel):
    contact_name: str
    new_biography: str


# ─── Endpoints ────────────────────────────────────────────────────────────────


@app.post("/conversation/start", response_model=StartResponse)
async def start_conversation(req: StartRequest) -> StartResponse:
    """
    Start a new conversation session with a persona.
    Loads the contact's biography from MongoDB, generates an opening greeting,
    and returns the session ID for subsequent messages.
    """
    try:
        profile = load_contact_profile(req.contact_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    import uuid

    session_id = str(uuid.uuid4())
    _sessions[session_id] = {
        "contact_name": req.contact_name,
        "user_name": req.user_name,
        "voice_id": profile["voice_id"],
        "history": [],
    }

    # Generate an opening greeting from the persona.
    opening_prompt = f"You are starting a fresh conversation. Greet {req.user_name} warmly, as you naturally would."
    try:
        greeting = reply_as_persona(
            contact_name=req.contact_name,
            user_name=req.user_name,
            history=[],
            user_message=opening_prompt,
        )
    except Exception as exc:
        logger.error("Failed to generate greeting: %s", exc)
        raise HTTPException(status_code=502, detail=f"Failed to generate greeting: {exc}")

    # Record the greeting in history as an assistant turn.
    _sessions[session_id]["history"].append(
        {"role": "assistant", "content": greeting}
    )

    audio_b64 = _tts_to_b64(greeting, profile["voice_id"])
    return StartResponse(
        session_id=session_id,
        greeting_text=greeting,
        greeting_audio_b64=audio_b64,
    )


@app.post("/conversation/message", response_model=MessageResponse)
async def send_message(req: MessageRequest) -> MessageResponse:
    """
    Send a message to the persona and receive a text (+ optional audio) reply.
    """
    session = _sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    contact_name = session["contact_name"]
    user_name = session["user_name"]
    history = session["history"]
    voice_id = session["voice_id"]

    # Add the user's message to history before calling Claude.
    history.append({"role": "user", "content": req.message})

    try:
        reply = reply_as_persona(
            contact_name=contact_name,
            user_name=user_name,
            history=history[:-1],  # history without the current message
            user_message=req.message,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.error("Claude call failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Model call failed: {exc}")

    # Append the assistant's reply to history.
    history.append({"role": "assistant", "content": reply})

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
        logger.error("Biography update failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _tts_to_b64(text: str, voice_id: str) -> Optional[str]:
    """Convert text to speech and base64-encode the result. Returns None on failure."""
    audio_bytes = text_to_speech(text, voice_id)
    if audio_bytes:
        return base64.b64encode(audio_bytes).decode("utf-8")
    return None
