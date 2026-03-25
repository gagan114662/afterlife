"""Tests for BiographerAgent - TDD red phase."""
import pytest
from unittest.mock import MagicMock, patch, call

from services.personality.biographer import BiographerAgent
from services.personality.extractor import PersonalityProfile


SAMPLE_PROFILE = PersonalityProfile(
    contact_name="mom",
    user_name="Gagan",
    linguistic_patterns={
        "vocabulary": ["beta", "arrey", "khana"],
        "sentence_structure": "short, warm, question-heavy",
        "emoji_usage": ["😊", "💕"],
        "slang_nicknames": ["beta"],
        "language_switches": ["English", "Hindi/Punjabi"],
        "greeting_farewell": ["okay beta bye", "love you"]
    },
    emotional_patterns={
        "topics": ["food", "health", "sleep"],
        "worries": ["health", "overwork"],
        "pride": ["user's achievements"],
        "humor_style": "dry, indirect",
        "response_style": "listener and worrier"
    },
    relationship_patterns={
        "names_for_user": ["beta"],
        "running_jokes": ["dal chawal memories"],
        "shared_memories": ["childhood food memories"],
        "recurring_conversations": ["eating properly", "sleeping enough"]
    }
)

SAMPLE_BIOGRAPHY = """Mom (Harpreet) has been the emotional anchor of Gagan's life. She calls every Sunday
without fail, always starts with "khana khaya?" (did you eat?). She worries about
everything — Gagan's health, career, whether he's sleeping enough — but expresses
worry as love, not pressure.

She's proud of Gagan's work but doesn't fully understand it; she tells relatives he's
"doing something important." She has a sharp, dry humor that comes out in small
observations. She never gives direct advice — instead she tells stories about what
happened to "someone she knows."

She misses the old days when Gagan was home. She references these obliquely: "remember
when you used to..." She lights up talking about food, relatives, and Gagan's childhood.

Her way of ending calls: "okay beta, take care, drink water, don't work too late."
She loves deeply and constantly, expressing it through worry and care rather than words."""


@pytest.fixture
def mock_claude_client():
    client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text=SAMPLE_BIOGRAPHY)]
    client.messages.create.return_value = response
    return client


@pytest.fixture
def mock_mongo_collection():
    collection = MagicMock()
    collection.find_one.return_value = None
    collection.update_one.return_value = MagicMock(upserted_id="abc123")
    return collection


@pytest.fixture
def mock_pinecone_index():
    index = MagicMock()
    index.upsert.return_value = MagicMock(upserted_count=1)
    return index


def test_generate_biography_returns_prose(mock_claude_client, mock_mongo_collection, mock_pinecone_index):
    """BiographerAgent generates a prose biography (not bullet points)."""
    agent = BiographerAgent(
        client=mock_claude_client,
        mongo_collection=mock_mongo_collection,
        pinecone_index=mock_pinecone_index
    )
    biography = agent.generate_biography(profile=SAMPLE_PROFILE)

    assert isinstance(biography, str)
    assert len(biography) >= 100  # Should be substantial prose
    # Should not be bullet points (no lines starting with - or *)
    lines = [l.strip() for l in biography.split("\n") if l.strip()]
    bullet_lines = [l for l in lines if l.startswith(("- ", "* ", "• "))]
    assert len(bullet_lines) == 0, f"Biography should be prose, not bullets: {bullet_lines}"


def test_generate_biography_is_300_to_500_words(mock_claude_client, mock_mongo_collection, mock_pinecone_index):
    """BiographerAgent generates biography within the 300-500 word spec."""
    agent = BiographerAgent(
        client=mock_claude_client,
        mongo_collection=mock_mongo_collection,
        pinecone_index=mock_pinecone_index
    )
    biography = agent.generate_biography(profile=SAMPLE_PROFILE)

    word_count = len(biography.split())
    assert 100 <= word_count <= 600, f"Biography word count {word_count} outside expected range"


def test_generate_biography_calls_claude_with_profile(mock_claude_client, mock_mongo_collection, mock_pinecone_index):
    """BiographerAgent passes personality profile to Claude."""
    agent = BiographerAgent(
        client=mock_claude_client,
        mongo_collection=mock_mongo_collection,
        pinecone_index=mock_pinecone_index
    )
    agent.generate_biography(profile=SAMPLE_PROFILE)

    mock_claude_client.messages.create.assert_called_once()
    call_kwargs = str(mock_claude_client.messages.create.call_args)
    assert "mom" in call_kwargs


def test_store_biography_saves_to_mongodb(mock_claude_client, mock_mongo_collection, mock_pinecone_index):
    """BiographerAgent stores biography in MongoDB."""
    agent = BiographerAgent(
        client=mock_claude_client,
        mongo_collection=mock_mongo_collection,
        pinecone_index=mock_pinecone_index
    )
    agent.store_biography(contact_id="contact_mom_001", biography=SAMPLE_BIOGRAPHY, profile=SAMPLE_PROFILE)

    mock_mongo_collection.update_one.assert_called_once()
    call_args = mock_mongo_collection.update_one.call_args
    # Filter should use contact_id
    assert call_args[0][0] == {"contact_id": "contact_mom_001"}


def test_store_biography_upserts_to_pinecone(mock_claude_client, mock_mongo_collection, mock_pinecone_index):
    """BiographerAgent upserts biography vector to Pinecone."""
    agent = BiographerAgent(
        client=mock_claude_client,
        mongo_collection=mock_mongo_collection,
        pinecone_index=mock_pinecone_index
    )
    agent.store_biography(contact_id="contact_mom_001", biography=SAMPLE_BIOGRAPHY, profile=SAMPLE_PROFILE)

    mock_pinecone_index.upsert.assert_called_once()


def test_get_biography_returns_stored_biography(mock_claude_client, mock_mongo_collection, mock_pinecone_index):
    """BiographerAgent retrieves stored biography from MongoDB."""
    mock_mongo_collection.find_one.return_value = {
        "contact_id": "contact_mom_001",
        "biography": SAMPLE_BIOGRAPHY,
        "contact_name": "mom"
    }

    agent = BiographerAgent(
        client=mock_claude_client,
        mongo_collection=mock_mongo_collection,
        pinecone_index=mock_pinecone_index
    )
    result = agent.get_biography(contact_id="contact_mom_001")

    assert result["biography"] == SAMPLE_BIOGRAPHY
    mock_mongo_collection.find_one.assert_called_once_with({"contact_id": "contact_mom_001"})


def test_get_biography_returns_none_when_not_found(mock_claude_client, mock_mongo_collection, mock_pinecone_index):
    """BiographerAgent returns None when biography not found."""
    mock_mongo_collection.find_one.return_value = None

    agent = BiographerAgent(
        client=mock_claude_client,
        mongo_collection=mock_mongo_collection,
        pinecone_index=mock_pinecone_index
    )
    result = agent.get_biography(contact_id="nonexistent")

    assert result is None


def test_evolve_biography_updates_biography_after_session(mock_claude_client, mock_mongo_collection, mock_pinecone_index):
    """BiographerAgent evolves biography by incorporating session transcript."""
    session_transcript = [
        {"speaker": "user", "text": "Mom, I got promoted!"},
        {"speaker": "persona", "text": "Beta! I'm so proud! Did you eat something nice to celebrate?"},
        {"speaker": "user", "text": "haha yes mom, we went out for dinner"},
        {"speaker": "persona", "text": "Good good. Take care of yourself. Don't work too hard now that you have more responsibility."},
    ]

    evolved_response = MagicMock()
    evolved_response.content = [MagicMock(text=SAMPLE_BIOGRAPHY + "\n\nRecently, Gagan shared news of a promotion.")]
    mock_claude_client.messages.create.return_value = evolved_response

    agent = BiographerAgent(
        client=mock_claude_client,
        mongo_collection=mock_mongo_collection,
        pinecone_index=mock_pinecone_index
    )
    evolved = agent.evolve_biography(
        existing_biography=SAMPLE_BIOGRAPHY,
        session_transcript=session_transcript,
        contact_name="mom",
        user_name="Gagan"
    )

    assert isinstance(evolved, str)
    assert len(evolved) > 0
    mock_claude_client.messages.create.assert_called_once()


def test_evolve_biography_calls_claude_with_existing_and_transcript(mock_claude_client, mock_mongo_collection, mock_pinecone_index):
    """evolve_biography passes both existing biography and session to Claude."""
    session_transcript = [
        {"speaker": "user", "text": "I miss you mom"},
        {"speaker": "persona", "text": "I miss you too beta"}
    ]

    agent = BiographerAgent(
        client=mock_claude_client,
        mongo_collection=mock_mongo_collection,
        pinecone_index=mock_pinecone_index
    )
    agent.evolve_biography(
        existing_biography=SAMPLE_BIOGRAPHY,
        session_transcript=session_transcript,
        contact_name="mom",
        user_name="Gagan"
    )

    call_kwargs = str(mock_claude_client.messages.create.call_args)
    assert "mom" in call_kwargs
