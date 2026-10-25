# Builder 3: Orchestration, scheduling, and alerts

## Current state

Builder 3 is a standalone orchestration application in
`src/api/builder3_server.py`. It uses `Orchestrator`, replaceable provider
adapters, and a SQLite state snapshot. It implements daily scheduling,
outbound-call intent, inbound profile matching, retries, missed/incomplete-call
states, structured assessment ingestion, caregiver alert claims, and delivery
status updates.

This application is separate from the active phone server in
`src/api/server.py`. The active server handles Twilio Voice, Deepgram Flux/Aura,
the local llama.cpp agent, Supabase call snapshots, and the current caller-only
post-call summary SMS. It does not create Builder 3 `call_id` records, submit
Builder 3 assessments, or feed its status callbacks into `Orchestrator`.

## Standalone responsibilities

- `src/orchestration/service.py` — profiles, local-date check-ins, call state,
  retry/missed-call policy, assessment validation, caregiver alert claims, and
  SMS delivery status.
- `src/cron/scheduler.py` — optional one-process scheduler tick every 30
  seconds.
- `src/api/builder3_server.py` and `src/api/routes/` — protected profile, call,
  assessment, status, and message webhook endpoints.
- `src/orchestration/store.py` — transactional SQLite JSON state. It is not the
  Supabase call-log adapter used by the active phone server.

The standalone assessment endpoint accepts `NORMAL`, `LOW_RISK`, or `HIGH_RISK`
and validates optional `meds_taken` and `current_pain_level` fields. It stores
the assessment and creates a caregiver alert for `HIGH_RISK`. The service does
not calculate the assessment from raw speech; Builder 2 must submit the
structured payload.

## Run the standalone demo

From the repository root:

```sh
python3 scripts/demo_builder3.py
python3 -m unittest discover -s tests -v
```

The offline demo uses fictional data and a mock provider. To run the standalone
API, install the development requirements and start one worker:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
uvicorn src.api.builder3_server:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

Set `SCHEDULER_ENABLED=true` for the optional scheduler, or call
`POST /scheduler/tick`. `GET /history` exposes private SQLite demo state. This
standalone service uses `API_TOKEN`; it is distinct from Twilio credentials and
the active server's `ADMIN_TOKEN`.

## Integration boundary

The intended product still calls for scheduled outbound check-ins, caregiver
alerts for missed/concerning calls, and persisted assessments. Those behaviors
are implemented in this standalone path, but the active Twilio conversation is
not connected to them. The active call's model summary uses `ok`, `watch`,
`concern`, and `emergency`, rather than the Builder 3 `NORMAL`, `LOW_RISK`, and
`HIGH_RISK` assessment contract.

The active post-call SMS is intentionally different from this builder's
caregiver alert path: it sends a short patient-reported summary back to the
call participant. It does not query `emergency_contacts`; the older concern-only
caregiver alert feature is separately gated by `SMS_ALERTS_ENABLED` and is
disabled in the current environment. A Twilio trial rejected the custom summary
body with error `572006`; custom live summaries therefore remain unverified
until the account permits them.

Supabase logging is also separate: the active server stores call rows,
transcripts, and summaries in `calls`, while Builder 3 continues to use its
SQLite state. Joining these paths requires an explicit integration change.

## Known limits

The active trial Gather/Say mode is bounded to four turns and has no barge-in.
The streaming mode supports interruption. Neither path has a verified
end-to-end under-1.5-second measurement. This repository does not claim a
production scheduler, multi-worker SQLite deployment, or live caregiver alert
delivery from the active phone server.

See [HANDOFF.md](HANDOFF.md) for the implementation boundary and remaining
integration work.
