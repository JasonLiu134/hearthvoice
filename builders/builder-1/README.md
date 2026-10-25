# Builder 1: Telephony and voice pipeline

## Goal

Deliver a phone number that supports a live spoken conversation on a regular landline or mobile phone. The person receiving the call needs no app, account, or smartphone.

The active implementation is in `src/api/server.py` and `src/core/voice_pipeline.py`. It supports a Twilio trial Gather/Say path and a separate Deepgram Media Streams path. The trial path is the currently configured demo; both paths use the local llama.cpp server for dialogue.

## Responsibilities

- Connect inbound phone calls using Twilio Voice. Outbound Calls API support is implemented, but trial accounts may reject custom outbound webhooks.
- Stream call audio through speech recognition, dialogue, and speech synthesis.
- Integrate Deepgram Flux speech recognition and Deepgram Aura-2 speech synthesis using one Deepgram API key. See [STT handoff](STT.md) and [TTS setup](TTS.md).
- Support patient turn-taking. Barge-in interruption handling is implemented in the streaming path; trial Gather/Say is turn-based and has no barge-in.
- Log the streaming path's end-of-turn to first-audio component. The README's under-1.5-second target has not been established by a credentialed end-to-end measurement.

## Implemented paths

- `src/core/voice_pipeline.py`: Twilio Media Streams, Deepgram Flux turn events, Aura-2 audio, llama.cpp replies, and streaming barge-in.
- `src/core/trial_voice.py`: four-turn Gather/Say trial conversation with a three-second model deadline and in-memory retries.
- `src/core/stt.py` and `src/core/tts.py`: reusable provider client boundaries with mocked unit tests.
- `src/api/server.py`: signed Twilio webhooks, call status callbacks, Supabase call snapshots, and optional post-call summary SMS to the caller.

Builder 3's scheduler, profiles, missed-call rules, and caregiver alerts remain a separate application path; they are not connected to the active phone server.

These paths refer to the proposed application layout at the repository root. This builder folder holds documentation.

## Handoffs

- Send recognized speech and call context to Builder 2's dialogue interface.
- The active agent streams plain spoken text to TTS. The README's separate `spoken_response`/`triage` JSON contract is not yet used by the live phone path.
- Twilio provider identifiers and transcripts are persisted through the active server's Supabase logger when configured.

## Demo acceptance

- A consenting demo participant can answer an outbound call and complete a spoken exchange.
- The phone number also supports an inbound check-in.
- The agent asks one question at a time and handles interruptions.
- The missed-medication and pain scenario can be exercised against the dialogue prompt, but baseline comparison and deterministic triage are not wired into the active call.
- Tests use mocked Twilio and Deepgram boundaries; they do not prove live account permissions or phone audio.

## Open decisions

The active gateway and providers are Twilio, Deepgram Flux/Aura-2, and local llama.cpp. Define consent, retention, and access rules before using real transcripts. Custom SMS summaries are subject to Twilio trial restrictions; the current post-call SMS sends to the caller, not a caregiver.
