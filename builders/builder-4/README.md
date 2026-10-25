# Builder 4: Data, integration, and demo

## Current state

Automatic phone-call logging is implemented in `src/services/database.py`. Trial Gather/Say and Deepgram streaming sessions supply call transcripts; signed completion callbacks finalize status and generate a local llama.cpp summary. Supabase receives call rows, and retained local snapshots can be replayed after an upload failure. A live call's transcript and summary have been uploaded and verified against the local snapshot.

The current post-call SMS feature sends the summary **back to the caller**, without querying emergency contacts. It has a separate local SQLite delivery ledger, retry deduplication, and signed message-status callbacks. The latest live request was rejected with Twilio `572006`; delivery is not verified. The earlier caregiver concern-alert feature is separate and disabled in the current setup.

See [Supabase and caller SMS setup](SUPABASE.md), [phone setup](../builder-1/TWILIO.md), and the [project status](../../README.md).

## Implemented storage boundary

- One configured patient number maps to `SUPABASE_USER_ID`; other callers are not automatically resolved to profiles for logging.
- Each Twilio call SID maps to a stable UUID call-row ID. Retries upsert the same row.
- Stored data includes call status, observed start/end times, transcript, and summary text. Structured severity/concern fields are not persisted by this integration.
- On-demand calls use their observed start as `scheduled_for` when uploading to the deployed table's required field. This does not implement scheduling.
- `audio_path` remains empty; audio recording is not implemented.
- Local snapshots are retained for replay; retention is not automated. Hard process exits can lose a turn before its background save starts.
- Caller-SMS selection can operate without Supabase logging or a patient profile. A current transcript or retained summary is still required.

Use one main backend process. This implementation does not provide shared session routing across workers or a durable background job queue.

## Schema and fixtures

`supabase/calls.sql` provides a minimal calls table and RLS setup. `supabase/emergency_contacts.sql` describes the earlier contacts-table feature; those contacts are not read by caller-summary SMS. These scripts do not recreate the full deployed schema or migrate existing tables.

The deployed database includes profiles and additional constraints. Its call status enum uses `in_progress`; the adapter translates Twilio's hyphenated status. A logging user ID must refer to the existing profile when that database has a foreign-key constraint.

Versioned profile/baseline/schedule/assessment migrations and a complete fictional demo seed remain unfinished. The code does not load those profiles or historical baselines into the live agent. Existing remote rows do not constitute a reproducible repository fixture.

## Remaining integration work

| Original responsibility | Current gap |
| --- | --- |
| Baselines and historical comparison | No database lookup feeds baseline pain or prior assessments to live phone sessions. |
| Shared call history | Builder 3 uses separate SQLite records and IDs; its scheduler and missed-call state are not joined to Supabase call rows. |
| Structured assessment persistence | The main phone path saves summary text, not the original `NORMAL`/`LOW_RISK`/`HIGH_RISK` contract. |
| Caregiver notifications | Current requested behavior is caller-only summaries; the original caregiver/missed-check-in demo remains a separate scope. |
| Reproducible demo data | No complete seed/migration set for profiles, baseline pain, schedules, and assessments is supplied. |

## Current demo

1. Configure a consenting participant's number and an existing logging profile ID.
2. Keep the local model server, phone backend, and public tunnel running.
3. Call the purchased Twilio number and have a short conversation.
4. Inspect the Supabase transcript and summary after completion.
5. Inspect caller-SMS attempt status separately from delivery. In mock mode no message is sent; the latest live trial attempt was rejected.

The original demonstration—baseline pain 3/10, reported pain 7/10 plus missed medication, a structured high-risk assessment, and caregiver SMS—remains an integration target. Builder 3's scripted test can demonstrate its state machine separately, but is not proof of that live end-to-end flow.
