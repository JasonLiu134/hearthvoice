"""Deepgram Flux STT helpers for Twilio's inbound phone audio."""

import base64
import binascii
import json
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlencode


@dataclass(frozen=True)
class FluxOptions:
    """Flux v2 settings for Twilio's raw mono μ-law audio at 8 kHz."""

    model: str = "flux-general-en"
    eot_threshold: float = 0.8

    def __post_init__(self) -> None:
        if not 0.5 <= self.eot_threshold <= 1.0:
            raise ValueError("eot_threshold must be between 0.5 and 1.0")

    @property
    def url(self) -> str:
        query = urlencode({
            "model": self.model,
            "encoding": "mulaw",
            "sample_rate": 8000,
            "eot_threshold": self.eot_threshold,
        })
        return f"wss://api.deepgram.com/v2/listen?{query}"


def validate_twilio_start(event: dict) -> str:
    """Validate incoming audio format and return the stream SID."""

    start = event.get("start") or {}
    audio_format = start.get("mediaFormat") or {}
    if (audio_format.get("encoding"), audio_format.get("sampleRate"), audio_format.get("channels")) != (
        "audio/x-mulaw", 8000, 1
    ):
        raise ValueError("Expected Twilio mono audio/x-mulaw at 8000 Hz")
    stream_sid = start.get("streamSid")
    if not stream_sid:
        raise ValueError("Missing Twilio stream SID")
    return stream_sid


def decode_inbound_media(event: dict) -> bytes:
    """Return raw caller audio, ignoring any outbound track."""

    media = event.get("media") or {}
    if media.get("track", "inbound") != "inbound":
        return b""
    payload = media.get("payload")
    if not isinstance(payload, str):
        raise ValueError("Twilio audio payload must be base64 text")
    try:
        return base64.b64decode(payload, validate=True)
    except binascii.Error as exc:
        raise ValueError("Invalid Twilio audio payload") from exc


@dataclass
class FluxAudioBuffer:
    """Batch Twilio's usual 20 ms frames into Flux's preferred 80 ms chunks."""

    _audio: bytearray = field(default_factory=bytearray)
    chunk_size: int = 640  # 8000 samples/s × 0.08 s × 1 byte/sample

    def add(self, audio: bytes) -> list[bytes]:
        self._audio.extend(audio)
        chunks = []
        while len(self._audio) >= self.chunk_size:
            chunks.append(bytes(self._audio[:self.chunk_size]))
            del self._audio[:self.chunk_size]
        return chunks

    def flush(self) -> bytes:
        remainder = bytes(self._audio)
        self._audio.clear()
        return remainder


@dataclass(frozen=True)
class FluxTurnEvent:
    kind: Literal["start", "end"]
    transcript: str = ""


class FluxSTTError(RuntimeError):
    """Deepgram reported an error on the active STT stream."""


def parse_flux_message(raw: str | bytes) -> FluxTurnEvent | None:
    """Return actionable turn events and surface Deepgram errors."""

    message = json.loads(raw)
    if message.get("type") == "Error":
        code = message.get("code") or "UNKNOWN"
        raise FluxSTTError(f"Deepgram STT error: {code}")
    if message.get("type") != "TurnInfo":
        return None
    if message.get("event") == "StartOfTurn":
        return FluxTurnEvent("start")
    if message.get("event") == "EndOfTurn":
        transcript = (message.get("transcript") or "").strip()
        return FluxTurnEvent("end", transcript) if transcript else None
    return None
