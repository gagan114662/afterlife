"""
ElevenLabs voice clone pipeline with fallback policy.

Workflow:
1. Check voice_rights_active; return FALLBACK immediately if not set.
2. Filter supplied voice notes by audio quality (audio_utils).
3. Convert accepted notes to 16 kHz mono WAV.
4. If total accepted duration >= MIN_CLONE_SECONDS, call ElevenLabs API.
5. On insufficient audio or API failure, return FALLBACK / FAILED result.
6. Persist clone metadata to MongoDB (voice_clones collection).
"""

from datetime import datetime
from enum import Enum
from typing import Optional

import httpx
import structlog
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

try:
    from .audio_utils import convert_to_wav, filter_quality_voice_notes
except ImportError:  # standalone load via importlib (e.g. tests)
    from audio_utils import convert_to_wav, filter_quality_voice_notes  # type: ignore[no-redef]

logger = structlog.get_logger(__name__)

# Minimum seconds of clean accepted audio required to register an ElevenLabs clone.
MIN_CLONE_SECONDS = 30.0

ELEVENLABS_ADD_VOICE_URL = "https://api.elevenlabs.io/v1/voices/add"


# ─── Domain Models ────────────────────────────────────────────────────────────


class CloneStatus(str, Enum):
    CLONED = "cloned"        # ElevenLabs voice ID successfully registered
    FALLBACK = "fallback"    # Not enough audio or rights revoked — use XTTS fallback
    FAILED = "failed"        # API call attempted but returned an error


class VoiceCloneRecord(BaseModel):
    """MongoDB document schema for voice_clones collection."""

    contact_name: str = Field(..., min_length=1, max_length=100)
    owner_user_id: str = Field(..., min_length=1, max_length=200)
    status: CloneStatus
    voice_id: Optional[str] = None
    fallback_reason: Optional[str] = Field(default=None, max_length=500)
    accepted_duration_seconds: float = Field(default=0.0, ge=0.0)
    created_at: datetime
    updated_at: datetime


class CloneResult(BaseModel):
    """Returned to callers; contains the voice_id (CLONED) or fallback info."""

    status: CloneStatus
    voice_id: Optional[str] = None
    fallback_reason: Optional[str] = None
    accepted_duration_seconds: float = 0.0


# ─── MongoDB Helpers ──────────────────────────────────────────────────────────


async def ensure_voice_clone_indexes(db: AsyncIOMotorDatabase) -> None:
    """Create indexes for voice_clones collection. Call once at startup."""
    await db.voice_clones.create_index(
        [("contact_name", 1), ("owner_user_id", 1)],
        unique=True,
    )


async def _upsert_clone_record(
    db: AsyncIOMotorDatabase,
    contact_name: str,
    owner_user_id: str,
    record: VoiceCloneRecord,
) -> None:
    doc = record.model_dump()
    await db.voice_clones.update_one(
        {"contact_name": contact_name, "owner_user_id": owner_user_id},
        {"$set": doc},
        upsert=True,
    )


async def get_voice_clone_record(
    db: AsyncIOMotorDatabase,
    contact_name: str,
    owner_user_id: str,
) -> Optional[dict]:
    """Return the most recent clone record for this contact/owner pair, or None."""
    return await db.voice_clones.find_one(
        {"contact_name": contact_name, "owner_user_id": owner_user_id},
        {"_id": 0},
    )


# ─── ElevenLabs API Call ──────────────────────────────────────────────────────


async def _register_elevenlabs_clone(
    api_key: str,
    name: str,
    wav_paths: list[str],
    description: str = "",
) -> str:
    """
    Call ElevenLabs Instant Voice Cloning API.

    Returns the new voice_id on success.
    Raises httpx.HTTPStatusError or httpx.RequestError on failure.
    """
    file_handles = [open(path, "rb") for path in wav_paths]
    try:
        files = [
            ("files", (f"sample_{i}.wav", fh, "audio/wav"))
            for i, fh in enumerate(file_handles)
        ]
        headers = {"xi-api-key": api_key}
        data = {"name": name, "description": description}

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                ELEVENLABS_ADD_VOICE_URL,
                headers=headers,
                data=data,
                files=files,
            )
        response.raise_for_status()
        return response.json()["voice_id"]
    finally:
        for fh in file_handles:
            fh.close()


# ─── Main Pipeline ────────────────────────────────────────────────────────────


async def build_voice_clone(
    db: AsyncIOMotorDatabase,
    contact_name: str,
    owner_user_id: str,
    voice_note_paths: list[str],
    api_key: str,
    voice_rights_active: bool = True,
) -> CloneResult:
    """
    Build an ElevenLabs voice clone for a contact.

    Args:
        db:                   Async Motor database handle.
        contact_name:         Display name of the contact.
        owner_user_id:        User who owns this twin.
        voice_note_paths:     Paths to raw voice note files (any ffmpeg-supported format).
        api_key:              ElevenLabs API key.
        voice_rights_active:  Must be True (from consent ledger) to proceed.

    Returns:
        CloneResult with status CLONED (voice_id set), FALLBACK, or FAILED.
    """
    now = datetime.utcnow()

    async def _persist(result: CloneResult) -> None:
        record = VoiceCloneRecord(
            contact_name=contact_name,
            owner_user_id=owner_user_id,
            status=result.status,
            voice_id=result.voice_id,
            fallback_reason=result.fallback_reason,
            accepted_duration_seconds=result.accepted_duration_seconds,
            created_at=now,
            updated_at=now,
        )
        await _upsert_clone_record(db, contact_name, owner_user_id, record)

    # Step 1: consent / rights check
    if not voice_rights_active:
        result = CloneResult(
            status=CloneStatus.FALLBACK,
            fallback_reason="voice_rights not active",
        )
        await _persist(result)
        logger.info(
            "voice_clone_fallback",
            reason="voice_rights not active",
            contact_name=contact_name,
        )
        return result

    # Step 2: filter voice notes for audio quality
    accepted_paths, total_duration = filter_quality_voice_notes(voice_note_paths)

    if not accepted_paths or total_duration < MIN_CLONE_SECONDS:
        reason = (
            f"insufficient audio: {total_duration:.1f}s accepted "
            f"(minimum {MIN_CLONE_SECONDS}s required)"
        )
        result = CloneResult(
            status=CloneStatus.FALLBACK,
            fallback_reason=reason,
            accepted_duration_seconds=total_duration,
        )
        await _persist(result)
        logger.info(
            "voice_clone_fallback",
            reason=reason,
            contact_name=contact_name,
            accepted_count=len(accepted_paths),
            total_duration=total_duration,
        )
        return result

    # Step 3: convert accepted audio to 16 kHz mono WAV
    wav_paths: list[str] = []
    for path in accepted_paths:
        converted = convert_to_wav(path)
        if converted:
            wav_paths.append(converted)
        else:
            logger.warning("audio_conversion_failed", path=path)

    if not wav_paths:
        reason = "audio conversion failed for all accepted files"
        result = CloneResult(
            status=CloneStatus.FALLBACK,
            fallback_reason=reason,
            accepted_duration_seconds=total_duration,
        )
        await _persist(result)
        logger.error(
            "voice_clone_fallback",
            reason=reason,
            contact_name=contact_name,
        )
        return result

    # Step 4: register clone with ElevenLabs
    try:
        voice_id = await _register_elevenlabs_clone(
            api_key=api_key,
            name=f"afterlife-{contact_name}",
            wav_paths=wav_paths,
            description=f"Voice clone for {contact_name}",
        )
        result = CloneResult(
            status=CloneStatus.CLONED,
            voice_id=voice_id,
            accepted_duration_seconds=total_duration,
        )
        logger.info(
            "voice_clone_registered",
            voice_id=voice_id,
            contact_name=contact_name,
            duration=total_duration,
        )
    except httpx.HTTPStatusError as exc:
        reason = f"ElevenLabs API HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        result = CloneResult(
            status=CloneStatus.FAILED,
            fallback_reason=reason,
            accepted_duration_seconds=total_duration,
        )
        logger.error(
            "voice_clone_api_error",
            status_code=exc.response.status_code,
            contact_name=contact_name,
            error=reason,
        )
    except httpx.RequestError as exc:
        reason = f"ElevenLabs API request error: {exc}"
        result = CloneResult(
            status=CloneStatus.FAILED,
            fallback_reason=reason,
            accepted_duration_seconds=total_duration,
        )
        logger.error(
            "voice_clone_request_error",
            contact_name=contact_name,
            error=str(exc),
        )

    # Step 5: persist to MongoDB
    await _persist(result)
    return result
