from __future__ import annotations

import io
import logging
import wave
from typing import Any, Dict, Optional

from backends.interface import STTBackendInterface

logger = logging.getLogger(__name__)


class ElevenLabsSTTBackend(STTBackendInterface):
    """ElevenLabs Scribe v1 STT backend (cloud API, batch transcription)."""

    def __init__(self):
        self._api_key: str = ""
        self._language: str = "it"
        self._model_id: str = "scribe_v1"
        self._initialized: bool = False
        self._session = None

    @classmethod
    def name(cls) -> str:
        return "elevenlabs"

    @classmethod
    def config_schema(cls) -> Dict[str, Any]:
        return {
            "api_key": {"type": "string", "required": True, "description": "ElevenLabs API key"},
            "language": {"type": "string", "required": False, "default": "it", "description": "Language code"},
            "model_id": {"type": "string", "required": False, "default": "scribe_v1"},
        }

    @classmethod
    def is_available(cls) -> bool:
        try:
            import requests
            return True
        except ImportError:
            return False

    def initialize(self, config: Dict[str, Any]) -> None:
        self._api_key = config.get("api_key", "")
        self._language = config.get("language", "it")
        self._model_id = config.get("model_id", "scribe_v1")
        if not self._api_key:
            raise ValueError("ElevenLabs API key is required for STT")
        self._initialized = True
        logger.info("ElevenLabs STT initialized (model=%s, language=%s)", self._model_id, self._language)

    def shutdown(self) -> None:
        self._initialized = False

    def transcribe_pcm16(self, pcm16_audio: bytes, sample_rate: int = 16000) -> str:
        """Transcribe PCM16 audio bytes via ElevenLabs Speech-to-Text API."""
        import requests

        wav_buf = io.BytesIO()
        with wave.open(wav_buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm16_audio)
        wav_bytes = wav_buf.getvalue()

        try:
            resp = requests.post(
                "https://api.elevenlabs.io/v1/speech-to-text",
                headers={"xi-api-key": self._api_key},
                files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                data={
                    "model_id": self._model_id,
                    "language_code": self._language,
                },
                timeout=15,
            )
            resp.raise_for_status()
            result = resp.json()
            text = result.get("text", "").strip()
            logger.info("ElevenLabs STT result: %r", text[:120])
            return text
        except Exception as exc:
            logger.error("ElevenLabs STT API error: %s", exc)
            return ""

    def process_audio(self, audio_bytes: bytes) -> Optional[str]:
        return self.transcribe_pcm16(audio_bytes)

    def status(self) -> Dict[str, Any]:
        return {
            "backend": "elevenlabs",
            "loaded": self._initialized,
            "model": self._model_id,
            "language": self._language,
        }
