"""Voicemail transcription.

Order: Deepgram (DEEPGRAM_API_KEY) -> OpenAI Whisper (OPENAI_API_KEY) -> None.
When no provider is configured (local development) the caller keeps whatever transcript the webhook supplied
(e.g. Twilio's own TranscriptionText, or the text posted to the local simulator) or a placeholder.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings

log = logging.getLogger("raven.transcription")

PLACEHOLDER = "(Voicemail received; no transcript available. Listen to the recording.)"


def provider() -> str | None:
    s = get_settings()
    if s.deepgram_api_key:
        return "deepgram"
    if s.openai_api_key:
        return "whisper"
    return None


def _recording_auth() -> tuple[str, str] | None:
    s = get_settings()
    if s.twilio_account_sid and s.twilio_auth_token:
        return (s.twilio_account_sid, s.twilio_auth_token)
    return None


async def _download(url: str) -> tuple[bytes, str]:
    # Twilio serves the recording at RecordingUrl(.mp3); auth is required when "enforce HTTP auth" is on.
    if "twilio.com" in url and not url.endswith((".mp3", ".wav")):
        url += ".mp3"
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.get(url, auth=_recording_auth())
        resp.raise_for_status()
        return resp.content, resp.headers.get("content-type", "audio/mpeg")


async def transcribe(recording_url: str) -> str | None:
    """Returns the transcript, or None if no provider is configured or transcription failed."""
    s = get_settings()
    p = provider()
    if p is None or not recording_url:
        return None
    try:
        audio, content_type = await _download(recording_url)
        async with httpx.AsyncClient(timeout=120) as client:
            if p == "deepgram":
                resp = await client.post(
                    "https://api.deepgram.com/v1/listen?smart_format=true&punctuate=true",
                    headers={"Authorization": f"Token {s.deepgram_api_key}", "Content-Type": content_type},
                    content=audio,
                )
                resp.raise_for_status()
                alts = resp.json()["results"]["channels"][0]["alternatives"]
                text = alts[0]["transcript"] if alts else ""
            else:
                resp = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {s.openai_api_key}"},
                    data={"model": s.whisper_model},
                    files={"file": ("voicemail.mp3", audio, content_type)},
                )
                resp.raise_for_status()
                text = resp.json().get("text", "")
        return text.strip() or None
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        log.warning("Transcription via %s failed for %s: %s", p, recording_url, e)
        return None
