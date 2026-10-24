# Deepgram Aura-2 text-to-speech

HearthVoice uses Deepgram Aura-2 for TTS and Deepgram Flux for STT. Both use the same `DEEPGRAM_API_KEY`. Twilio carries phone audio and handles calls and SMS.

## Setup

```bash
conda activate hearthvoice
python -m pip install -r requirements.txt
```

Set these values in the root `.env` file:

```dotenv
DEEPGRAM_API_KEY=your_deepgram_key
DEEPGRAM_TTS_MODEL=aura-2-thalia-en
```

The model setting is optional; `aura-2-thalia-en` is the default. Get a key from the [Deepgram Console](https://console.deepgram.com/). Keep `.env` out of Git. Run the backend with `uvicorn src.api.server:app --env-file .env`; see [Twilio setup](TWILIO.md) for the remaining call configuration.

## Persistent streaming client

`src/core/tts.py` provides `AuraTTSClient`, independent of Twilio and Ollama.
Open one connection per call and run one event consumer concurrently with text
generation:

```python
import asyncio
import os

from src.core.tts import AuraTTSClient


async def speak_phrases(phrases, on_audio):
    async with AuraTTSClient(os.environ["DEEPGRAM_API_KEY"]) as tts:
        async def receive():
            async for event in tts.events():
                if event.kind == "audio":
                    await on_audio(event.audio)
                elif event.kind == "warning":
                    raise RuntimeError(f"TTS warning: {event.code}")
                elif event.kind == "flushed":
                    return
            raise RuntimeError("TTS closed before completing the reply")

        reader = asyncio.create_task(receive())
        try:
            async for phrase in phrases:
                await tts.send_text(phrase)
            await tts.flush()
            await asyncio.wait_for(reader, timeout=30)
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
```

This example handles one reply; keep the context and event consumer alive across
turns in the eventual coordinator. Pass the selected `AuraOptions` explicitly
when configuring a different voice; the client itself does not read `.env`.

- `send_text(text)` sends a `Speak` message. Buffer Ollama tokens into meaningful
  phrases; supply speech text only, not raw JSON or reasoning.
- `events()` yields binary audio immediately, plus `flushed`, `cleared`, and
  `warning` events. Only one consumer is allowed. Connection, JSON, and provider
  errors propagate; there is no automatic retry or replay.
- `flush()` asks Aura to synthesize remaining text. Use it at reply completion,
  not for each token. Deepgram documents a limit of 20 flushes per 60 seconds.
- `clear()` sends `Clear` and immediately mutes subsequently received audio
  until a `cleared` event. Do not send new text until that acknowledgement is
  consumed; bound the wait with a timeout in the coordinator. Also cancel stale
  Ollama output and clear downstream playback separately. Audio already handed
  to the transport cannot be recalled by this module.
- Context exit closes the socket; cancellation propagates. Keep send/receive
  tasks scoped to that context and cancel them before leaving it.

The default output is raw mono 8 kHz mu-law for phone transport. No `container`
parameter is sent to the WebSocket API; its audio frames have no WAV header.
For local playback, use `AuraOptions(encoding="linear16", sample_rate=24000)`
and wrap the returned PCM bytes in a WAV file.

## Local speech check

From the repository root:

```bash
conda activate hearthvoice
python -m scripts.test_tts --text "Hello, how are you feeling today?"
afplay /private/tmp/hearthvoice-tts.wav
```

The command loads the root `.env`, synthesizes through the WebSocket client,
and saves a mono 24 kHz 16-bit PCM WAV. Use `--output` for another path and
`--timeout` for a different deadline. It uses Deepgram credits but places no
phone call. It prints time to first received audio after connection and total
time including connection. Playback starts after file generation, so this
command does not measure live audible response latency.

The unit tests are in `tests/test_tts.py` and mock all provider traffic:

```bash
python -m unittest discover -s tests -p 'test_tts.py' -v
```

The agent module remains a placeholder. The next integration is Ollama streamed
text into `send_text()` and a local audio player; microphone input can then use
`FluxSTTClient`. Neither is wired by this TTS-only change.

## Existing call bridge

`src/core/voice_pipeline.py` streams a POST request to `https://api.deepgram.com/v1/speak` with:

- Authorization: `Token <DEEPGRAM_API_KEY>`.
- Query parameters: `model=aura-2-thalia-en`, `encoding=mulaw`, `sample_rate=8000`, `container=none`.
- JSON body: `{"text": "the spoken response"}`.

The response is raw mono 8 kHz mu-law audio without a file header. The bridge forwards audio chunks as base64 Twilio `media` messages without transcoding. It checks HTTP status before forwarding audio. Caller speech cancels the synthesis stream and sends Twilio `clear` to discard buffered playback.

Only the spoken reply should be synthesized. Structured triage belongs to the dialogue service and must remain separate. The current dialogue is a temporary scripted demo, not integrated triage.

## Validation and latency

The automated tests mock the provider and verify authentication, streaming,
audio formats, flush/clear ordering, interruption gating, cancellation, errors,
and WAV generation. They do not spend credits or establish live provider access.

`end_of_turn_to_first_audio_ms` measures Flux's end-of-turn event to the first audio chunk sent to Twilio. It excludes the earlier turn-detection delay and actual phone playback latency. The under-1.5-second full-turn target still needs a credentialed call test.

Before claiming the demo works, verify synthesis on a consenting participant's phone, pronunciation of pain scores and medication names, cancellation during playback, and a provider failure. Keep responses short and measure repeated calls rather than relying on vendor latency claims.

## Official references

- [Single text request API](https://developers.deepgram.com/reference/text-to-speech/speak-request)
- [Streaming WebSocket API](https://developers.deepgram.com/reference/text-to-speech/speak-streaming)
- [Flush limits](https://developers.deepgram.com/docs/tts-ws-flush)
- [Clear acknowledgement](https://developers.deepgram.com/docs/tts-ws-clear)
- [Audio output settings](https://developers.deepgram.com/docs/tts-media-output-settings)
- [TTS latency](https://developers.deepgram.com/docs/text-to-speech-latency)
- [Twilio audio protocol](https://www.twilio.com/docs/voice/media-streams/websocket-messages)
