"""MongoDB-backed session store for the conversation API.

Sessions expire after 24 hours via a TTL index on `updated_at`.
"""
import uuid
from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorDatabase

SESSION_TTL_SECONDS = 86400  # 24 hours


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """Create TTL and uniqueness indexes. Call once at startup."""
    await db.sessions.create_index("updated_at", expireAfterSeconds=SESSION_TTL_SECONDS)
    await db.sessions.create_index("session_id", unique=True)


async def create_session(
    db: AsyncIOMotorDatabase,
    contact_name: str,
    user_name: str,
    voice_id: str = "",
) -> str:
    """Insert a new session document and return its session_id."""
    session_id = str(uuid.uuid4())
    now = datetime.utcnow()
    await db.sessions.insert_one(
        {
            "session_id": session_id,
            "contact_name": contact_name,
            "user_name": user_name,
            "voice_id": voice_id,
            "history": [],
            "created_at": now,
            "updated_at": now,
        }
    )
    return session_id


async def get_session(db: AsyncIOMotorDatabase, session_id: str) -> dict | None:
    """Return the session document (without MongoDB _id), or None if not found."""
    return await db.sessions.find_one({"session_id": session_id}, {"_id": 0})


async def append_message(
    db: AsyncIOMotorDatabase,
    session_id: str,
    role: str,
    content: str,
) -> None:
    """Push a message to the session history and bump updated_at."""
    await db.sessions.update_one(
        {"session_id": session_id},
        {
            "$push": {"history": {"role": role, "content": content}},
            "$set": {"updated_at": datetime.utcnow()},
        },
    )
