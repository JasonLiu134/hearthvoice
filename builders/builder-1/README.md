# Builder 1: Telephony and voice pipeline

## Goal

Deliver a phone number that supports a live spoken conversation on a regular landline or mobile phone. The person receiving the call needs no app, account, or smartphone.

This is a work plan derived from the [project specification](../../README.md). Integrations are not implemented yet, and provider choices remain open.

## Responsibilities

- Connect inbound and outbound phone calls using Twilio Voice or Vapi.
- Stream call audio through speech recognition, dialogue, and speech synthesis.
- Integrate Deepgram Flux speech recognition and the selected Pocket TTS speech synthesis engine. See [STT handoff](STT.md) and [Pocket TTS setup](TTS.md); the current call bridge still uses ElevenLabs until Pocket streaming is connected.
- Support patient turn-taking and interruption handling.
- Measure turn latency against the target of under 1.5 seconds; report actual results separately from the target.

## Planned implementation

- `src/core/voice_pipeline.py`: audio integration.
- Coordinate with Builder 3 on `src/services/twilio_client.py` and call handlers; agree on ownership before editing shared files.

These paths refer to the proposed application layout at the repository root. This builder folder holds documentation.

## Handoffs

- Send recognized speech and call context to Builder 2's dialogue interface.
- Play Builder 2's `spoken_response` through text-to-speech; keep triage data out of spoken output unless dialogue explicitly includes it.
- Share provider call identifiers and lifecycle events with Builder 3.
- Coordinate recording or transcript persistence with Builder 4 after consent and retention requirements are defined.

## Demo acceptance

- A consenting demo participant can answer an outbound call and complete a spoken exchange.
- The phone number also supports an inbound check-in.
- The agent asks one question at a time and handles interruptions.
- The missed-medication and pain scenario reaches the dialogue service successfully.
- Capture latency measurements and prepare a fallback recording or mock event with Builder 4.

## Open decisions

Choose the call gateway and audio transport with the team. Define behavior for audio failures and disconnects, and avoid logging sensitive audio or transcripts before data handling rules are agreed.
