# Builder 3: Orchestration, scheduling, and alerts

See [the account-owner handoff](HANDOFF.md) for current verification, the unresolved
Twilio trial rejection, and instructions to continue testing.

## Goal

Connect call events and assessments to an SMS alert when a check-in is concerning or missed.

The orchestration implementation uses FastAPI, a replaceable phone/SMS provider, and temporary SQLite persistence. Mock mode works without credentials. Twilio mode is wired for Builder 1's voice endpoint, but has not been verified against live Twilio. Other providers can implement `start_call(phone, call_id)` and `send_sms(phone, body, alert_id)`.

## Responsibilities

- Provide application and webhook entry points for call events.
- Orchestrate outbound calls and user-initiated inbound check-ins with Builder 1.
- Trigger daily calls at the person's chosen time.
- Send a brief, actionable SMS to the designated caregiver for concerning assessments.
- Handle missed calls as a separate alert condition because no conversation took place.
- Record call outcomes and alert results through Builder 4's database interface.

## Planned implementation

- `src/api/server.py`: application and webhook entry point.
- `src/api/routes/calls.py`: call handlers.
- `src/api/routes/webhooks.py`: provider events.
- `src/services/twilio_client.py`: calls and SMS, coordinated with Builder 1.
- `src/cron/scheduler.py`: daily check-ins.

These paths refer to the proposed application layout at the repository root. This builder folder holds documentation.

## Handoffs

- Agree with Builder 1 on call initiation, provider identifiers, and lifecycle event handling.
- Consume Builder 2's structured triage fields directly rather than parsing spoken responses.
- Read designated caregiver contacts and schedules, and write outcomes, through Builder 4's persistence interface.
- Agree with the team on what counts as a missed check-in, timezone handling, alert retries, and duplicate-event handling.

## Demo acceptance

- A high-risk assessment from the fictional missed-medication and pain scenario sends an SMS to the designated demo caregiver number.
- A simulated or live missed call exercises the separate missed-check-in alert path.
- SMS content is brief and actionable, with only the sensitive information needed to act.
- Call and alert outcomes are available in persisted history.
- Establish the end-to-end call-to-assessment-to-SMS path before extending scheduling and historical comparisons.

## Before real use

Define consent, webhook access controls, alert retry behavior, and urgent-situation behavior with the team. HearthVoice does not provide emergency response.

## Run locally

From the repository root, run the offline demo with no third-party dependencies:

```sh
python3 scripts/demo_builder3.py
python3 -m unittest discover -s tests -v
```

The demo uses fictional numbers, creates a temporary database, and prints two mock SMS outcomes. It makes no network requests. HTTP tests explicitly skip if their dependencies are missing.

To run the API:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
uvicorn src.api.builder3_server:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

The standalone test app is `src/api/builder3_server.py`; Builder 1's `src/api/server.py` remains unchanged. Add `--env-file .env` to load a local configuration file. Configuration comes from environment variables; `.env.example` lists them and is not automatically loaded. Mock mode is the default. Local API documentation is at `http://127.0.0.1:8000/docs`. Protected endpoints require `Authorization: Bearer local-demo-token` by default; replace `API_TOKEN` before exposing the API. Set `SCHEDULER_ENABLED=true` to run a scheduler tick every 30 seconds, or call `POST /scheduler/tick` manually. Run exactly one worker/process against this store; concurrent independent processes are unsupported.

Example fictional profile and call:

```sh
curl -X PUT http://127.0.0.1:8000/profiles/demo \
  -H 'Authorization: Bearer local-demo-token' \
  -H 'Content-Type: application/json' \
  -d '{"name":"Fictional Demo","phone":"+12025550101","caregiver_phone":"+12025550102","timezone":"America/Los_Angeles","local_call_time":"09:00"}'

curl -X POST http://127.0.0.1:8000/calls \
  -H 'Authorization: Bearer local-demo-token' \
  -H 'Content-Type: application/json' \
  -d '{"senior_id":"demo"}'
```

The call response contains `check_in_id` and `call_ids`. `GET /history` returns persisted profiles, check-ins, calls, assessments, events, and alerts. Treat that endpoint and SQLite data as private. `POST /calls` reuses the person's check-in for the current local date, so repeated requests do not create extra calls.

## Independent live phone test before Builders 1 and 2

Set `PROVIDER=twilio` and `SMOKE_TEST_ENABLED=true` to use the included scripted voice endpoint. `VOICE_URL` can be empty in this mode. The script uses Twilio's [Say](https://www.twilio.com/docs/voice/twiml/say) and keypad [Gather](https://www.twilio.com/docs/voice/twiml/gather); it performs no speech recognition or medical assessment.

1. Install `requirements-dev.txt` as above, and export the settings from `.env.example` with current Twilio credentials and a private `API_TOKEN`. `TWILIO_PHONE_NUMBER` is accepted as an alias for `TWILIO_FROM_NUMBER`.
2. Start the API locally and expose port 8000 through an HTTPS tunnel. Set `PUBLIC_BASE_URL` to the tunnel origin (no path prefix), then restart the API with `SMOKE_TEST_ENABLED=true`, `PROVIDER=twilio`, `SCHEDULER_ENABLED=false`, and a separate `DATABASE_PATH=data/live-test.sqlite3`.
3. From another terminal, run (the script loads the repository's `.env`):

```sh
python3 scripts/start_test_call.py --to +YOUR_TEST_NUMBER --caregiver +YOUR_TEST_SMS_NUMBER --place-call
```

Both recipients must consent; they may be the same person. Without `--place-call`, the command only prints a preparation message. The manually dialed test profile is excluded from automatic scheduling. This command can incur real Twilio call and SMS charges. Account restrictions may also prevent a call or SMS; inspect Twilio's delivery/debug logs if the provider rejects a request.

4. Answer and press **1** to simulate a concern, or **2** for a normal result. Choice 1 creates a synthetic assessment and one caregiver SMS prefixed `TEST ONLY:`. Choice 2 stores a normal synthetic assessment without sending an alert. No input results in an incomplete check-in after the normal two-minute grace period if scheduler ticks are running.
5. Inspect authenticated `GET /history`: the call must have a provider ID and terminal callback status; the synthetic assessment must appear; an alert's provider message ID and delivery callback distinguish acceptance from actual delivery. Check both the phone and stored delivery status before claiming the live path works.

The command reuses one test profile and one check-in per UTC date. Rerunning it does not place another call that day. To exercise missed-call retries with the manual test profile, call `POST /scheduler/tick` after the five-minute delay and temporarily enable that profile; otherwise test missed-call behavior offline. Use only disposable test state for repeated isolated runs and preserve unknown sends until reconciled.

After testing, set `SMOKE_TEST_ENABLED=false` and supply Builder 1's real `VOICE_URL`. This replaces only the scripted voice; the scheduling, call IDs, callback handlers, assessment endpoint, alerts, and persistence remain the integration boundary.

## Builder 2 assessment payload

Send this JSON to authenticated `POST /events/assessments` once an assessment is available:

```json
{
  "event_id": "a-stable-unique-id-for-this-assessment",
  "call_id": "the-orchestrator-call-id",
  "triage": {
    "status": "HIGH_RISK",
    "meds_taken": false,
    "current_pain_level": 7,
    "symptoms_flagged": ["increased pain", "missed medication"],
    "summary": "Fictional missed-medication and pain scenario."
  }
}
```

Only `event_id`, `call_id`, and `triage.status` are required. Status values currently assumed are `NORMAL`, `LOW_RISK`, and `HIGH_RISK`; medication and pain can be null when unknown. The API stores the assessment and alerts on `HIGH_RISK`. It does not recalculate Builder 2's clinical rules. Spoken responses stay in the voice pipeline. Reuse an event ID for a redelivery; reusing it with different content is rejected. A new assessment event for the same check-in cannot send a second high-risk SMS.

## Builder 1 call handoff

Each person has a `senior_id`. Each local calendar date has one `check_in_id`; multiple call attempts have distinct internal `call_id` values and distinct provider call IDs. Twilio's `CallSid` is stored as `provider_call_id`.

The Twilio adapter calls `VOICE_URL?call_id=...` to get Builder 1's TwiML. Builder 1 must preserve that internal ID through the dialogue session so Builder 2 can return it with the assessment. The voice endpoint owns speech, voicemail detection, and conversation behavior.

For an alternative gateway or local simulation, authenticated `POST /events/calls` accepts:

```json
{
  "call_id": "the-orchestrator-call-id",
  "provider_call_id": "the-provider-call-id",
  "status": "no-answer"
}
```

Supported statuses: `queued`, `initiated`, `ringing`, `in-progress`, `completed`, `busy`, `no-answer`, `failed`, `canceled`. Duplicate statuses are ignored; delayed progress events do not reopen terminal calls. Conflicting provider IDs are rejected.

Twilio mode requires credentials, a private API token, an HTTPS `PUBLIC_BASE_URL` without a path prefix, and Builder 1's HTTPS `VOICE_URL`. Configure the Twilio number's inbound voice webhook to `POST /webhooks/twilio/inbound`; configure its call status webhook to `POST /webhooks/twilio/calls` for inbound calls. Outbound call and SMS callback URLs are supplied automatically. Requests are validated with Twilio signatures against `PUBLIC_BASE_URL`; proxy headers are not trusted to construct that URL. Unknown inbound numbers receive a short rejection message. Registered callers are redirected to Builder 1's voice URL with their internal call ID.

SMS callbacks go to `/webhooks/twilio/messages?alert_id=...`. An equivalent authenticated `/events/messages` endpoint accepts `alert_id`, `provider_message_id`, and `status`. Provider acceptance is recorded as `accepted`, not `delivered`; delivery callbacks update that distinction.

## Assumed policies

| Situation | Current behavior |
| --- | --- |
| No answer or busy | Retry once after five minutes; a second unanswered/busy attempt sends a missed-check-in alert |
| Provider call fails or is canceled | Record a technical failure and send a distinct follow-up alert |
| Call completed without an assessment | Wait two minutes, then send an incomplete-check-in alert |
| Inbound call before a scheduled retry | Cancel the pending retry; a completed assessment satisfies today's check-in |
| Voicemail or early hang-up | Provider completion alone never counts as a completed check-in; absent assessment becomes incomplete |
| Scheduler restart | Catch up today's due schedule; do not backfill previous days or dial yesterday's retry |
| Timezones | Store an IANA zone and local HH:MM; use UTC event timestamps |
| Daylight saving | Call once on a repeated local date/time; catch up a skipped spring time on the next tick after the clock advances |
| Duplicate alerts | At most one alert per check-in and alert type, including across restarts |
| Rejected provider request | Record `failed`; no automatic resend |
| Timeout or uncertain create request | Record `unknown`; await a callback or manual provider reconciliation instead of automatically duplicating a send |

Automatic SMS retries are intentionally deferred until the team chooses a reconciliation/retry policy. A crash during a provider request also produces `unknown` on restart. No end-to-end exactly-once delivery is claimed. Calls that never receive a final callback remain pending for manual review; there is no provider reconciliation worker yet. An assessment arriving after an incomplete alert is still stored and may send a distinct high-risk alert.

SMS uses fixed, brief templates without medical details or transcripts. The generic wording assumes the demo caregiver knows the designated person; a caregiver serving multiple people will need an agreed, privacy-conscious label before integration.

## Builder 4 persistence handoff

`src/orchestration/store.py` supplies `load()` and `save(state)`. `state` contains `profiles`, `check_ins`, `calls`, `alerts`, and `events`, keyed by their identifiers. SQLite saves this state transactionally in `data/orchestration.sqlite3` by default. Builder 4 can provide an adapter or replace this temporary design with relational records and atomic claims. `src/services/database.py` remains untouched.

This implementation serializes operations within one process, including provider requests. It is a demo foundation; multi-worker deployment needs shared transactional claims and an outbox/reconciliation design. Builder 2's `agent.py` and Builder 1's voice implementation remain separate work.

Provider integration follows the official Twilio [call API](https://www.twilio.com/docs/voice/api/call-resource), [message API](https://www.twilio.com/docs/messaging/api/message-resource), and [webhook security documentation](https://www.twilio.com/docs/usage/webhooks/webhooks-security).
