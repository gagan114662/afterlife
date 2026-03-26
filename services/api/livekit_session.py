"""
LiveKit voice session lifecycle module.

Manages:
- Creating and resuming LiveKit voice sessions (MongoDB-backed)
- Generating participant access tokens
- Session state transitions (active → ended)

The stable room name pattern (afterlife-{contact}-{user}) ensures that
reconnecting clients rejoin the same room without creating orphaned sessions.
"""

import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

import structlog
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

# Sessions auto-expire after 4 hours (TTL index on updated_at)
LIVEKIT_SESSION_TTL_SECONDS = 3600 * 4


class LiveKitSessionState(str, Enum):
    ACTIVE = "active"
    ENDED = "ended"


class LiveKitSessionRecord(BaseModel):
    """Schema for a LiveKit session document in MongoDB."""

    session_id: str = Field(..., min_length=36, max_length=36)
    room_name: str = Field(..., min_length=1, max_length=200)
    contact_name: str = Field(..., min_length=1, max_length=100)
    user_name: str = Field(..., min_length=1, max_length=100)
    voice_id: str = Field(default="")
    state: LiveKitSessionState = Field(default=LiveKitSessionState.ACTIVE)
    created_at: datetime
    updated_at: datetime


async def ensure_livekit_indexes(db: AsyncIOMotorDatabase) -> None:
    """Create indexes for the livekit_sessions collection. Call once at startup."""
    await db.livekit_sessions.create_index("session_id", unique=True)
    await db.livekit_sessions.create_index(
        [("contact_name", 1), ("user_name", 1), ("state", 1)]
    )
    await db.livekit_sessions.create_index(
        "updated_at",
        expireAfterSeconds=LIVEKIT_SESSION_TTL_SECONDS,
    )


def make_room_name(contact_name: str, user_name: str) -> str:
    """
    Return a stable LiveKit room name for a contact/user pair.

    Stability is intentional: reconnecting clients rejoin the same room
    without creating orphaned rooms.
    """
    safe_c = contact_name.lower().replace(" ", "-")
    safe_u = user_name.lower().replace(" ", "-")
    return f"afterlife-{safe_c}-{safe_u}"


async def get_active_session(
    db: AsyncIOMotorDatabase,
    contact_name: str,
    user_name: str,
) -> Optional[dict]:
    """Return the active LiveKit session for this contact/user pair, or None."""
    return await db.livekit_sessions.find_one(
        {
            "contact_name": contact_name,
            "user_name": user_name,
            "state": LiveKitSessionState.ACTIVE,
        },
        {"_id": 0},
    )


async def create_or_resume_session(
    db: AsyncIOMotorDatabase,
    contact_name: str,
    user_name: str,
    voice_id: str = "",
) -> dict:
    """
    Create a new LiveKit session or return the existing active one.

    Returns a dict with: session_id, room_name, contact_name, user_name,
    voice_id, state, created_at, updated_at, is_new.

    The stable room name ensures reconnecting clients rejoin the same room.
    """
    existing = await get_active_session(db, contact_name, user_name)
    if existing:
        logger.info(
            "livekit_session_resumed",
            session_id=existing["session_id"],
            contact_name=contact_name,
        )
        return {**existing, "is_new": False}

    session_id = str(uuid.uuid4())
    room_name = make_room_name(contact_name, user_name)
    now = datetime.utcnow()
    doc = {
        "session_id": session_id,
        "room_name": room_name,
        "contact_name": contact_name,
        "user_name": user_name,
        "voice_id": voice_id,
        "state": LiveKitSessionState.ACTIVE,
        "created_at": now,
        "updated_at": now,
    }
    await db.livekit_sessions.insert_one(doc)
    doc.pop("_id", None)
    logger.info(
        "livekit_session_created",
        session_id=session_id,
        room_name=room_name,
        contact_name=contact_name,
    )
    return {**doc, "is_new": True}


async def get_livekit_session(
    db: AsyncIOMotorDatabase,
    session_id: str,
) -> Optional[dict]:
    """Return the LiveKit session document (without MongoDB _id), or None."""
    return await db.livekit_sessions.find_one(
        {"session_id": session_id},
        {"_id": 0},
    )


async def end_livekit_session(
    db: AsyncIOMotorDatabase,
    session_id: str,
) -> bool:
    """
    Mark a session as ended.

    Returns True if the session was found and updated.
    """
    result = await db.livekit_sessions.update_one(
        {"session_id": session_id},
        {
            "$set": {
                "state": LiveKitSessionState.ENDED,
                "updated_at": datetime.utcnow(),
            }
        },
    )
    if result.matched_count > 0:
        logger.info("livekit_session_ended", session_id=session_id)
    return result.matched_count > 0


def generate_participant_token(
    room_name: str,
    participant_identity: str,
    api_key: str,
    api_secret: str,
    ttl_seconds: int = 3600,
) -> str:
    """
    Generate a signed LiveKit participant access token (JWT).

    The returned token grants the participant permission to join `room_name`
    with publish + subscribe rights.

    Raises ValueError if api_key or api_secret are empty.
    Raises ImportError if livekit-api is not installed.
    """
    if not api_key or not api_secret:
        raise ValueError(
            "LIVEKIT_API_KEY and LIVEKIT_API_SECRET are required to generate tokens"
        )

    from livekit.api import AccessToken, VideoGrants  # lazy import — not in test env

    token = (
        AccessToken(api_key=api_key, api_secret=api_secret)
        .with_identity(participant_identity)
        .with_ttl(timedelta(seconds=ttl_seconds))
        .with_grants(
            VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
            )
        )
    )
    return token.to_jwt()
