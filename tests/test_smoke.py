import unittest
from unittest.mock import patch
from xml.etree.ElementTree import fromstring

import test_orchestration as fixtures
from src.orchestration.config import Settings
from src.orchestration.smoke import handle_smoke_voice


class SmokeTests(unittest.TestCase):
    def setUp(self):
        # Reuse the fixture without inheriting and rerunning the full test suite.
        fixture = fixtures.OrchestrationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.service, self.provider = fixture.service, fixture.provider
        self.call = fixture.start()
        self.args = (self.service, "https://example.test/webhooks/twilio/smoke/voice",
                     self.call["call_id"], self.call["provider_call_id"])

    def test_prompt_contains_keypad_action(self):
        xml = fromstring(handle_smoke_voice(*self.args))
        gather = xml.find("Gather")
        self.assertEqual(gather.attrib["numDigits"], "1")
        self.assertIn("stage=answer", gather.attrib["action"])
        self.assertIn("only a test", gather.find("Say").text)

    def test_concern_choice_alerts_once_and_is_synthetic(self):
        for _ in range(2):
            fromstring(handle_smoke_voice(*self.args, stage="answer", digits="1"))
        self.assertEqual(len(self.provider.messages), 1)
        self.assertTrue(self.provider.messages[0]["body"].startswith("TEST ONLY:"))
        self.assertIn("SYNTHETIC", self.call["triage"]["summary"])

    def test_normal_choice_does_not_alert(self):
        handle_smoke_voice(*self.args, stage="answer", digits="2")
        self.assertEqual(self.call["triage"]["status"], "NORMAL")
        self.assertEqual(self.provider.messages, [])

    def test_empty_or_invalid_choice_does_not_invent_assessment(self):
        for digits in ("", "9"):
            xml = fromstring(handle_smoke_voice(*self.args, stage="answer", digits=digits))
            self.assertIsNotNone(xml.find("Hangup"))
        self.assertNotIn("triage", self.call)

    def test_wrong_call_id_binding_is_rejected(self):
        with self.assertRaises(ValueError):
            handle_smoke_voice(*self.args[:3], "wrong_sid")

    def test_smoke_mode_supplies_voice_url_and_phone_alias(self):
        with patch.dict("os.environ", {
            "PROVIDER": "twilio", "API_TOKEN": "private-token", "SMOKE_TEST_ENABLED": "true",
            "PUBLIC_BASE_URL": "https://example.test", "TWILIO_ACCOUNT_SID": "test",
            "TWILIO_AUTH_TOKEN": "test", "TWILIO_FROM_NUMBER": "",
            "TWILIO_PHONE_NUMBER": "+12025550101",
        }, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.twilio_from_number, "+12025550101")
        self.assertEqual(settings.effective_voice_url, self.args[1])


if __name__ == "__main__":
    unittest.main()
