import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from src.services import call_summary_sms
from src.services.twilio_client import Rejected


CALL = {
    "id": "call-1",
    "user_id": "u1",
    "status": "completed",
    "summary": "Patient reported feeling better after resting.",
    "transcript": "Patient: I feel better.\nAgent: Thank you.",
}


class FakeProvider:
    def __init__(self, failures=None):
        self.messages = []
        self.failures = failures or {}

    def send_sms(self, phone, body, alert_id, *, status_callback=None):
        self.messages.append({"phone": phone, "body": body, "alert_id": alert_id,
                              "status_callback": status_callback})
        failure = self.failures.get(phone)
        if failure:
            raise failure
        return "SM" + str(len(self.messages))


class CallSummarySmsTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.env = patch.dict(os.environ, {
            "POST_CALL_SMS_ENABLED": "true",
            "POST_CALL_SMS_PROVIDER": "twilio",
            "POST_CALL_SMS_DATABASE": os.path.join(self.tempdir.name, "deliveries.sqlite3"),
            "PUBLIC_BASE_URL": "https://voice.example.test",
        }, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    def rows(self):
        with sqlite3.connect(os.environ["POST_CALL_SMS_DATABASE"]) as db:
            return db.execute("SELECT id,phone,status,provider_sid FROM deliveries ORDER BY phone").fetchall()

    def test_caller_is_normalized_and_webhook_retries_send_once_without_database_lookup(self):
        provider = FakeProvider()
        with patch("src.services.database._client", side_effect=AssertionError("Unexpected DB access")), patch(
            "src.services.call_summary_sms._provider", return_value=("twilio", provider)):
            call_summary_sms.notify_caller(CALL, "310-111-6677")
            call_summary_sms.notify_caller(CALL, "+13101116677")
        self.assertEqual([item["phone"] for item in provider.messages], ["+13101116677"])
        self.assertEqual(self.rows()[0][2:], ("accepted", "SM1"))
        self.assertIn("Patient reported feeling better", provider.messages[0]["body"])

    def test_missing_or_anonymous_caller_never_uses_another_recipient(self):
        with patch("src.services.call_summary_sms._provider") as provider:
            for caller in ["", "anonymous", "unknown", None]:
                call_summary_sms.notify_caller(CALL, caller)
        provider.assert_not_called()

    def test_rejected_send_is_recorded_without_retry(self):
        provider = FakeProvider({"+13101116677": Rejected(code=21610, http_status=400)})
        with patch("src.services.call_summary_sms._provider", return_value=("twilio", provider)):
            call_summary_sms.notify_caller(CALL, "+13101116677")
            call_summary_sms.notify_caller(CALL, "+13101116677")
        self.assertEqual(len(provider.messages), 1)
        self.assertEqual(self.rows()[0][2], "failed")

    def test_unknown_provider_outcome_is_not_automatically_retried(self):
        provider = FakeProvider({"+13101116677": TimeoutError("network")})
        with patch(
                "src.services.call_summary_sms._provider", return_value=("twilio", provider)):
            call_summary_sms.notify_caller(CALL, "+13101116677")
            call_summary_sms.notify_caller(CALL, "+13101116677")
        self.assertEqual(len(provider.messages), 1)
        self.assertEqual(self.rows()[0][2], "unknown")

    def test_only_completed_calls_with_summary_and_patient_transcript_send(self):
        provider = FakeProvider()
        with patch(
                "src.services.call_summary_sms._provider", return_value=("twilio", provider)):
            for update in (
                {"status": "in-progress"},
                {"status": "completed", "summary": ""},
                {"status": "completed", "summary": "Ready", "transcript": "Agent: hello"},
            ):
                call_summary_sms.notify_caller({**CALL, **update}, "+13101116677")
        self.assertEqual(provider.messages, [])

    def test_delivery_status_rejects_wrong_sid_and_ignores_late_status(self):
        provider = FakeProvider()
        with patch(
                "src.services.call_summary_sms._provider", return_value=("twilio", provider)):
            call_summary_sms.notify_caller(CALL, "+13101116677")
        delivery_id = self.rows()[0][0]  # deterministic one-row test database
        with self.assertRaises(ValueError):
            call_summary_sms.message_status(delivery_id, "SM-wrong", "delivered")
        call_summary_sms.message_status(delivery_id, "SM1", "delivered")
        call_summary_sms.message_status(delivery_id, "SM1", "queued")
        self.assertEqual(self.rows()[0][2:], ("delivered", "SM1"))


    def test_actual_mock_provider_records_acceptance_without_twilio(self):
        with patch.dict(os.environ, {"POST_CALL_SMS_PROVIDER": "mock"}), patch("src.services.call_summary_sms.TwilioProvider") as live:
            call_summary_sms.notify_caller(CALL, "+13101116677")
        live.assert_not_called()
        record = call_summary_sms.delivery_history()[0]
        self.assertEqual(record["provider"], "mock")
        self.assertEqual(record["status"], "accepted")
        self.assertNotIn("phone", record)

    def test_disabled_never_fetches_contacts_or_sends(self):
        with patch.dict(os.environ, {"POST_CALL_SMS_ENABLED": "false"}), patch(
            "src.services.database._client"
        ) as lookup:
            call_summary_sms.notify_caller(CALL, "+13101116677")
        lookup.assert_not_called()

    def test_early_delivery_callback_is_not_overwritten_by_send_acceptance(self):
        class EarlyCallbackProvider:
            def send_sms(self, phone, body, delivery_id, **kwargs):
                call_summary_sms.message_status(delivery_id, "SMearly", "delivered")
                return "SMearly"
        with patch(
            "src.services.call_summary_sms._provider", return_value=("twilio", EarlyCallbackProvider())
        ):
            call_summary_sms.notify_caller(CALL, "+13101116677")
        self.assertEqual(self.rows()[0][2:], ("delivered", "SMearly"))


if __name__ == "__main__":
    unittest.main()
