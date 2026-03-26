"""Consent ledger: MongoDB-backed source of truth for contact approval.

Every twin creation and voice-cloning operation must pass through this module.
No twin may be built, chatted with, or cloned unless the contact has explicit
approval. Voice cloning requires a separate voice_rights flag. Revocation
disables all future sessions immediately.
"""
from datetime import datetime
from enum import Enum
from typing import Optional

import structlog
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)


class ConsentStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    REVOKED = "revoked"


class ConsentRecord(BaseModel):
    """Schema for a consent record stored in MongoDB."""

    contact_name: str = Field(..., min_length=1, max_length=100)
    owner_user_id: str = Field(..., min_length=1, max_length=200)
    approved: bool = Field(default=False)
    voice_rights: bool = Field(default=False)
    status: ConsentStatus = Field(default=ConsentStatus.PENDING)
    created_at: datetime
    approved_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    revoke_reason: Optional[str] = Field(default=None, max_length=500)


# ─── Custom Exceptions ────────────────────────────────────────────────────────


class ConsentNotFoundError(Exception):
    """No consent record exists for this contact/owner pair."""


class ConsentRevokedError(Exception):
    """Consent was previously granted but has since been revoked."""


class ConsentNotGrantedError(Exception):
    """Consent has not been granted for this contact/owner pair."""


class VoiceConsentError(Exception):
    """Voice-rights flag is not set; voice cloning is not permitted."""


# ─── Index Setup ─────────────────────────────────────────────────────────────


async def ensure_consent_indexes(db: AsyncIOMotorDatabase) -> None:
    """Create indexes for the consents collection. Call once at startup."""
    await db.consents.create_index(
        [("contact_name", 1), ("owner_user_id", 1)],
        unique=True,
    )
    await db.consents.create_index("status")


# ─── Write Operations ─────────────────────────────────────────────────────────


async def grant_consent(
    db: AsyncIOMotorDatabase,
    contact_name: str,
    owner_user_id: str,
    voice_rights: bool = False,
) -> None:
    """Grant (or re-grant) consent for a contact/owner pair.

    Creates the record if it does not exist; updates it if it does.
    Voice-rights must be explicitly set — defaulting to False ensures
    voice cloning requires a deliberate opt-in.
    """
    now = datetime.utcnow()
    await db.consents.update_one(
        {"contact_name": contact_name, "owner_user_id": owner_user_id},
        {
            "$set": {
                "approved": True,
                "voice_rights": voice_rights,
                "status": ConsentStatus.ACTIVE,
                "approved_at": now,
                "revoked_at": None,
                "revoke_reason": None,
            },
            "$setOnInsert": {
                "contact_name": contact_name,
                "owner_user_id": owner_user_id,
                "created_at": now,
            },
        },
        upsert=True,
    )
    logger.info(
        "consent_granted",
        contact_name=contact_name,
        owner_user_id=owner_user_id,
        voice_rights=voice_rights,
    )


async def revoke_consent(
    db: AsyncIOMotorDatabase,
    contact_name: str,
    owner_user_id: str,
    reason: Optional[str] = None,
) -> None:
    """Revoke consent for a contact/owner pair.

    Sets status=REVOKED and clears both approved and voice_rights flags.
    Any existing sessions will be blocked on next message attempt.
    """
    now = datetime.utcnow()
    result = await db.consents.update_one(
        {"contact_name": contact_name, "owner_user_id": owner_user_id},
        {
            "$set": {
                "approved": False,
                "voice_rights": False,
                "status": ConsentStatus.REVOKED,
                "revoked_at": now,
                "revoke_reason": reason,
            }
        },
    )
    if result.matched_count == 0:
        raise ConsentNotFoundError(
            f"No consent record found for contact '{contact_name}'"
        )
    logger.info(
        "consent_revoked",
        contact_name=contact_name,
        owner_user_id=owner_user_id,
        reason=reason,
    )


# ─── Read Operations ──────────────────────────────────────────────────────────


async def get_consent(
    db: AsyncIOMotorDatabase,
    contact_name: str,
    owner_user_id: str,
) -> Optional[dict]:
    """Return the consent record for this contact/owner pair, or None."""
    return await db.consents.find_one(
        {"contact_name": contact_name, "owner_user_id": owner_user_id},
        {"_id": 0},
    )


# ─── Eligibility Guards ───────────────────────────────────────────────────────


async def check_twin_eligibility(
    db: AsyncIOMotorDatabase,
    contact_name: str,
    owner_user_id: str,
) -> None:
    """Assert that an active consent record exists for this contact/owner pair.

    Raises:
        ConsentNotFoundError: No record exists.
        ConsentRevokedError: Record exists but status is REVOKED.
        ConsentNotGrantedError: Record exists but approved=False.
    """
    record = await get_consent(db, contact_name, owner_user_id)
    if record is None:
        raise ConsentNotFoundError(
            f"No consent record found for contact '{contact_name}'. "
            "Explicit approval is required before creating a twin."
        )
    if record.get("status") == ConsentStatus.REVOKED:
        raise ConsentRevokedError(
            f"Consent for contact '{contact_name}' has been revoked."
        )
    if not record.get("approved"):
        raise ConsentNotGrantedError(
            f"Consent for contact '{contact_name}' has not been granted."
        )


async def check_voice_eligibility(
    db: AsyncIOMotorDatabase,
    contact_name: str,
    owner_user_id: str,
) -> None:
    """Assert that voice-rights are active for this contact/owner pair.

    Voice cloning requires a separate opt-in beyond basic twin consent.

    Raises:
        ConsentNotFoundError: No record exists.
        ConsentRevokedError: Consent has been revoked.
        VoiceConsentError: Voice-rights flag is not set.
    """
    record = await get_consent(db, contact_name, owner_user_id)
    if record is None:
        raise ConsentNotFoundError(
            f"No consent record found for contact '{contact_name}'."
        )
    if record.get("status") == ConsentStatus.REVOKED:
        raise ConsentRevokedError(
            f"Consent for contact '{contact_name}' has been revoked."
        )
    if not record.get("voice_rights"):
        raise VoiceConsentError(
            f"Voice-rights not granted for contact '{contact_name}'. "
            "Explicit voice-rights approval is required for voice cloning."
        )
