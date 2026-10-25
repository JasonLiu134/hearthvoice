# HearthVoice

**Track 2: No App** · Hackathon project specification

HearthVoice is a screen-free daily wellness check-in for older adults. It calls a regular landline or mobile phone, has a short, patient conversation, and alerts a designated caregiver by SMS when a call is missed or a concern is reported. The person receiving the call needs no app, account, or smartphone.

> **Project status:** The repository has a file scaffold and a tested Deepgram STT adapter. Calling, conversation, database, TTS, and alerting integration are still in progress or planned.

See [local installation and testing](SETUP.md) for the Conda environment,
llama.cpp CPU server, Deepgram speech setup, and available test commands.

## The experience

1. At the person's chosen time, HearthVoice places an outbound call. They can also call in for a check-in.
2. The voice agent asks one simple question at a time about mood, meals, medication, pain, and other symptoms.
3. The service compares answers with the person's recorded baseline and assigns a structured risk level.
4. A concerning response or missed check-in triggers an SMS to a designated caregiver with a brief, actionable summary.
5. The call outcome and assessment are recorded for follow-up.

The goal is to provide companionship and earlier awareness of possible problems while keeping the senior's side of the experience entirely on the phone.

## Hackathon scope

| Capability   | Intended behavior                                                                               |
| ------------ | ----------------------------------------------------------------------------------------------- |
| Phone calls  | Scheduled outbound and user-initiated inbound calls over the public telephone network           |
| Conversation | Warm, concise responses, patient turn-taking, and interruption handling                         |
| Triage       | Structured assessment of symptoms, meals, medication adherence, mood, and changes from baseline |
| Alerts       | SMS to a designated caregiver for concerning responses or missed calls                          |
| History      | Profiles, contacts, schedules, call outcomes, transcripts, and assessments                      |

The voice latency target is **under 1.5 seconds per turn**. This is a design target, not a measured result.

## Proposed architecture

```text
Senior's phone (landline or mobile)
              ↕ PSTN
Twilio Voice or Vapi
              ↕ call events and audio
FastAPI orchestration service
  ├── Speech-to-text: Deepgram Flux (current demo path; Nova-2 in the original brief)
  ├── Dialogue and triage: Claude or OpenAI
  ├── Text-to-speech: Deepgram Aura-2
  ├── Profiles, schedules, and logs: Supabase/PostgreSQL
  └── Caregiver alerts: Twilio SMS
```

The current voice pipeline uses Twilio, FastAPI, Deepgram Flux, and Deepgram Aura-2. Other providers in the source specification remain options, not active integrations. Keep triage output structured so the alerting service can act on it without parsing conversational prose.

### Triage contract

The proposed agent response contains a short `spoken_response` for text-to-speech and a separate assessment:

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

The initial rules call for escalation on missed medication, a pain increase of at least three points, shortness of breath, confusion, dizziness, or explicit distress. Normal and low-risk outcomes should remain distinguishable in the stored assessment. A missed call is a separate alert condition because no conversation took place.

**Safety boundary:** HearthVoice is a check-in and caregiver notification concept, not a diagnostic tool or emergency response service. The team must define consent, data retention, access controls, alert retry behavior, and what the agent says during an urgent situation before using it with real people. Do not include sensitive transcripts or medical details in SMS beyond what a caregiver needs to act.
