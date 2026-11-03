"""Speech-to-text and text-to-speech for WhatsApp voice notes.

Uses any Whisper-compatible HTTP API (OpenAI, Groq, or a self-hosted server),
configured with STT_* / TTS_* in .env. Both are optional.
"""
import httpx

from .config import settings


def stt_enabled() -> bool:
    return bool(settings.stt_api_url and settings.stt_api_key)


def tts_enabled() -> bool:
    return bool(settings.tts_api_url and settings.tts_api_key and settings.voice_note_replies)


def transcribe(audio: bytes, mime_type: str = "audio/ogg") -> str:
    ext = "ogg" if "ogg" in mime_type else mime_type.split("/")[-1]
    resp = httpx.post(
        settings.stt_api_url,
        headers={"Authorization": f"Bearer {settings.stt_api_key}"},
        data={"model": settings.stt_model},
        files={"file": (f"voice.{ext}", audio, mime_type)},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json().get("text", "").strip()


def synthesize(text: str) -> bytes:
    """Returns OGG/Opus audio, the format WhatsApp plays as a voice note."""
    resp = httpx.post(
        settings.tts_api_url,
        headers={"Authorization": f"Bearer {settings.tts_api_key}"},
        json={"model": settings.tts_model, "voice": settings.tts_voice, "input": text[:3000], "response_format": "opus"},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.content
