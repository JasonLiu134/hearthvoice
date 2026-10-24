# Builder 3 handoff: orchestration and Twilio investigation

## Current state

Implemented a standalone FastAPI orchestration app, provider adapters, daily scheduling,
temporary SQLite persistence, assessment ingestion, signed Twilio callbacks, deduplication,
and scripted keypad testing. Builder 1's `src/api/server.py` is preserved; the standalone
Builder 3 entry point is `src/api/builder3_server.py`.

Verified locally: 50 automated tests pass, the offline demo persists high-risk and missed-call
alerts, and the local/public HTTPS health endpoints were reachable during testing.

Live calls have **not** succeeded and real caregiver SMS delivery is **not verified**.
The account owner needs to finish the Twilio configuration/request investigation.

## Observed Twilio issue

- Read-only account authentication returned HTTP 200, account `active`, type `Trial`.
- Sender lookup returned HTTP 200 with no matching `IncomingPhoneNumbers` record.
  This does **not** establish that the trial sender or credentials are wrong.
- Outbound create requests returned HTTP 400, provider code `0`, with no call SID.
  The exact cause remains unknown; changing the recipient to the confirmed international
  number did not resolve the rejection.
- The diagnostic now reports `listed_in_account_phone_numbers` rather than claiming
  that an empty lookup proves the number does not belong to the account.

Current [Twilio Voice trial documentation](https://www.twilio.com/docs/usage/trials/try-out-voice)
describes trial-provided numbers, product/recipient-specific resources, and restricted
request parameters. The adapter currently uses the standard Calls API with `From`,
custom `Url`, `Timeout`, and callback options. Compatibility with this account's trial
request format needs verification; it is a hypothesis, not a confirmed root cause.

## Account owner's next steps

1. Configure a private `.env` from `.env.example` using the intended account's credentials.
   Do not share or commit credentials. Rotate any real token previously pasted in chat.
2. In Twilio Console, open Communications > Voice > Overview > Try out Voice. Confirm
   the verified recipient and the correct Voice trial sender. Compare the Console's
   request example and response with `TwilioProvider.start_call`. Inspect Twilio logs
   for the rejection description. Preserve secrets when sharing diagnostic output.
3. Run the read-only check:

   ```sh
   .venv/bin/python scripts/check_twilio.py
   ```

4. Fix account-specific request/configuration issues before making another call. Confirm
   Voice and SMS sender requirements separately; this implementation assumes one sender.

## Reproduce the standalone test

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

Fill credentials and set these additional values locally:

```dotenv
PROVIDER=twilio
SMOKE_TEST_ENABLED=true
SCHEDULER_ENABLED=false
DATABASE_PATH=data/live-test.sqlite3
PUBLIC_BASE_URL=https://YOUR-CURRENT-TUNNEL-HOST
API_TOKEN=YOUR-PRIVATE-RANDOM-BACKEND-TOKEN
```

`API_TOKEN` protects HearthVoice's API; it is distinct from `TWILIO_AUTH_TOKEN`.
`VOICE_URL` is unnecessary when scripted test mode is enabled. `TWILIO_FROM_NUMBER`
is the sender, not the personal test recipient. Use your own HTTPS tunnel; the prior
developer's tunnel address is temporary and is not committed configuration.

```sh
uvicorn src.api.builder3_server:create_app --factory --host 127.0.0.1 --port 8000 --env-file .env
```

Keep the server and tunnel running. Health should report `provider: twilio` and
`smoke_test_enabled: true`. In another terminal, with consenting real recipients:

```sh
.venv/bin/python scripts/start_test_call.py \
  --to '+YOUR_VERIFIED_RECIPIENT' \
  --caregiver '+YOUR_VERIFIED_CAREGIVER' \
  --place-call
```

This requests a real, potentially billable call. Press 1 to create an explicitly synthetic
high-risk assessment and attempt a `TEST ONLY:` caregiver SMS; press 2 for a normal
synthetic assessment without an SMS. Inspect authenticated `GET /history` and provider
delivery callbacks; provider acceptance alone is not proof of delivery.

The script deduplicates check-ins by UTC date. After fixing a definitively rejected
request with no provider call ID, explicitly add `--retry-rejected-call`. Unknown sends,
accepted calls, or completed calls cannot be retried through that option. Historical
rejections stay in local history. The script loads the repository's `.env` automatically;
preexisting shell variables take precedence.

## Offline verification and integration boundaries

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/demo_builder3.py
```

- Builder 1: preserve internal `call_id` through the voice session and relay lifecycle events.
- Builder 2: send `event_id`, `call_id`, and structured `triage` to `POST /events/assessments`.
- Builder 4: replace the temporary `load()` / `save(state)` store with shared persistence.
- Full API contracts, assumptions, and limits are in [README.md](README.md).

Run a single process against the SQLite store. Automatic SMS retries and provider
reconciliation remain pending; uncertain sends are recorded as `unknown` to avoid
blind duplication. No credentials, personal phone numbers, transcripts, or local
SQLite history are included in this handoff.
