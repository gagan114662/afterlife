# Branch 2: Open Source Stack Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace every paid API (Anthropic Claude, ElevenLabs TTS, Pinecone vector DB) with open-source alternatives that run locally on a MacBook — Ollama+llama3.2:3b for LLM, Kokoro TTS for speech synthesis, Coqui XTTS-v2 for voice cloning, and Chroma for vector memory.

**Architecture:** This branch rebases from main after Branch 1 merges. All replacements happen in-place — same function signatures, same callers, zero breaking API changes. The `conversation.py` module switches from anthropic → ollama; `memory.py` switches from Pinecone → Chroma; `voice-cloner/clone.py` adds Coqui XTTS-v2. Startup validation is updated to check Ollama health instead of API keys.

**Tech Stack:** Ollama (local LLM server), llama3.2:3b model, kokoro-tts (Python TTS), chromadb (vector DB), TTS package (Coqui XTTS-v2), sentence-transformers (local embeddings)

---

### Task 1: Install Ollama and pull the model

**Step 1: Install Ollama**

```bash
brew install ollama
```

Expected: `ollama` binary available at `/opt/homebrew/bin/ollama` (or `/usr/local/bin/ollama`).

**Step 2: Start Ollama server**

```bash
ollama serve &
```

Expected: server starts on `http://localhost:11434`.

**Step 3: Pull llama3.2:3b**

```bash
ollama pull llama3.2:3b
```

Expected: download completes, model appears in `ollama list`.

**Step 4: Verify model is available**

```bash
ollama list
```

Expected: output includes `llama3.2:3b`.

**Step 5: Quick smoke test**

```bash
curl -s http://localhost:11434/api/generate \
  -d '{"model":"llama3.2:3b","prompt":"Say hello","stream":false}' | python3 -m json.tool
```

Expected: JSON with a `response` field containing text.

---

### Task 2: Update requirements.txt — remove paid APIs, add open source

**Files:**
- Modify: `requirements.txt`

**Step 1: Write new requirements.txt**

Replace the entire file:

```
fastapi>=0.110
uvicorn[standard]>=0.29
ollama>=0.3
kokoro-tts>=0.9
chromadb>=0.5
TTS>=0.22
sentence-transformers>=3.0
pymongo>=4.10
motor>=3.3
structlog>=24.1
httpx>=0.27
pydantic>=2.6
pydantic-settings>=2.2
python-dotenv>=1.0
pytest>=8.1
pytest-asyncio>=0.23
ruff>=0.3
```

Note: removed `anthropic`, `elevenlabs`, `pinecone-client`. Added `ollama`, `kokoro-tts`, `chromadb`, `TTS`, `sentence-transformers`.

**Step 2: Install into virtualenv**

```bash
cd ~/gt/afterlife/refinery/rig
source .venv/bin/activate
pip install -r requirements.txt
```

Expected: all packages install without errors.

Note: `TTS` (Coqui) is large (~1GB) and will take a few minutes. `sentence-transformers` downloads models on first use.

**Step 3: Verify imports**

```bash
python3 -c "import ollama; import chromadb; from kokoro import KPipeline; print('all imports OK')"
```

Expected: `all imports OK`.

**Step 4: Commit**

```bash
cd ~/gt/afterlife/refinery/rig
git add requirements.txt
git commit -m "chore: replace paid API deps with ollama, kokoro-tts, chromadb, TTS"
```

---

### Task 3: Replace Anthropic Claude with Ollama in conversation.py

**Files:**
- Modify: `services/api/conversation.py`

**Step 1: Write the failing test first**

Create `tests/api/test_conversation_ollama.py`:

```python
"""Tests that conversation.py uses Ollama (not Anthropic) for LLM calls."""
import pytest
from unittest.mock import patch, MagicMock

def test_reply_as_persona_uses_ollama(monkeypatch):
    """reply_as_persona should call ollama.chat, not anthropic."""
    mock_response = {"message": {"content": "Hello janu!"}}

    with patch("services.api.conversation.ollama") as mock_ollama, \
         patch("services.api.conversation.load_contact_profile") as mock_profile, \
         patch("services.api.conversation.retrieve_relevant_memories") as mock_mem:
        mock_ollama.chat.return_value = mock_response
        mock_profile.return_value = {
            "name": "mom",
            "biography": "Warm woman.",
            "personality_profile": "Nurturing.",
            "common_phrases": "Janu!",
            "voice_id": "",
        }
        mock_mem.return_value = ""

        from services.api.conversation import reply_as_persona
        result = reply_as_persona("mom", "Gagan", [], "hi")

        assert mock_ollama.chat.called
        assert result == "Hello janu!"


def test_text_to_speech_uses_kokoro(monkeypatch):
    """text_to_speech should call KPipeline from kokoro, not ElevenLabs."""
    with patch("services.api.conversation.KPipeline") as mock_pipeline_cls:
        mock_pipeline = MagicMock()
        # KPipeline returns an iterable of (grapheme, phoneme, audio_tensor)
        import numpy as np
        mock_pipeline.return_value = [(None, None, np.zeros(1000, dtype="float32"))]
        mock_pipeline_cls.return_value = mock_pipeline

        from services.api import conversation
        # Force re-init
        conversation._kokoro_pipeline = None

        result = conversation.text_to_speech("hello", "")
        assert result is not None
        assert isinstance(result, bytes)
```

**Step 2: Run test to confirm it fails**

```bash
python -m pytest tests/api/test_conversation_ollama.py -v
```

Expected: FAIL — `ollama` not imported in `conversation.py` yet.

**Step 3: Rewrite conversation.py**

Replace entire `services/api/conversation.py`:

```python
"""
Conversation engine: build persona system prompt, call Ollama LLM, Kokoro TTS.
"""

import logging
import os
import io
from pathlib import Path
from typing import Optional

import numpy as np
import ollama
import soundfile as sf
from kokoro import KPipeline

from services.api.memory import load_contact_profile, retrieve_relevant_memories

logger = logging.getLogger(__name__)

PERSONA_PROMPT_PATH = Path(__file__).parent / "prompts" / "persona.txt"
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

_kokoro_pipeline: Optional[KPipeline] = None


def _get_kokoro() -> KPipeline:
    global _kokoro_pipeline
    if _kokoro_pipeline is None:
        _kokoro_pipeline = KPipeline(lang_code="a")  # "a" = American English
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

        combined = np.concatenate(audio_chunks)
        buf = io.BytesIO()
        sf.write(buf, combined, samplerate=24000, format="mp3")
        return buf.getvalue()
    except Exception as exc:
        logger.error("Kokoro TTS failed: %s", exc)
        return None
```

**Step 4: Run the test — it should pass now**

```bash
python -m pytest tests/api/test_conversation_ollama.py -v
```

Expected: both tests PASS.

**Step 5: Run full test suite**

```bash
python -m pytest tests/ -x -q
```

Expected: all pass.

**Step 6: Commit**

```bash
git add services/api/conversation.py tests/api/test_conversation_ollama.py
git commit -m "feat: replace Anthropic Claude with Ollama (llama3.2:3b) and ElevenLabs with Kokoro TTS"
```

---

### Task 4: Replace Pinecone with Chroma in memory.py

**Files:**
- Modify: `services/api/memory.py`
- Modify: `tests/api/test_memory.py`

**Step 1: Write failing test for Chroma**

Add this to `tests/api/test_memory.py`:

```python
def test_retrieve_memories_uses_chroma(monkeypatch, tmp_path):
    """retrieve_relevant_memories should query Chroma, not Pinecone."""
    import chromadb
    from unittest.mock import patch, MagicMock

    mock_collection = MagicMock()
    mock_collection.query.return_value = {
        "documents": [["Memory 1", "Memory 2"]],
        "distances": [[0.1, 0.2]],
    }

    with patch("services.api.memory.chromadb.PersistentClient") as mock_client_cls, \
         patch("services.api.memory._get_embedding") as mock_embed:
        mock_client = MagicMock()
        mock_client.get_or_create_collection.return_value = mock_collection
        mock_client_cls.return_value = mock_client
        mock_embed.return_value = [0.1] * 384

        from services.api import memory
        memory._chroma_client = None  # force re-init

        result = memory.retrieve_relevant_memories("mom", "hello", top_k=2)
        assert "Memory 1" in result
        assert "Memory 2" in result
```

**Step 2: Run test — confirm it fails**

```bash
python -m pytest tests/api/test_memory.py::test_retrieve_memories_uses_chroma -v
```

Expected: FAIL — Chroma not in memory.py yet.

**Step 3: Rewrite memory.py**

Replace entire `services/api/memory.py`:

```python
"""
Memory module: retrieve relevant memories from Chroma and manage biography in MongoDB.
"""

import logging
import os
from typing import Optional

import chromadb
from sentence_transformers import SentenceTransformer
from pymongo import MongoClient
from pymongo.collection import Collection

logger = logging.getLogger(__name__)

_EMBEDDING_MODEL = "all-MiniLM-L6-v2"  # 384-dim, runs on CPU
_CHROMA_PATH = os.environ.get("CHROMA_PATH", "./data/chroma")
_COLLECTION_NAME = "afterlife-memories"

_embedding_model: Optional[SentenceTransformer] = None
_chroma_client: Optional[chromadb.PersistentClient] = None


def _get_embedding_model() -> SentenceTransformer:
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = SentenceTransformer(_EMBEDDING_MODEL)
    return _embedding_model


def _get_embedding(text: str) -> list[float]:
    model = _get_embedding_model()
    return model.encode([text])[0].tolist()


def _get_chroma_collection() -> chromadb.Collection:
    global _chroma_client
    if _chroma_client is None:
        _chroma_client = chromadb.PersistentClient(path=_CHROMA_PATH)
    return _chroma_client.get_or_create_collection(_COLLECTION_NAME)


def _get_contacts_collection() -> Collection:
    uri = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
    db_name = os.environ.get("MONGODB_DB", "afterlife")
    client = MongoClient(uri)
    return client[db_name]["contacts"]


def load_contact_profile(contact_name: str) -> dict:
    """
    Load biography and personality profile for a contact from MongoDB.
    Returns dict with keys: biography, personality_profile, common_phrases, voice_id.
    Raises ValueError if contact is not found.
    """
    collection = _get_contacts_collection()
    doc = collection.find_one({"name": contact_name})
    if not doc:
        raise ValueError(f"Contact '{contact_name}' not found in database")
    return {
        "name": doc.get("name", contact_name),
        "biography": doc.get("biography", ""),
        "personality_profile": doc.get("personality_profile", ""),
        "common_phrases": doc.get("common_phrases", ""),
        "voice_id": doc.get("voice_id", ""),
    }


def retrieve_relevant_memories(contact_name: str, message: str, top_k: int = 5) -> str:
    """
    Embed the user's message and retrieve the most relevant episodic memories
    from Chroma for the given contact.

    Returns a formatted string of relevant memories, or empty string if unavailable.
    """
    try:
        collection = _get_chroma_collection()
        embedding = _get_embedding(message)
        results = collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
            where={"contact": contact_name},
            include=["documents"],
        )
        memories = []
        for doc in results.get("documents", [[]])[0]:
            if doc:
                memories.append(f"- {doc}")
        return "\n".join(memories)
    except Exception as exc:
        logger.warning("Could not retrieve memories from Chroma: %s", exc)
        return ""


def store_memory(contact_name: str, memory_text: str, memory_id: str) -> None:
    """
    Store a new episodic memory for the given contact in Chroma.
    Called after conversation sessions to persist notable exchanges.
    """
    try:
        collection = _get_chroma_collection()
        embedding = _get_embedding(memory_text)
        collection.add(
            ids=[memory_id],
            embeddings=[embedding],
            documents=[memory_text],
            metadatas=[{"contact": contact_name}],
        )
    except Exception as exc:
        logger.warning("Could not store memory in Chroma: %s", exc)


def update_biography(contact_name: str, new_biography: str) -> None:
    """
    Persist an updated biography for the contact back to MongoDB.
    Called by the Biographer Agent after each conversation session.
    """
    collection = _get_contacts_collection()
    result = collection.update_one(
        {"name": contact_name},
        {"$set": {"biography": new_biography}},
    )
    if result.matched_count == 0:
        logger.warning(
            "No contact document found for '%s' — biography not saved", contact_name
        )
```

**Step 4: Run the Chroma test — should pass**

```bash
python -m pytest tests/api/test_memory.py -v
```

Expected: all pass.

**Step 5: Run full test suite**

```bash
python -m pytest tests/ -x -q
```

Expected: all pass.

**Step 6: Commit**

```bash
git add services/api/memory.py tests/api/test_memory.py
git commit -m "feat: replace Pinecone with Chroma + sentence-transformers for vector memory"
```

---

### Task 5: Add Coqui XTTS-v2 for voice cloning

**Files:**
- Modify: `services/voice-cloner/clone.py`

**Step 1: Read existing clone.py**

Read `services/voice-cloner/clone.py` to understand current structure before editing.

**Step 2: Write failing test**

Create `tests/test_voice_cloner.py`:

```python
"""Tests that clone.py uses Coqui XTTS-v2."""
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


def test_clone_voice_uses_xtts(tmp_path):
    """clone_voice should call TTS().tts_to_file with XTTS-v2 model."""
    wav_sample = tmp_path / "sample.wav"
    wav_sample.write_bytes(b"\x00" * 100)  # dummy WAV
    output_path = tmp_path / "output.wav"

    with patch("services.voice_cloner.clone.TTS") as mock_tts_cls:
        mock_tts = MagicMock()
        mock_tts_cls.return_value = mock_tts

        from services.voice_cloner import clone
        clone._tts_instance = None  # force re-init

        clone.clone_voice(
            text="Hello janu!",
            speaker_wav=str(wav_sample),
            output_path=str(output_path),
        )

        mock_tts.tts_to_file.assert_called_once()
        call_kwargs = mock_tts.tts_to_file.call_args[1]
        assert call_kwargs["text"] == "Hello janu!"
        assert "xtts_v2" in mock_tts_cls.call_args[1].get("model_name", "")
```

**Step 3: Run test — confirm it fails**

```bash
python -m pytest tests/test_voice_cloner.py -v
```

Expected: FAIL — clone.py doesn't have `clone_voice` function yet.

**Step 4: Rewrite clone.py**

Replace entire `services/voice-cloner/clone.py`:

```python
"""
Voice cloner: generate speech in a contact's cloned voice using Coqui XTTS-v2.
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_XTTS_MODEL = "tts_models/multilingual/multi-dataset/xtts_v2"
_tts_instance = None


def _get_tts():
    global _tts_instance
    if _tts_instance is None:
        from TTS.api import TTS  # lazy import — large model
        _tts_instance = TTS(model_name=_XTTS_MODEL, progress_bar=False, gpu=False)
    return _tts_instance


def clone_voice(text: str, speaker_wav: str, output_path: str, language: str = "en") -> None:
    """
    Synthesize speech in the cloned voice of the speaker.

    Args:
        text: The text to speak.
        speaker_wav: Path to a WAV file of the target speaker (3–30 seconds).
        output_path: Where to write the output WAV file.
        language: Language code (default "en").
    """
    tts = _get_tts()
    tts.tts_to_file(
        text=text,
        speaker_wav=speaker_wav,
        language=language,
        file_path=output_path,
    )
    logger.info("Voice clone written to %s", output_path)


def get_best_voice_sample(voice_samples_dir: str) -> Optional[str]:
    """
    Return the path to the longest WAV file in voice_samples_dir, as
    XTTS-v2 performs better with longer reference audio.
    Returns None if no WAV files exist.
    """
    import os

    if not os.path.isdir(voice_samples_dir):
        return None

    wavs = [
        os.path.join(voice_samples_dir, f)
        for f in os.listdir(voice_samples_dir)
        if f.endswith(".wav")
    ]
    if not wavs:
        return None

    return max(wavs, key=os.path.getsize)
```

**Step 5: Add `__init__.py` if missing**

```bash
touch services/voice-cloner/__init__.py
```

Note: The directory is `voice-cloner` (with hyphen). Python can't import it with a hyphen. Check if there's an existing `__init__.py` and how it's imported. If the directory name prevents import, add a `voice_cloner` symlink or rename in the test to use the path directly.

Actually — the test uses `services.voice_cloner.clone`. The directory is `voice-cloner`. Fix the import in the test to use:
```python
import importlib.util, sys
spec = importlib.util.spec_from_file_location("clone", "services/voice-cloner/clone.py")
clone = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clone)
```

Or rename test import. Update test to use file-based import:

```python
def test_clone_voice_uses_xtts(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "clone", "services/voice-cloner/clone.py"
    )
    import types
    clone_module = types.ModuleType("clone")
    # ... use patch directly on the module
```

Simpler: just import directly from the file path in conftest or use sys.path. Add this to `tests/conftest.py`:

```python
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
```

And rename the test import to use `importlib`:

```python
import importlib.util, sys, types

def load_clone_module():
    path = os.path.join(os.path.dirname(__file__), "../services/voice-cloner/clone.py")
    spec = importlib.util.spec_from_file_location("clone", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
```

**Step 6: Run test — should pass**

```bash
python -m pytest tests/test_voice_cloner.py -v
```

Expected: PASS.

**Step 7: Run full test suite**

```bash
python -m pytest tests/ -x -q
```

Expected: all pass.

**Step 8: Commit**

```bash
git add services/voice-cloner/clone.py tests/test_voice_cloner.py
git commit -m "feat: add Coqui XTTS-v2 voice cloning (replaces ElevenLabs voice clone)"
```

---

### Task 6: Update startup validation in main.py

Remove ANTHROPIC_API_KEY and ELEVENLABS_API_KEY requirements. Add Ollama health check.

**Files:**
- Modify: `services/api/main.py`

**Step 1: Write failing test**

Add to `tests/api/test_health.py`:

```python
def test_startup_does_not_require_anthropic_key(monkeypatch):
    """Startup should not raise if ANTHROPIC_API_KEY is missing."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")

    # Should not raise RuntimeError about missing ANTHROPIC_API_KEY
    from services.api import main
    # The _require_env calls happen at startup — test that ANTHROPIC_API_KEY
    # is no longer in the required list
    import inspect
    source = inspect.getsource(main.startup)
    assert "ANTHROPIC_API_KEY" not in source
    assert "ELEVENLABS_API_KEY" not in source
```

**Step 2: Run test — confirm it fails**

```bash
python -m pytest tests/api/test_health.py::test_startup_does_not_require_anthropic_key -v
```

Expected: FAIL — ANTHROPIC_API_KEY is still in startup.

**Step 3: Update startup() in main.py**

Replace the `startup()` function:

```python
@app.on_event("startup")
async def startup() -> None:
    # Only MongoDB is required. Ollama runs locally.
    _require_env("MONGODB_URI")

    # Verify Ollama is reachable
    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    try:
        import httpx as _httpx
        with _httpx.Client(timeout=5) as c:
            resp = c.get(f"{ollama_host}/api/tags")
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
            ollama_model = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
            if not any(ollama_model in m for m in models):
                logger.warning(
                    "ollama_model_not_found",
                    model=ollama_model,
                    available=models,
                )
    except Exception as exc:
        logger.warning("ollama_health_check_failed", error=str(exc))
        # Don't crash — let first request fail with clear error

    mongodb_uri = os.environ["MONGODB_URI"]
    db_name = os.environ.get("MONGODB_DB", "afterlife")
    client = AsyncIOMotorClient(mongodb_uri)
    app.state.db = client[db_name]
    await ensure_indexes(app.state.db)
    logger.info("startup_complete", service="conversation-api")
```

**Step 4: Run the test — should pass**

```bash
python -m pytest tests/api/test_health.py -v
```

Expected: all pass.

**Step 5: Run full test suite**

```bash
python -m pytest tests/ -x -q
```

Expected: all pass.

**Step 6: Commit**

```bash
git add services/api/main.py tests/api/test_health.py
git commit -m "feat: update startup validation — remove API key checks, add Ollama health check"
```

---

### Task 7: Update demo.py for open source stack

**Files:**
- Modify: `demo.py`

**Step 1: Update demo.py**

Replace the env var setup and stubs section:

```python
# New env vars — no API keys needed
os.environ["MONGODB_URI"] = "mongodb://localhost:27017"
os.environ["MONGODB_DB"] = "afterlife_demo"
os.environ["OLLAMA_HOST"] = "http://localhost:11434"
os.environ["OLLAMA_MODEL"] = "llama3.2:3b"
os.environ["CHROMA_PATH"] = "/tmp/afterlife_chroma_demo"
```

Remove the `anthropic.Anthropic` patch and `sys.modules['pinecone']` patch — they're no longer needed.

Keep the MongoDB seeding and uvicorn startup unchanged.

**Step 2: Verify demo.py runs without error (dry check)**

```bash
python3 -c "import ast; ast.parse(open('demo.py').read()); print('syntax OK')"
```

Expected: `syntax OK`.

**Step 3: Run ruff**

```bash
ruff check services/ tests/ demo.py
```

Expected: no errors.

**Step 4: Commit**

```bash
git add demo.py
git commit -m "chore: update demo.py — remove API key stubs, use local Ollama + Chroma"
```

---

### Final CI Verification

Before opening MR, run this full checklist and confirm every command exits 0:

```bash
cd ~/gt/afterlife/refinery/rig

# Python
ruff check services/ tests/ --fix
ruff check services/ tests/
python -m pytest tests/ -x -q

# TypeScript
cd services/whatsapp-sync
npm install
npx tsc --noEmit
npm test
```
