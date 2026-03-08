from __future__ import annotations

import logging
from typing import Any, Dict

import numpy as np

from backends.interface import TTSBackendInterface


class XTTSBackend(TTSBackendInterface):
    """XTTS v2 TTS backend for high-quality multilingual voice cloning."""

    def __init__(self):
        self._tts = None
        self._speaker_wav: str = ""
        self._language: str = "it"

    @classmethod
    def name(cls) -> str:
        return "xtts"

    @classmethod
    def config_schema(cls) -> Dict[str, Any]:
        return {
            "model_path": {"type": "string", "required": False, "description": "Path to XTTS v2 model (empty = auto-download from HuggingFace)"},
            "speaker_wav": {"type": "string", "required": True, "description": "Path to speaker reference WAV for voice cloning"},
            "language": {"type": "string", "required": False, "description": "Language code (default: it)"},
        }

    @classmethod
    def is_available(cls) -> bool:
        try:
            from TTS.api import TTS  # noqa: F401
            return True
        except ImportError:
            return False

    def initialize(self, config: Dict[str, Any]) -> None:
        from TTS.api import TTS

        model_path = config.get("model_path", "")
        self._speaker_wav = config.get("speaker_wav", "/app/models/tts/speaker.wav")
        self._language = config.get("language", "it")

        if model_path:
            self._tts = TTS(model_path=model_path).to("cuda")
        else:
            self._tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to("cuda")

        logging.info(
            "XTTS v2 backend initialized (lang=%s, speaker_wav=%s)",
            self._language,
            self._speaker_wav,
        )

    def shutdown(self) -> None:
        self._tts = None

    def synthesize(self, text: str) -> bytes:
        """Synthesize text to PCM16 bytes at 24kHz."""
        if not self._tts:
            raise RuntimeError("XTTS backend not initialized")

        wav = self._tts.tts(
            text=text,
            speaker_wav=self._speaker_wav,
            language=self._language,
        )
        # wav is a list of floats in [-1, 1]; convert to PCM16 bytes
        audio_array = np.array(wav, dtype=np.float32)
        audio_array = np.clip(audio_array, -1.0, 1.0)
        pcm16 = (audio_array * 32767).astype(np.int16)
        return pcm16.tobytes()

    def status(self) -> Dict[str, Any]:
        return {
            "backend": "xtts",
            "loaded": self._tts is not None,
            "language": self._language,
            "speaker_wav": self._speaker_wav,
        }
