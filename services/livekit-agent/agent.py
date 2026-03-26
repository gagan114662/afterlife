"""
LiveKit voice agent for After-Life realtime sessions.

The agent joins every LiveKit room matching the pattern "afterlife-*" and runs
the voice pipeline:

    user audio → VAD → STT → persona LLM (Ollama) → ElevenLabs TTS → audio out

Session context (contact name, user name, voice ID) is loaded from MongoDB
using the room name as the lookup key. Conversation history is accumulated
in memory for the duration of the room session.

Run:
    # From the project root (so services.api.* imports resolve)
    PYTHONPATH=. python services/livekit-agent/agent.py start

Required environment variables:
    LIVEKIT_URL         LiveKit server WebSocket URL (wss://...)
    LIVEKIT_API_KEY     LiveKit API key
    LIVEKIT_API_SECRET  LiveKit API secret
    MONGODB_URI         MongoDB connection string
    MONGODB_DB          MongoDB database name (default: afterlife)
    ELEVENLABS_API_KEY  ElevenLabs API key (required for cloned-voice TTS)
    OLLAMA_HOST         Ollama server URL (default: http://localhost:11434)
    OLLAMA_MODEL        Ollama model name (default: llama3.2:3b)
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Optional

import structlog
from motor.motor_asyncio import AsyncIOMotorClient

if TYPE_CHECKING:
    pass

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)
logger = structlog.get_logger(__name__)


# ─── Settings ─────────────────────────────────────────────────────────────────


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Required environment variable {name!r} is not set")
    return val


class AgentSettings:
    """Validate and hold agent configuration from environment."""

    def __init__(self) -> None:
        self.livekit_url: str = _require_env("LIVEKIT_URL")
        self.livekit_api_key: str = _require_env("LIVEKIT_API_KEY")
        self.livekit_api_secret: str = _require_env("LIVEKIT_API_SECRET")
        self.mongodb_uri: str = _require_env("MONGODB_URI")
        self.mongodb_db: str = os.environ.get("MONGODB_DB", "afterlife")
        self.elevenlabs_api_key: str = os.environ.get("ELEVENLABS_API_KEY", "")
        self.ollama_host: str = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        self.ollama_model: str = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")


# ─── Persona LLM Bridge ───────────────────────────────────────────────────────


async def generate_persona_reply(
    contact_name: str,
    user_name: str,
    history: list[dict],
    user_message: str,
) -> str:
    """
    Async wrapper around the synchronous reply_as_persona function.

    Runs the blocking Ollama call in a thread-pool executor so the event loop
    remains responsive while the model generates.
    """
    from services.api.conversation import reply_as_persona  # noqa: PLC0415

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: reply_as_persona(
            contact_name=contact_name,
            user_name=user_name,
            history=list(history),
            user_message=user_message,
        ),
    )


# ─── Custom LLM adapter for livekit-agents ────────────────────────────────────


def _build_persona_llm(contact_name: str, user_name: str, history: list[dict]):
    """
    Build a livekit-agents LLM object backed by the After-Life persona engine.

    Lazy-imports livekit.agents so unit tests that mock the SDK can import
    this module without the real package installed.
    """
    from livekit.agents import llm  # type: ignore[import]

    class PersonaLLM(llm.LLM):
        """Routes transcribed speech through the After-Life persona engine."""

        def chat(
            self,
            *,
            chat_ctx: llm.ChatContext,
            fnc_ctx: Optional[llm.FunctionContext] = None,
        ) -> "PersonaLLMStream":
            return PersonaLLMStream(
                llm=self,
                chat_ctx=chat_ctx,
                contact_name=contact_name,
                user_name=user_name,
                history=history,
            )

    class PersonaLLMStream(llm.LLMStream):
        def __init__(
            self,
            *,
            llm: llm.LLM,
            chat_ctx: llm.ChatContext,
            contact_name: str,
            user_name: str,
            history: list[dict],
        ) -> None:
            super().__init__(llm, chat_ctx=chat_ctx, fnc_ctx=None)
            self._contact_name = contact_name
            self._user_name = user_name
            self._history = history

        async def _run(self) -> None:
            user_message = _extract_last_user_message(self._chat_ctx)
            if not user_message:
                return

            reply = await generate_persona_reply(
                contact_name=self._contact_name,
                user_name=self._user_name,
                history=self._history,
                user_message=user_message,
            )

            self._history.append({"role": "user", "content": user_message})
            self._history.append({"role": "assistant", "content": reply})

            self._event_ch.send_nowait(
                llm.ChatChunk(
                    choices=[
                        llm.Choice(
                            delta=llm.ChoiceDelta(role="assistant", content=reply)
                        )
                    ]
                )
            )

    return PersonaLLM()


def _extract_last_user_message(chat_ctx) -> str:
    """Extract the most recent user message from a livekit-agents ChatContext."""
    try:
        messages = list(chat_ctx.messages)
        for msg in reversed(messages):
            if getattr(msg, "role", None) == "user":
                content = getattr(msg, "content", None)
                if content:
                    return content if isinstance(content, str) else str(content)
    except (AttributeError, TypeError) as exc:
        logger.warning("chat_ctx_parse_error", error=str(exc))
    return ""


# ─── STT / TTS plugin builders ────────────────────────────────────────────────


def _build_stt():
    """
    Return an STT plugin.

    Prefers Deepgram; falls back to OpenAI Whisper if Deepgram is unavailable.
    Raises RuntimeError if no STT plugin can be loaded.
    """
    try:
        from livekit.plugins import deepgram  # type: ignore[import]

        return deepgram.STT()
    except (ImportError, Exception) as exc:
        logger.warning("deepgram_stt_unavailable", error=str(exc))

    try:
        from livekit.plugins import openai as lk_openai  # type: ignore[import]

        return lk_openai.STT(model="whisper-1")
    except (ImportError, Exception) as exc:
        logger.warning("openai_stt_unavailable", error=str(exc))

    raise RuntimeError(
        "No STT plugin available. "
        "Install livekit-plugins-deepgram or livekit-plugins-openai."
    )


def _build_tts(voice_id: str, elevenlabs_api_key: str):
    """
    Return a TTS plugin.

    Uses ElevenLabs with the cloned voice_id when both are set.
    Falls back to OpenAI TTS if ElevenLabs is not configured.
    Raises RuntimeError if no TTS plugin can be loaded.
    """
    if elevenlabs_api_key and voice_id:
        try:
            from livekit.plugins import elevenlabs as lk_eleven  # type: ignore[import]

            return lk_eleven.TTS(
                api_key=elevenlabs_api_key,
                voice_id=voice_id,
            )
        except (ImportError, Exception) as exc:
            logger.warning("elevenlabs_tts_unavailable", error=str(exc))

    try:
        from livekit.plugins import openai as lk_openai  # type: ignore[import]

        return lk_openai.TTS()
    except (ImportError, Exception) as exc:
        logger.warning("openai_tts_unavailable", error=str(exc))

    raise RuntimeError(
        "No TTS plugin available. "
        "Set ELEVENLABS_API_KEY or install livekit-plugins-openai."
    )


# ─── Session context loader ───────────────────────────────────────────────────


async def _load_session_context(db, room_name: str) -> Optional[dict]:
    """
    Return the active livekit_sessions document for the given room name,
    or None if not found.
    """
    return await db.livekit_sessions.find_one(
        {"room_name": room_name, "state": "active"},
        {"_id": 0},
    )


# ─── Agent entrypoint ─────────────────────────────────────────────────────────


async def run_voice_pipeline(ctx, settings: AgentSettings, db) -> None:
    """
    Core voice pipeline for a single LiveKit room.

    Called by the livekit-agents framework when a new room job is dispatched.
    Loads session context from MongoDB, builds the STT/LLM/TTS chain, and
    starts the VoiceAssistant.
    """
    from livekit.agents import AutoSubscribe  # type: ignore[import]
    from livekit.agents.voice_assistant import VoiceAssistant  # type: ignore[import]
    from livekit.plugins import silero  # type: ignore[import]

    room_name = ctx.room.name
    session = await _load_session_context(db, room_name)
    if not session:
        logger.error(
            "livekit_agent_no_session",
            room_name=room_name,
        )
        return

    contact_name = session["contact_name"]
    user_name = session["user_name"]
    voice_id = session.get("voice_id", "")
    history: list[dict] = []

    logger.info(
        "livekit_agent_joining",
        room=room_name,
        contact=contact_name,
        user=user_name,
    )

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    try:
        stt = _build_stt()
        tts = _build_tts(voice_id, settings.elevenlabs_api_key)
        persona_llm = _build_persona_llm(contact_name, user_name, history)
    except RuntimeError as exc:
        logger.error(
            "livekit_agent_plugin_error",
            room=room_name,
            error=str(exc),
        )
        return

    assistant = VoiceAssistant(
        vad=silero.VAD.load(),
        stt=stt,
        llm=persona_llm,
        tts=tts,
    )
    assistant.start(ctx.room)

    logger.info(
        "livekit_agent_ready",
        room=room_name,
        contact=contact_name,
    )

    # Keep the agent alive until the room closes
    await asyncio.Event().wait()


def _make_entrypoint(settings: AgentSettings, db):
    """Return the async entrypoint function registered with the LiveKit worker."""

    async def entrypoint(ctx) -> None:
        try:
            await run_voice_pipeline(ctx, settings, db)
        except Exception as exc:
            logger.error(
                "livekit_agent_unhandled_error",
                room=getattr(getattr(ctx, "room", None), "name", "unknown"),
                error=str(exc),
                exc_info=True,
            )
            raise

    return entrypoint


# ─── Health probe ─────────────────────────────────────────────────────────────


async def health_check(settings: AgentSettings, db) -> dict:
    """
    Return a health snapshot for the agent worker.

    Checks MongoDB connectivity and reports settings availability.
    Used by external health monitors and the CI smoke test.
    """
    mongo_ok = False
    try:
        await db.command("ping")
        mongo_ok = True
    except Exception as exc:
        logger.warning("health_check_mongo_failed", error=str(exc))

    return {
        "status": "ok" if mongo_ok else "degraded",
        "service": "livekit-voice-agent",
        "livekit_url_set": bool(settings.livekit_url),
        "elevenlabs_key_set": bool(settings.elevenlabs_api_key),
        "mongo_reachable": mongo_ok,
    }


# ─── Main ─────────────────────────────────────────────────────────────────────


def run_worker() -> None:
    """Entry point: validate settings and start the LiveKit worker."""
    settings = AgentSettings()

    async def _init_db():
        client = AsyncIOMotorClient(settings.mongodb_uri)
        return client[settings.mongodb_db]

    db = asyncio.get_event_loop().run_until_complete(_init_db())

    from livekit.agents import WorkerOptions, cli  # type: ignore[import]

    entrypoint = _make_entrypoint(settings, db)
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, worker_type="room"))


if __name__ == "__main__":
    run_worker()
