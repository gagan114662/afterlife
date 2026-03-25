"""Audio utility functions for voice note quality filtering."""

import subprocess
import tempfile
import os
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MIN_DURATION_SECONDS = 5.0
NOISE_FLOOR_DB = -30.0  # Mean amplitude must be above this (less negative = louder)
SILENCE_RATIO_MAX = 0.6  # At most 60% silence


def get_audio_duration(audio_path: str) -> Optional[float]:
    """Return duration in seconds using ffprobe, or None on error."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_streams", audio_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        data = json.loads(result.stdout)
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "audio":
                return float(stream["duration"])
        return None
    except Exception as e:
        logger.warning("ffprobe failed for %s: %s", audio_path, e)
        return None


def get_rms_db(audio_path: str) -> Optional[float]:
    """Return mean RMS level in dBFS using ffmpeg volumedetect filter."""
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-i", audio_path,
                "-af", "volumedetect",
                "-vn", "-sn", "-dn",
                "-f", "null", "/dev/null",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        # volumedetect writes to stderr
        for line in result.stderr.splitlines():
            if "mean_volume" in line:
                # "mean_volume: -23.5 dB"
                parts = line.split("mean_volume:")
                if len(parts) == 2:
                    return float(parts[1].strip().split()[0])
        return None
    except Exception as e:
        logger.warning("ffmpeg volumedetect failed for %s: %s", audio_path, e)
        return None


def get_silence_ratio(audio_path: str, silence_threshold_db: float = -40.0) -> Optional[float]:
    """Return fraction of audio that is silence (below threshold)."""
    try:
        duration = get_audio_duration(audio_path)
        if not duration or duration == 0:
            return None

        result = subprocess.run(
            [
                "ffmpeg", "-i", audio_path,
                "-af", f"silencedetect=noise={silence_threshold_db}dB:d=0.3",
                "-vn", "-sn", "-dn",
                "-f", "null", "/dev/null",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        silent_seconds = 0.0
        silence_end = None
        for line in result.stderr.splitlines():
            if "silence_end" in line:
                # "silence_end: 1.23 | silence_duration: 0.45"
                for part in line.split("|"):
                    if "silence_duration" in part:
                        silent_seconds += float(part.split(":")[1].strip())
        return min(silent_seconds / duration, 1.0)
    except Exception as e:
        logger.warning("Silence detection failed for %s: %s", audio_path, e)
        return None


def is_quality_audio(audio_path: str) -> tuple[bool, str]:
    """
    Check if an audio file meets quality thresholds for voice cloning.

    Returns (passes, reason) where passes is True if acceptable.
    """
    path = str(audio_path)

    duration = get_audio_duration(path)
    if duration is None:
        return False, "could not read audio duration"
    if duration < MIN_DURATION_SECONDS:
        return False, f"too short ({duration:.1f}s < {MIN_DURATION_SECONDS}s)"

    rms = get_rms_db(path)
    if rms is None:
        return False, "could not measure audio level"
    if rms < NOISE_FLOOR_DB:
        return False, f"too quiet ({rms:.1f} dBFS < {NOISE_FLOOR_DB} dBFS)"

    silence_ratio = get_silence_ratio(path)
    if silence_ratio is not None and silence_ratio > SILENCE_RATIO_MAX:
        return False, f"too much silence ({silence_ratio:.0%} > {SILENCE_RATIO_MAX:.0%})"

    return True, "ok"


def convert_to_wav(input_path: str, output_path: Optional[str] = None) -> Optional[str]:
    """
    Convert audio file to 16kHz mono WAV suitable for ElevenLabs.

    If output_path is None, writes to a temp file. Returns path on success or None.
    """
    if output_path is None:
        suffix = ".wav"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        output_path = tmp.name
        tmp.close()

    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", input_path,
                "-ar", "16000",
                "-ac", "1",
                "-c:a", "pcm_s16le",
                output_path,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            logger.error("ffmpeg conversion failed: %s", result.stderr[-500:])
            return None
        return output_path
    except Exception as e:
        logger.error("convert_to_wav failed for %s: %s", input_path, e)
        return None


def filter_quality_voice_notes(
    voice_note_paths: list[str],
) -> tuple[list[str], float]:
    """
    Filter a list of voice note paths for quality.

    Returns (accepted_paths, total_accepted_duration_seconds).
    """
    accepted = []
    total_duration = 0.0

    for path in voice_note_paths:
        ok, reason = is_quality_audio(path)
        if ok:
            duration = get_audio_duration(path) or 0.0
            accepted.append(path)
            total_duration += duration
            logger.info("Accepted %s (%.1fs)", path, duration)
        else:
            logger.info("Rejected %s: %s", path, reason)

    return accepted, total_duration
