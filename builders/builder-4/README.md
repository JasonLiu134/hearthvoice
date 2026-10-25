# Builder 4: Data, integration, and demo

## Goal

Provide persisted call history and a rehearsed end-to-end demonstration using fictional profile data and consenting participants.

This is a work plan derived from the [project specification](../../README.md). Automatic Supabase phone conversation logging is implemented; see [setup and behavior](SUPABASE.md). Profile lookup, demo fixtures, and broader orchestration persistence remain planned.

## Responsibilities

- Design storage for profiles, caregiver contacts, schedules, call outcomes, transcripts, and assessments.
- Expose a database interface for the other builders.
- Seed fictional demo data, including a previous pain score of 3/10 and a designated demo caregiver number.
- Coordinate integration of telephony, dialogue, orchestration, persistence, and SMS.
- Prepare the presentation and a fallback recording or mock event if live telephony fails.

## Planned implementation

- `src/services/database.py`: profiles and call history.
- Database schema and demo seed files, with locations to be agreed when implementation begins.
- Coordinate shared setup in `requirements.txt` and `.env.example` once the team chooses providers; commit placeholders rather than credentials.

These paths refer to the proposed application layout at the repository root. This builder folder holds documentation.

## Handoffs

- Supply Builder 2 with baseline data for historical comparisons.
- Supply Builder 3 with caregiver contacts and schedules, and persist call and alert outcomes.
- Coordinate call identifiers and transcript storage with Builders 1 and 3.
- Agree on schema fields, assessment status values, and database interface contracts with all builders.
- Define consent, retention, and access controls with the team before storing real personal information.

## Demo sequence

1. Seed a fictional profile with baseline pain of 3/10 and a designated demo caregiver number.
2. Place an outbound call to a consenting demo participant.
3. Have the participant report missed medication and pain of 7/10.
4. Show the structured high-risk assessment and resulting caregiver SMS.
5. Show persisted call history and assessment.
6. Rehearse the fallback recording or mock event.

## Demo acceptance

- The baseline profile is retrievable by the conversation service.
- The call outcome and structured assessment are persisted and can be retrieved for presentation.
- The integrated scenario produces the expected high-risk assessment and test SMS.
- Demo fixtures contain fictional profile information; phone numbers belong to consenting demo participants.
- The presentation clearly distinguishes implemented behavior, design targets, and remaining work.

## Milestone order

Prioritize one end-to-end demo call that produces a structured assessment and sends a test SMS. Extend scheduling and historical comparisons after that path works. Define transcript retention and access controls before persisting real conversations.
