# Speech-to-text handoff

The live voice pipeline uses **Deepgram Flux** on `/v2/listen` for caller transcription and turn detection. This follows the current `src/core/voice_pipeline.py` implementation. The earlier hackathon brief proposed Nova-2; switching models changes both the endpoint and the response schema, so the two cannot be swapped by changing only a model name.

`src/core/stt.py` owns the STT settings and parsing boundary:

- `FluxOptions().url` requests raw mono μ-law at 8 kHz, matching Twilio Media Streams.
- `validate_twilio_start` checks the stream format and SID.
- `decode_inbound_media` forwards only caller audio and decodes its base64 payload.
- `FluxAudioBuffer` groups audio into 80 ms chunks before forwarding to Flux.
- `parse_flux_message` maps `StartOfTurn` and `EndOfTurn` to simple events and raises on Deepgram errors. Only a nonempty `EndOfTurn` transcript should reach the dialogue agent.

The API key stays in the server environment and is sent only in the Deepgram WebSocket authorization header. Keep one Deepgram connection per call. The Twilio/TTS handlers remain responsible for audio playback and interruption.

Unit tests cover the audio format, batching, turn events, and error response. Live transcription and latency still need a credentialed phone-call test.

## Reusable streaming client

`FluxSTTClient` now provides a transport-independent async client. It takes a
Deepgram key explicitly so the application remains responsible for loading `.env`.
It holds one Flux WebSocket open per context, batches raw audio into 640-byte
frames internally, and yields `FluxTurnEvent("start")` or
`FluxTurnEvent("end", transcript)` without waiting for the connection to close.

```python
import asyncio
import os

from src.core.stt import FluxSTTClient


async def transcribe(audio_chunks, on_turn):
    # audio_chunks yields raw mono 8 kHz mu-law bytes, including silence.
    async with FluxSTTClient(os.environ["DEEPGRAM_API_KEY"]) as stt:
        async def read_turns():
            async for event in stt.events():
                await on_turn(event)

        reader = asyncio.create_task(read_turns())
        try:
            async for audio in audio_chunks:
                await stt.send_audio(audio)
            await stt.finish_audio()
            await asyncio.wait_for(reader, timeout=5)
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
```

Run sending and event consumption concurrently. Keep `on_turn` fast: signal
interruption or schedule an agent response rather than awaiting the entire
Ollama/TTS response inside the receive loop. Only one task may consume `events()`.

- `send_audio(bytes)` applies backpressure through awaited sends; callers should
  provide small live chunks rather than entire recordings. Do not send WAV
  headers or base64 text. Already-decoded bytes can go directly from the Twilio
  owner's transport to this method; do not batch them a second time.
- Keep sending captured silence so Flux can detect the end of a turn. No
  application-level `KeepAlive` message is added to the Flux v2 protocol.
- `flush_audio()` sends a partial local frame without ending the stream or
  forcing a turn. `finish_audio()` sends the remainder and `CloseStream` once,
  then rejects further audio. Continue reading for final results with a bounded
  timeout.
- Leaving the context closes the socket. Cancellation propagates and drops any
  unsent partial frame; provider errors, malformed messages, and abnormal socket
  failures also propagate. There is no automatic retry or audio replay.

The existing `voice_pipeline.py` still owns its original connection and helper
calls. This addition does not change the teammate-owned call bridge; its owner
can adopt the client through the interface above. Ollama and TTS are not called
by this module.

Tests use mocked connections to cover concurrent sending/receiving, batching,
normal input completion, provider and transport errors, and cancellation cleanup.
No credentialed transcription or end-to-end latency is claimed by these tests.

Protocol references: [Flux v2 API](https://developers.deepgram.com/reference/speech-to-text/listen-flux)
and [Flux quickstart](https://developers.deepgram.com/docs/flux/quickstart).
