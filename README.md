# HearthVoice

**Track 2: No App** · Phone-based wellness check-in prototype

Demo Video: [Drive Link](https://drive.google.com/file/d/1f2aydWfgqa0GFI1qbqNkLZlYLlxJ1iSm/view?usp=drive_link)

HearthVoice lets an older adult have a short wellness conversation over a regular landline or mobile phone, without installing an app. The original project goal is a scheduled daily call that compares answers with a baseline and alerts a designated caregiver about concerns or missed check-ins. Those pieces are not yet connected into one service.

The current phone demo supports inbound conversations with a local llama.cpp agent, Supabase call transcripts and summaries, and an optional post-call summary SMS **back to the caller**. Caller-only SMS is the current requested scope; it does not implement the original caregiver escalation behavior. The latest live SMS request was rejected by Twilio with `572006` (trial template restriction), so message delivery is not verified.

See [installation and testing](SETUP.md), [phone setup](builders/builder-1/TWILIO.md), and [Supabase logging and caller SMS](builders/builder-4/SUPABASE.md).

## Implemented behavior and remaining integration

| Capability | Current implementation | Remaining work from the original brief |
| --- | --- | --- |
| Phone calls | Main FastAPI app handles inbound voice and exposes an admin-protected outbound Calls API. Inbound conversation has been exercised live; outbound custom-call support depends on the Twilio account. | Connect scheduled outbound calls and their status callbacks to the live conversation app. |
| Conversation | Local llama.cpp agent streams text with instructions for short, patient replies and one question at a time. Trial phone mode uses Twilio Gather/Say; Deepgram streaming mode supports interruption. | Profile-aware dialogue and verification that the full wellness topic set is covered. Trial mode ends after four turns, including silence. |
| Assessment | A separate model summary returns mood, severity, concerns, and summary text. Concern classification is prompt-based. | Deterministic baseline comparison, pain-delta rules, and the original structured triage contract. |
| SMS | Optional completed-call summary goes to the call participant, using the signed caller/callee number rather than a contact lookup. Delivery attempts and callbacks have a durable local ledger. | Live custom SMS delivery is account-blocked in the latest test. Original caregiver and missed-call alerts are separate from this flow. |
| History | Supabase call rows store status, observed timestamps, transcript, and summary for one configured patient mapping. Local snapshots support upload replay. | Integrated profile/baseline lookup, scheduling history, and persisted structured assessments. No call audio recording is implemented. |
| Scheduling and caregiver alerts | Standalone Builder 3 app implements SQLite-backed schedules, retries, missed-call detection, assessment ingestion, and caregiver SMS attempts. | Join this app to the main phone app and Supabase; validate real scheduled calls and caregiver delivery. |

The voice latency target remains **under 1.5 seconds per turn**. It is a target, not a measured guarantee. Existing first-audio timing excludes parts of turn detection, network transport, and audible playback.

## Current architecture

```text
Senior's landline or mobile phone
              ↕ Twilio Voice
Main FastAPI phone app: src/api/server.py
  ├── trial mode: Twilio Gather speech recognition → Twilio Say speech synthesis
  ├── stream mode: Deepgram Flux STT → Deepgram Aura-2 TTS
  ├── dialogue and summary: local llama.cpp via an OpenAI-compatible API
  ├── call transcripts and summaries: Supabase + retained local snapshots
  └── post-call SMS: Twilio → call participant + local delivery ledger

Separate Builder 3 app: src/api/builder3_server.py
  └── SQLite profiles, schedules, retry/missed-call state, and caregiver alerts
      (not integrated end to end with the main phone app)
```

The configured phone demo currently uses `TWILIO_VOICE_MODE=trial`. Its four-turn Gather/Say flow waits for a complete agent response before returning the next TwiML and does not support barge-in. The Deepgram streaming mode is a separate path; the local microphone CLI also uses Deepgram, but mutes input during playback and has no interruption support.

There is no active Claude, cloud OpenAI model, Vapi, ElevenLabs, or Pocket TTS integration. The OpenAI Python package is used to call the local llama.cpp server. A landline can participate in voice calls, but the current caller-summary SMS requires a text-capable recipient and provider eligibility.

## Current summary contract

The agent emits spoken reply text and generates a separate summary object:

```json
{
  "mood": "calm",
  "severity": "watch",
  "concerns": ["summary example only"],
  "summary": "Patient described their current wellbeing during the call."
}
```

Severity is `ok`, `watch`, `concern`, or `emergency`. The main Supabase integration currently persists the **summary text**, rather than the complete structured assessment. Live conversation sessions do not load a baseline or previous check-in from Supabase. Model instructions mention missed medication, pain, dizziness, confusion, and distress; this is not a deterministic symptom or pain-delta rules engine.

## Original roadmap and triage contract

The original experience remains roadmap context:

1. Place a daily call at the person's chosen time, while also allowing inbound check-ins.
2. Ask about mood, meals, medication, pain, and symptoms.
3. Compare answers with a recorded baseline and assign structured risk.
4. Notify a designated caregiver about concerns or missed check-ins.
5. Persist the outcome and structured assessment for follow-up.

The original proposed response below is **not the live agent's current output**:

```json
{
  "spoken_response": "I'm sorry you're feeling worse today. Can you tell me more about the pain?",
  "triage": {
    "status": "HIGH_RISK",
    "meds_taken": false,
    "current_pain_level": 7,
    "symptoms_flagged": ["increased pain", "missed medication"],
    "summary": "Reported pain of 7/10, up from a baseline of 3/10, and missed medication."
  }
}
```

Builder 3 can ingest `NORMAL`, `LOW_RISK`, and `HIGH_RISK` assessments, but does not calculate baseline comparisons. The original escalation rules—missed medication, a pain increase of at least three points, shortness of breath, confusion, dizziness, or distress—still need an agreed implementation and integration with the conversation path.

HearthVoice is a check-in prototype, not a diagnostic tool or emergency response service. The agent has urgent-situation wording, but consent, retention, access controls, and escalation behavior require product-level decisions before broader use. The current SMS contains a model-generated, patient-reported summary; it is not a verified medical assessment.

## Builder responsibilities

| Builder | Implemented slice | Remaining handoff |
| --- | --- | --- |
| [1: Telephony and speech](builders/builder-1/README.md) | Twilio phone modes, Deepgram adapters, voice CLI, and protocol tests | Account-compatible live outbound/streaming validation and full-turn latency measurements |
| [2: Dialogue and triage](builders/builder-2/README.md) | Local agent persona and separate summary classification | Baseline-aware structured triage and assessment handoff |
| [3: Orchestration](builders/builder-3/README.md) | Standalone scheduler, retries, missed-call state, and caregiver alerts | Integrate with live voice sessions and Supabase |
| [4: Data and demo](builders/builder-4/README.md) | Supabase call logging and caller-summary delivery tracking | Full schema/fixtures, profile lookup, assessment persistence, and integrated demo |

## Demo and verification

The current demo is: call the configured Twilio number, speak to the agent, end the call, and inspect the persisted transcript/summary and caller-SMS attempt status. SMS acceptance and delivery must be reported separately. The latest live custom-summary attempt failed with Twilio `572006`; no successful SMS delivery is claimed.

The original pain-baseline-to-caregiver-SMS demo is not yet an integrated acceptance test. Builder 3's scripted scenarios demonstrate orchestration behavior separately.

Automated tests cover the agent client, speech protocols, phone webhooks, orchestration, database adapter, and SMS deduplication/status handling. Tests named end-to-end use mocked provider boundaries; passing them does not prove real phone audio, live SMS delivery, risk accuracy, or the latency target. See [test commands](SETUP.md#6-run-all-automated-checks).

## Repository layout

```text
src/api/server.py              Main phone backend and signed Twilio webhooks
src/api/builder3_server.py     Standalone orchestration backend
src/api/routes/                Builder 3 call and webhook routes
src/core/agent.py              Local dialogue and summary client
src/core/stt.py                Deepgram Flux adapter
src/core/tts.py                Deepgram Aura adapter
src/core/voice_pipeline.py     Twilio/Deepgram streaming phone mode
src/core/trial_voice.py        Twilio Gather/Say phone mode
src/services/database.py      Supabase call snapshots and summaries
src/services/call_summary_sms.py  Caller SMS and durable delivery ledger
src/services/phone_alerts.py   Optional earlier concern-only caregiver bridge
src/orchestration/            Standalone SQLite orchestration and provider state
src/cron/scheduler.py          Builder 3 scheduler loop
scripts/                      Local voice, phone configuration, and demo commands
supabase/                     Minimal SQL scripts; not a complete deployed-schema migration
builders/                     Per-builder status, setup, and handoffs
tests/                        Unit and mocked integration tests
```
