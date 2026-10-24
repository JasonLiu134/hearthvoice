"""Persistent Deepgram Aura-2 speech synthesis, independent of phone transport."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlencode

import websockets


@dataclass(frozen=True)
class AuraOptions:
    model: str = "aura-2-thalia-en"
    encoding: Literal["mulaw", "linear16"] = "mulaw"
    sample_rate: int = 8000

    def __post_init__(self) -> None:
        rates = {"mulaw": (8000, 16000), "linear16": (8000, 16000, 24000, 32000, 48000)}
        if self.encoding not in rates or self.sample_rate not in rates[self.encoding]:
            raise ValueError("Unsupported Aura encoding/sample-rate combination")
        if not self.model.startswith("aura-2-"):
            raise ValueError("Choose an Aura-2 voice model")

    @property
    def url(self) -> str:
        return "wss://api.deepgram.com/v1/speak?" + urlencode({
            "model": self.model,
            "encoding": self.encoding,
            "sample_rate": self.sample_rate,
        })


@dataclass(frozen=True)
class AuraEvent:
    kind: Literal["audio", "flushed", "cleared", "warning"]
    audio: bytes = b""
    code: str = ""


class AuraTTSError(RuntimeError):
    """Fatal provider response; no raw provider text or credentials included."""


class AuraTTSClient:
    """One connection per call, with one event consumer and serialized writers.

    Send phrase-sized text while consuming events concurrently. After clear(),
    keep consuming events and wait for 'cleared' before sending a new reply.
    Audio received before that acknowledgement is discarded. Downstream playback
    must be cleared separately; already-delivered audio cannot be recalled.
    """

    def __init__(self, api_key: str, options: AuraOptions | None = None) -> None:
        if not api_key.strip():
            raise ValueError("A Deepgram API key is required")
        self._api_key = api_key.strip()
        self.options = options or AuraOptions()
        self._socket = None
        self._connection = None
        self._send_lock = asyncio.Lock()
        self._reading = False
        self._clearing = False

    async def __aenter__(self) -> "AuraTTSClient":
        if self._connection is not None:
            raise RuntimeError("Aura client is already connected")
        connection = websockets.connect(
            self.options.url,
            additional_headers={"Authorization": f"Token {self._api_key}"},
            open_timeout=5,
            close_timeout=5,
            max_queue=16,
        )
        self._socket = await connection.__aenter__()
        self._connection = connection
        self._clearing = False
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        try:
            if self._connection is not None:
                await self._connection.__aexit__(exc_type, exc, traceback)
        finally:
            self._socket = None
            self._connection = None
            self._clearing = False

    def _connected_socket(self):
        if self._socket is None:
            raise RuntimeError("Use AuraTTSClient inside an async with block")
        return self._socket

    async def send_text(self, text: str) -> None:
        """Queue spoken text; callers own phrase buffering and text normalization."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Speech text must be a nonempty string")
        async with self._send_lock:
            socket = self._connected_socket()
            if self._clearing:
                raise RuntimeError("Wait for the cleared event before sending text")
            await socket.send(json.dumps({"type": "Speak", "text": text}))

    async def flush(self) -> None:
        """Commit buffered text, normally once at response end; rate limits apply."""
        async with self._send_lock:
            socket = self._connected_socket()
            if self._clearing:
                raise RuntimeError("Wait for the cleared event before flushing")
            await socket.send(json.dumps({"type": "Flush"}))

    async def clear(self) -> None:
        """Stop pending synthesis; audio stays muted until Cleared is consumed."""
        async with self._send_lock:
            socket = self._connected_socket()
            if not self._clearing:
                self._clearing = True
                try:
                    await socket.send(json.dumps({"type": "Clear"}))
                except BaseException:
                    self._clearing = False
                    raise

    async def events(self) -> AsyncIterator[AuraEvent]:
        """Yield audio immediately and control events without buffering a whole turn."""
        socket = self._connected_socket()
        if self._reading:
            raise RuntimeError("Only one task may consume Aura events")
        self._reading = True
        try:
            async for raw in socket:
                if isinstance(raw, bytes):
                    if raw and not self._clearing:
                        yield AuraEvent("audio", audio=raw)
                    continue
                message = json.loads(raw)
                kind = message.get("type")
                if kind == "Error":
                    raise AuraTTSError(f"Deepgram TTS error: {message.get('code') or 'UNKNOWN'}")
                if kind == "Cleared":
                    self._clearing = False
                    yield AuraEvent("cleared")
                elif kind == "Flushed" and not self._clearing:
                    yield AuraEvent("flushed")
                elif kind == "Warning":
                    yield AuraEvent("warning", code=message.get("code") or "UNKNOWN")
        finally:
            self._reading = False
