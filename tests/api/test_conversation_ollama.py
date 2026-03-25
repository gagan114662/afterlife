"""Tests that conversation.py uses Ollama (not Anthropic) for LLM calls."""
from unittest.mock import patch, MagicMock


def test_reply_as_persona_uses_ollama(monkeypatch):
    """reply_as_persona should call ollama.chat, not anthropic."""
    mock_response = {"message": {"content": "Hello janu!"}}

    with patch("services.api.conversation.ollama") as mock_ollama, \
         patch("services.api.conversation.load_contact_profile") as mock_profile, \
         patch("services.api.conversation.retrieve_relevant_memories") as mock_mem:
        mock_ollama.chat.return_value = mock_response
        mock_profile.return_value = {
            "name": "mom",
            "biography": "Warm woman.",
            "personality_profile": "Nurturing.",
            "common_phrases": "Janu!",
            "voice_id": "",
        }
        mock_mem.return_value = ""

        from services.api.conversation import reply_as_persona
        result = reply_as_persona("mom", "Gagan", [], "hi")

        assert mock_ollama.chat.called
        assert result == "Hello janu!"


def test_text_to_speech_uses_kokoro(monkeypatch):
    """text_to_speech should call KPipeline from kokoro, not ElevenLabs."""
    with patch("services.api.conversation.KPipeline") as mock_pipeline_cls:
        mock_pipeline = MagicMock()
        # KPipeline returns an iterable of (grapheme, phoneme, audio_tensor)
        import numpy as np
        mock_pipeline.return_value = [(None, None, np.zeros(1000, dtype="float32"))]
        mock_pipeline_cls.return_value = mock_pipeline

        from services.api import conversation
        # Force re-init
        conversation._kokoro_pipeline = None

        result = conversation.text_to_speech("hello", "")
        assert result is not None
        assert isinstance(result, bytes)
