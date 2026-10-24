# Builder 2: Dialogue and structured triage

## Goal

Turn a live transcript into a warm, senior-friendly spoken response and a clear structured assessment.

This is a work plan derived from the [project specification](../../README.md). Dialogue and triage are not implemented yet; Claude or OpenAI remains a team decision.

## Responsibilities

- Ask one simple question at a time about mood, meals, medication, pain, and other symptoms.
- Keep responses concise and allow patient turn-taking.
- Compare reported answers with the person's recorded baseline.
- Produce separate `spoken_response` and `triage` fields so downstream services do not need to parse conversational prose.
- Distinguish normal, low-risk, and high-risk assessments. A missed call is a separate condition owned by Builder 3.

## Planned implementation

- `src/core/agent.py`: conversation behavior.
- `src/core/triage.py`: assessment rules.
- `tests/test_agent.py`: dialogue and assessment validation.

These paths refer to the proposed application layout at the repository root. This builder folder holds documentation.

## Proposed output contract

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

The initial specification calls for escalation on missed medication, a pain increase of at least three points, shortness of breath, confusion, dizziness, or explicit distress. Agree on the remaining status values and handling of incomplete answers before integration.

## Handoffs

- Receive recognized speech and call context from Builder 1; return text for speech synthesis.
- Receive baseline and profile data through Builder 4's database interface.
- Supply structured assessments to Builder 3 for alert decisions and Builder 4 for persistence.

## Demo acceptance

- A fictional baseline pain score of 3/10 followed by reported pain of 7/10 and missed medication produces `HIGH_RISK`.
- Validate each specified escalation trigger and the three-point pain-increase boundary.
- Verify normal and low-risk outcomes remain distinguishable.
- Return valid structured output with a short spoken response.

## Safety boundary

HearthVoice is a check-in and caregiver notification concept, not a diagnostic tool or emergency response service. The team must define urgent-situation wording before use with real people. Keep sensitive transcript details out of caregiver SMS beyond what is needed to act.
