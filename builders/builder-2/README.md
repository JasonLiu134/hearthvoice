# Builder 2: Dialogue and structured triage

## Current state

The dialogue path is implemented in `src/core/agent.py`. It uses a local
llama.cpp server through the OpenAI-compatible client configured by
`MODEL_URL` and `MODEL_NAME`. The agent streams warm, short phone responses and
has separate summary generation for a completed transcript.

The active phone session passes an empty profile (`CallSession.user = {}`), so
profile notes, recorded baselines, and historical comparisons are not wired
into the live call yet. The trial Gather/Say path limits a conversation to four
turns and has no barge-in. The Deepgram streaming path supports interruption by
cancelling the current reply and clearing Twilio audio.

## Responsibilities and implementation

- `src/core/agent.py` — persona, streamed replies, emergency wording, and JSON
  summary parsing.
- `src/core/voice_pipeline.py` — live Deepgram Flux turn handling and Aura-2
  speech streaming.
- `src/core/trial_voice.py` — bounded Twilio Gather/Say trial conversation.
- `tests/test_agent.py`, `tests/test_voice_pipeline.py`, and
  `tests/test_trial_voice.py` — model and conversation boundary tests.

The current summary schema is:

```json
{
  "mood": "string",
  "severity": "ok | watch | concern | emergency",
  "concerns": ["string"],
  "summary": "At most two plain sentences"
}
```

The summary is used for the post-call status message. With the current
configuration, that message is sent back to the call participant; it is not a
caregiver escalation and it does not read `emergency_contacts`.

## What remains planned

The original project contract proposed a separate `spoken_response` plus
`triage` object with `NORMAL`, `LOW_RISK`, and `HIGH_RISK`, medication status,
pain level, symptoms, and baseline comparison. That contract is consumed by the
standalone Builder 3 orchestration app, but the active phone agent does not
produce it. Deterministic rules such as a three-point pain increase or missed
medication escalation are therefore not implemented in the active call path;
the current severity is model-generated (`ok`, `watch`, `concern`, or
`emergency`).

Historical comparison, profile lookup, structured assessments from the live
Twilio call, and the scheduler-to-live-call handoff remain integration work.
The under-1.5-second target is a design target. Streaming logs
`end_of_turn_to_first_audio_ms`, but the repository does not claim a measured
end-to-end phone latency result.

## Safety boundary

HearthVoice is a wellness check-in and notification concept, not a diagnostic or
emergency-response service. The agent directs a person to emergency services
for the configured emergency phrases. Consent, retention, access control, and
production escalation policy still need to be defined before real use.
