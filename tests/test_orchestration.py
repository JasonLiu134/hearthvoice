import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.orchestration.config import Settings
from src.orchestration.service import Orchestrator
from src.orchestration.store import SQLiteStore
from src.services.twilio_client import MockProvider, Rejected


class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "state.sqlite3")
        self.store = SQLiteStore(self.path)
        self.addCleanup(self.store.close)
        self.now = datetime(2026, 10, 24, 16, tzinfo=timezone.utc)
        self.provider = MockProvider()
        self.service = Orchestrator(self.store, self.provider, lambda: self.now)
        self.profile = {
            "senior_id": "demo", "name": "Fictional Demo",
            "phone": "+12025550101", "caregiver_phone": "+12025550102",
            "timezone": "America/Los_Angeles", "local_call_time": "09:00",
        }
        self.service.put_profile(self.profile)

    def start(self):
        check = self.service.start_check_in("demo")
        return self.service.state["calls"][check["call_ids"][-1]]

    def status(self, call, status):
        return self.service.call_event(call["call_id"], call["provider_call_id"], status)

    def assess(self, call, status="HIGH_RISK", event_id="evt1"):
        return self.service.assessment(event_id, call["call_id"], {
            "status": status, "meds_taken": False, "current_pain_level": 7,
            "summary": "Sensitive information must not appear in SMS",
        })

    def test_high_risk_alert_is_persisted_and_deduplicated(self):
        call = self.start()
        self.assess(call)
        self.assess(call)
        self.assess(call, event_id="different-event-same-check-in")
        self.assertEqual(len(self.provider.messages), 1)
        self.assertNotIn("Sensitive", self.provider.messages[0]["body"])
        persisted = self.store.load()
        self.assertEqual(next(iter(persisted["alerts"].values()))["status"], "accepted")
        self.assertEqual(persisted["calls"][call["call_id"]]["triage"]["status"], "HIGH_RISK")

    def test_normal_and_low_risk_do_not_alert(self):
        call = self.start()
        for status in ("NORMAL", "LOW_RISK"):
            self.assess(call, status, status)
        self.assertEqual(self.provider.messages, [])

    def test_missed_check_in_waits_for_second_attempt(self):
        first = self.start()
        self.status(first, "no-answer")
        self.status(first, "no-answer")
        self.assertEqual(len(self.provider.calls), 1)
        self.now += timedelta(minutes=4)
        self.service.tick()
        self.assertEqual(len(self.provider.calls), 1)
        self.now += timedelta(minutes=1)
        self.service.tick()
        second = list(self.service.state["calls"].values())[-1]
        self.status(second, "busy")
        self.status(second, "busy")
        self.assertEqual(len(self.provider.calls), 2)
        self.assertEqual(len(self.provider.messages), 1)
        self.assertEqual(next(iter(self.service.state["alerts"].values()))["kind"], "missed")

    def test_completed_call_needs_assessment(self):
        call = self.start()
        self.status(call, "completed")
        self.assertEqual(self.provider.messages, [])
        self.now += timedelta(minutes=2)
        self.service.tick()
        self.assertEqual(next(iter(self.service.state["alerts"].values()))["kind"], "incomplete")

    def test_assessment_prevents_incomplete_alert(self):
        call = self.start()
        self.status(call, "completed")
        self.assess(call, "NORMAL")
        self.now += timedelta(minutes=3)
        self.service.tick()
        self.assertEqual(self.provider.messages, [])

    def test_technical_failure_is_not_missed(self):
        call = self.start()
        self.status(call, "failed")
        self.assertEqual(next(iter(self.service.state["alerts"].values()))["kind"], "technical_failure")

    def test_out_of_order_call_callbacks_cannot_reopen_call(self):
        call = self.start()
        self.status(call, "in-progress")
        self.status(call, "ringing")
        self.assertEqual(call["status"], "in-progress")
        self.status(call, "completed")
        self.status(call, "initiated")
        self.assertEqual(call["status"], "completed")

    def test_inbound_satisfies_check_in_and_cancels_retry(self):
        first = self.start()
        self.status(first, "no-answer")
        inbound = self.service.inbound(self.profile["phone"], "inbound_sid")
        repeated = self.service.inbound(self.profile["phone"], "inbound_sid")
        self.assertEqual(inbound, repeated)
        self.assess(inbound, "NORMAL")
        self.now += timedelta(minutes=6)
        self.service.tick()
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.provider.messages, [])

    def test_timezone_schedule_and_restart_deduplication(self):
        self.now -= timedelta(minutes=1)
        self.service.tick()
        self.assertEqual(self.provider.calls, [])
        self.now += timedelta(minutes=1)
        self.service.tick()
        restarted = Orchestrator(self.store, self.provider, lambda: self.now)
        restarted.tick()
        self.assertEqual(len(self.provider.calls), 1)

    def test_dst_fall_back_calls_once(self):
        self.service.put_profile({**self.profile, "local_call_time": "01:30"})
        self.now = datetime(2026, 11, 1, 8, 30, tzinfo=timezone.utc)
        self.service.tick()
        self.now += timedelta(hours=1)
        self.service.tick()
        self.assertEqual(len(self.provider.calls), 1)

    def test_dst_spring_gap_catches_up(self):
        self.service.put_profile({**self.profile, "local_call_time": "02:30"})
        self.now = datetime(2026, 3, 8, 9, 59, tzinfo=timezone.utc)
        self.service.tick()
        self.assertEqual(self.provider.calls, [])
        self.now += timedelta(minutes=1)
        self.service.tick()
        self.assertEqual(len(self.provider.calls), 1)

    def test_previous_day_retry_is_expired(self):
        call = self.start()
        self.status(call, "no-answer")
        self.now += timedelta(days=1)
        self.service.tick()
        self.assertEqual(len(self.provider.calls), 2)  # Today's first attempt only.
        self.assertEqual(next(iter(self.service.state["check_ins"].values()))["status"], "expired")

    def test_disabled_profile_is_not_scheduled(self):
        self.service.put_profile({**self.profile, "enabled": False})
        self.service.tick()
        self.assertEqual(self.provider.calls, [])

    def test_ambiguous_sms_failure_is_not_automatically_retried(self):
        def timeout(*args):
            raise TimeoutError()
        self.provider.send_sms = timeout
        call = self.start()
        self.assess(call)
        alert = next(iter(self.service.state["alerts"].values()))
        self.assertEqual(alert["status"], "unknown")
        self.service.message_event(alert["alert_id"], "late_sid", "delivered")
        self.assess(call, event_id="another")
        self.assertEqual(alert["status"], "delivered")
        self.assertEqual(len(self.service.state["alerts"]), 1)

    def test_ambiguous_call_failure_does_not_redial(self):
        def timeout(*args):
            raise TimeoutError()
        self.provider.start_call = timeout
        call = self.start()
        self.service.tick()
        self.assertEqual(call["status"], "unknown")
        self.assertEqual(len(self.service.state["calls"]), 1)
        self.service.call_event(call["call_id"], "late_sid", "in-progress")
        self.assertEqual(call["status"], "in-progress")

    def test_restart_marks_unfinished_send_unknown(self):
        call = self.start()
        call["status"] = "sending"
        self.service.save()
        restarted = Orchestrator(self.store, self.provider, lambda: self.now)
        self.assertEqual(restarted.state["calls"][call["call_id"]]["status"], "unknown")

    def test_definite_rejection_is_recorded(self):
        def rejected(*args):
            raise Rejected()
        self.provider.start_call = rejected
        self.provider.send_sms = rejected
        self.assertEqual(self.start()["status"], "failed")
        self.assertEqual(next(iter(self.service.state["alerts"].values()))["status"], "failed")

    def test_message_status_does_not_regress(self):
        self.assess(self.start())
        alert = next(iter(self.service.state["alerts"].values()))
        args = alert["alert_id"], alert["provider_message_id"]
        self.service.message_event(*args, "delivered")
        self.service.message_event(*args, "queued")
        self.assertEqual(alert["status"], "delivered")

    def test_conflicting_assessment_event_is_rejected(self):
        call = self.start()
        self.assess(call)
        with self.assertRaises(ValueError):
            self.assess(call, "NORMAL")

    def test_invalid_inputs_and_mismatched_provider_ids(self):
        for change in ({"timezone": "invalid"}, {"local_call_time": "25:00"}, {"phone": "123"}):
            with self.assertRaises(ValueError):
                self.service.put_profile({**self.profile, **change})
        call = self.start()
        with self.assertRaises(ValueError):
            self.service.call_event(call["call_id"], "wrong_sid", "completed")
        with self.assertRaises(ValueError):
            self.service.assessment("bad", call["call_id"], {"status": "UNKNOWN"})

    def test_live_mode_requires_credentials_and_private_token(self):
        with self.assertRaises(ValueError):
            Settings(provider="twilio").validate()

    def test_rejected_call_can_be_explicitly_retried_and_keeps_error_code(self):
        provider = self.provider.start_call
        def rejected(*args):
            raise Rejected(code=20003, http_status=401)
        self.provider.start_call = rejected
        call = self.start()
        self.assertEqual(call["provider_error"], {"code": 20003, "http_status": 401})
        self.provider.start_call = provider
        check = self.service.start_check_in("demo", retry_rejected=True)
        self.assertEqual(len(check["call_ids"]), 2)
        self.assertEqual(self.service.state["calls"][check["call_ids"][-1]]["status"], "queued")
        with self.assertRaises(ValueError):
            self.service.start_check_in("demo", retry_rejected=True)

    def test_unknown_send_cannot_be_explicitly_retried(self):
        def timeout(*args):
            raise TimeoutError()
        self.provider.start_call = timeout
        self.start()
        with self.assertRaises(ValueError):
            self.service.start_check_in("demo", retry_rejected=True)


if __name__ == "__main__":
    unittest.main()
