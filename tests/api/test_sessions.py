"""Tests for MongoDB-backed session management."""
import pytest

from services.api.sessions import append_message, create_session, get_session


class FakeCollection:
    """In-memory collection stub for session tests."""

    def __init__(self):
        self._docs: list[dict] = []

    async def create_index(self, *args, **kwargs):
        pass

    async def insert_one(self, doc: dict):
        self._docs.append(doc.copy())

    async def find_one(self, query: dict, projection: dict | None = None):
        session_id = query.get("session_id")
        for doc in self._docs:
            if doc.get("session_id") == session_id:
                result = doc.copy()
                if projection and projection.get("_id") == 0:
                    result.pop("_id", None)
                return result
        return None

    async def update_one(self, query: dict, update: dict):
        session_id = query.get("session_id")
        for doc in self._docs:
            if doc.get("session_id") == session_id:
                if "$push" in update:
                    for field, value in update["$push"].items():
                        doc.setdefault(field, []).append(value)
                if "$set" in update:
                    for field, value in update["$set"].items():
                        doc[field] = value
                break


class FakeDB:
    def __init__(self):
        self.sessions = FakeCollection()


@pytest.mark.asyncio
async def test_session_persists():
    db = FakeDB()
    session_id = await create_session(db, "mom", "Gagan")
    session = await get_session(db, session_id)
    assert session["contact_name"] == "mom"
    assert session["user_name"] == "Gagan"
    assert session["history"] == []


@pytest.mark.asyncio
async def test_append_message():
    db = FakeDB()
    session_id = await create_session(db, "dad", "Gagan")
    await append_message(db, session_id, "user", "hello")
    session = await get_session(db, session_id)
    assert len(session["history"]) == 1
    assert session["history"][0] == {"role": "user", "content": "hello"}


@pytest.mark.asyncio
async def test_get_session_not_found():
    db = FakeDB()
    result = await get_session(db, "nonexistent-id")
    assert result is None


@pytest.mark.asyncio
async def test_session_stores_voice_id():
    db = FakeDB()
    session_id = await create_session(db, "grandma", "Gagan", voice_id="voice-123")
    session = await get_session(db, session_id)
    assert session["voice_id"] == "voice-123"
