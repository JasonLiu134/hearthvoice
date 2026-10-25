# Builder 3 handoff: standalone orchestration boundary

## Current state

Builder 3 has a standalone FastAPI app in `src/api/builder3_server.py`. Its
`Orchestrator` and SQLite store implement profiles, daily local-time scheduling,
outbound call intent, retries, inbound profile matching, missed/incomplete-call
states, structured assessment ingestion, caregiver alert claims, and message
delivery callbacks.

This is a separate application path from the active phone server,
`src/api/server.py`. The active server owns Twilio Voice, Deepgram Flux/Aura,
the local llama.cpp agent, Supabase call snapshots, and caller-only post-call
summary SMS. It does not create Builder 3 call IDs, submit structured
assessments, or call `Orchestrator.call_event()` from its Twilio status route.

## Contracts

Builder 3 assessment ingestion accepts `NORMAL`, `LOW_RISK`, or `HIGH_RISK`, with
optional `meds_taken` and `current_pain_level`. A high-risk assessment creates a
caregiver alert; a second unanswered attempt creates a missed-check-in alert.
The service validates submitted fields but does not infer this structured object
from raw dialogue.

The active model summary has a different schema: `mood`, `severity` (`ok`,
`watch`, `concern`, or `emergency`), `concerns`, and `summary`. It does not yet
perform baseline lookup or deterministic pain-delta/medication rules. Preserve
the two schemas as separate contracts until an explicit integration is made.

## Local verification

```sh
python3 scripts/demo_builder3.py
python3 -m unittest discover -s tests -v
```

The offline demo uses fictional data and a mock provider. The API can be run
with one worker:

```sh
uvicorn src.api.builder3_server:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

Set `SCHEDULER_ENABLED=true` to tick every 30 seconds, or call
`POST /scheduler/tick`. Use a private `API_TOKEN`; it is distinct from the
active server's `ADMIN_TOKEN` and Twilio credentials. The SQLite state is a
single-process demo store.

## Active phone path status

The active phone path has completed inbound voice conversations and persists
transcripts/summaries to Supabase when configured. Its current post-call SMS is
sent to the call participant, not a caregiver, and does not query
`emergency_contacts`. The older concern-only caregiver-alert path is separately
gated by `SMS_ALERTS_ENABLED` and is disabled in the current environment.

The Twilio trial account rejected the custom summary SMS body with error
`572006`; custom live summary delivery is therefore not verified. This is a
trial restriction, not evidence that the active inbound phone conversation is
unimplemented. The active trial Gather/Say mode is limited to four turns and
has no barge-in; the Deepgram streaming mode supports interruption.

## Remaining integration work

- Pass a Builder 3 internal `call_id` into the active Twilio voice session.
- Relay active call lifecycle events into `Orchestrator.call_event()`.
- Convert the active model summary or a dedicated triage result into the Builder
  3 assessment contract.
- Decide whether product alerts go to a caregiver, the caller, or both, then
  enable only that path.
- Replace or reconcile the standalone SQLite state with shared Supabase
  profiles, schedules, assessments, and alert records.
- Measure full phone turn latency before claiming the README's under-1.5-second
  target.

## Safety and deployment limits

Run one standalone Builder 3 process against SQLite. Provider timeouts are
recorded as unknown rather than blindly retried. Live caregiver delivery and
multi-worker reconciliation are not claimed. Use consenting test participants,
keep webhook credentials private, and do not treat this service as emergency
response or diagnosis.
