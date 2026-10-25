import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.services import phone_alerts


class PhoneAlertTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "SMS_ALERTS_ENABLED": "true",
            "SMS_ALERT_PROVIDER": "mock",
            "PATIENT_PHONE_NUMBER": "+12025550101",
            "CAREGIVER_PHONE_NUMBER": "+12025550102",
            "PHONE_ALERTS_DATABASE": str(Path(self.temp.name) / "alerts.sqlite3"),
        }, clear=False)
        self.env.start()
        phone_alerts.close()
        self.addCleanup(phone_alerts.close)
        self.addCleanup(self.env.stop)
        self.addCleanup(self.temp.cleanup)

    def test_high_risk_assessment_sends_one_mock_sms(self):
        async def summary(agent, transcript):
            return {"severity": "concern", "summary": "private details omitted"}

        with patch.object(phone_alerts.HearthAgent, "summarize", summary), patch.object(
            phone_alerts.HearthAgent, "close", new_callable=AsyncMock
        ):
            asyncio.run(phone_alerts.assess_call(
                "CA-alert", "+12025550101", 1,
                [{"role": "user", "content": "I feel unwell"}],
            ))
            # A retry of the same turn is idempotent at the orchestrator layer.
            asyncio.run(phone_alerts.assess_call(
                "CA-alert", "+12025550101", 1,
                [{"role": "user", "content": "I feel unwell"}],
            ))

        target = phone_alerts.service()
        self.assertEqual(len(target.provider.messages), 1)
        self.assertNotIn("unwell", target.provider.messages[0]["body"])
        alert = next(iter(target.snapshot()["alerts"].values()))
        self.assertEqual(alert["status"], "accepted")

    def test_normal_assessment_does_not_text(self):
        async def summary(agent, transcript):
            return {"severity": "ok", "summary": "fine"}

        with patch.object(phone_alerts.HearthAgent, "summarize", summary):
            asyncio.run(phone_alerts.assess_call(
                "CA-normal", "+12025550101", 1,
                [{"role": "user", "content": "I am okay"}],
            ))
        self.assertEqual(phone_alerts.service().provider.messages, [])

    def test_unmatched_caller_and_disabled_alerts_are_ignored(self):
        with patch.object(phone_alerts.HearthAgent, "summarize", new_callable=AsyncMock) as summarize:
            asyncio.run(phone_alerts.assess_call(
                "CA-other", "+12025550999", 1,
                [{"role": "user", "content": "help"}],
            ))
            self.assertFalse(summarize.called)
        phone_alerts.close()
        with patch.dict(os.environ, {"SMS_ALERTS_ENABLED": "false"}), patch.object(
            phone_alerts.HearthAgent, "summarize", new_callable=AsyncMock
        ) as summarize:
            asyncio.run(phone_alerts.assess_call(
                "CA-disabled", "+12025550101", 1,
                [{"role": "user", "content": "help"}],
            ))
            self.assertFalse(summarize.called)


if __name__ == "__main__":
    unittest.main()
