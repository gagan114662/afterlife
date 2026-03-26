"""
Tests for the ElevenLabs voice clone pipeline (elevenlabs_cloner.py).

Covers:
- Clone success: enough quality audio → ElevenLabs returns voice_id → persisted
- Clone failure: ElevenLabs API error → FAILED status with fallback_reason
- Fallback: voice_rights not active
- Fallback: insufficient audio duration
- Fallback: all audio conversion fails
- get_voice_clone_record returns persisted data
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─── Module Loader ────────────────────────────────────────────────────────────

_cloner_path = (
    Path(__file__).parent.parent
    / "services"
    / "voice-cloner"
    / "elevenlabs_cloner.py"
)


def _load_fresh():
    """
    Load elevenlabs_cloner.py via importlib.

    The module uses a try/except import:
      try: from .audio_utils import ...
      except ImportError: from audio_utils import ...

    When loaded standalone the relative import fails; the fallback resolves
    'audio_utils' from sys.modules.  We inject a mock there first.
    """
    # Inject a mock audio_utils so the fallback bare import succeeds.
    mock_audio_utils = MagicMock()
    sys.modules["audio_utils"] = mock_audio_utils

    spec = importlib.util.spec_from_file_location(
        "elevenlabs_cloner", str(_cloner_path)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ─── In-Memory DB Stub ───────────────────────────────────────────────────────


class FakeVoiceClonesCollection:
    """Minimal in-memory stub for voice_clones collection."""

    def __init__(self):
        self._docs: list[dict] = []

    async def create_index(self, *args, **kwargs):
        pass

    async def find_one(self, query: dict, projection: dict | None = None):
        for doc in self._docs:
            if all(doc.get(k) == v for k, v in query.items()):
                result = doc.copy()
                if projection and projection.get("_id") == 0:
                    result.pop("_id", None)
                return result
        return None

    async def update_one(self, query: dict, update: dict, upsert: bool = False):
        for doc in self._docs:
            if all(doc.get(k) == v for k, v in query.items()):
                if "$set" in update:
                    doc.update(update["$set"])
                return
        if upsert:
            new_doc = dict(update.get("$set", {}))
            self._docs.append(new_doc)


class FakeDB:
    def __init__(self):
        self.voice_clones = FakeVoiceClonesCollection()


# ─── Helpers ─────────────────────────────────────────────────────────────────

FAKE_API_KEY = "test-xi-api-key"
FAKE_VOICE_ID = "abc123voice"


def _make_voice_notes(n: int = 3) -> list[str]:
    return [f"/fake/audio_{i}.ogg" for i in range(n)]


# ─── Tests ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clone_success_registers_voice_id():
    """Enough quality audio → ElevenLabs called → CLONED result persisted."""
    mod = _load_fresh()

    with (
        patch.object(
            mod,
            "filter_quality_voice_notes",
            return_value=(["/fake/a.ogg", "/fake/b.ogg"], 60.0),
        ),
        patch.object(
            mod,
            "convert_to_wav",
            side_effect=["/tmp/a.wav", "/tmp/b.wav"],
        ),
        patch.object(
            mod,
            "_register_elevenlabs_clone",
            new=AsyncMock(return_value=FAKE_VOICE_ID),
        ),
    ):
        db = FakeDB()
        result = await mod.build_voice_clone(
            db=db,
            contact_name="mom",
            owner_user_id="gagan",
            voice_note_paths=_make_voice_notes(),
            api_key=FAKE_API_KEY,
            voice_rights_active=True,
        )

    assert result.status == mod.CloneStatus.CLONED
    assert result.voice_id == FAKE_VOICE_ID
    assert result.accepted_duration_seconds == 60.0
    assert result.fallback_reason is None

    # Verify MongoDB persistence
    record = await db.voice_clones.find_one(
        {"contact_name": "mom", "owner_user_id": "gagan"}, {"_id": 0}
    )
    assert record is not None
    assert record["status"] == mod.CloneStatus.CLONED
    assert record["voice_id"] == FAKE_VOICE_ID


@pytest.mark.asyncio
async def test_clone_api_http_error_returns_failed():
    """ElevenLabs returns HTTP error → FAILED status, fallback_reason set."""
    import httpx

    mod = _load_fresh()

    fake_response = MagicMock()
    fake_response.status_code = 422
    fake_response.text = "Unprocessable Entity"
    api_error = httpx.HTTPStatusError(
        "422", request=MagicMock(), response=fake_response
    )

    with (
        patch.object(
            mod,
            "filter_quality_voice_notes",
            return_value=(["/fake/a.ogg"], 45.0),
        ),
        patch.object(
            mod,
            "convert_to_wav",
            return_value="/tmp/a.wav",
        ),
        patch.object(
            mod,
            "_register_elevenlabs_clone",
            new=AsyncMock(side_effect=api_error),
        ),
    ):
        db = FakeDB()
        result = await mod.build_voice_clone(
            db=db,
            contact_name="dad",
            owner_user_id="gagan",
            voice_note_paths=_make_voice_notes(1),
            api_key=FAKE_API_KEY,
            voice_rights_active=True,
        )

    assert result.status == mod.CloneStatus.FAILED
    assert result.voice_id is None
    assert "422" in result.fallback_reason

    record = await db.voice_clones.find_one(
        {"contact_name": "dad", "owner_user_id": "gagan"}, {"_id": 0}
    )
    assert record["status"] == mod.CloneStatus.FAILED


@pytest.mark.asyncio
async def test_clone_api_request_error_returns_failed():
    """ElevenLabs network error → FAILED status."""
    import httpx

    mod = _load_fresh()

    with (
        patch.object(
            mod,
            "filter_quality_voice_notes",
            return_value=(["/fake/a.ogg"], 45.0),
        ),
        patch.object(
            mod,
            "convert_to_wav",
            return_value="/tmp/a.wav",
        ),
        patch.object(
            mod,
            "_register_elevenlabs_clone",
            new=AsyncMock(
                side_effect=httpx.RequestError("connection refused", request=MagicMock())
            ),
        ),
    ):
        db = FakeDB()
        result = await mod.build_voice_clone(
            db=db,
            contact_name="sister",
            owner_user_id="gagan",
            voice_note_paths=_make_voice_notes(1),
            api_key=FAKE_API_KEY,
            voice_rights_active=True,
        )

    assert result.status == mod.CloneStatus.FAILED
    assert "connection refused" in result.fallback_reason


@pytest.mark.asyncio
async def test_fallback_when_voice_rights_not_active():
    """voice_rights_active=False → immediate FALLBACK without touching audio."""
    mod = _load_fresh()

    with patch.object(mod, "filter_quality_voice_notes") as mock_filter:
        db = FakeDB()
        result = await mod.build_voice_clone(
            db=db,
            contact_name="uncle",
            owner_user_id="gagan",
            voice_note_paths=_make_voice_notes(),
            api_key=FAKE_API_KEY,
            voice_rights_active=False,
        )
        mock_filter.assert_not_called()

    assert result.status == mod.CloneStatus.FALLBACK
    assert "voice_rights" in result.fallback_reason

    record = await db.voice_clones.find_one(
        {"contact_name": "uncle", "owner_user_id": "gagan"}, {"_id": 0}
    )
    assert record["status"] == mod.CloneStatus.FALLBACK


@pytest.mark.asyncio
async def test_fallback_when_no_audio_passes_quality_filter():
    """No audio passes quality filter → FALLBACK."""
    mod = _load_fresh()

    with patch.object(
        mod, "filter_quality_voice_notes", return_value=([], 0.0)
    ):
        db = FakeDB()
        result = await mod.build_voice_clone(
            db=db,
            contact_name="neighbor",
            owner_user_id="gagan",
            voice_note_paths=_make_voice_notes(),
            api_key=FAKE_API_KEY,
            voice_rights_active=True,
        )

    assert result.status == mod.CloneStatus.FALLBACK
    assert "insufficient audio" in result.fallback_reason
    assert result.accepted_duration_seconds == 0.0


@pytest.mark.asyncio
async def test_fallback_when_audio_duration_below_minimum():
    """Quality audio passes filter but total duration < MIN_CLONE_SECONDS → FALLBACK."""
    mod = _load_fresh()

    # Only 10 seconds accepted — below 30s threshold
    with patch.object(
        mod, "filter_quality_voice_notes", return_value=(["/fake/a.ogg"], 10.0)
    ):
        db = FakeDB()
        result = await mod.build_voice_clone(
            db=db,
            contact_name="cousin",
            owner_user_id="gagan",
            voice_note_paths=_make_voice_notes(1),
            api_key=FAKE_API_KEY,
            voice_rights_active=True,
        )

    assert result.status == mod.CloneStatus.FALLBACK
    assert "insufficient audio" in result.fallback_reason
    assert result.accepted_duration_seconds == 10.0


@pytest.mark.asyncio
async def test_fallback_when_all_wav_conversion_fails():
    """All accepted audio fails WAV conversion → FALLBACK."""
    mod = _load_fresh()

    with (
        patch.object(
            mod,
            "filter_quality_voice_notes",
            return_value=(["/fake/a.ogg", "/fake/b.ogg"], 60.0),
        ),
        patch.object(mod, "convert_to_wav", return_value=None),  # all fail
    ):
        db = FakeDB()
        result = await mod.build_voice_clone(
            db=db,
            contact_name="friend",
            owner_user_id="gagan",
            voice_note_paths=_make_voice_notes(2),
            api_key=FAKE_API_KEY,
            voice_rights_active=True,
        )

    assert result.status == mod.CloneStatus.FALLBACK
    assert "conversion failed" in result.fallback_reason


@pytest.mark.asyncio
async def test_get_voice_clone_record_returns_persisted_data():
    """get_voice_clone_record returns what was persisted by build_voice_clone."""
    mod = _load_fresh()

    with (
        patch.object(
            mod,
            "filter_quality_voice_notes",
            return_value=(["/fake/a.ogg"], 50.0),
        ),
        patch.object(mod, "convert_to_wav", return_value="/tmp/a.wav"),
        patch.object(
            mod,
            "_register_elevenlabs_clone",
            new=AsyncMock(return_value="voice-xyz"),
        ),
    ):
        db = FakeDB()
        await mod.build_voice_clone(
            db=db,
            contact_name="grandma",
            owner_user_id="gagan",
            voice_note_paths=_make_voice_notes(1),
            api_key=FAKE_API_KEY,
            voice_rights_active=True,
        )

    record = await mod.get_voice_clone_record(db, "grandma", "gagan")
    assert record is not None
    assert record["voice_id"] == "voice-xyz"
    assert record["status"] == mod.CloneStatus.CLONED
    assert record["accepted_duration_seconds"] == 50.0


@pytest.mark.asyncio
async def test_get_voice_clone_record_returns_none_when_missing():
    """get_voice_clone_record returns None when no record exists."""
    mod = _load_fresh()
    db = FakeDB()
    record = await mod.get_voice_clone_record(db, "nobody", "gagan")
    assert record is None


@pytest.mark.asyncio
async def test_ensure_voice_clone_indexes_runs_without_error():
    """ensure_voice_clone_indexes completes without raising."""
    mod = _load_fresh()
    db = FakeDB()
    await mod.ensure_voice_clone_indexes(db)  # Should not raise
