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
