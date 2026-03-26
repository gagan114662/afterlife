"""
Integration tests for the LiveKit voice session bootstrap path.

Covers:
- Session creation (new room)
- Reconnect idempotency (same room name returned for active session)
- Session state transitions (active → ended)
- Consent gates on /voice/session/start
- Token generation (mocked LiveKit SDK)
- /voice/session/end and /voice/session/{id}/status endpoints
"""

import pytest
from unittest.mock import MagicMock, patch

from services.api.livekit_session import (
    LiveKitSessionState,
    create_or_resume_session,
    end_livekit_session,
    get_active_session,
    get_livekit_session,
    make_room_name,
)


# ─── In-memory DB stubs ───────────────────────────────────────────────────────


class _FakeUpdateResult:
    def __init__(self, matched_count: int) -> None:
        self.matched_count = matched_count


class FakeLKCollection:
    """In-memory collection stub for livekit_session tests."""

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
        self, query: dict, update: dict, **_kwargs
    ) -> _FakeUpdateResult:
        for doc in self._docs:
            if self._matches(doc, query):
                if "$set" in update:
                    doc.update(update["$set"])
                return _FakeUpdateResult(matched_count=1)
        return _FakeUpdateResult(matched_count=0)

    @staticmethod
    def _matches(doc: dict, query: dict) -> bool:
        return all(doc.get(k) == v for k, v in query.items())


class FakeLKDB:
    def __init__(self) -> None:
        self.livekit_sessions = FakeLKCollection()


# ─── make_room_name ───────────────────────────────────────────────────────────


def test_make_room_name_stable() -> None:
    """Same contact/user always yields the same room name."""
    assert make_room_name("Alice Smith", "Bob") == make_room_name("Alice Smith", "Bob")


def test_make_room_name_format() -> None:
    assert make_room_name("Alice Smith", "Bob") == "afterlife-alice-smith-bob"


def test_make_room_name_unique_per_contact() -> None:
    assert make_room_name("Alice", "Bob") != make_room_name("Charlie", "Bob")


def test_make_room_name_unique_per_user() -> None:
    assert make_room_name("Alice", "Bob") != make_room_name("Alice", "Carol")


# ─── create_or_resume_session ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_session_returns_new() -> None:
    db = FakeLKDB()
    session = await create_or_resume_session(db, "Alice", "Bob", voice_id="v123")
    assert session["is_new"] is True
    assert session["contact_name"] == "Alice"
    assert session["user_name"] == "Bob"
    assert session["voice_id"] == "v123"
    assert session["state"] == LiveKitSessionState.ACTIVE
    assert session["room_name"] == make_room_name("Alice", "Bob")
    assert len(session["session_id"]) == 36  # UUID


@pytest.mark.asyncio
async def test_create_session_idempotent_on_reconnect() -> None:
    """Second call for same pair returns existing session (reconnect-safe)."""
    db = FakeLKDB()
    first = await create_or_resume_session(db, "Alice", "Bob")
    second = await create_or_resume_session(db, "Alice", "Bob")
    assert second["is_new"] is False
    assert second["session_id"] == first["session_id"]
    assert second["room_name"] == first["room_name"]


@pytest.mark.asyncio
async def test_different_contacts_get_different_sessions() -> None:
    db = FakeLKDB()
    a = await create_or_resume_session(db, "Alice", "Bob")
    b = await create_or_resume_session(db, "Charlie", "Bob")
    assert a["session_id"] != b["session_id"]
    assert a["room_name"] != b["room_name"]


# ─── get_active_session ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_active_session_returns_none_when_missing() -> None:
    db = FakeLKDB()
    result = await get_active_session(db, "NoOne", "Nobody")
    assert result is None


@pytest.mark.asyncio
async def test_get_active_session_returns_created_session() -> None:
    db = FakeLKDB()
    created = await create_or_resume_session(db, "Alice", "Bob")
    found = await get_active_session(db, "Alice", "Bob")
    assert found is not None
    assert found["session_id"] == created["session_id"]


# ─── get_livekit_session ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_livekit_session_by_id() -> None:
    db = FakeLKDB()
    created = await create_or_resume_session(db, "Alice", "Bob")
    fetched = await get_livekit_session(db, created["session_id"])
    assert fetched is not None
    assert fetched["session_id"] == created["session_id"]


@pytest.mark.asyncio
async def test_get_livekit_session_not_found_returns_none() -> None:
    db = FakeLKDB()
    result = await get_livekit_session(db, "00000000-0000-0000-0000-000000000000")
    assert result is None


# ─── end_livekit_session ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_end_session_marks_state_ended() -> None:
    db = FakeLKDB()
    session = await create_or_resume_session(db, "Alice", "Bob")
    found = await end_livekit_session(db, session["session_id"])
    assert found is True
    doc = await get_livekit_session(db, session["session_id"])
    assert doc is not None
    assert doc["state"] == LiveKitSessionState.ENDED


@pytest.mark.asyncio
async def test_end_session_not_found_returns_false() -> None:
    db = FakeLKDB()
    found = await end_livekit_session(db, "00000000-0000-0000-0000-000000000000")
    assert found is False


@pytest.mark.asyncio
async def test_reconnect_after_end_creates_new_session() -> None:
    """Once a session is ended, the next call creates a fresh session."""
    db = FakeLKDB()
    first = await create_or_resume_session(db, "Alice", "Bob")
    await end_livekit_session(db, first["session_id"])
    second = await create_or_resume_session(db, "Alice", "Bob")
    assert second["is_new"] is True
    # Room name is stable — same room slot for reconnects
    assert second["room_name"] == first["room_name"]
    # But it's a fresh session record
    assert second["session_id"] != first["session_id"]


# ─── generate_participant_token ───────────────────────────────────────────────


def test_generate_token_raises_on_empty_api_key() -> None:
    from services.api.livekit_session import generate_participant_token

    with pytest.raises(ValueError, match="LIVEKIT_API_KEY"):
        generate_participant_token("room", "user", api_key="", api_secret="secret")


def test_generate_token_raises_on_empty_api_secret() -> None:
    from services.api.livekit_session import generate_participant_token

    with pytest.raises(ValueError, match="LIVEKIT_API_KEY"):
        generate_participant_token("room", "user", api_key="key", api_secret="")


def test_generate_token_calls_livekit_sdk() -> None:
    """Token generation delegates to livekit.api.AccessToken.to_jwt()."""
    from services.api.livekit_session import generate_participant_token

    mock_token = MagicMock()
    mock_token.to_jwt.return_value = "test.jwt.token"
    mock_token.with_identity.return_value = mock_token
    mock_token.with_ttl.return_value = mock_token
    mock_token.with_grants.return_value = mock_token

    mock_livekit_api = MagicMock()
    mock_livekit_api.AccessToken.return_value = mock_token
    mock_livekit_api.VideoGrants = MagicMock()

    with patch.dict(
        "sys.modules",
        {
            "livekit": MagicMock(),
            "livekit.api": mock_livekit_api,
        },
    ):
        result = generate_participant_token(
            room_name="afterlife-alice-bob",
            participant_identity="Bob",
            api_key="test-key",
            api_secret="test-secret",
        )

    assert result == "test.jwt.token"
    mock_livekit_api.AccessToken.assert_called_once_with(
        api_key="test-key", api_secret="test-secret"
    )
    mock_token.with_identity.assert_called_once_with("Bob")
    mock_token.to_jwt.assert_called_once()


# ─── FastAPI endpoint integration tests ───────────────────────────────────────


def _make_app_db(consent_docs=None, contact_doc=None):
    """
    Build a minimal fake DB that satisfies the /voice/session/start handler:
    - consents collection (for consent gates)
    - contacts collection (for load_contact_profile, which uses sync pymongo)
    - livekit_sessions collection
    """

    class FakeConsentCollection:
        def __init__(self, docs):
            self._docs = docs or []

        async def create_index(self, *args, **kwargs):
            pass

        async def find_one(self, query, projection=None):
            for doc in self._docs:
                if all(doc.get(k) == v for k, v in query.items()):
                    result = doc.copy()
                    if projection and projection.get("_id") == 0:
                        result.pop("_id", None)
                    return result
            return None

        async def update_one(self, *args, **kwargs):
            return _FakeUpdateResult(0)

    class FakeSessionsCollection:
        def __init__(self):
            self._docs: list[dict] = []

        async def create_index(self, *args, **kwargs):
            pass

        async def insert_one(self, doc):
            self._docs.append(doc.copy())

        async def find_one(self, query, projection=None):
            for doc in self._docs:
                if all(doc.get(k) == v for k, v in query.items()):
                    result = doc.copy()
                    if projection and projection.get("_id") == 0:
                        result.pop("_id", None)
                    return result
            return None

        async def update_one(self, query, update, **kwargs):
            for doc in self._docs:
                if all(doc.get(k) == v for k, v in query.items()):
                    if "$set" in update:
                        doc.update(update["$set"])
                    return _FakeUpdateResult(1)
            return _FakeUpdateResult(0)

    class FakeAppDB:
        def __init__(self, consent_docs, contact_doc):
            self.consents = FakeConsentCollection(consent_docs)
            self.sessions = FakeSessionsCollection()
            self.livekit_sessions = FakeLKCollection()
            self._contact_doc = contact_doc

        async def command(self, *args, **kwargs):
            return {"ok": 1}

    return FakeAppDB(consent_docs, contact_doc)


@pytest.mark.asyncio
async def test_start_voice_session_consent_not_found() -> None:
    """Returns 403 when no consent record exists."""
    from httpx import ASGITransport, AsyncClient
    from services.api.main import app

    db = _make_app_db(consent_docs=[])
    app.state.db = db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/voice/session/start",
            json={"contact_name": "Alice", "user_name": "Bob"},
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_start_voice_session_no_voice_rights() -> None:
    """Returns 403 when consent exists but voice_rights=False."""
    from httpx import ASGITransport, AsyncClient
    from services.api.main import app
    from services.api.consent import ConsentStatus

    consent_doc = {
        "contact_name": "alice",
        "owner_user_id": "bob",
        "approved": True,
        "voice_rights": False,
        "status": ConsentStatus.ACTIVE,
    }
    db = _make_app_db(consent_docs=[consent_doc])
    app.state.db = db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/voice/session/start",
            json={"contact_name": "alice", "user_name": "bob"},
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_start_voice_session_revoked_consent() -> None:
    """Returns 403 when consent has been revoked."""
    from httpx import ASGITransport, AsyncClient
    from services.api.main import app
    from services.api.consent import ConsentStatus

    consent_doc = {
        "contact_name": "alice",
        "owner_user_id": "bob",
        "approved": False,
        "voice_rights": False,
        "status": ConsentStatus.REVOKED,
    }
    db = _make_app_db(consent_docs=[consent_doc])
    app.state.db = db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/voice/session/start",
            json={"contact_name": "alice", "user_name": "bob"},
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_start_voice_session_bootstrap() -> None:
    """
    Full session bootstrap path: consent granted with voice_rights → returns
    session_id, room_name, token, and livekit_url.
    """
    from httpx import ASGITransport, AsyncClient
    from services.api.main import app
    from services.api.consent import ConsentStatus

    consent_doc = {
        "contact_name": "alice",
        "owner_user_id": "bob",
        "approved": True,
        "voice_rights": True,
        "status": ConsentStatus.ACTIVE,
    }
    db = _make_app_db(consent_docs=[consent_doc])
    app.state.db = db

    # Patch load_contact_profile (uses sync pymongo, not our fake async DB)
    mock_profile = {"name": "alice", "biography": "", "personality_profile": "",
                    "common_phrases": "", "voice_id": "voice-abc"}
    # Patch generate_participant_token to avoid needing the real LiveKit SDK
    with patch(
        "services.api.main.load_contact_profile", return_value=mock_profile
    ), patch(
        "services.api.main.generate_participant_token", return_value="fake.jwt.token"
    ), patch.dict(
        "os.environ",
        {"LIVEKIT_URL": "wss://test.livekit.io", "LIVEKIT_API_KEY": "k", "LIVEKIT_API_SECRET": "s"},
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/voice/session/start",
                json={"contact_name": "alice", "user_name": "bob"},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data
    assert data["room_name"] == make_room_name("alice", "bob")
    assert data["token"] == "fake.jwt.token"
    assert data["livekit_url"] == "wss://test.livekit.io"
    assert data["is_new"] is True


@pytest.mark.asyncio
async def test_start_voice_session_reconnect_returns_same_room() -> None:
    """
    Calling start twice for the same contact/user returns the same room_name
    and session_id (reconnect-safe).
    """
    from httpx import ASGITransport, AsyncClient
    from services.api.main import app
    from services.api.consent import ConsentStatus

    consent_doc = {
        "contact_name": "alice",
        "owner_user_id": "bob",
        "approved": True,
        "voice_rights": True,
        "status": ConsentStatus.ACTIVE,
    }
    db = _make_app_db(consent_docs=[consent_doc])
    app.state.db = db

    mock_profile = {"name": "alice", "biography": "", "personality_profile": "",
                    "common_phrases": "", "voice_id": "v1"}

    with patch(
        "services.api.main.load_contact_profile", return_value=mock_profile
    ), patch(
        "services.api.main.generate_participant_token", return_value="jwt"
    ), patch.dict(
        "os.environ",
        {"LIVEKIT_URL": "wss://lk.io", "LIVEKIT_API_KEY": "k", "LIVEKIT_API_SECRET": "s"},
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r1 = await client.post(
                "/voice/session/start",
                json={"contact_name": "alice", "user_name": "bob"},
            )
            r2 = await client.post(
                "/voice/session/start",
                json={"contact_name": "alice", "user_name": "bob"},
            )

    assert r1.status_code == 200
    assert r2.status_code == 200
    d1, d2 = r1.json(), r2.json()
    assert d1["session_id"] == d2["session_id"]
    assert d1["room_name"] == d2["room_name"]
    assert d2["is_new"] is False


@pytest.mark.asyncio
async def test_end_voice_session_not_found() -> None:
    """Returns 404 when the session_id does not exist."""
    from httpx import ASGITransport, AsyncClient
    from services.api.main import app

    db = _make_app_db()
    app.state.db = db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/voice/session/end",
            json={"session_id": "00000000-0000-0000-0000-000000000000"},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_voice_session_status_not_found() -> None:
    """Returns 404 when the session_id does not exist."""
    from httpx import ASGITransport, AsyncClient
    from services.api.main import app

    db = _make_app_db()
    app.state.db = db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/voice/session/00000000-0000-0000-0000-000000000000/status"
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_voice_session_end_and_status_flow() -> None:
    """
    Full end-to-end: create session via DB layer, check status via API,
    end via API, verify status reflects ENDED state.
    """
    from httpx import ASGITransport, AsyncClient
    from services.api.main import app

    db = _make_app_db()
    app.state.db = db

    # Seed a session via the session module
    session = await create_or_resume_session(db, "alice", "bob")
    session_id = session["session_id"]

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Check status — should be active
        r_status = await client.get(f"/voice/session/{session_id}/status")
        assert r_status.status_code == 200
        assert r_status.json()["state"] == "active"

        # End the session
        r_end = await client.post(
            "/voice/session/end",
            json={"session_id": session_id},
        )
        assert r_end.status_code == 204

        # Check status again — should be ended
        r_status2 = await client.get(f"/voice/session/{session_id}/status")
        assert r_status2.status_code == 200
        assert r_status2.json()["state"] == "ended"
