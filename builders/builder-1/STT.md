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
