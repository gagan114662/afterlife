"""Tests that clone.py uses Coqui XTTS-v2."""
import importlib.util
from pathlib import Path
from unittest.mock import patch, MagicMock


def _load_clone_module():
    """Load voice-cloner/clone.py via importlib (hyphen in dir name prevents normal import)."""
    path = Path(__file__).parent.parent / "services" / "voice-cloner" / "clone.py"
    spec = importlib.util.spec_from_file_location("clone", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_clone_voice_uses_xtts(tmp_path):
    """clone_voice should call TTS().tts_to_file with XTTS-v2 model."""
    wav_sample = tmp_path / "sample.wav"
    wav_sample.write_bytes(b"\x00" * 100)  # dummy WAV
    output_path = tmp_path / "output.wav"

    clone = _load_clone_module()
    clone._tts_instance = None  # ensure fresh init

    with patch.dict("sys.modules", {"TTS": MagicMock(), "TTS.api": MagicMock()}):
        mock_tts_cls = MagicMock()
        mock_tts = MagicMock()
        mock_tts_cls.return_value = mock_tts

        import sys
        tts_api_mock = MagicMock()
        tts_api_mock.TTS = mock_tts_cls
        sys.modules["TTS.api"] = tts_api_mock

        clone._tts_instance = None  # force re-init with the new mock
        clone.clone_voice(
            text="Hello janu!",
            speaker_wav=str(wav_sample),
            output_path=str(output_path),
        )

        mock_tts.tts_to_file.assert_called_once()
        call_kwargs = mock_tts.tts_to_file.call_args[1]
        assert call_kwargs["text"] == "Hello janu!"
        assert mock_tts_cls.call_args[1].get("model_name") == "tts_models/multilingual/multi-dataset/xtts_v2"


def test_get_best_voice_sample_returns_largest(tmp_path):
    """get_best_voice_sample should return the largest WAV file."""
    clone = _load_clone_module()

    small = tmp_path / "small.wav"
    large = tmp_path / "large.wav"
    small.write_bytes(b"\x00" * 100)
    large.write_bytes(b"\x00" * 1000)

    result = clone.get_best_voice_sample(str(tmp_path))
    assert result == str(large)


def test_get_best_voice_sample_missing_dir():
    """get_best_voice_sample should return None for missing directory."""
    clone = _load_clone_module()
    result = clone.get_best_voice_sample("/nonexistent/path")
    assert result is None
