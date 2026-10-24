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

## Implementation

`src/core/voice_pipeline.py` streams a POST request to `https://api.deepgram.com/v1/speak` with:

- Authorization: `Token <DEEPGRAM_API_KEY>`.
- Query parameters: `model=aura-2-thalia-en`, `encoding=mulaw`, `sample_rate=8000`, `container=none`.
- JSON body: `{"text": "the spoken response"}`.

The response is raw mono 8 kHz mu-law audio without a file header. The bridge forwards audio chunks as base64 Twilio `media` messages without transcoding. It checks HTTP status before forwarding audio. Caller speech cancels the synthesis stream and sends Twilio `clear` to discard buffered playback.

Only the spoken reply should be synthesized. Structured triage belongs to the dialogue service and must remain separate. The current dialogue is a temporary scripted demo, not integrated triage.

## Validation and latency

The automated tests mock the provider and verify the endpoint, authentication, output format, text payload, forwarding, and interruption behavior. They do not spend credits or establish live provider access.

`end_of_turn_to_first_audio_ms` measures Flux's end-of-turn event to the first audio chunk sent to Twilio. It excludes the earlier turn-detection delay and actual phone playback latency. The under-1.5-second full-turn target still needs a credentialed call test.

Before claiming the demo works, verify synthesis on a consenting participant's phone, pronunciation of pain scores and medication names, cancellation during playback, and a provider failure. Keep responses short and measure repeated calls rather than relying on vendor latency claims.

## Official references

- [Single text request API](https://developers.deepgram.com/reference/text-to-speech/speak-request)
- [Audio output settings](https://developers.deepgram.com/docs/tts-media-output-settings)
- [TTS latency](https://developers.deepgram.com/docs/text-to-speech-latency)
- [Twilio audio protocol](https://www.twilio.com/docs/voice/media-streams/websocket-messages)
