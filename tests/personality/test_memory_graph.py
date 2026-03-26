"""Tests for MemoryGraphBuilder and helpers (af-1ob).

Coverage:
- build_from_source: derives biography, profile, common_phrases, episodic_memories
- Idempotency: same source_hash returns cached graph without re-deriving
- MongoDB upserts for source messages and derived graph
- Pinecone upsert for biography embedding
- get_memory_graph: retrieves stored graph
- get_memory_graph: returns None for unknown contact
- regenerate: force-rebuilds from stored source data
- regenerate: returns None when no source data exists
- extract_common_phrases / extract_episodic_memories helpers
"""
import pytest
from unittest.mock import MagicMock

from services.personality.extractor import PersonalityProfile
from services.personality.memory_graph import (
    MemoryGraph,
    MemoryGraphBuilder,
    _compute_source_hash,
    extract_common_phrases,
    extract_episodic_memories,
)


# ─── Fixtures ─────────────────────────────────────────────────────────────────

SAMPLE_MESSAGES = [
    {"sender": "mom", "text": "beta khana khaya?", "timestamp": "2024-01-01T10:00"},
    {"sender": "Gagan", "text": "haan mom", "timestamp": "2024-01-01T10:01"},
    {"sender": "mom", "text": "okay beta bye", "timestamp": "2024-01-01T10:02"},
]

SAMPLE_PROFILE = PersonalityProfile(
    contact_name="mom",
    user_name="Gagan",
    linguistic_patterns={
        "vocabulary": ["beta", "arrey", "khana"],
        "sentence_structure": "short, warm, question-heavy",
        "emoji_usage": ["😊"],
        "slang_nicknames": ["beta"],
        "language_switches": ["English", "Hindi/Punjabi"],
        "greeting_farewell": ["okay beta bye", "love you"],
    },
    emotional_patterns={
        "topics": ["food", "health"],
        "worries": ["health"],
        "pride": ["user's achievements"],
        "humor_style": "dry",
        "response_style": "listener",
    },
    relationship_patterns={
        "names_for_user": ["beta"],
        "running_jokes": ["dal chawal memories"],
        "shared_memories": ["childhood food memories"],
        "recurring_conversations": ["eating properly"],
    },
)

SAMPLE_BIOGRAPHY = (
    "Mom is the emotional anchor of Gagan's life. "
    "She always asks 'khana khaya?' first thing."
)


@pytest.fixture
def mock_extractor():
    ext = MagicMock()
    ext.extract.return_value = SAMPLE_PROFILE
    return ext


@pytest.fixture
def mock_biographer():
    bio = MagicMock()
    bio.generate_biography.return_value = SAMPLE_BIOGRAPHY
    return bio


@pytest.fixture
def mock_source_col():
    col = MagicMock()
    col.find_one.return_value = None
    return col


@pytest.fixture
def mock_graph_col():
    col = MagicMock()
    col.find_one.return_value = None
    return col


@pytest.fixture
def mock_pinecone():
    return MagicMock()


@pytest.fixture
def builder(mock_source_col, mock_graph_col, mock_pinecone, mock_extractor, mock_biographer):
    return MemoryGraphBuilder(
        source_collection=mock_source_col,
        graph_collection=mock_graph_col,
        pinecone_index=mock_pinecone,
        extractor=mock_extractor,
        biographer=mock_biographer,
    )


# ─── Source hash ──────────────────────────────────────────────────────────────


def test_source_hash_is_deterministic():
    """Same messages always produce the same hash."""
    h1 = _compute_source_hash(SAMPLE_MESSAGES)
    h2 = _compute_source_hash(SAMPLE_MESSAGES)
    assert h1 == h2


def test_source_hash_differs_for_different_messages():
    """Different messages produce different hashes."""
    other = [{"sender": "dad", "text": "hello", "timestamp": "2024-01-02"}]
    assert _compute_source_hash(SAMPLE_MESSAGES) != _compute_source_hash(other)


# ─── extract_common_phrases ───────────────────────────────────────────────────


def test_extract_common_phrases_combines_vocabulary_and_farewells():
    phrases = extract_common_phrases(SAMPLE_PROFILE)
    assert "beta" in phrases
    assert "okay beta bye" in phrases
    assert "love you" in phrases


def test_extract_common_phrases_deduplicates():
    """'beta' appears in both vocabulary and slang_nicknames — should appear once."""
    phrases = extract_common_phrases(SAMPLE_PROFILE)
    assert phrases.count("beta") == 1


def test_extract_common_phrases_empty_profile():
    empty = PersonalityProfile(contact_name="x", user_name="y")
    assert extract_common_phrases(empty) == []


# ─── extract_episodic_memories ────────────────────────────────────────────────


def test_extract_episodic_memories_combines_shared_and_jokes():
    memories = extract_episodic_memories(SAMPLE_PROFILE)
    assert "childhood food memories" in memories
    assert "dal chawal memories" in memories


def test_extract_episodic_memories_deduplicates():
    profile = PersonalityProfile(
        contact_name="x",
        user_name="y",
        relationship_patterns={
            "shared_memories": ["memory A", "memory B"],
            "running_jokes": ["memory A"],  # duplicate
        },
    )
    memories = extract_episodic_memories(profile)
    assert memories.count("memory A") == 1


def test_extract_episodic_memories_empty_profile():
    empty = PersonalityProfile(contact_name="x", user_name="y")
    assert extract_episodic_memories(empty) == []


# ─── build_from_source ────────────────────────────────────────────────────────


def test_build_from_source_returns_memory_graph(builder):
    graph = builder.build_from_source(
        SAMPLE_MESSAGES, "contact_mom_001", "mom", "Gagan"
    )
    assert isinstance(graph, MemoryGraph)
    assert graph.contact_id == "contact_mom_001"
    assert graph.contact_name == "mom"
    assert graph.user_name == "Gagan"


def test_build_from_source_biography_is_derived(builder, mock_biographer):
    graph = builder.build_from_source(
        SAMPLE_MESSAGES, "contact_mom_001", "mom", "Gagan"
    )
    assert graph.biography == SAMPLE_BIOGRAPHY
    mock_biographer.generate_biography.assert_called_once_with(SAMPLE_PROFILE)


def test_build_from_source_extracts_common_phrases(builder):
    graph = builder.build_from_source(
        SAMPLE_MESSAGES, "contact_mom_001", "mom", "Gagan"
    )
    assert isinstance(graph.common_phrases, list)
    assert len(graph.common_phrases) > 0
    assert "beta" in graph.common_phrases


def test_build_from_source_extracts_episodic_memories(builder):
    graph = builder.build_from_source(
        SAMPLE_MESSAGES, "contact_mom_001", "mom", "Gagan"
    )
    assert isinstance(graph.episodic_memories, list)
    assert len(graph.episodic_memories) > 0
    assert "childhood food memories" in graph.episodic_memories


def test_build_from_source_includes_personality_profile(builder):
    graph = builder.build_from_source(
        SAMPLE_MESSAGES, "contact_mom_001", "mom", "Gagan"
    )
    assert isinstance(graph.personality_profile, dict)
    assert "linguistic_patterns" in graph.personality_profile


def test_build_from_source_stores_source_messages(builder, mock_source_col):
    builder.build_from_source(SAMPLE_MESSAGES, "contact_mom_001", "mom", "Gagan")
    mock_source_col.update_one.assert_called_once()
    filter_arg = mock_source_col.update_one.call_args[0][0]
    assert filter_arg == {"contact_id": "contact_mom_001"}


def test_build_from_source_stores_derived_graph(builder, mock_graph_col):
    builder.build_from_source(SAMPLE_MESSAGES, "contact_mom_001", "mom", "Gagan")
    mock_graph_col.update_one.assert_called_once()
    filter_arg = mock_graph_col.update_one.call_args[0][0]
    assert filter_arg == {"contact_id": "contact_mom_001"}


def test_build_from_source_upserts_to_pinecone(builder, mock_pinecone):
    builder.build_from_source(SAMPLE_MESSAGES, "contact_mom_001", "mom", "Gagan")
    mock_pinecone.upsert.assert_called_once()
    vectors = mock_pinecone.upsert.call_args[1]["vectors"]
    assert vectors[0]["id"] == "contact_mom_001"


def test_build_from_source_records_source_hash(builder):
    graph = builder.build_from_source(
        SAMPLE_MESSAGES, "contact_mom_001", "mom", "Gagan"
    )
    expected_hash = _compute_source_hash(SAMPLE_MESSAGES)
    assert graph.source_hash == expected_hash


# ─── Idempotency ──────────────────────────────────────────────────────────────


def test_build_from_source_is_idempotent_when_hash_unchanged(
    mock_source_col, mock_graph_col, mock_pinecone, mock_extractor, mock_biographer
):
    """Second call with same messages returns cached graph; extractor not called again."""
    h = _compute_source_hash(SAMPLE_MESSAGES)
    cached_doc = {
        "contact_id": "contact_mom_001",
        "contact_name": "mom",
        "user_name": "Gagan",
        "biography": SAMPLE_BIOGRAPHY,
        "personality_profile": SAMPLE_PROFILE.to_dict(),
        "common_phrases": ["beta"],
        "episodic_memories": ["childhood food memories"],
        "source_hash": h,
        "updated_at": "2024-01-01T00:00:00+00:00",
    }
    mock_graph_col.find_one.return_value = cached_doc

    builder = MemoryGraphBuilder(
        source_collection=mock_source_col,
        graph_collection=mock_graph_col,
        pinecone_index=mock_pinecone,
        extractor=mock_extractor,
        biographer=mock_biographer,
    )
    graph = builder.build_from_source(
        SAMPLE_MESSAGES, "contact_mom_001", "mom", "Gagan"
    )

    # Extractor and biographer must NOT be called when cache is fresh
    mock_extractor.extract.assert_not_called()
    mock_biographer.generate_biography.assert_not_called()
    assert graph.biography == SAMPLE_BIOGRAPHY


# ─── get_memory_graph ─────────────────────────────────────────────────────────


def test_get_memory_graph_returns_stored_graph(mock_source_col, mock_graph_col):
    stored = {
        "contact_id": "contact_mom_001",
        "contact_name": "mom",
        "user_name": "Gagan",
        "biography": SAMPLE_BIOGRAPHY,
        "personality_profile": SAMPLE_PROFILE.to_dict(),
        "common_phrases": ["beta", "okay beta bye"],
        "episodic_memories": ["childhood food memories"],
        "source_hash": "abc123",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }
    mock_graph_col.find_one.return_value = stored

    builder = MemoryGraphBuilder(
        source_collection=mock_source_col,
        graph_collection=mock_graph_col,
    )
    graph = builder.get_memory_graph("contact_mom_001")

    assert graph is not None
    assert graph.biography == SAMPLE_BIOGRAPHY
    assert graph.common_phrases == ["beta", "okay beta bye"]
    assert graph.episodic_memories == ["childhood food memories"]
    mock_graph_col.find_one.assert_called_once_with({"contact_id": "contact_mom_001"})


def test_get_memory_graph_returns_none_when_not_found(mock_source_col, mock_graph_col):
    """Missing contact returns None, not an error."""
    mock_graph_col.find_one.return_value = None

    builder = MemoryGraphBuilder(
        source_collection=mock_source_col,
        graph_collection=mock_graph_col,
    )
    graph = builder.get_memory_graph("unknown_contact")

    assert graph is None


# ─── regenerate ───────────────────────────────────────────────────────────────


def test_regenerate_rebuilds_from_stored_source(
    mock_source_col, mock_graph_col, mock_pinecone, mock_extractor, mock_biographer
):
    """regenerate re-derives artifacts from stored source messages."""
    mock_source_col.find_one.return_value = {
        "contact_id": "contact_mom_001",
        "contact_name": "mom",
        "user_name": "Gagan",
        "messages": SAMPLE_MESSAGES,
        "source_hash": "old_hash",
    }
    # After the $unset the graph has no source_hash → triggers re-derivation
    mock_graph_col.find_one.return_value = None

    builder = MemoryGraphBuilder(
        source_collection=mock_source_col,
        graph_collection=mock_graph_col,
        pinecone_index=mock_pinecone,
        extractor=mock_extractor,
        biographer=mock_biographer,
    )
    graph = builder.regenerate("contact_mom_001")

    assert graph is not None
    assert graph.biography == SAMPLE_BIOGRAPHY
    mock_extractor.extract.assert_called_once()
    mock_biographer.generate_biography.assert_called_once()


def test_regenerate_clears_source_hash_before_rebuilding(
    mock_source_col, mock_graph_col, mock_extractor, mock_biographer
):
    """regenerate must $unset source_hash so build_from_source re-derives."""
    mock_source_col.find_one.return_value = {
        "contact_id": "contact_mom_001",
        "contact_name": "mom",
        "user_name": "Gagan",
        "messages": SAMPLE_MESSAGES,
        "source_hash": "stale_hash",
    }
    mock_graph_col.find_one.return_value = None

    builder = MemoryGraphBuilder(
        source_collection=mock_source_col,
        graph_collection=mock_graph_col,
        extractor=mock_extractor,
        biographer=mock_biographer,
    )
    builder.regenerate("contact_mom_001")

    unset_call = mock_graph_col.update_one.call_args_list[0]
    assert "$unset" in unset_call[0][1]
    assert "source_hash" in unset_call[0][1]["$unset"]


def test_regenerate_returns_none_when_no_source_exists(mock_source_col, mock_graph_col):
    """regenerate returns None when there are no stored source messages."""
    mock_source_col.find_one.return_value = None

    builder = MemoryGraphBuilder(
        source_collection=mock_source_col,
        graph_collection=mock_graph_col,
    )
    result = builder.regenerate("unknown_contact")

    assert result is None


# ─── No Pinecone ──────────────────────────────────────────────────────────────


def test_build_from_source_without_pinecone_does_not_error(
    mock_source_col, mock_graph_col, mock_extractor, mock_biographer
):
    """build_from_source works when no Pinecone index is configured."""
    builder = MemoryGraphBuilder(
        source_collection=mock_source_col,
        graph_collection=mock_graph_col,
        pinecone_index=None,
        extractor=mock_extractor,
        biographer=mock_biographer,
    )
    graph = builder.build_from_source(
        SAMPLE_MESSAGES, "contact_mom_001", "mom", "Gagan"
    )
    assert graph.biography == SAMPLE_BIOGRAPHY
