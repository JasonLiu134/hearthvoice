"""Single-process orchestration. No dependency on dialogue or Builder 4's schema."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re
from threading import RLock
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.services.twilio_client import Rejected

UTC = timezone.utc
CALL_STATES = {"queued", "initiated", "ringing", "in-progress", "completed",
               "busy", "no-answer", "failed", "canceled"}
TERMINAL = {"completed", "busy", "no-answer", "failed", "canceled"}
RANK = {"queued": 0, "initiated": 1, "ringing": 2, "in-progress": 3}


def utcnow():
    return datetime.now(UTC)


class Orchestrator:
    def __init__(self, store, provider, clock=utcnow):
        self.store, self.provider, self.clock = store, provider, clock
        self.lock = RLock()
        self.state = store.load()
        # A process may have died after the provider accepted a create request.
        for collection in ("calls", "alerts"):
            for record in self.state[collection].values():
                if record["status"] == "sending":
                    record["status"] = "unknown"
        self.save()

    def save(self):
        self.store.save(self.state)

    def snapshot(self):
        with self.lock:
            return deepcopy(self.state)

    def put_profile(self, profile):
        required = {"senior_id", "name", "phone", "caregiver_phone", "timezone", "local_call_time"}
        if not required <= profile.keys():
            raise ValueError("Missing profile fields: " + ", ".join(sorted(required - profile.keys())))
        for field in required:
            if not isinstance(profile[field], str) or not profile[field].strip():
                raise ValueError(field + " must be a nonempty string")
        for field in ("phone", "caregiver_phone"):
            if not re.fullmatch(r"\+[1-9]\d{7,14}", profile[field]):
                raise ValueError(field + " must use E.164 format")
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", profile["local_call_time"]):
            raise ValueError("local_call_time must be HH:MM")
        try:
            ZoneInfo(profile["timezone"])
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Unknown timezone") from exc
        if type(profile.get("enabled", True)) is not bool:
            raise ValueError("enabled must be boolean")
        with self.lock:
            if any(p["phone"] == profile["phone"] and key != profile["senior_id"]
                   for key, p in self.state["profiles"].items()):
                raise ValueError("Phone already belongs to another profile")
            clean = {key: profile[key] for key in required}
            clean["enabled"] = profile.get("enabled", True)
            self.state["profiles"][clean["senior_id"]] = clean
            self.save()
            return deepcopy(clean)

    def _check_in(self, senior_id):
        profile = self.state["profiles"][senior_id]
        day = self.clock().astimezone(ZoneInfo(profile["timezone"])).date().isoformat()
        key = senior_id + ":" + day
        return self.state["check_ins"].setdefault(key, {
            "check_in_id": key, "senior_id": senior_id, "status": "pending",
            "call_ids": [], "next_attempt_at": None,
        })

    def start_check_in(self, senior_id, retry_rejected=False):
        with self.lock:
            check = self._check_in(senior_id)
            if retry_rejected and check["call_ids"]:
                last = self.state["calls"][check["call_ids"][-1]]
                if (check["status"] != "technical_failure" or last["status"] != "failed"
                        or last["provider_call_id"] is not None):
                    raise ValueError("Only a rejected call without a provider ID can be retried")
                check["status"] = "pending"
                self._dial(check)
                return deepcopy(check)
            # Repeated API requests and scheduler ticks reuse today's check-in.
            if not check["call_ids"] and check["status"] == "pending":
                self._dial(check)
            return deepcopy(check)

    def _new_call(self, check, direction):
        call_id = uuid4().hex
        call = {"call_id": call_id, "check_in_id": check["check_in_id"],
                "direction": direction, "status": "sending", "provider_call_id": None,
                "created_at": self.clock().isoformat()}
        self.state["calls"][call_id] = call
        check["call_ids"].append(call_id)
        check["next_attempt_at"] = None
        return call

    def _dial(self, check):
        call = self._new_call(check, "outbound")
        self.save()  # Persist intent before making an external request.
        try:
            phone = self.state["profiles"][check["senior_id"]]["phone"]
            call["provider_call_id"] = self.provider.start_call(phone, call["call_id"])
            call["status"] = "queued"
        except Rejected as exc:
            call["status"] = "failed"
            call["provider_error"] = exc.details()
            check["status"] = "technical_failure"
            self._alert(check, "technical_failure")
        except Exception:
            call["status"] = "unknown"
        self.save()

    def inbound(self, phone, provider_call_id):
        if not provider_call_id:
            raise ValueError("Missing provider call ID")
        with self.lock:
            for call in self.state["calls"].values():
                if call["provider_call_id"] == provider_call_id:
                    return deepcopy(call)
            profile = next((p for p in self.state["profiles"].values() if p["phone"] == phone), None)
            if profile is None:
                raise LookupError("Unknown inbound number")
            check = self._check_in(profile["senior_id"])
            call = self._new_call(check, "inbound")
            call.update(status="in-progress", provider_call_id=provider_call_id)
            # An inbound conversation replaces a pending outbound retry.
            if check["status"] != "completed":
                check["status"] = "pending"
            self.save()
            return deepcopy(call)

    def call_event(self, call_id, provider_call_id, status):
        if status not in CALL_STATES or not provider_call_id:
            raise ValueError("Invalid call status or provider call ID")
        with self.lock:
            call = self.state["calls"][call_id]
            if call["provider_call_id"] not in (None, provider_call_id):
                raise ValueError("Provider call ID does not match")
            event_key = "call:" + provider_call_id + ":" + status
            if event_key in self.state["events"]:
                return deepcopy(call)
            self.state["events"][event_key] = {"received_at": self.clock().isoformat()}
            call["provider_call_id"] = provider_call_id
            old = call["status"]
            # Do not let delayed ringing/answered callbacks reopen a finished call.
            if old in TERMINAL or (status not in TERMINAL and RANK.get(old, -1) > RANK[status]):
                self.save()
                return deepcopy(call)
            call["status"] = status
            check = self.state["check_ins"][call["check_in_id"]]
            active = any(self.state["calls"][cid]["status"] not in TERMINAL
                         for cid in check["call_ids"] if cid != call_id)
            if status in TERMINAL and check["status"] != "completed" and not active:
                if status in {"busy", "no-answer"}:
                    attempts = sum(self.state["calls"][cid]["direction"] == "outbound"
                                   for cid in check["call_ids"])
                    if attempts < 2:
                        check["next_attempt_at"] = (self.clock() + timedelta(minutes=5)).isoformat()
                    else:
                        check["status"] = "missed"
                        self._alert(check, "missed")
                elif status == "completed":
                    # Provider completion does not establish a successful assessment.
                    check["assessment_due_at"] = (self.clock() + timedelta(minutes=2)).isoformat()
                else:
                    check["status"] = "technical_failure"
                    self._alert(check, "technical_failure")
            self.save()
            return deepcopy(call)

    def assessment(self, event_id, call_id, triage):
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("event_id is required")
        if not isinstance(triage, dict) or triage.get("status") not in {"NORMAL", "LOW_RISK", "HIGH_RISK"}:
            raise ValueError("triage.status must be NORMAL, LOW_RISK, or HIGH_RISK")
        pain, meds = triage.get("current_pain_level"), triage.get("meds_taken")
        if pain is not None and (type(pain) is not int or not 0 <= pain <= 10):
            raise ValueError("current_pain_level must be an integer from 0 to 10 or null")
        if meds is not None and type(meds) is not bool:
            raise ValueError("meds_taken must be boolean or null")
        with self.lock:
            call = self.state["calls"][call_id]
            payload = {"call_id": call_id, "triage": triage}
            key = "assessment:" + event_id
            if key in self.state["events"]:
                if self.state["events"][key] != payload:
                    raise ValueError("event_id already used with different content")
                return deepcopy(call)
            self.state["events"][key] = deepcopy(payload)
            call["triage"] = deepcopy(triage)
            check = self.state["check_ins"][call["check_in_id"]]
            check["status"] = "completed"
            check["next_attempt_at"] = None
            check.pop("assessment_due_at", None)
            if triage["status"] == "HIGH_RISK":
                self._alert(check, "high_risk")
            self.save()
            return deepcopy(call)

    def _alert(self, check, kind):
        key = check["check_in_id"] + ":" + kind
        if key in self.state["alerts"]:
            return
        # Avoid putting transcript, symptoms, medication, or free text in SMS.
        texts = {
            "high_risk": "A wellness check-in reported a concern. Please contact your designated person now.",
            "missed": "Your designated person did not answer two check-in attempts. Please contact them.",
            "incomplete": "A wellness call ended without a completed check-in. Please contact your designated person.",
            "technical_failure": "HearthVoice could not complete a check-in due to a call failure. Please follow up directly.",
        }
        alert = {"alert_id": key, "check_in_id": check["check_in_id"], "kind": kind,
                 "status": "sending", "provider_message_id": None,
                 "created_at": self.clock().isoformat()}
        self.state["alerts"][key] = alert
        self.save()
        try:
            phone = self.state["profiles"][check["senior_id"]]["caregiver_phone"]
            text = texts[kind]
            if kind == "high_risk" and any(
                self.state["calls"][cid].get("triage", {}).get("synthetic") is True
                for cid in check["call_ids"]
            ):
                text = "TEST ONLY: " + text
            alert["provider_message_id"] = self.provider.send_sms(phone, text, key)
            alert["status"] = "accepted"
        except Rejected as exc:
            alert["status"] = "failed"
            alert["provider_error"] = exc.details()
        except Exception:
            alert["status"] = "unknown"
        self.save()

    def message_event(self, alert_id, provider_message_id, status):
        ranks = {"accepted": 0, "queued": 1, "sending": 2, "sent": 3,
                 "delivered": 4, "read": 5, "failed": 4, "undelivered": 4, "canceled": 4}
        if status not in ranks or not provider_message_id:
            raise ValueError("Invalid message status or provider message ID")
        with self.lock:
            alert = self.state["alerts"][alert_id]
            if alert["provider_message_id"] not in (None, provider_message_id):
                raise ValueError("Provider message ID does not match")
            alert["provider_message_id"] = provider_message_id
            old = alert["status"]
            if old not in {"delivered", "read", "failed", "undelivered", "canceled"} and ranks.get(old, -1) <= ranks[status]:
                alert["status"] = status
            self.save()
            return deepcopy(alert)

    def tick(self):
        with self.lock:
            now = self.clock()
            for profile in self.state["profiles"].values():
                if not profile["enabled"]:
                    continue
                local = now.astimezone(ZoneInfo(profile["timezone"]))
                hour, minute = map(int, profile["local_call_time"].split(":"))
                # Local comparison permits spring-gap catch-up and deduplicates fall folds.
                if local.hour * 60 + local.minute >= hour * 60 + minute:
                    self.start_check_in(profile["senior_id"])
            for check in list(self.state["check_ins"].values()):
                profile = self.state["profiles"][check["senior_id"]]
                if check["status"] != "pending":
                    continue
                if check["next_attempt_at"] and now >= datetime.fromisoformat(check["next_attempt_at"]):
                    # Do not place yesterday's retry after restarting on another local date.
                    today = now.astimezone(ZoneInfo(profile["timezone"])).date().isoformat()
                    if check["check_in_id"].endswith(":" + today) and profile["enabled"]:
                        self._dial(check)
                    else:
                        check["next_attempt_at"] = None
                        check["status"] = "expired"
                if check.get("assessment_due_at") and now >= datetime.fromisoformat(check["assessment_due_at"]):
                    if all(self.state["calls"][cid]["status"] in TERMINAL for cid in check["call_ids"]):
                        check["status"] = "incomplete"
                        self._alert(check, "incomplete")
            self.save()
