"""Low-latency Twilio -> Deepgram Flux -> Aura-2 phone audio bridge."""

import asyncio
import base64
import logging
import re
import time
from contextlib import aclosing
from dataclasses import dataclass

import httpx
import websockets
from fastapi import WebSocket, WebSocketDisconnect
from src.core.agent import END_MARKER, HearthAgent
from src.services import phone_alerts, database

from src.core.stt import (
    FluxAudioBuffer,
    FluxOptions,
    decode_inbound_media,
    parse_flux_message,
    validate_twilio_start,
)

log = logging.getLogger(__name__)
active_sessions = {}


@dataclass(frozen=True)
class SpeechConfig:
    deepgram_api_key: str
    tts_model: str = "aura-2-thalia-en"
    flux_model: str = "flux-general-en"

    @property
    def deepgram_url(self) -> str:
        return FluxOptions(model=self.flux_model).url


class CallSession:
    def __init__(self, twilio: WebSocket, config: SpeechConfig) -> None:
        self.twilio = twilio
        self.config = config
        self.stream_sid: str | None = None
        self.turn = 0
        self.speech_task: asyncio.Task[None] | None = None
        self.send_lock = asyncio.Lock()
        self.last_eot_at: float | None = None
        self.agent = HearthAgent()
        self.history: list[dict] = []
        self.transcript: list[dict] = []
        self.user = {}  # No profile lookup is wired to the phone session yet.
        self.caller = ""
        self.call_sid = ""
        self.alert_tasks = set()
        self.finished = asyncio.Event()

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

    async def respond(self, client: httpx.AsyncClient, transcript: str) -> None:
        self.history.append({"role": "user", "content": transcript})
        self.transcript.append({"role": "user", "content": transcript})
        del self.history[:-11]
        if phone_alerts.enabled() and self.call_sid:
            task = asyncio.create_task(phone_alerts.assess_call(
                self.call_sid, self.caller, self.turn, [dict(item) for item in self.history]))
            self.alert_tasks.add(task)
            task.add_done_callback(self.alert_tasks.discard)
        spoken = []
        try:
            async with asyncio.timeout(45):
                pending = ""
                async with aclosing(self.agent.reply_stream(self.history, self.user)) as stream:
                    async for chunk in stream:
                        pending += chunk
                        while match := re.search(r"[.!?](?:\s|$)", pending):
                            phrase, pending = pending[:match.end()], pending[match.end():]
                            phrase = phrase.replace(END_MARKER, "").strip()
                            if phrase:
                                await self.speak(client, phrase)
                                spoken.append(phrase)
                    tail = pending.replace(END_MARKER, "").strip()
                    if tail:
                        await self.speak(client, tail)
                        spoken.append(tail)
                if not spoken:
                    raise RuntimeError("Agent returned no spoken text")
                log.info("Phone agent reply completed")
        except asyncio.CancelledError:
            log.info("Phone agent reply interrupted")
            raise
        except Exception as exc:
            log.error("Phone agent reply failed: %s", type(exc).__name__)
            fallback = "I'm having trouble responding right now. Please try again in a moment."
            await self.speak(client, fallback)
            spoken.append(fallback)
        finally:
            # Only record phrases actually submitted to Twilio, excluding pending tokens.
            if spoken:
                self.history.append({"role": "assistant", "content": " ".join(spoken)})
                self.transcript.append(dict(self.history[-1]))
            task = asyncio.create_task(database.save_call(self.call_sid, self.caller, list(self.transcript)))
            self.alert_tasks.add(task)
            task.add_done_callback(self.alert_tasks.discard)

    @staticmethod
    def _speech_done(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            log.error("Speech synthesis failed: %s", type(task.exception()).__name__)

    async def speak(self, client: httpx.AsyncClient, text: str) -> None:
        if not self.stream_sid:
            return
        async with client.stream(
            "POST",
            "https://api.deepgram.com/v1/speak",
            params={
                "model": self.config.tts_model,
                "encoding": "mulaw",
                "sample_rate": 8000,
                "container": "none",
            },
            headers={"Authorization": f"Token {self.config.deepgram_api_key}"},
            json={"text": text},
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
                    self.call_sid = event["start"].get("callSid", "")
                    if self.call_sid:
                        active_sessions[self.call_sid] = self
                    self.caller = (event["start"].get("customParameters") or {}).get("caller", "")
                    greeting = self.agent.opening_line(self.user)
                    self.transcript.append({"role": "assistant", "content": greeting})
                    self.start_speech(client, greeting)
                    task = asyncio.create_task(database.save_call(
                        self.call_sid, self.caller, list(self.transcript)))
                    self.alert_tasks.add(task)
                    task.add_done_callback(self.alert_tasks.discard)
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
                self.turn += 1
                self.speech_task = asyncio.create_task(self.respond(client, event.transcript))
                self.speech_task.add_done_callback(self._speech_done)

    async def run(self) -> None:
        try:
            await self._run_connections()
        finally:
            try:
                if self.alert_tasks:
                    await asyncio.gather(*self.alert_tasks, return_exceptions=True)
                await database.save_call(self.call_sid, self.caller, list(self.transcript))
                await self.agent.close()
            finally:
                active_sessions.pop(self.call_sid, None)
                self.finished.set()

    async def _run_connections(self) -> None:
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
