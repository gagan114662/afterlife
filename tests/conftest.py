"""
Test configuration: mock heavy ML dependencies that may not be installed
(kokoro-tts requires Python <3.13; chromadb/sentence-transformers are large).
These stubs allow service modules to be imported without the real packages.
"""
import sys
from unittest.mock import MagicMock

# Stub out packages that are not available in this environment:
# - kokoro-tts: requires Python <3.13, we run 3.14
# - chromadb: disk-space-sensitive install
# - sentence_transformers: depends on heavy ML stack
# - TTS (Coqui): disk-space-sensitive install
for _mod in [
    "kokoro",
    "chromadb",
    "sentence_transformers",
    "TTS",
    "TTS.api",
]:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()
