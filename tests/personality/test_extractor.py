"""Tests for PersonalityExtractor - TDD red phase."""
import json
import pytest
from unittest.mock import MagicMock, patch

from services.personality.extractor import PersonalityExtractor, PersonalityProfile


SAMPLE_MESSAGES = [
    {"sender": "mom", "text": "beta khana khaya?", "timestamp": "2024-01-01T09:00:00"},
    {"sender": "user", "text": "haan mom, kha liya", "timestamp": "2024-01-01T09:01:00"},
    {"sender": "mom", "text": "okay beta, take care drink water don't work too late 😊", "timestamp": "2024-01-01T09:02:00"},
    {"sender": "mom", "text": "remember when you used to love my dal chawal?", "timestamp": "2024-01-02T10:00:00"},
    {"sender": "user", "text": "haha yes mom, still do!", "timestamp": "2024-01-02T10:01:00"},
    {"sender": "mom", "text": "arrey, don't work so hard. I know someone who got sick from stress lol", "timestamp": "2024-01-03T08:00:00"},
    {"sender": "mom", "text": "beta are you sleeping properly?", "timestamp": "2024-01-04T22:00:00"},
    {"sender": "mom", "text": "okay beta bye, love you 💕", "timestamp": "2024-01-04T22:30:00"},
]

MOCK_CLAUDE_RESPONSE = {
    "linguistic_patterns": {
        "vocabulary": ["beta", "arrey", "khana", "okay", "lol"],
        "sentence_structure": "short, warm, question-heavy",
        "emoji_usage": ["😊", "💕"],
        "slang_nicknames": ["beta"],
        "language_switches": ["English", "Hindi/Punjabi"],
        "greeting_farewell": ["okay beta bye", "love you"]
    },
    "emotional_patterns": {
        "topics": ["food", "health", "sleep", "work"],
        "worries": ["health", "overwork", "sleep deprivation"],
        "pride": ["user's achievements"],
        "humor_style": "dry, indirect - tells stories about 'someone she knows'",
        "response_style": "listener and worrier, expresses care as concern"
    },
    "relationship_patterns": {
        "names_for_user": ["beta"],
        "running_jokes": ["dal chawal memories"],
        "shared_memories": ["childhood food memories"],
        "recurring_conversations": ["eating properly", "sleeping enough", "not overworking"]
    }
}


@pytest.fixture
def mock_anthropic_client():
    """Mock Anthropic client that returns a structured personality analysis."""
    client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(MOCK_CLAUDE_RESPONSE))]
    client.messages.create.return_value = response
    return client


def test_extractor_returns_personality_profile(mock_anthropic_client):
    """Extractor returns a PersonalityProfile dataclass from message history."""
    extractor = PersonalityExtractor(client=mock_anthropic_client)
    profile = extractor.extract(messages=SAMPLE_MESSAGES, contact_name="mom", user_name="Gagan")
    assert isinstance(profile, PersonalityProfile)


def test_extractor_captures_linguistic_patterns(mock_anthropic_client):
    """Extractor captures vocabulary, nicknames, and farewell patterns."""
    extractor = PersonalityExtractor(client=mock_anthropic_client)
    profile = extractor.extract(messages=SAMPLE_MESSAGES, contact_name="mom", user_name="Gagan")

    assert "beta" in profile.linguistic_patterns["vocabulary"]
    assert "beta" in profile.linguistic_patterns["slang_nicknames"]
    assert len(profile.linguistic_patterns["greeting_farewell"]) > 0


def test_extractor_captures_emotional_patterns(mock_anthropic_client):
    """Extractor captures humor style, worries, and response style."""
    extractor = PersonalityExtractor(client=mock_anthropic_client)
    profile = extractor.extract(messages=SAMPLE_MESSAGES, contact_name="mom", user_name="Gagan")

    assert "health" in profile.emotional_patterns["worries"]
    assert profile.emotional_patterns["humor_style"] != ""
    assert profile.emotional_patterns["response_style"] != ""


def test_extractor_captures_relationship_patterns(mock_anthropic_client):
    """Extractor captures what contact called user and shared memories."""
    extractor = PersonalityExtractor(client=mock_anthropic_client)
    profile = extractor.extract(messages=SAMPLE_MESSAGES, contact_name="mom", user_name="Gagan")

    assert "beta" in profile.relationship_patterns["names_for_user"]
    assert len(profile.relationship_patterns["shared_memories"]) > 0


def test_extractor_calls_claude_with_messages(mock_anthropic_client):
    """Extractor passes message history to Claude for analysis."""
    extractor = PersonalityExtractor(client=mock_anthropic_client)
    extractor.extract(messages=SAMPLE_MESSAGES, contact_name="mom", user_name="Gagan")

    mock_anthropic_client.messages.create.assert_called_once()
    call_kwargs = mock_anthropic_client.messages.create.call_args
    # Messages should be in the prompt
    prompt_text = str(call_kwargs)
    assert "mom" in prompt_text


def test_extractor_handles_empty_messages(mock_anthropic_client):
    """Extractor handles empty message list gracefully."""
    empty_response = MagicMock()
    empty_response.content = [MagicMock(text=json.dumps({
        "linguistic_patterns": {"vocabulary": [], "sentence_structure": "", "emoji_usage": [],
                                 "slang_nicknames": [], "language_switches": [], "greeting_farewell": []},
        "emotional_patterns": {"topics": [], "worries": [], "pride": [],
                                "humor_style": "", "response_style": ""},
        "relationship_patterns": {"names_for_user": [], "running_jokes": [],
                                   "shared_memories": [], "recurring_conversations": []}
    }))]
    mock_anthropic_client.messages.create.return_value = empty_response

    extractor = PersonalityExtractor(client=mock_anthropic_client)
    profile = extractor.extract(messages=[], contact_name="unknown", user_name="Gagan")
    assert isinstance(profile, PersonalityProfile)


def test_extractor_profile_serializes_to_dict(mock_anthropic_client):
    """PersonalityProfile can be serialized to dict for storage."""
    extractor = PersonalityExtractor(client=mock_anthropic_client)
    profile = extractor.extract(messages=SAMPLE_MESSAGES, contact_name="mom", user_name="Gagan")

    data = profile.to_dict()
    assert isinstance(data, dict)
    assert "linguistic_patterns" in data
    assert "emotional_patterns" in data
    assert "relationship_patterns" in data
    assert data["contact_name"] == "mom"
