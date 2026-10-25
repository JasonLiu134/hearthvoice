"""Single-patient demo integration with the existing durable SMS orchestrator."""

import asyncio
import logging
import os
import re
from threading import RLock

from src.core.agent import HearthAgent
from src.orchestration.service import Orchestrator
from src.orchestration.store import SQLiteStore
from src.services.twilio_client import MockProvider, TwilioProvider

log = logging.getLogger(__name__)
_lock = RLock()
_service = None
_store = None
_provider_mode = None


def enabled():
    return os.getenv("SMS_ALERTS_ENABLED", "false").lower() == "true"


def service():
    global _service, _store, _provider_mode
    with _lock:
        if _service is not None:
            return _service
        patient = os.getenv("PATIENT_PHONE_NUMBER", "").strip()
        caregiver = os.getenv("CAREGIVER_PHONE_NUMBER", "").strip()
        for name, number in (("PATIENT_PHONE_NUMBER", patient), ("CAREGIVER_PHONE_NUMBER", caregiver)):
            if not re.fullmatch(r"\+[1-9]\d{7,14}", number):
                raise ValueError(f"Configure {name} in E.164 format")
        mode = os.getenv("SMS_ALERT_PROVIDER", "mock")
        if mode not in {"mock", "twilio"}:
            raise ValueError("SMS_ALERT_PROVIDER must be mock or twilio")
        if mode == "twilio":
            def required(name):
                value = os.getenv(name, "").strip()
                if not value:
                    raise ValueError(f"Configure {name}")
                return value
            base = required("PUBLIC_BASE_URL").rstrip("/")
            sender = os.getenv("TWILIO_SMS_FROM_NUMBER") or required("TWILIO_FROM_NUMBER")
            provider = TwilioProvider(required("TWILIO_ACCOUNT_SID"), required("TWILIO_AUTH_TOKEN"),
                                      sender, base, base + "/twilio/voice")
        else:
            provider = MockProvider()
        store = SQLiteStore(os.getenv("PHONE_ALERTS_DATABASE", "data/phone-alerts.sqlite3"))
        try:
            candidate = Orchestrator(store, provider)
            candidate.put_profile({"senior_id": "phone-patient", "name": "Designated patient",
                                   "phone": patient, "caregiver_phone": caregiver,
                                   "timezone": "UTC", "local_call_time": "00:00", "enabled": False})
        except BaseException:
            store.close()
            raise
        _service, _store, _provider_mode = candidate, store, mode
        return candidate


def record_concern(call_sid, caller, turn, severity):
    """Called in a worker thread; only a configured patient can trigger a text."""
    target = service()
    if caller != os.getenv("PATIENT_PHONE_NUMBER", "").strip():
        return
    call = target.inbound(caller, call_sid)
    target.assessment(f"phone:{call_sid}:{turn}", call["call_id"], {
        "status": "HIGH_RISK", "summary": "Patient-reported concern flagged for human follow-up.",
    })
    # No transcript or symptom details go in the SMS or persisted assessment.
    log.info("SMS concern recorded severity=%s provider=%s", severity, _provider_mode)


async def assess_call(call_sid, caller, turn, history):
    if not enabled() or caller != os.getenv("PATIENT_PHONE_NUMBER", "").strip():
        return
    if not call_sid:
        return
    # Only patient utterances: assistant emergency instructions aren't patient symptoms.
    patient_text = [dict(item) for item in history if item.get("role") == "user"]
    if not patient_text:
        return
    agent = HearthAgent()
    try:
        await asyncio.to_thread(service)  # Validate routing before assessing.
        async with asyncio.timeout(20):
            result = await agent.summarize(patient_text)
        severity = result.get("severity")
        if severity in {"concern", "emergency"}:
            await asyncio.to_thread(record_concern, call_sid, caller, turn, severity)
        else:
            log.info("SMS assessment completed; no concern alert")
    except Exception as exc:
        log.error("SMS assessment/send failed error_type=%s", type(exc).__name__)
    finally:
        await agent.close()


def alert_history():
    target = service() if enabled() else _service
    records = [] if target is None else list(target.snapshot()["alerts"].values())
    return {"enabled": enabled(), "provider": _provider_mode, "alerts": records}


def close():
    global _service, _store, _provider_mode
    with _lock:
        if _store is not None:
            _store.close()
        _service = _store = _provider_mode = None
