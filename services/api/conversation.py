"""
Conversation engine: build persona system prompt, call Claude, call ElevenLabs TTS.
"""

import logging
import os
from pathlib import Path
from typing import Optional

import anthropic
import httpx

from services.api.memory import load_contact_profile, retrieve_relevant_memories

logger = logging.getLogger(__name__)

PERSONA_PROMPT_PATH = Path(__file__).parent / "prompts" / "persona.txt"
CLAUDE_MODEL = "claude-sonnet-4-6"
ELEVENLABS_API_BASE = "https://api.elevenlabs.io/v1"


def _load_persona_template() -> str:
    return PERSONA_PROMPT_PATH.read_text(encoding="utf-8")


def build_system_prompt(
    contact_name: str,
    user_name: str,
    biography: str,
    personality_profile: str,
    common_phrases: str,
    relevant_memories: str,
) -> str:
    """Render the persona system prompt template with contact-specific values."""
    template = _load_persona_template()
    return template.format(
        person_name=contact_name,
        user_name=user_name,
        living_biography=biography,
        personality_profile=personality_profile,
        common_phrases=common_phrases,
        relevant_memories=relevant_memories or "(no specific memories retrieved)",
    )


def reply_as_persona(
    contact_name: str,
    user_name: str,
    history: list[dict],
    user_message: str,
) -> str:
    """
    Send the conversation history + new user message to Claude.
    Returns the persona's text reply.

    history: list of {"role": "user"|"assistant", "content": str}
    """
    profile = load_contact_profile(contact_name)
    relevant_memories = retrieve_relevant_memories(contact_name, user_message)

    system_prompt = build_system_prompt(
        contact_name=profile["name"],
        user_name=user_name,
        biography=profile["biography"],
        personality_profile=profile["personality_profile"],
        common_phrases=profile["common_phrases"],
        relevant_memories=relevant_memories,
    )

    messages = list(history) + [{"role": "user", "content": user_message}]

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable is required")

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        system=system_prompt,
        messages=messages,
    )
    return response.content[0].text


def text_to_speech(text: str, voice_id: str) -> Optional[bytes]:
    """
    Convert text to audio bytes using ElevenLabs TTS.
    Returns raw MP3 bytes, or None if TTS is unavailable / voice_id is empty.
    """
    if not voice_id:
        logger.info("No voice_id provided — skipping TTS")
        return None

    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        logger.warning("ELEVENLABS_API_KEY not set — skipping TTS")
        return None

    url = f"{ELEVENLABS_API_BASE}/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.8,
        },
    }

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.content
    except httpx.HTTPError as exc:
        logger.error("ElevenLabs TTS request failed: %s", exc)
        return None
