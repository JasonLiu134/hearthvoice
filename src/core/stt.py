"""Deepgram Flux STT helpers for Twilio's inbound phone audio."""

import base64
import asyncio
import binascii
import json
from dataclasses import dataclass, field
from collections.abc import AsyncIterator
from typing import Literal
from urllib.parse import urlencode

import websockets


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


class FluxSTTClient:
    """One live Flux connection, with one audio producer and one event consumer.

    Use as an async context manager. Feed raw mono 8 kHz mu-law bytes with
    send_audio while a separate task consumes events. Cancellation propagates;
    leaving the context closes the socket without replaying audio or retrying.
    """

    def __init__(self, api_key: str, options: FluxOptions | None = None) -> None:
        if not api_key.strip():
            raise ValueError("A Deepgram API key is required")
        self._api_key = api_key.strip()
        self.options = options or FluxOptions()
        self._socket = None
        self._connection = None
        self._audio = FluxAudioBuffer()
        self._send_lock = asyncio.Lock()
        self._reading = False
        self._finished = False

    async def __aenter__(self) -> "FluxSTTClient":
        if self._connection is not None:
            raise RuntimeError("Flux client is already connected")
        connection = websockets.connect(
            self.options.url,
            additional_headers={"Authorization": f"Token {self._api_key}"},
            open_timeout=5,
            close_timeout=5,
            max_queue=16,
        )
        self._socket = await connection.__aenter__()
        self._connection = connection
        self._audio = FluxAudioBuffer()
        self._finished = False
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        try:
            if self._connection is not None:
                await self._connection.__aexit__(exc_type, exc, traceback)
        finally:
            self._socket = None
            self._connection = None
            self._audio = FluxAudioBuffer()

    def _connected_socket(self):
        if self._socket is None:
            raise RuntimeError("Use FluxSTTClient inside an async with block")
        return self._socket

    async def send_audio(self, audio: bytes) -> None:
        """Batch raw audio into 80 ms frames, awaiting each send for backpressure."""
        if not isinstance(audio, bytes):
            raise TypeError("audio must be raw bytes, not base64 text or a WAV file")
        async with self._send_lock:
            socket = self._connected_socket()
            if self._finished:
                raise RuntimeError("Audio input is already finished")
            for chunk in self._audio.add(audio):
                await socket.send(chunk)

    async def flush_audio(self) -> None:
        """Send a trailing partial frame; this does not force an EndOfTurn event."""
        async with self._send_lock:
            socket = self._connected_socket()
            remainder = self._audio.flush()
            if remainder:
                await socket.send(remainder)

    async def finish_audio(self) -> None:
        """Finish normal input once; continue consuming events to drain results.

        The caller should bound draining with a timeout before leaving the
        context. Cancellation instead exits directly, dropping partial audio.
        """
        async with self._send_lock:
            socket = self._connected_socket()
            if self._finished:
                return
            self._finished = True
            remainder = self._audio.flush()
            if remainder:
                await socket.send(remainder)
            await socket.send(json.dumps({"type": "CloseStream"}))

    async def events(self) -> AsyncIterator[FluxTurnEvent]:
        """Yield actionable turn events; provider, transport and parse errors propagate."""
        socket = self._connected_socket()
        if self._reading:
            raise RuntimeError("Only one task may consume Flux events")
        self._reading = True
        try:
            async for raw in socket:
                event = parse_flux_message(raw)
                if event is not None:
                    yield event
        finally:
            self._reading = False
