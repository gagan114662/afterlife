"""Memory graph builder: derives biography, profile, common phrases, and episodic
memories from normalized source messages.

Separation of concerns:
- ``source_messages`` MongoDB collection: raw messages per contact (source of truth)
- ``memory_graph`` MongoDB collection: derived artifacts (biography, profile, phrases,
  memories, source_hash)
- Pinecone: biography embeddings for semantic search

Regeneration is deterministic: given identical source messages the derived artifacts
are re-derived from the same inputs, and the ``source_hash`` lets callers skip
re-derivation when source data has not changed.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from services.personality.biographer import BiographerAgent
from services.personality.extractor import PersonalityExtractor, PersonalityProfile


class MemoryGraph(BaseModel):
    """Derived memory artifacts for one contact.

    All fields are derived from stored source messages; the ``source_hash`` records
    which version of the source produced these artifacts so stale-cache checks are O(1).
    """

    contact_id: str
    contact_name: str
    user_name: str
    biography: str = Field(default="")
    personality_profile: dict[str, Any] = Field(default_factory=dict)
    common_phrases: list[str] = Field(default_factory=list)
    episodic_memories: list[str] = Field(default_factory=list)
    source_hash: str = Field(default="")
    updated_at: str = Field(default="")


# ─── Derivation helpers ────────────────────────────────────────────────────────


def _compute_source_hash(messages: list[dict]) -> str:
    """Return a deterministic SHA-256 digest of the normalized source messages."""
    canonical = json.dumps(messages, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def extract_common_phrases(profile: PersonalityProfile) -> list[str]:
    """Derive a deduplicated list of common phrases from a PersonalityProfile.

    Combines vocabulary, greeting/farewell patterns, and slang/nicknames from
    the linguistic_patterns section of the profile.

    Args:
        profile: PersonalityProfile extracted from source messages

    Returns:
        Ordered, deduplicated list of phrase strings
    """
    lp = profile.linguistic_patterns
    phrases: list[str] = []
    if isinstance(lp, dict):
        phrases.extend(lp.get("vocabulary") or [])
        phrases.extend(lp.get("greeting_farewell") or [])
        phrases.extend(lp.get("slang_nicknames") or [])
    # Deduplicate while preserving insertion order
    seen: set[str] = set()
    result: list[str] = []
    for p in phrases:
        s = str(p)
        if s not in seen:
            seen.add(s)
            result.append(s)
    return result


def extract_episodic_memories(profile: PersonalityProfile) -> list[str]:
    """Derive episodic memories from a PersonalityProfile.

    Combines shared memories and running jokes from the relationship_patterns
    section of the profile.

    Args:
        profile: PersonalityProfile extracted from source messages

    Returns:
        Ordered, deduplicated list of memory strings
    """
    rp = profile.relationship_patterns
    memories: list[str] = []
    if isinstance(rp, dict):
        memories.extend(rp.get("shared_memories") or [])
        memories.extend(rp.get("running_jokes") or [])
    seen: set[str] = set()
    result: list[str] = []
    for m in memories:
        s = str(m)
        if s not in seen:
            seen.add(s)
            result.append(s)
    return result


# ─── Builder ──────────────────────────────────────────────────────────────────


class MemoryGraphBuilder:
    """Builds and retrieves the derived memory graph for a contact.

    Injected dependencies allow full mocking in tests without touching MongoDB or
    Pinecone:

    - ``source_collection``: MongoDB collection for raw source messages
    - ``graph_collection``:  MongoDB collection for derived artifacts
    - ``pinecone_index``:    Pinecone index for biography embeddings (optional)
    - ``extractor``:         PersonalityExtractor (defaults to a new instance)
    - ``biographer``:        BiographerAgent (defaults to a new instance)
    """

    def __init__(
        self,
        *,
        source_collection: Any,
        graph_collection: Any,
        pinecone_index: Any | None = None,
        extractor: PersonalityExtractor | None = None,
        biographer: BiographerAgent | None = None,
    ) -> None:
        self._source_col = source_collection
        self._graph_col = graph_collection
        self._pinecone = pinecone_index
        self._extractor = extractor if extractor is not None else PersonalityExtractor()
        self._biographer = (
            biographer
            if biographer is not None
            else BiographerAgent(
                mongo_collection=graph_collection,
                pinecone_index=pinecone_index,
            )
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    def build_from_source(
        self,
        messages: list[dict],
        contact_id: str,
        contact_name: str,
        user_name: str,
    ) -> MemoryGraph:
        """Build the full memory graph from normalized source messages.

        Idempotent: if the source hash has not changed the existing graph is
        returned immediately without re-deriving any artifacts.

        Args:
            messages:     Normalized messages, each with keys: sender, text, timestamp
            contact_id:   Unique identifier for the contact
            contact_name: Human-readable name of the contact
            user_name:    Name of the user

        Returns:
            MemoryGraph with biography, personality_profile, common_phrases, and
            episodic_memories derived from the supplied messages
        """
        h = _compute_source_hash(messages)

        # Fast path: return cached graph when source data is unchanged
        existing = self._graph_col.find_one({"contact_id": contact_id})
        if existing is not None and existing.get("source_hash") == h:
            return MemoryGraph(**{k: v for k, v in existing.items() if k != "_id"})

        # Persist raw source messages (source of truth)
        self._source_col.update_one(
            {"contact_id": contact_id},
            {
                "$set": {
                    "contact_id": contact_id,
                    "contact_name": contact_name,
                    "user_name": user_name,
                    "messages": messages,
                    "source_hash": h,
                }
            },
            upsert=True,
        )

        # Derive all artifacts from source
        profile = self._extractor.extract(messages, contact_name, user_name)
        biography = self._biographer.generate_biography(profile)
        common_phrases = extract_common_phrases(profile)
        episodic_memories = extract_episodic_memories(profile)

        now = datetime.now(timezone.utc).isoformat()
        graph = MemoryGraph(
            contact_id=contact_id,
            contact_name=contact_name,
            user_name=user_name,
            biography=biography,
            personality_profile=profile.to_dict(),
            common_phrases=common_phrases,
            episodic_memories=episodic_memories,
            source_hash=h,
            updated_at=now,
        )

        # Persist derived artifacts to MongoDB
        self._graph_col.update_one(
            {"contact_id": contact_id},
            {"$set": graph.model_dump()},
            upsert=True,
        )

        # Upsert biography embedding to Pinecone when configured
        if self._pinecone is not None:
            embedding = [0.0] * 1536  # stub; replace with real embeddings API
            self._pinecone.upsert(
                vectors=[
                    {
                        "id": contact_id,
                        "values": embedding,
                        "metadata": {
                            "contact_name": contact_name,
                            "user_name": user_name,
                            "biography_excerpt": biography[:200],
                        },
                    }
                ]
            )

        return graph

    def get_memory_graph(self, contact_id: str) -> MemoryGraph | None:
        """Retrieve the stored memory graph for a contact.

        Args:
            contact_id: Unique identifier for the contact

        Returns:
            MemoryGraph if one exists, None otherwise
        """
        doc = self._graph_col.find_one({"contact_id": contact_id})
        if doc is None:
            return None
        return MemoryGraph(**{k: v for k, v in doc.items() if k != "_id"})

    def regenerate(self, contact_id: str) -> MemoryGraph | None:
        """Force-regenerate derived artifacts from stored source messages.

        Use this when prompt or model changes should produce fresh artifacts even
        though the source data has not changed.

        Args:
            contact_id: Unique identifier for the contact

        Returns:
            Freshly derived MemoryGraph, or None if no source data exists
        """
        source_doc = self._source_col.find_one({"contact_id": contact_id})
        if source_doc is None:
            return None

        messages = source_doc.get("messages", [])
        contact_name = source_doc.get("contact_name", contact_id)
        user_name = source_doc.get("user_name", "")

        # Clear the cached source_hash so build_from_source will re-derive
        self._graph_col.update_one(
            {"contact_id": contact_id},
            {"$unset": {"source_hash": ""}},
        )

        return self.build_from_source(messages, contact_id, contact_name, user_name)
