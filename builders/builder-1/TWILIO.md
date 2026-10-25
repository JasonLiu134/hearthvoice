# Twilio voice demo setup

The phone integration is in `src/api/server.py` and `src/core/voice_pipeline.py`.
It has two modes. Trial mode uses Twilio Gather/Say and the local llama.cpp
HearthAgent. Streaming mode uses Twilio bidirectional Media Streams, Deepgram
Flux for end-of-turn recognition, local llama.cpp dialogue, and Deepgram Aura-2
speech. The active post-call SMS summary goes to the caller; it is separate from
the optional concern-alert service.

## Configure

### Trial-compatible inbound conversation

If you have purchased a configurable Voice number, set `TWILIO_FROM_NUMBER`
to that number and `TWILIO_VOICE_MODE=trial` in `.env`. Configure the owned
number's incoming webhook without placing a call:

```sh
python -m scripts.configure_phone
```

This verifies ownership and Voice capability, then sets the number's `VoiceUrl`
to `PUBLIC_BASE_URL/twilio/voice` using POST. It requires the account Auth Token
and changes that number's existing Voice webhook. Restart the API, keep the
public tunnel and llama-server running, then dial the purchased number from
a verified phone. Rerun the configuration command if the public hostname changes.

The trial mode uses Twilio speech recognition and speech synthesis, with
llama.cpp generating replies. Deepgram and Media Streams are not used.

1. Start llama-server as described in [SETUP.md](../../SETUP.md).
2. Start the voice API and your public tunnel, with `PUBLIC_BASE_URL` set to
   that tunnel's HTTPS origin:

   ```sh
   python -m uvicorn src.api.server:app --host 127.0.0.1 --port 8000 --env-file .env
   ngrok http 8000
   ```

   Run those commands in separate terminals. Restart the API after editing `.env`.
3. In Twilio Console's Voice **Try out Voice → Inbound → Custom** flow,
   configure the custom webhook to POST to:

   ```text
   https://YOUR-PUBLIC-HOST/twilio/trial/voice
   ```

4. Save, then dial the displayed trial number from the verified phone selected
   in the Console. Speak and pause. Twilio posts `SpeechResult` to our server,
   llama.cpp generates the reply, and Twilio reads it aloud and listens again.

Alternatively, setting `TWILIO_VOICE_MODE=trial` makes `/twilio/voice` use the
same Gather/Say implementation. It does not change outbound trial restrictions:
the trial Calls API may still reject a custom webhook URL. This mode is intended
for the supported custom inbound trial flow, not the supplied outbound template.

The demo ends after four turns (including silence) or the model's `[END]` marker,
to stay below the trial's action-hop limit. Model generation is bounded to three
seconds to fit the documented five-second TwiML fetch deadline; a slow model
produces a spoken fallback. This is a turn-based demo, with no streaming or
barge-in. The full reply must be ready before Twilio receives the next TwiML.

History and retry responses live in memory for up to ten minutes, with lazy
expiry and a 100-call capacity. Run one API worker. A restart loses the active
session, although completed-call transcript snapshots can be uploaded to
Supabase when configured. Signed retries for the same turn reuse its response.
This mode has no barge-in. Local tests mock the model and provider; they do not
prove live phone behavior. See [Twilio trial docs](https://www.twilio.com/docs/usage/trials/try-out-voice).

### Streaming phone conversation

1. Create a Python virtual environment and install `requirements.txt`.
   Start the llama.cpp server using [SETUP.md](../../SETUP.md), and set
   `MODEL_URL` and `MODEL_NAME` in `.env`. Leave that server running.
2. Copy `.env.example` to `.env` and fill in the values. Do not commit `.env`.
   `TWILIO_API_KEY_SID` starts with `SK`; its secret authorizes outbound REST
   calls. If neither API key setting is provided, outbound calls use the Account
   SID and Auth Token instead. A partially configured API key pair is rejected.
   `TWILIO_AUTH_TOKEN` is also the account auth token used to verify
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
5. To dial and connect the recipient to HearthAgent, run from the project root:

   ```sh
   python -m scripts.call_agent --to +15551234567
   ```

   This loads `.env`, calls the authorized local `/calls` API, and prints the
   returned call SID and status. It places a real call. Check progress without
   dialing again:

   ```sh
   python -m scripts.call_agent --status CA_YOUR_CALL_SID
   ```

   Replace the placeholder with the actual `CA` plus 32-character SID. `queued`
   means accepted, not connected. `in-progress` means the call was answered,
   not proof of audible agent speech. Signed `/twilio/status` callbacks record
   lifecycle updates in server logs. A timeout has an uncertain outcome; check
   Twilio call logs before retrying. The CLI never retries automatically.

   The equivalent backend request is:

   ```sh
   curl -X POST http://localhost:8000/calls \
     -H 'Content-Type: application/json' \
     -H "X-Admin-Token: $ADMIN_TOKEN" \
     -d '{"to":"+15551234567"}'
   ```

Use a consenting test number. A Twilio trial account may require the destination
number to be verified first.

The dial request uses your public `/twilio/voice` URL instead of the Twilio
`voice_speech_recognition` template. The template in the trial Console is a
Twilio demo and does not connect to our agent. The current custom agent bridge
still requires permission to use Media Streams; successful template dialing
does not establish that permission. Starting `scripts.voice_chat` is unnecessary
for a phone call: llama-server, this API server, and public routing must run.

## Connection verification before a live call

Run the full suite from your activated environment:

```sh
python -m unittest discover -s tests -v
```

`tests/test_phone_e2e.py` exercises the API, signed voice webhook (including
Builder 3's `call_id` query), real ASGI WebSocket route, audio batching,
transcription event, and synthesized audio returned to the caller. External
Twilio and Deepgram connections are mocked; it never dials or consumes credits.
It also checks rejected dialing and unsigned stream rejection.
`tests/test_twilio_provider.py` checks voice/status callback routing, provider
rejection handling, and that uncertain call requests are not retried.

Passing these checks proves the local agent bridge is wired with mocked providers. It does not
prove account acceptance, public tunnel reachability, phone ringing/answering,
or audible delivery. Confirm those with one live call and Twilio's call logs.
The microphone CLI (`python -m scripts.voice_chat`) is a separate local demo;
phone calls require the `uvicorn src.api.server:app` command above.
Phone transcripts now go to HearthAgent. Complete sentences are synthesized
in order as the model generates them. Each call has its own recent history
(up to six exchanges); long turns may exceed the model's context limit.
Caller speech cancels generation and speech synthesis and clears queued audio.
The model response has a 45-second deadline with a spoken fallback on failure.
Builder 3's keypad smoke-test server is also a separate entry point; use its
handoff instructions for that workflow.

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

Conversation history is held in memory for the call, and the model client is
closed at hangup. When enabled, Supabase stores the transcript, observed
timestamps, and generated summary. Profile lookup, baseline comparison, and
structured assessment delivery are not connected to this phone session.

## Post-call caller SMS and Supabase call logs

After a completed call, the server can send the generated patient-reported
summary back to the participant's call number. It does not look up emergency
contacts and it does not send to `CAREGIVER_PHONE_NUMBER`:

```dotenv
POST_CALL_SMS_ENABLED=true
POST_CALL_SMS_PROVIDER=mock             # use twilio for a live attempt
POST_CALL_SMS_DATABASE=data/post-call-sms.sqlite3
SMS_ALERTS_ENABLED=false                # older caregiver-alert path
```

`mock` persists a simulated caller message without contacting Twilio. `twilio`
requires the normal Twilio credentials, an SMS-capable `TWILIO_FROM_NUMBER` (or
`TWILIO_SMS_FROM_NUMBER`), and `PUBLIC_BASE_URL`; delivery callbacks update the
ledger at `/webhooks/twilio/call-summary`. Duplicate completion callbacks do not
send a second message for the same call and caller.

Twilio trial accounts can send SMS only under their trial restrictions, including
verified recipients and predefined content. A custom generated call summary was
rejected in this account with error 572006, so the mock provider is the available
local path until the account can send custom bodies.

To persist complete call transcripts, configure the optional Supabase logger:

```dotenv
SUPABASE_CALL_LOGGING=true
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-server-side-key
SUPABASE_USER_ID=the-associated-user-id
```

Create a `calls` table with the columns `id`, `user_id`, `scheduled_for`, `status`,
`started_at`, `ended_at`, `summary`, `audio_path`, and `transcript`. The server
upserts one row per Twilio call and retains a local `data/call-logs/` snapshot if
Supabase is unavailable; run `python -m src.services.database` to replay snapshots.

The current bridge uses `<Connect><Stream>`. Twilio's documented trial restrictions
block `<Stream>`; use an account that permits it. ngrok supplies public routing
and does not change Twilio account permissions. See
[Twilio trial restrictions](https://www.twilio.com/docs/usage/trials/try-out-voice#blocked-verbs).
