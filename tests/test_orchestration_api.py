"""HTTP integration tests; install requirements-dev.txt to enable."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

HAS_DEPS = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx", "twilio"))
if HAS_DEPS:
    from fastapi.testclient import TestClient
    from twilio.request_validator import RequestValidator
    from src.api.builder3_server import create_app
    from src.orchestration.config import Settings
    from src.orchestration.service import Orchestrator
    from src.orchestration.store import SQLiteStore
    from src.services.twilio_client import MockProvider


@unittest.skipUnless(HAS_DEPS, "Install requirements-dev.txt for HTTP integration tests")
class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SQLiteStore(str(Path(self.temp.name) / "test.sqlite3"))
        self.addCleanup(self.store.close)
        self.provider = MockProvider()
        self.service = Orchestrator(self.store, self.provider)
        self.settings = Settings()
        self.client = self.make_client(self.settings)
        self.auth = {"Authorization": "Bearer local-demo-token"}
        self.profile = {"name": "Fictional", "phone": "+12025550101",
                        "caregiver_phone": "+12025550102", "timezone": "America/Los_Angeles",
                        "local_call_time": "09:00"}

    def make_client(self, settings):
        client = TestClient(create_app(settings, self.service))
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        return client

    def start(self):
        response = self.client.put("/profiles/demo", json=self.profile, headers=self.auth)
        self.assertEqual(response.status_code, 200)
        response = self.client.post("/calls", json={"senior_id": "demo"}, headers=self.auth)
        self.assertEqual(response.status_code, 200)
        return response.json()["call_ids"][0]

    def test_auth_and_health(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/history").status_code, 401)
        self.assertEqual(self.client.get("/history", headers=self.auth).status_code, 200)

    def test_high_risk_http_handoff(self):
        call_id = self.start()
        event = {"event_id": "evt1", "call_id": call_id, "triage": {"status": "HIGH_RISK"}}
        for _ in range(2):
            self.assertEqual(self.client.post("/events/assessments", json=event, headers=self.auth).status_code, 200)
        self.assertEqual(len(self.provider.messages), 1)

    def test_invalid_or_missing_record(self):
        self.assertEqual(self.client.post("/calls", json={"senior_id": "missing"}, headers=self.auth).status_code, 404)
        self.assertEqual(self.client.put("/profiles/demo", json={}, headers=self.auth).status_code, 422)

    def test_triage_validation(self):
        call_id = self.start()
        event = {"event_id": "evt1", "call_id": call_id,
                 "triage": {"status": "HIGH_RISK", "current_pain_level": True}}
        self.assertEqual(self.client.post("/events/assessments", json=event, headers=self.auth).status_code, 422)

    def test_twilio_disabled_in_mock_mode(self):
        self.assertEqual(self.client.post("/webhooks/twilio/inbound").status_code, 404)

    def test_twilio_signature_and_inbound_redirect(self):
        self.start()
        settings = Settings(provider="twilio", api_token="private-token", twilio_account_sid="ACtest",
                            twilio_auth_token="secret", twilio_from_number="+12025550104",
                            public_base_url="https://example.test", voice_url="https://voice.test/twiml")
        client = self.make_client(settings)
        path = "/webhooks/twilio/inbound"
        form = {"From": self.profile["phone"], "CallSid": "CAinbound"}
        self.assertEqual(client.post(path, data=form).status_code, 403)
        signature = RequestValidator("secret").compute_signature(settings.public_base_url + path, form)
        result = client.post(path, data=form, headers={"X-Twilio-Signature": signature})
        self.assertEqual(result.status_code, 200)
        self.assertIn("https://voice.test/twiml?call_id=", result.text)

    def test_scripted_voice_disabled_by_default(self):
        self.assertEqual(self.client.post(
            "/webhooks/twilio/smoke/voice?call_id=unknown").status_code, 404)

    def test_scripted_voice_signed_choice_sends_one_test_sms(self):
        call_id = self.start()
        call = self.service.snapshot()["calls"][call_id]
        settings = Settings(provider="twilio", api_token="private-token", twilio_account_sid="ACtest",
                            twilio_auth_token="secret", twilio_from_number="+12025550104",
                            public_base_url="https://example.test", smoke_test_enabled=True)
        client = self.make_client(settings)
        path = "/webhooks/twilio/smoke/voice?call_id=" + call_id + "&stage=answer"
        form = {"CallSid": call["provider_call_id"], "Digits": "1"}
        self.assertEqual(client.post(path, data=form).status_code, 403)
        signature = RequestValidator("secret").compute_signature(settings.public_base_url + path, form)
        for _ in range(2):
            result = client.post(path, data=form, headers={"X-Twilio-Signature": signature})
            self.assertEqual(result.status_code, 200)
            self.assertIn("<Say>", result.text)
        self.assertEqual(len(self.provider.messages), 1)
        self.assertTrue(self.provider.messages[0]["body"].startswith("TEST ONLY:"))


if __name__ == "__main__":
    unittest.main()
