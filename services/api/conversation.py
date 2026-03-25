"""
Conversation engine: build persona system prompt, call Ollama LLM, Kokoro TTS.
"""

import logging
import os
import io
from pathlib import Path
from typing import Optional

import ollama

from services.api.memory import load_contact_profile, retrieve_relevant_memories

logger = logging.getLogger(__name__)

PERSONA_PROMPT_PATH = Path(__file__).parent / "prompts" / "persona.txt"
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

_kokoro_pipeline = None


def _get_kokoro():
    global _kokoro_pipeline
    if _kokoro_pipeline is None:
        from kokoro import KPipeline  # lazy import — not installed in all envs
        _kokoro_pipeline = KPipeline(lang_code="a")
    return _kokoro_pipeline


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
    Send conversation history + user message to Ollama.
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

    messages = (
        [{"role": "system", "content": system_prompt}]
        + list(history)
        + [{"role": "user", "content": user_message}]
    )

    response = ollama.chat(
        model=OLLAMA_MODEL,
        messages=messages,
        options={"num_predict": 256},
    )
    return response["message"]["content"]


def text_to_speech(text: str, voice_id: str) -> Optional[bytes]:
    """
    Convert text to MP3 bytes using Kokoro TTS.
    voice_id is ignored (Kokoro uses a fixed voice profile).
    Returns MP3 bytes, or None on failure.
    """
    if not text.strip():
        return None

    try:
        pipeline = _get_kokoro()
        audio_chunks = []
        for _, _, audio in pipeline(text):
            if audio is not None:
                audio_chunks.append(audio)

        if not audio_chunks:
            return None

        import numpy as np  # noqa: PLC0415
        import soundfile as sf  # noqa: PLC0415
        combined = np.concatenate(audio_chunks)
        buf = io.BytesIO()
        sf.write(buf, combined, samplerate=24000, format="mp3")
        return buf.getvalue()
    except Exception as exc:
        logger.error("Kokoro TTS failed: %s", exc)
        return None
