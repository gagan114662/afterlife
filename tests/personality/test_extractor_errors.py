"""Tests for PersonalityExtractor error handling."""
from unittest.mock import MagicMock


def test_extractor_handles_invalid_json():
    """Extractor must not crash when Claude returns malformed JSON."""
    from services.personality.extractor import PersonalityExtractor
    extractor = PersonalityExtractor.__new__(PersonalityExtractor)
    extractor._client = MagicMock()
    extractor._model = "claude-sonnet-4-6"
    extractor._client.messages.create.return_value.content = [
        MagicMock(text="this is not json {{{")
    ]
    # Should not raise — should return a fallback profile
    result = extractor.extract([{"sender": "user", "text": "hi", "timestamp": ""}], "mom", "Gagan")
    assert result is not None
    assert result.contact_name == "mom"


def test_extractor_returns_empty_patterns_on_parse_failure():
    """Extractor returns empty patterns when Claude response is malformed."""
    from services.personality.extractor import PersonalityExtractor
    extractor = PersonalityExtractor.__new__(PersonalityExtractor)
    extractor._client = MagicMock()
    extractor._model = "claude-sonnet-4-6"
    extractor._client.messages.create.return_value.content = [
        MagicMock(text="not valid json at all!!!")
    ]
    result = extractor.extract([], "dad", "Gagan")
    assert result.contact_name == "dad"
    assert result.linguistic_patterns == {}
    assert result.emotional_patterns == {}
    assert result.relationship_patterns == {}


def test_extractor_handles_empty_content_list():
    """Extractor must not crash when Claude returns empty content list."""
    from services.personality.extractor import PersonalityExtractor
    extractor = PersonalityExtractor.__new__(PersonalityExtractor)
    extractor._client = MagicMock()
    extractor._model = "claude-sonnet-4-6"
    extractor._client.messages.create.return_value.content = []
    result = extractor.extract([], "grandma", "Gagan")
    assert result is not None
    assert result.contact_name == "grandma"
