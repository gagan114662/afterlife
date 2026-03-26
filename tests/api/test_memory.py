"""Tests for the memory module."""
from unittest.mock import MagicMock, patch


def test_retrieve_memories_uses_chroma(monkeypatch, tmp_path):
    """retrieve_relevant_memories should query Chroma, not Pinecone."""

    mock_collection = MagicMock()
    mock_collection.query.return_value = {
        "documents": [["Memory 1", "Memory 2"]],
        "distances": [[0.1, 0.2]],
    }

    # chromadb is lazy-imported inside _get_chroma_collection, so patch the
    # helper function directly rather than the module-level attribute.
    with patch("services.api.memory._get_chroma_collection") as mock_get_col, \
         patch("services.api.memory._get_embedding") as mock_embed:
        mock_get_col.return_value = mock_collection
        mock_embed.return_value = [0.1] * 384

        from services.api import memory
        result = memory.retrieve_relevant_memories("mom", "hello", top_k=2)
        assert "Memory 1" in result
        assert "Memory 2" in result


def test_load_contact_profile_not_found():
    """load_contact_profile should raise ValueError if contact is missing."""
    with patch("services.api.memory._get_contacts_collection") as mock_col_fn:
        mock_collection = MagicMock()
        mock_collection.find_one.return_value = None
        mock_col_fn.return_value = mock_collection

        from services.api.memory import load_contact_profile
        import pytest
        with pytest.raises(ValueError, match="not found"):
            load_contact_profile("unknown_contact")


def test_load_contact_profile_returns_fields():
    """load_contact_profile should return all expected keys."""
    with patch("services.api.memory._get_contacts_collection") as mock_col_fn:
        mock_collection = MagicMock()
        mock_collection.find_one.return_value = {
            "name": "mom",
            "biography": "A warm woman.",
            "personality_profile": "Nurturing.",
            "common_phrases": "Janu!",
            "voice_id": "voice-123",
        }
        mock_col_fn.return_value = mock_collection

        from services.api.memory import load_contact_profile
        result = load_contact_profile("mom")

        assert result["name"] == "mom"
        assert result["biography"] == "A warm woman."
        assert result["voice_id"] == "voice-123"
