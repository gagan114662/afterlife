"""Biographer Agent service.

Generates and evolves a Living Biography per contact — a 300-500 word prose narrative
that describes who the person is, how they relate to the user, their emotional patterns,
and their history. Injected at the start of every conversation.

Storage:
- MongoDB: biography text + personality profile, indexed by contact_id
- Pinecone: embedding of biography for semantic search during conversations
"""
from __future__ import annotations

from typing import Any

import anthropic


_GENERATE_PROMPT = """\
You are writing a Living Biography for an AI persona system called After-Life.
This biography will be injected at the start of every conversation so the AI
"knows" this person before saying a word.

CONTACT: {contact_name}
USER: {user_name}

PERSONALITY PROFILE:
Linguistic patterns: {linguistic_patterns}
Emotional patterns: {emotional_patterns}
Relationship patterns: {relationship_patterns}

Write a 300-500 word PROSE NARRATIVE about {contact_name} as they relate to {user_name}.
The narrative should describe:
- Who this person is to {user_name}
- Their emotional style, humor, and way of communicating
- What they worry about, what they're proud of
- Running themes in their relationship with {user_name}
- Their characteristic phrases, habits, and mannerisms
- How they end conversations or express love

CRITICAL RULES:
- Write ONLY flowing prose paragraphs. NO bullet points. NO numbered lists. NO headers.
- Write in third person (e.g. "She always starts with...")
- Make it feel like a vivid portrait, not a clinical profile
- Reference specific patterns from the personality data naturally
- The narrative should read like it was written by someone who deeply knew this person
"""

_EVOLVE_PROMPT = """\
You are updating a Living Biography after a new conversation session.

EXISTING BIOGRAPHY:
{existing_biography}

RECENT SESSION TRANSCRIPT:
{session_text}

Based on what was revealed in this session, update the biography to incorporate
any new insights, memories, or patterns. Keep the 300-500 word length.

CRITICAL RULES:
- Write ONLY flowing prose paragraphs. NO bullet points. NO numbered lists. NO headers.
- Preserve what's already known unless the session reveals something different
- Integrate new details naturally into the narrative
- Return ONLY the updated biography text, nothing else
"""


class BiographerAgent:
    """Generates and evolves Living Biographies using Claude, stored in MongoDB + Pinecone."""

    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        mongo_collection: Any = None,
        pinecone_index: Any = None,
        model: str = "claude-sonnet-4-6",
    ):
        self._client = client or anthropic.Anthropic()
        self._mongo = mongo_collection
        self._pinecone = pinecone_index
        self._model = model

    def generate_biography(self, profile) -> str:
        """Generate a 300-500 word prose Living Biography from a PersonalityProfile.

        Args:
            profile: PersonalityProfile from PersonalityExtractor

        Returns:
            Prose biography string
        """
        prompt = _GENERATE_PROMPT.format(
            contact_name=profile.contact_name,
            user_name=profile.user_name,
            linguistic_patterns=profile.linguistic_patterns,
            emotional_patterns=profile.emotional_patterns,
            relationship_patterns=profile.relationship_patterns,
        )
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()

    def evolve_biography(
        self,
        existing_biography: str,
        session_transcript: list[dict],
        contact_name: str,
        user_name: str,
    ) -> str:
        """Update biography after a conversation session to incorporate new insights.

        Args:
            existing_biography: Current biography text
            session_transcript: List of dicts with keys: speaker, text
            contact_name: Name of the contact
            user_name: Name of the user

        Returns:
            Updated prose biography string
        """
        session_text = "\n".join(
            f"{turn.get('speaker', 'unknown')}: {turn.get('text', '')}"
            for turn in session_transcript
        )
        prompt = _EVOLVE_PROMPT.format(
            existing_biography=existing_biography,
            session_text=session_text,
        )
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()

    def store_biography(self, contact_id: str, biography: str, profile) -> None:
        """Store biography in MongoDB and upsert embedding to Pinecone.

        Args:
            contact_id: Unique identifier for the contact
            biography: Prose biography string
            profile: PersonalityProfile with structured data
        """
        doc = {
            "contact_id": contact_id,
            "contact_name": profile.contact_name,
            "user_name": profile.user_name,
            "biography": biography,
            "linguistic_patterns": profile.linguistic_patterns,
            "emotional_patterns": profile.emotional_patterns,
            "relationship_patterns": profile.relationship_patterns,
        }
        self._mongo.update_one(
            {"contact_id": contact_id},
            {"$set": doc},
            upsert=True,
        )

        # Pinecone expects pre-computed embeddings; use a simple hash-based stub vector
        # In production this would use an embeddings API (e.g. OpenAI or Cohere)
        embedding = self._text_to_embedding(biography)
        self._pinecone.upsert(vectors=[{
            "id": contact_id,
            "values": embedding,
            "metadata": {
                "contact_name": profile.contact_name,
                "user_name": profile.user_name,
                "biography_excerpt": biography[:200],
            },
        }])

    def get_biography(self, contact_id: str) -> dict | None:
        """Retrieve biography document from MongoDB.

        Args:
            contact_id: Unique identifier for the contact

        Returns:
            Biography document dict or None if not found
        """
        return self._mongo.find_one({"contact_id": contact_id})

    def _text_to_embedding(self, text: str) -> list[float]:
        """Stub embedding — in production, replace with a real embeddings API call."""
        # 1536-dim zero vector as placeholder; real impl would call OpenAI/Cohere embeddings
        return [0.0] * 1536
