"""Low-latency Twilio -> Deepgram Flux -> ElevenLabs phone audio bridge."""

import asyncio
import base64
import logging
import time
from dataclasses import dataclass

import httpx
import websockets
from fastapi import WebSocket, WebSocketDisconnect

from src.core.stt import (
    FluxAudioBuffer,
    FluxOptions,
    decode_inbound_media,
    parse_flux_message,
    validate_twilio_start,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpeechConfig:
    deepgram_api_key: str
    elevenlabs_api_key: str
    elevenlabs_voice_id: str
    flux_model: str = "flux-general-en"

    @property
    def deepgram_url(self) -> str:
        return FluxOptions(model=self.flux_model).url


def reply_to(transcript: str, turn: int) -> str:
    """Temporary, bounded demo dialogue; triage belongs to the agent service."""
    if turn == 0:
        return "Thank you for sharing. Have you eaten today?"
    if turn == 1:
        return "Thank you. Have you taken your medication today?"
    if turn == 2:
        return "Thank you. Are you having any pain today?"
    return "Thank you for checking in. Is there anything else you would like to tell me?"


class CallSession:
    def __init__(self, twilio: WebSocket, config: SpeechConfig) -> None:
        self.twilio = twilio
        self.config = config
        self.stream_sid: str | None = None
        self.turn = 0
        self.speech_task: asyncio.Task[None] | None = None
        self.send_lock = asyncio.Lock()
        self.last_eot_at: float | None = None

    async def send_twilio(self, message: dict) -> None:
        async with self.send_lock:
            await self.twilio.send_json(message)

    async def interrupt(self) -> None:
        if self.speech_task and not self.speech_task.done():
            self.speech_task.cancel()
            await asyncio.gather(self.speech_task, return_exceptions=True)
        # Twilio may still have buffered audio after the TTS request has ended.
        if self.stream_sid:
            await self.send_twilio({"event": "clear", "streamSid": self.stream_sid})

    def start_speech(self, client: httpx.AsyncClient, text: str) -> None:
        self.speech_task = asyncio.create_task(self.speak(client, text))
        self.speech_task.add_done_callback(self._speech_done)

    @staticmethod
    def _speech_done(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            log.error("Speech synthesis failed: %s", type(task.exception()).__name__)

    async def speak(self, client: httpx.AsyncClient, text: str) -> None:
        if not self.stream_sid:
            return
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.config.elevenlabs_voice_id}/stream"
        async with client.stream(
            "POST",
            url,
            params={"output_format": "ulaw_8000"},
            headers={"xi-api-key": self.config.elevenlabs_api_key},
            json={"text": text, "model_id": "eleven_flash_v2_5"},
        ) as response:
            response.raise_for_status()
            first_chunk = True
            async for audio in response.aiter_bytes(chunk_size=320):
                if not audio:
                    continue
                if first_chunk and self.last_eot_at is not None:
                    log.info("call=%s end_of_turn_to_first_audio_ms=%.0f", self.stream_sid,
                             (time.monotonic() - self.last_eot_at) * 1000)
                    first_chunk = False
                await self.send_twilio({
                    "event": "media",
                    "streamSid": self.stream_sid,
                    "media": {"payload": base64.b64encode(audio).decode("ascii")},
                })

    async def receive_twilio(self, deepgram: websockets.ClientConnection, client: httpx.AsyncClient) -> None:
        inbound_audio = FluxAudioBuffer()
        try:
            while True:
                event = await self.twilio.receive_json()
                kind = event.get("event")
                if kind == "start":
                    self.stream_sid = validate_twilio_start(event)
                    self.start_speech(client, "Hello, this is HearthVoice. How are you feeling today?")
                elif kind == "media":
                    if not self.stream_sid:
                        continue
                    for chunk in inbound_audio.add(decode_inbound_media(event)):
                        await deepgram.send(chunk)
                elif kind == "stop":
                    remainder = inbound_audio.flush()
                    if remainder:
                        await deepgram.send(remainder)
                    return
        except WebSocketDisconnect:
            return

    async def receive_deepgram(self, deepgram: websockets.ClientConnection, client: httpx.AsyncClient) -> None:
        async for raw in deepgram:
            event = parse_flux_message(raw)
            if event is None:
                continue
            if event.kind == "start":
                await self.interrupt()
            elif event.kind == "end":
                self.last_eot_at = time.monotonic()
                await self.interrupt()
                response = reply_to(event.transcript, self.turn)
                self.turn += 1
                self.start_speech(client, response)

    async def run(self) -> None:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5)) as client:
            async with websockets.connect(
                self.config.deepgram_url,
                additional_headers={"Authorization": f"Token {self.config.deepgram_api_key}"},
                open_timeout=5,
            ) as deepgram:
                tasks = [
                    asyncio.create_task(self.receive_twilio(deepgram, client)),
                    asyncio.create_task(self.receive_deepgram(deepgram, client)),
                ]
                try:
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    if self.speech_task:
                        self.speech_task.cancel()
                        await asyncio.gather(self.speech_task, return_exceptions=True)
