# Twilio voice demo setup

The phone integration is in `src/api/server.py` and `src/core/voice_pipeline.py`.
It uses Twilio bidirectional Media Streams, Deepgram Flux for end-of-turn speech
recognition, and Deepgram Aura-2 for streamed speech. The dialogue is a
short scripted demo in `reply_to`; it does not yet perform triage or send alerts.

## Configure

1. Create a Python virtual environment and install `requirements.txt`.
2. Copy `.env.example` to `.env` and fill in the values. Do not commit `.env`.
   `TWILIO_API_KEY_SID` starts with `SK`; its secret authorizes outbound REST
   calls. `TWILIO_AUTH_TOKEN` is the separate account auth token used to verify
   Twilio's webhook and WebSocket signatures. Set `TWILIO_FROM_NUMBER` to a
   Twilio voice-capable number.
3. Set `PUBLIC_BASE_URL` to the public HTTPS origin of your tunnel or deployment.
   Both `/twilio/voice` and `/twilio/media` must reach this server over that
   origin. Run:

   ```sh
   uvicorn src.api.server:app --host 0.0.0.0 --port 8000 --env-file .env
   ```

4. In the Twilio number's Voice configuration, set **A call comes in** to the
   HTTP POST webhook `https://YOUR-HOST/twilio/voice`. Signed webhook validation
   uses the exact `PUBLIC_BASE_URL` origin, so keep the configured URL identical.
5. To make an outbound demo call, send an authorized POST to the local server:

   ```sh
   curl -X POST http://localhost:8000/calls \
     -H 'Content-Type: application/json' \
     -H "X-Admin-Token: $ADMIN_TOKEN" \
     -d '{"to":"+15551234567"}'
   ```

Use a consenting test number. A Twilio trial account may require the destination
number to be verified first.

## Latency design

- Twilio's 8 kHz μ-law frames pass to Deepgram without transcoding. The bridge
  groups four usual 20 ms frames into the 80 ms frames recommended for Flux.
- Flux's `EndOfTurn` event starts a short reply immediately. The end-of-turn
  threshold is `0.8` to avoid cutting off a caller who pauses while speaking.
- Aura-2 streams raw mu-law at 8 kHz directly back to Twilio. Caller speech cancels
  synthesis and sends Twilio a `clear` event to discard buffered playback.
- Logs record `end_of_turn_to_first_audio_ms` per stream. This is one component
  of perceived turn latency; measure the full call path before claiming the
  1.5-second target.

The demo flow does not yet call a dialogue model, store transcripts, or alert a
caregiver. Those integrations need separate validation before real check-ins.
