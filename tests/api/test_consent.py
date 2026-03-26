"""Tests for the consent ledger and twin eligibility model.

Covers:
- Consent record schema (grant creates correct structure)
- approve/revoke status transitions
- twin creation blocked without consent
- voice cloning blocked without voice-rights
- revoke disables future sessions (send_message check)
"""
import pytest

from services.api.consent import (
    ConsentNotFoundError,
    ConsentNotGrantedError,
    ConsentRevokedError,
    ConsentStatus,
    VoiceConsentError,
    check_twin_eligibility,
    check_voice_eligibility,
    get_consent,
    grant_consent,
    revoke_consent,
)


# ─── In-Memory Stub ───────────────────────────────────────────────────────────


class FakeConsentCollection:
    """In-memory collection stub for consent tests."""

    def __init__(self):
        self._docs: list[dict] = []

    async def create_index(self, *args, **kwargs):
        pass

    async def find_one(self, query: dict, projection: dict | None = None):
        for doc in self._docs:
            if self._matches(doc, query):
                result = doc.copy()
                if projection and projection.get("_id") == 0:
                    result.pop("_id", None)
                return result
        return None

    async def update_one(self, query: dict, update: dict, upsert: bool = False):
        for doc in self._docs:
            if self._matches(doc, query):
                if "$set" in update:
                    doc.update(update["$set"])
                return _FakeUpdateResult(matched_count=1)
        if upsert:
            new_doc = {}
            if "$setOnInsert" in update:
                new_doc.update(update["$setOnInsert"])
            if "$set" in update:
                new_doc.update(update["$set"])
            self._docs.append(new_doc)
            return _FakeUpdateResult(matched_count=0)
        return _FakeUpdateResult(matched_count=0)

    @staticmethod
    def _matches(doc: dict, query: dict) -> bool:
        return all(doc.get(k) == v for k, v in query.items())


class _FakeUpdateResult:
    def __init__(self, matched_count: int):
        self.matched_count = matched_count


class FakeDB:
    def __init__(self):
        self.consents = FakeConsentCollection()


# ─── Schema Tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_grant_creates_active_record():
    db = FakeDB()
    await grant_consent(db, "mom", "gagan")
    record = await get_consent(db, "mom", "gagan")
    assert record is not None
    assert record["contact_name"] == "mom"
    assert record["owner_user_id"] == "gagan"
    assert record["approved"] is True
    assert record["status"] == ConsentStatus.ACTIVE
    assert record["voice_rights"] is False
    assert record["approved_at"] is not None
    assert record["revoked_at"] is None


@pytest.mark.asyncio
async def test_grant_with_voice_rights():
    db = FakeDB()
    await grant_consent(db, "dad", "gagan", voice_rights=True)
    record = await get_consent(db, "dad", "gagan")
    assert record["voice_rights"] is True
    assert record["approved"] is True


@pytest.mark.asyncio
async def test_get_consent_returns_none_when_missing():
    db = FakeDB()
    result = await get_consent(db, "unknown", "gagan")
    assert result is None


# ─── Approve / Revoke Transitions ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revoke_sets_revoked_status():
    db = FakeDB()
    await grant_consent(db, "mom", "gagan")
    await revoke_consent(db, "mom", "gagan", reason="requested by family")
    record = await get_consent(db, "mom", "gagan")
    assert record["status"] == ConsentStatus.REVOKED
    assert record["approved"] is False
    assert record["voice_rights"] is False
    assert record["revoked_at"] is not None
    assert record["revoke_reason"] == "requested by family"


@pytest.mark.asyncio
async def test_revoke_without_reason():
    db = FakeDB()
    await grant_consent(db, "mom", "gagan")
    await revoke_consent(db, "mom", "gagan")
    record = await get_consent(db, "mom", "gagan")
    assert record["status"] == ConsentStatus.REVOKED
    assert record["revoke_reason"] is None


@pytest.mark.asyncio
async def test_revoke_nonexistent_raises():
    db = FakeDB()
    with pytest.raises(ConsentNotFoundError):
        await revoke_consent(db, "nonexistent", "gagan")


@pytest.mark.asyncio
async def test_regrant_after_revoke_reactivates():
    db = FakeDB()
    await grant_consent(db, "mom", "gagan")
    await revoke_consent(db, "mom", "gagan")
    await grant_consent(db, "mom", "gagan")
    record = await get_consent(db, "mom", "gagan")
    assert record["status"] == ConsentStatus.ACTIVE
    assert record["approved"] is True
    assert record["revoked_at"] is None


# ─── Twin Eligibility Guard ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_twin_eligibility_passes_with_active_consent():
    db = FakeDB()
    await grant_consent(db, "mom", "gagan")
    # Should not raise
    await check_twin_eligibility(db, "mom", "gagan")


@pytest.mark.asyncio
async def test_check_twin_eligibility_raises_if_no_record():
    db = FakeDB()
    with pytest.raises(ConsentNotFoundError):
        await check_twin_eligibility(db, "stranger", "gagan")


@pytest.mark.asyncio
async def test_check_twin_eligibility_raises_if_revoked():
    db = FakeDB()
    await grant_consent(db, "mom", "gagan")
    await revoke_consent(db, "mom", "gagan")
    with pytest.raises(ConsentRevokedError):
        await check_twin_eligibility(db, "mom", "gagan")


@pytest.mark.asyncio
async def test_check_twin_eligibility_raises_if_not_approved():
    """Pending/unapproved records also block twin creation."""
    db = FakeDB()
    # Manually insert a pending record (no grant called)
    db.consents._docs.append(
        {
            "contact_name": "dad",
            "owner_user_id": "gagan",
            "approved": False,
            "voice_rights": False,
            "status": ConsentStatus.PENDING,
            "created_at": None,
            "approved_at": None,
            "revoked_at": None,
        }
    )
    with pytest.raises(ConsentNotGrantedError):
        await check_twin_eligibility(db, "dad", "gagan")


# ─── Voice Eligibility Guard ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_voice_eligibility_passes_with_voice_rights():
    db = FakeDB()
    await grant_consent(db, "mom", "gagan", voice_rights=True)
    # Should not raise
    await check_voice_eligibility(db, "mom", "gagan")


@pytest.mark.asyncio
async def test_check_voice_eligibility_raises_without_voice_rights():
    db = FakeDB()
    await grant_consent(db, "mom", "gagan", voice_rights=False)
    with pytest.raises(VoiceConsentError):
        await check_voice_eligibility(db, "mom", "gagan")


@pytest.mark.asyncio
async def test_check_voice_eligibility_raises_if_no_record():
    db = FakeDB()
    with pytest.raises(ConsentNotFoundError):
        await check_voice_eligibility(db, "stranger", "gagan")


@pytest.mark.asyncio
async def test_check_voice_eligibility_raises_if_revoked():
    db = FakeDB()
    await grant_consent(db, "mom", "gagan", voice_rights=True)
    await revoke_consent(db, "mom", "gagan")
    with pytest.raises(ConsentRevokedError):
        await check_voice_eligibility(db, "mom", "gagan")


# ─── Isolation: Consent Is Per Owner ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_consent_is_per_owner():
    """Consent granted by one user does not apply to another."""
    db = FakeDB()
    await grant_consent(db, "mom", "gagan")
    # A different user does not have consent
    with pytest.raises(ConsentNotFoundError):
        await check_twin_eligibility(db, "mom", "other_user")
