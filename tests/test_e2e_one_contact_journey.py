"""
End-to-end acceptance tests for the one-contact journey.

Exercises every critical path through the After-Life system with a fixture
contact (Margaret / "mom"). No external services are required — all LLM,
TTS, and LiveKit SDK calls are mocked. An in-memory DB stub is wired into
the FastAPI app for each test.

Critical paths covered:
  1. Consented contact ingest    POST /consent/grant
  2. Media backfill              POST /biography/update
  3. Live voice session start    POST /conversation/start
  4. Grounded text reply         POST /conversation/message (text path)
  5. Grounded voice reply        POST /conversation/message (TTS path)
  6. LiveKit voice bootstrap     POST /voice/session/start
  7. Consent revoke              POST /consent/revoke → blocks all above

Assertion style: each step logs a clear failure message so failing tests
are easy to diagnose without reading the implementation.
"""

import base64
import pytest
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from services.api.consent import ConsentStatus
from services.api.livekit_session import make_room_name
from services.api.main import app


# ─── Fixture: one contact ─────────────────────────────────────────────────────

CONTACT = "mom"
USER = "gagan"

CONTACT_PROFILE = {
    "name": CONTACT,
    "biography": (
        "Margaret was a warm, witty woman who loved gardening and cooking. "
        "She always had a pot of chai on the stove and greeted everyone with a hug."
    ),
    "personality_profile": "Warm, nurturing. Uses Punjabi endearments. Always asks if you've eaten.",
    "common_phrases": "Janu, have you eaten? | Come home soon",
    "voice_id": "eleven-mom-001",
}

LIVEKIT_ENV = {
    "LIVEKIT_URL": "wss://test.livekit.io",
    "LIVEKIT_API_KEY": "test-key",
    "LIVEKIT_API_SECRET": "test-secret",
}


# ─── In-memory DB stub ────────────────────────────────────────────────────────


class _FakeUpdateResult:
    def __init__(self, matched_count: int) -> None:
        self.matched_count = matched_count


class _FakeCollection:
    """Generic in-memory MongoDB collection stub used across all e2e tests."""

    def __init__(self) -> None:
        self._docs: list[dict] = []

    async def create_index(self, *args, **kwargs) -> None:
        pass

    async def insert_one(self, doc: dict) -> None:
        self._docs.append(doc.copy())

    async def find_one(
        self, query: dict, projection: dict | None = None
    ) -> dict | None:
        for doc in self._docs:
            if self._matches(doc, query):
                result = doc.copy()
                if projection and projection.get("_id") == 0:
                    result.pop("_id", None)
                return result
        return None

    async def update_one(
        self, query: dict, update: dict, upsert: bool = False, **_kwargs
    ) -> _FakeUpdateResult:
        for doc in self._docs:
            if self._matches(doc, query):
                if "$set" in update:
                    doc.update(update["$set"])
                if "$push" in update:
                    for field, value in update["$push"].items():
                        doc.setdefault(field, []).append(value)
                return _FakeUpdateResult(matched_count=1)
        if upsert:
            new_doc: dict = {}
            if "$setOnInsert" in update:
                new_doc.update(update["$setOnInsert"])
            if "$set" in update:
                new_doc.update(update["$set"])
            self._docs.append(new_doc)
        return _FakeUpdateResult(matched_count=0)

    @staticmethod
    def _matches(doc: dict, query: dict) -> bool:
        return all(doc.get(k) == v for k, v in query.items())


class FakeDB:
    """In-memory stand-in for AsyncIOMotorDatabase."""

    def __init__(self) -> None:
        self.sessions = _FakeCollection()
        self.consents = _FakeCollection()
        self.livekit_sessions = _FakeCollection()
        # contacts collection is not used by the API service directly
        # (load_contact_profile uses a sync MongoClient — patched in tests)


# ─── Test: 1. Consented contact ingest ───────────────────────────────────────


@pytest.mark.asyncio
async def test_consent_grant_activates_record() -> None:
    """
    Critical path: consented contact ingest.
    POST /consent/grant must create an ACTIVE consent record with the correct
    contact_name, user_name, and voice_rights flag.
    """
    db = FakeDB()
    app.state.db = db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/consent/grant",
            json={"contact_name": CONTACT, "user_name": USER, "voice_rights": True},
        )

    assert resp.status_code == 204, (
        f"consent/grant failed: expected 204, got {resp.status_code} — {resp.text}"
    )

    record = db.consents._docs[0]
    assert record["contact_name"] == CONTACT, "consent record has wrong contact_name"
    assert record["owner_user_id"] == USER, "consent record has wrong owner_user_id"
    assert record["approved"] is True, "consent record not approved after grant"
    assert record["voice_rights"] is True, "voice_rights not set after grant"
    assert record["status"] == ConsentStatus.ACTIVE, "consent status not ACTIVE"


@pytest.mark.asyncio
async def test_consent_status_endpoint_reflects_grant() -> None:
    """
    GET /consent/status must return the live consent state after a grant.
    """
    db = FakeDB()
    app.state.db = db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/consent/grant",
            json={"contact_name": CONTACT, "user_name": USER, "voice_rights": True},
        )
        resp = await client.get(
            "/consent/status",
            params={"contact_name": CONTACT, "user_name": USER},
        )

    assert resp.status_code == 200, (
        f"consent/status failed: {resp.status_code} — {resp.text}"
    )
    data = resp.json()
    assert data["approved"] is True, "consent/status shows not approved"
    assert data["voice_rights"] is True, "consent/status shows voice_rights=False"
    assert data["status"] == "active", f"unexpected status: {data['status']}"


# ─── Test: 2. Media backfill ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_biography_update_accepted() -> None:
    """
    Critical path: media backfill.
    POST /biography/update must persist a new biography without error.
    """
    db = FakeDB()
    app.state.db = db

    new_bio = "Margaret loved tea and had a deep laugh that filled the room."

    with patch("services.api.main.update_biography") as mock_update:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/biography/update",
                json={"contact_name": CONTACT, "new_biography": new_bio},
            )

    assert resp.status_code == 204, (
        f"biography/update failed: expected 204, got {resp.status_code} — {resp.text}"
    )
    mock_update.assert_called_once_with(CONTACT, new_bio)


# ─── Test: 3. Live voice session start (conversation) ────────────────────────


@pytest.mark.asyncio
async def test_conversation_start_requires_consent() -> None:
    """
    Critical path: live voice session start.
    POST /conversation/start must return 403 when no consent record exists.
    """
    db = FakeDB()
    app.state.db = db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/conversation/start",
            json={"contact_name": CONTACT, "user_name": USER},
        )

    assert resp.status_code == 403, (
        f"conversation/start should be 403 without consent, got {resp.status_code}"
    )


@pytest.mark.asyncio
async def test_conversation_start_with_consent_returns_session() -> None:
    """
    Critical path: live voice session start.
    POST /conversation/start must return a session_id and greeting after
    consent is granted.
    """
    db = FakeDB()
    app.state.db = db

    # Grant consent first
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/consent/grant",
            json={"contact_name": CONTACT, "user_name": USER, "voice_rights": False},
        )

        with patch(
            "services.api.main.load_contact_profile", return_value=CONTACT_PROFILE
        ), patch(
            "services.api.main.reply_as_persona",
            return_value="Janu, I missed you! Have you eaten?",
        ):
            resp = await client.post(
                "/conversation/start",
                json={"contact_name": CONTACT, "user_name": USER},
            )

    assert resp.status_code == 200, (
        f"conversation/start failed: {resp.status_code} — {resp.text}"
    )
    data = resp.json()
    assert "session_id" in data, "response missing session_id"
    assert data["greeting_text"], "greeting_text is empty"
    assert len(data["session_id"]) == 36, f"session_id malformed: {data['session_id']}"


# ─── Test: 4. Grounded text reply ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_grounded_text_reply_full_path() -> None:
    """
    Critical path: grounded text reply.
    POST /conversation/message must return a text reply generated by the persona.
    Consent is re-checked on every message; an active session is required.
    """
    db = FakeDB()
    app.state.db = db

    # Grant consent
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/consent/grant",
            json={"contact_name": CONTACT, "user_name": USER, "voice_rights": False},
        )

        # Start session
        with patch(
            "services.api.main.load_contact_profile", return_value=CONTACT_PROFILE
        ), patch(
            "services.api.main.reply_as_persona", return_value="Hello janu!"
        ):
            start = await client.post(
                "/conversation/start",
                json={"contact_name": CONTACT, "user_name": USER},
            )
        session_id = start.json()["session_id"]

        # Send text message
        with patch(
            "services.api.main.reply_as_persona",
            return_value="I made your favourite dal today.",
        ):
            resp = await client.post(
                "/conversation/message",
                json={"session_id": session_id, "message": "How are you, mom?"},
            )

    assert resp.status_code == 200, (
        f"conversation/message failed: {resp.status_code} — {resp.text}"
    )
    data = resp.json()
    assert data["reply_text"] == "I made your favourite dal today.", (
        f"unexpected reply_text: {data['reply_text']!r}"
    )


@pytest.mark.asyncio
async def test_text_reply_blocked_after_consent_revoke() -> None:
    """
    Critical path: consent revoke.
    Sending a message after consent is revoked must return 403.
    Revocation must take effect on the NEXT message — not just future sessions.
    """
    db = FakeDB()
    app.state.db = db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Grant consent + start session
        await client.post(
            "/consent/grant",
            json={"contact_name": CONTACT, "user_name": USER, "voice_rights": False},
        )
        with patch(
            "services.api.main.load_contact_profile", return_value=CONTACT_PROFILE
        ), patch(
            "services.api.main.reply_as_persona", return_value="Hi janu!"
        ):
            start = await client.post(
                "/conversation/start",
                json={"contact_name": CONTACT, "user_name": USER},
            )
        session_id = start.json()["session_id"]

        # Revoke consent
        await client.post(
            "/consent/revoke",
            json={
                "contact_name": CONTACT,
                "user_name": USER,
                "reason": "user requested deletion",
            },
        )

        # Try to send a message — must be blocked
        resp = await client.post(
            "/conversation/message",
            json={"session_id": session_id, "message": "You still there?"},
        )

    assert resp.status_code == 403, (
        f"Expected 403 after consent revoke, got {resp.status_code} — {resp.text}"
    )


# ─── Test: 5. Grounded voice reply (TTS path) ────────────────────────────────


@pytest.mark.asyncio
async def test_grounded_voice_reply_returns_audio() -> None:
    """
    Critical path: grounded voice reply.
    POST /conversation/message must return base64-encoded audio when the
    session's voice_id is set AND voice-rights consent is active.
    """
    db = FakeDB()
    app.state.db = db

    fake_audio = b"FAKE_AUDIO_BYTES"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Grant with voice_rights=True so TTS path is active
        await client.post(
            "/consent/grant",
            json={"contact_name": CONTACT, "user_name": USER, "voice_rights": True},
        )

        with patch(
            "services.api.main.load_contact_profile",
            return_value={**CONTACT_PROFILE, "voice_id": "eleven-mom-001"},
        ), patch(
            "services.api.main.reply_as_persona", return_value="Hi janu!"
        ), patch(
            "services.api.main.text_to_speech", return_value=fake_audio
        ):
            start = await client.post(
                "/conversation/start",
                json={"contact_name": CONTACT, "user_name": USER},
            )
            session_id = start.json()["session_id"]

            with patch(
                "services.api.main.reply_as_persona",
                return_value="Come home, janu.",
            ), patch(
                "services.api.main.text_to_speech", return_value=fake_audio
            ):
                resp = await client.post(
                    "/conversation/message",
                    json={
                        "session_id": session_id,
                        "message": "Miss you, mom.",
                    },
                )

    assert resp.status_code == 200, (
        f"voice reply path failed: {resp.status_code} — {resp.text}"
    )
    data = resp.json()
    assert data["reply_text"], "reply_text is empty in voice reply"
    assert data["reply_audio_b64"] is not None, "reply_audio_b64 is None — TTS path not exercised"
    decoded = base64.b64decode(data["reply_audio_b64"])
    assert decoded == fake_audio, "decoded audio bytes do not match expected"


@pytest.mark.asyncio
async def test_voice_reply_falls_back_when_no_voice_rights() -> None:
    """
    When voice_rights is False, the reply must still succeed but audio must
    be None (no cloned voice used). Text-only fallback is correct behaviour.
    """
    db = FakeDB()
    app.state.db = db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Grant without voice_rights
        await client.post(
            "/consent/grant",
            json={"contact_name": CONTACT, "user_name": USER, "voice_rights": False},
        )

        with patch(
            "services.api.main.load_contact_profile",
            return_value={**CONTACT_PROFILE, "voice_id": "eleven-mom-001"},
        ), patch(
            "services.api.main.reply_as_persona", return_value="Hello!"
        ), patch(
            "services.api.main.text_to_speech", return_value=b"audio"
        ):
            start = await client.post(
                "/conversation/start",
                json={"contact_name": CONTACT, "user_name": USER},
            )
            session_id = start.json()["session_id"]

            with patch(
                "services.api.main.reply_as_persona",
                return_value="Text only reply.",
            ), patch(
                "services.api.main.text_to_speech", return_value=None
            ):
                resp = await client.post(
                    "/conversation/message",
                    json={"session_id": session_id, "message": "Hello"},
                )

    assert resp.status_code == 200
    data = resp.json()
    assert data["reply_text"] == "Text only reply."
    # audio may be None or absent — both are acceptable for text-only path
    assert data.get("reply_audio_b64") is None, (
        "Expected no audio when voice_rights=False and TTS returns None"
    )


# ─── Test: 6. LiveKit voice session bootstrap ─────────────────────────────────


@pytest.mark.asyncio
async def test_livekit_voice_session_start_full_bootstrap() -> None:
    """
    Critical path: live voice session start (LiveKit).
    POST /voice/session/start must:
    - Verify twin consent (active consent required)
    - Verify voice_rights (voice cloning requires explicit opt-in)
    - Create a LiveKit session with a stable room name
    - Return a participant JWT token and LiveKit URL
    """
    db = FakeDB()
    app.state.db = db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/consent/grant",
            json={"contact_name": CONTACT, "user_name": USER, "voice_rights": True},
        )

        with patch(
            "services.api.main.load_contact_profile", return_value=CONTACT_PROFILE
        ), patch(
            "services.api.main.generate_participant_token",
            return_value="signed.jwt.token",
        ), patch.dict("os.environ", LIVEKIT_ENV):
            resp = await client.post(
                "/voice/session/start",
                json={"contact_name": CONTACT, "user_name": USER},
            )

    assert resp.status_code == 200, (
        f"voice/session/start failed: {resp.status_code} — {resp.text}"
    )
    data = resp.json()
    assert "session_id" in data, "response missing session_id"
    assert data["room_name"] == make_room_name(CONTACT, USER), (
        f"unexpected room_name: {data['room_name']!r}"
    )
    assert data["token"] == "signed.jwt.token", "response missing participant token"
    assert data["livekit_url"] == LIVEKIT_ENV["LIVEKIT_URL"], "response missing livekit_url"
    assert data["is_new"] is True, "expected is_new=True for first session"


@pytest.mark.asyncio
async def test_livekit_session_start_requires_voice_rights() -> None:
    """
    POST /voice/session/start must return 403 when voice_rights=False.
    Voice cloning cannot proceed without an explicit voice opt-in.
    """
    db = FakeDB()
    app.state.db = db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Grant consent but NO voice rights
        await client.post(
            "/consent/grant",
            json={"contact_name": CONTACT, "user_name": USER, "voice_rights": False},
        )

        with patch.dict("os.environ", LIVEKIT_ENV):
            resp = await client.post(
                "/voice/session/start",
                json={"contact_name": CONTACT, "user_name": USER},
            )

    assert resp.status_code == 403, (
        f"Expected 403 without voice_rights, got {resp.status_code}"
    )


@pytest.mark.asyncio
async def test_livekit_session_reconnect_returns_same_room() -> None:
    """
    Calling /voice/session/start twice for the same contact/user must return
    the same session_id and room_name (reconnect-safe, no orphaned rooms).
    """
    db = FakeDB()
    app.state.db = db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/consent/grant",
            json={"contact_name": CONTACT, "user_name": USER, "voice_rights": True},
        )

        with patch(
            "services.api.main.load_contact_profile", return_value=CONTACT_PROFILE
        ), patch(
            "services.api.main.generate_participant_token", return_value="jwt"
        ), patch.dict("os.environ", LIVEKIT_ENV):
            r1 = await client.post(
                "/voice/session/start",
                json={"contact_name": CONTACT, "user_name": USER},
            )
            r2 = await client.post(
                "/voice/session/start",
                json={"contact_name": CONTACT, "user_name": USER},
            )

    assert r1.status_code == 200
    assert r2.status_code == 200
    d1, d2 = r1.json(), r2.json()
    assert d1["session_id"] == d2["session_id"], (
        "reconnect returned a different session_id — orphaned room risk"
    )
    assert d1["room_name"] == d2["room_name"], (
        "reconnect returned a different room_name — orphaned room risk"
    )
    assert d2["is_new"] is False, "second call should have is_new=False"


# ─── Test: 7. Consent revoke ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_consent_revoke_blocks_conversation_start() -> None:
    """
    Critical path: consent revoke.
    POST /consent/revoke followed by POST /conversation/start must return 403.
    """
    db = FakeDB()
    app.state.db = db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/consent/grant",
            json={"contact_name": CONTACT, "user_name": USER},
        )
        await client.post(
            "/consent/revoke",
            json={"contact_name": CONTACT, "user_name": USER},
        )

        with patch(
            "services.api.main.load_contact_profile", return_value=CONTACT_PROFILE
        ), patch(
            "services.api.main.reply_as_persona", return_value="Hi!"
        ):
            resp = await client.post(
                "/conversation/start",
                json={"contact_name": CONTACT, "user_name": USER},
            )

    assert resp.status_code == 403, (
        f"Expected 403 after revoke, got {resp.status_code} — {resp.text}"
    )


@pytest.mark.asyncio
async def test_consent_revoke_blocks_livekit_session_start() -> None:
    """
    POST /voice/session/start must return 403 after consent is revoked.
    """
    db = FakeDB()
    app.state.db = db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/consent/grant",
            json={"contact_name": CONTACT, "user_name": USER, "voice_rights": True},
        )
        await client.post(
            "/consent/revoke",
            json={"contact_name": CONTACT, "user_name": USER},
        )

        with patch.dict("os.environ", LIVEKIT_ENV):
            resp = await client.post(
                "/voice/session/start",
                json={"contact_name": CONTACT, "user_name": USER},
            )

    assert resp.status_code == 403, (
        f"Expected 403 after revoke, got {resp.status_code} — {resp.text}"
    )


@pytest.mark.asyncio
async def test_consent_revoke_records_reason() -> None:
    """
    POST /consent/revoke must persist the revoke reason and timestamp.
    GET /consent/status must reflect the revoked state.
    """
    db = FakeDB()
    app.state.db = db

    revoke_reason = "user requested full deletion"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/consent/grant",
            json={"contact_name": CONTACT, "user_name": USER},
        )
        await client.post(
            "/consent/revoke",
            json={
                "contact_name": CONTACT,
                "user_name": USER,
                "reason": revoke_reason,
            },
        )
        status_resp = await client.get(
            "/consent/status",
            params={"contact_name": CONTACT, "user_name": USER},
        )

    assert status_resp.status_code == 200
    data = status_resp.json()
    assert data["status"] == "revoked", f"expected revoked, got {data['status']!r}"
    assert data["approved"] is False, "approved should be False after revoke"
    assert data["revoked_at"] is not None, "revoked_at must be set"


# ─── Full sequential journey ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_one_contact_journey() -> None:
    """
    Full end-to-end smoke: exercises all six critical paths in order for a
    single consented contact, from ingest through revoke.

    Steps:
      1. Grant consent (ingest)
      2. Update biography (media backfill)
      3. Start conversation (session start / consent gate)
      4. Exchange a message (grounded text reply)
      5. Start LiveKit session (voice bootstrap)
      6. Revoke consent (revoke gate)
      7. Verify all subsequent calls are blocked (403)
    """
    db = FakeDB()
    app.state.db = db
    fake_audio = b"AUDIO"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # ── Step 1: Consented contact ingest ──────────────────────────────────
        r = await client.post(
            "/consent/grant",
            json={"contact_name": CONTACT, "user_name": USER, "voice_rights": True},
        )
        assert r.status_code == 204, f"[step 1] consent/grant → {r.status_code}"

        status = await client.get(
            "/consent/status",
            params={"contact_name": CONTACT, "user_name": USER},
        )
        assert status.json()["approved"] is True, "[step 1] consent not approved"

        # ── Step 2: Media backfill ─────────────────────────────────────────────
        with patch("services.api.main.update_biography") as mock_bio:
            r = await client.post(
                "/biography/update",
                json={
                    "contact_name": CONTACT,
                    "new_biography": "Updated biography after chat analysis.",
                },
            )
        assert r.status_code == 204, f"[step 2] biography/update → {r.status_code}"
        mock_bio.assert_called_once()

        # ── Step 3: Live voice session start ──────────────────────────────────
        with patch(
            "services.api.main.load_contact_profile", return_value=CONTACT_PROFILE
        ), patch(
            "services.api.main.reply_as_persona",
            return_value="Janu, I missed you!",
        ), patch(
            "services.api.main.text_to_speech", return_value=fake_audio
        ):
            r = await client.post(
                "/conversation/start",
                json={"contact_name": CONTACT, "user_name": USER},
            )
        assert r.status_code == 200, f"[step 3] conversation/start → {r.status_code} — {r.text}"
        session_id = r.json()["session_id"]
        assert r.json()["greeting_text"], "[step 3] empty greeting"

        # ── Step 4: Grounded text reply ────────────────────────────────────────
        with patch(
            "services.api.main.reply_as_persona",
            return_value="I made dal today, come home.",
        ), patch(
            "services.api.main.text_to_speech", return_value=fake_audio
        ):
            r = await client.post(
                "/conversation/message",
                json={"session_id": session_id, "message": "How are you?"},
            )
        assert r.status_code == 200, f"[step 4] conversation/message → {r.status_code}"
        assert r.json()["reply_text"], "[step 4] empty reply_text"
        assert r.json()["reply_audio_b64"] is not None, (
            "[step 4] no audio returned — grounded voice reply path not exercised"
        )

        # ── Step 5: LiveKit voice session bootstrap ────────────────────────────
        with patch(
            "services.api.main.load_contact_profile", return_value=CONTACT_PROFILE
        ), patch(
            "services.api.main.generate_participant_token",
            return_value="signed.jwt",
        ), patch.dict("os.environ", LIVEKIT_ENV):
            r = await client.post(
                "/voice/session/start",
                json={"contact_name": CONTACT, "user_name": USER},
            )
        assert r.status_code == 200, (
            f"[step 5] voice/session/start → {r.status_code} — {r.text}"
        )
        lk_data = r.json()
        assert lk_data["token"] == "signed.jwt", "[step 5] missing participant token"
        assert lk_data["room_name"] == make_room_name(CONTACT, USER), (
            "[step 5] wrong room_name"
        )

        # ── Step 6: Consent revoke ─────────────────────────────────────────────
        r = await client.post(
            "/consent/revoke",
            json={"contact_name": CONTACT, "user_name": USER, "reason": "test"},
        )
        assert r.status_code == 204, f"[step 6] consent/revoke → {r.status_code}"

        # ── Step 7: All subsequent calls must be blocked ───────────────────────
        with patch(
            "services.api.main.load_contact_profile", return_value=CONTACT_PROFILE
        ), patch(
            "services.api.main.reply_as_persona", return_value="Hi!"
        ):
            r_start = await client.post(
                "/conversation/start",
                json={"contact_name": CONTACT, "user_name": USER},
            )
        assert r_start.status_code == 403, (
            f"[step 7] conversation/start after revoke → {r_start.status_code}"
        )

        r_msg = await client.post(
            "/conversation/message",
            json={"session_id": session_id, "message": "Still there?"},
        )
        assert r_msg.status_code == 403, (
            f"[step 7] conversation/message after revoke → {r_msg.status_code}"
        )

        with patch.dict("os.environ", LIVEKIT_ENV):
            r_lk = await client.post(
                "/voice/session/start",
                json={"contact_name": CONTACT, "user_name": USER},
            )
        assert r_lk.status_code == 403, (
            f"[step 7] voice/session/start after revoke → {r_lk.status_code}"
        )
