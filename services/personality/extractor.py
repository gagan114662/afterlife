"""Personality extraction service.

Analyzes WhatsApp message history via Claude to extract:
- Linguistic patterns (vocabulary, sentence structure, emoji usage, slang, language switches)
- Emotional patterns (topics, worries, humor style, response style)
- Relationship patterns (nicknames for user, running jokes, shared memories)

Outputs a PersonalityProfile used by BiographerAgent to generate Living Biographies.
"""
import json
from dataclasses import dataclass, field, asdict
from typing import Any

import anthropic


@dataclass
class PersonalityProfile:
    """Structured personality profile extracted from message history."""
    contact_name: str
    user_name: str
    linguistic_patterns: dict[str, Any] = field(default_factory=dict)
    emotional_patterns: dict[str, Any] = field(default_factory=dict)
    relationship_patterns: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


_EXTRACTION_PROMPT = """\
You are analyzing a WhatsApp conversation between {user_name} and {contact_name}.
Your task is to build a deep personality profile of {contact_name} based on their messages.

MESSAGES (only {contact_name}'s side matters — analyze their patterns):
{messages_text}

Return a JSON object with exactly this structure (no extra keys):
{{
  "linguistic_patterns": {{
    "vocabulary": [list of words/phrases they use often],
    "sentence_structure": "description of how they write (short/long, formal/casual, etc.)",
    "emoji_usage": [list of emojis they use],
    "slang_nicknames": [nicknames and slang they use],
    "language_switches": [languages they use, e.g. "English", "Hindi", "Punjabi"],
    "greeting_farewell": [their typical greetings and sign-offs]
  }},
  "emotional_patterns": {{
    "topics": [topics they frequently bring up],
    "worries": [things they worry about],
    "pride": [things they are proud of],
    "humor_style": "how they express humor (dry, sarcastic, warm, etc.)",
    "response_style": "how they respond to the user's problems (advice-giver, listener, deflector, etc.)"
  }},
  "relationship_patterns": {{
    "names_for_user": [what they call {user_name}],
    "running_jokes": [recurring jokes or references],
    "shared_memories": [memories or events they reference],
    "recurring_conversations": [topics that come up again and again]
  }}
}}

Return ONLY the JSON. No explanation, no markdown fences.
"""


class PersonalityExtractor:
    """Extracts personality profile from message history using Claude."""

    def __init__(self, client: anthropic.Anthropic | None = None, model: str = "claude-sonnet-4-6"):
        self._client = client or anthropic.Anthropic()
        self._model = model

    def extract(self, messages: list[dict], contact_name: str, user_name: str) -> PersonalityProfile:
        """Analyze message history and return a PersonalityProfile.

        Args:
            messages: List of message dicts with keys: sender, text, timestamp
            contact_name: Name of the contact being analyzed
            user_name: Name of the user (the other party in the conversation)

        Returns:
            PersonalityProfile with linguistic, emotional, and relationship patterns
        """
        messages_text = self._format_messages(messages, contact_name)
        prompt = _EXTRACTION_PROMPT.format(
            user_name=user_name,
            contact_name=contact_name,
            messages_text=messages_text,
        )

        response = self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()
        data = json.loads(raw)

        return PersonalityProfile(
            contact_name=contact_name,
            user_name=user_name,
            linguistic_patterns=data.get("linguistic_patterns", {}),
            emotional_patterns=data.get("emotional_patterns", {}),
            relationship_patterns=data.get("relationship_patterns", {}),
        )

    def _format_messages(self, messages: list[dict], contact_name: str) -> str:
        if not messages:
            return "(no messages)"
        lines = []
        for msg in messages:
            sender = msg.get("sender", "unknown")
            text = msg.get("text", "")
            ts = msg.get("timestamp", "")
            lines.append(f"[{ts}] {sender}: {text}")
        return "\n".join(lines)
