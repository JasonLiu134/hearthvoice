# Builder 3: Orchestration, scheduling, and alerts

## Goal

Connect call events and assessments to an SMS alert when a check-in is concerning or missed.

This is a work plan derived from the [project specification](../../README.md). Webhooks, scheduling, and alerting are not implemented yet. FastAPI and Twilio are proposed components, with final backend and gateway choices still open.

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
