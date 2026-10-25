# Automatic phone conversation logging

The FastAPI phone backend logs Gather/Say and Deepgram streaming conversations through `src/services/database.py`. This is separate from Builder 3's SQLite orchestration state and the local microphone CLI.

1. Install dependencies: `pip install -r requirements.txt`.
2. Apply `supabase/calls.sql` in the Supabase SQL editor. If `calls` already exists, verify the listed columns/types; `create table if not exists` does not migrate an existing table. For an existing UUID user foreign key, supply a valid referenced user UUID below.
3. Add these settings to the project `.env`:

```dotenv
SUPABASE_CALL_LOGGING=true
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-backend-secret-or-service-role-key
SUPABASE_USER_ID=your-patient-user-id
PATIENT_PHONE_NUMBER=+15551234567
CALL_LOG_DIRECTORY=data/call-logs
```

Get the URL and backend key from your Supabase project settings. Keep the key on the backend. RLS is enabled by the SQL with no public policies; the backend secret/service-role key supplies server access.

4. Restart the API. Run `python -m scripts.configure_phone` to configure the purchased number's voice webhook **and call status callback**. Keep ngrok and llama-server running. Call the purchased number from `PATIENT_PHONE_NUMBER`.
5. View `calls` in Supabase Table Editor. Watch server logs for `Supabase call log SUCCESS` or `FAIL`. Only the configured patient number is mapped; other callers are skipped with a warning.

One stable UUID derived from Twilio's CallSid identifies each row; webhook retries upsert the same row. The transcript includes the greeting, patient speech, and replies/fallbacks submitted for speech. It does not prove every word was heard: audio may be interrupted. Conversation snapshots upload after turns, outside the response path. Terminal signed Twilio status callbacks set the final status/end timestamp and request a llama summary in the background. Missing callbacks leave `in-progress`; a dropped media connection alone does not prove call completion.

`scheduled_for` uses the observed start time for unscheduled calls when uploading to the deployed table, which requires this field. Existing scheduled times are preserved. Timestamps represent backend observation times. `audio_path` stays empty because calls are not recorded. This demo uses one explicit patient mapping, rather than a multi-user profile lookup.

Snapshots are retained under `data/call-logs` with restricted creation permissions. Upload errors log only exception type and retain the local copy. Replay them after fixing connectivity/credentials with:

```sh
python -m src.services.database
```

Replay uploads the saved snapshots; it does not regenerate a failed summary. Protect and manage retention of both local files and Supabase transcripts. No automatic retention policy is installed. A hard process exit before a background save begins can lose the newest turn. Use one backend process; shared disk/multiworker coordination is not implemented.

## Text the caller after a call

After a signed Twilio `completed` callback, the backend generates/reuses the call summary and sends one SMS back to the phone conversation participant. For inbound calls, the recipient is Twilio's signed `From` number; for outbound calls it is the called `To` number. The active session's participant number is used when available. Emergency contacts are not queried, and `CAREGIVER_PHONE_NUMBER` is not used. Missing/anonymous/non-phone caller IDs are skipped; there is no fallback to a different person's number.

Completed conversations with patient speech and a summary receive a message, including normal check-ins. Busy/failed/unanswered calls and greeting-only calls do not send. The text labels the summary as patient-reported and contains no full transcript. Model summaries may be inaccurate.

```dotenv
POST_CALL_SMS_ENABLED=true
POST_CALL_SMS_PROVIDER=twilio
POST_CALL_SMS_DATABASE=data/post-call-sms.sqlite3
SMS_ALERTS_ENABLED=false
```

Restart the API after changing `.env`. Use `POST_CALL_SMS_PROVIDER=mock` for a local test that records simulated acceptance without sending real SMS. Live sending uses `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_SMS_FROM_NUMBER` (or `TWILIO_FROM_NUMBER`). The sender must support SMS and the caller must be eligible under the account's trial/recipient and sender restrictions. See [Twilio SMS quickstart](https://www.twilio.com/docs/messaging/quickstart).

Supabase call logging still uses the configured patient/profile mapping, but selecting the SMS recipient does not require Supabase or a profile. SMS can also summarize the current session when `SUPABASE_CALL_LOGGING=false`. Keep local llama-server running for summary generation. A callback received after a process restart can reuse a saved summary, but cannot recover session speech that was never saved.

The earlier caregiver concern alerts are disabled using `SMS_ALERTS_ENABLED=false`; they are separate from this post-call summary feature. Existing emergency-contact data is left intact.

Watch `Post-call SMS ACCEPTED`, `REJECTED`, `UNKNOWN`, and delivery callbacks in backend logs. Acceptance is not delivery. The admin-protected `GET /calls/{TwilioCallSid}/messages` endpoint reports status, message SID, and numeric errors without phone numbers or medical details. Callbacks use `POST /webhooks/twilio/call-summary?delivery_id=...` and validate Twilio signatures.

The local SQLite ledger claims each call/recipient before sending. Duplicate completion callbacks cannot send again to the same number for the call. Timeouts are `unknown`; a process stop during a request can leave `sending`. Neither is automatically resent, since Twilio may already have accepted it. Do not delete the ledger to retry. Mock attempts also claim recipients, so test live mode with a new call.

Failed summary generation or a missing completion callback prevents sending and is logged. A repeated terminal callback can retry summary work while preserving message claims. Snapshot replay uploads call records only and never sends SMS. Stream finalization waits up to 60 seconds for the final transcript before deferring summary delivery. This remains a single-backend-process demo.

### Troubleshooting missing rows or texts

The deployed `public.call_status` enum uses `in_progress`; the adapter translates Twilio's `in-progress` before uploading. Failed uploads now include a safe PostgreSQL/PostgREST error code in the backend log (for example `22P02` for an invalid enum or `23503` for a missing profile reference).

For Supabase logging, `SUPABASE_USER_ID` must be the existing patient's `profiles.id`. Supplying the patient's name or phone number as that ID will not join the records. Verify `PATIENT_PHONE_NUMBER` against the caller's E.164 number. When ngrok's URL changes, update `PUBLIC_BASE_URL`, run `python -m scripts.configure_phone`, and restart the API; that command now verifies the completion callback as well as the voice webhook.
