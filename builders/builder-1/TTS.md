# Pocket TTS setup and handoff

Pocket TTS is the selected local text-to-speech engine for HearthVoice. It runs on CPU and does not need a paid TTS API key. The `hearthvoice` Conda environment uses Python 3.12 and Pocket TTS 4.0.1; the package version is recorded in the root `requirements.txt`.

## Install

```bash
conda activate hearthvoice
python -m pip install -r requirements.txt
python -m pip check
```

To install only the speech engine:

```bash
conda activate hearthvoice
python -m pip install pocket-tts==4.0.1
```

Installing the package and downloading the model are separate steps. The first synthesis or server startup downloads model assets, so it needs network access and takes longer than a warmed-up request. If Hugging Face returns an access error, visit the [model page](https://huggingface.co/kyutai/pocket-tts), accept the access conditions with your own account, and authenticate locally with `hf auth login`. Do not commit authentication tokens.

## Run the local API

```bash
conda activate hearthvoice
pocket-tts serve --host localhost --port 8001
```

Open <http://localhost:8001> to try the web interface. Port 8001 avoids competing with the HearthVoice backend on port 8000. Keep the server local for development. Use the installed CLI's `pocket-tts serve --help` for available model and voice options.

For a standalone audio sample:

```bash
conda activate hearthvoice
pocket-tts generate --text "Hello, this is HearthVoice. How are you feeling today?"
```

The default output is `tts_output.wav` in the current directory. Treat generated samples as local artifacts rather than source files.

## Python integration

The backend can call Pocket directly rather than running a second HTTP service:

```python
from pocket_tts import TTSModel

model = TTSModel.load_model()
voice_state = model.get_state_for_audio_prompt("alba")
audio = model.generate_audio(voice_state, "How are you feeling today?")
```

This example generates a complete waveform; it is a setup example, not the streaming call implementation. Use the streaming Python API for the live bridge. Load the model and voice state once at application startup, warm them before accepting calls, and keep inference off the asynchronous WebSocket event loop. Define how concurrent calls share model capacity before enabling multiple calls.

## Remaining call integration

The current `src/core/voice_pipeline.py` still synthesizes through ElevenLabs. Installing Pocket does not change that behavior, and the existing ElevenLabs environment variables remain relevant to that implementation.

1. Replace the ElevenLabs synthesis request with Pocket's streaming generation while preserving Deepgram Flux transcription and interruption handling.
2. Read the model's actual sample rate, resample PCM to 8 kHz, and encode it as raw mono mu-law. Twilio expects base64 audio payloads without WAV headers.
3. On caller interruption, stop forwarding stale generation chunks and clear Twilio's playback buffer. Ensure background inference does not block receipt of caller audio.
4. Speak only the dialogue agent's `spoken_response`; keep structured triage separate.
5. Exercise a real phone call and compare pronunciation after conversion to phone audio.

## Latency and acceptance

- Measure cold model loading separately from warm synthesis.
- Measure text submission to the first usable audio chunk, including conversion to Twilio's format; report median and slow-tail results over repeated short prompts.
- Measure caller end-of-speech to first audible reply separately. The project's under-1.5-second target covers the full pipeline, not TTS alone.
- Test medication names, dates, pain scores, negation, pauses, and interruption during playback.
- Keep prompts short and cache fixed greetings where useful.

Upstream reports approximately 200 ms to the first audio chunk and approximately six times real-time generation on a MacBook Air M4. These are upstream figures, not measured HearthVoice results. A successful package import does not prove model access, synthesis, or phone latency.

### Local installation verification (2026-10-24)

Pocket TTS 4.0.1 imported successfully in the Python 3.12 `hearthvoice` environment, `serve --help` ran successfully, and `pip check` reported no broken requirements. The default English model downloaded and synthesized "Hello, this is HearthVoice. How are you feeling today?" without requiring a Hugging Face login in this run.

The CLI reported 3.28 seconds of generated speech in 473 ms (6.93 times real time), excluding initial model loading/download. The resulting `/private/tmp/tts_output.wav` was verified as mono, 24 kHz, 16-bit PCM with a total file duration of 3.48 seconds. This single run measures generation throughput, not time to first audio, audible quality, or live phone latency; those checks remain pending.

## References and licenses

- [Upstream repository and Python examples](https://github.com/kyutai-labs/pocket-tts)
- [HTTP server documentation](https://kyutai-labs.github.io/pocket-tts/CLI%20Commands/serve/)
- [Python API documentation](https://kyutai-labs.github.io/pocket-tts/API%20Reference/python-api/)
- [Model weights and access conditions](https://huggingface.co/kyutai/pocket-tts)
- [Voice assets and their licenses](https://huggingface.co/kyutai/tts-voices)
- [Twilio audio protocol](https://www.twilio.com/docs/voice/media-streams/websocket-messages)

Pocket's code uses the MIT license; the model page labels its weights CC-BY-4.0. Check the chosen voice's terms and preserve required attribution.
