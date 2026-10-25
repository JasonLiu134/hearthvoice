import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from src.api.server import app


BASE = "https://voice.example.test"


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "PUBLIC_BASE_URL": BASE,
            "TWILIO_AUTH_TOKEN": "test-auth-token",
            "ADMIN_TOKEN": "admin-secret",
            "TWILIO_VOICE_MODE": "stream",
        })
        self.env.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.env.stop()

    def test_voice_webhook_rejects_unsigned_request(self):
        response = self.client.post("/twilio/voice", data={"CallSid": "CA1"})
        self.assertEqual(response.status_code, 403)

    def test_voice_webhook_returns_bidirectional_stream(self):
        params = {"CallSid": "CA1"}
        signature = RequestValidator("test-auth-token").compute_signature(
            BASE + "/twilio/voice", params
        )
        response = self.client.post("/twilio/voice", data=params,
                                    headers={"X-Twilio-Signature": signature})
        self.assertEqual(response.status_code, 200)
        self.assertIn('<Connect><Stream url="wss://voice.example.test/twilio/media"', response.text)

    def test_outbound_endpoint_requires_admin_token(self):
        response = self.client.post("/calls", json={"to": "+15551234567"})
        self.assertEqual(response.status_code, 403)

    def test_voice_webhook_rejects_tampered_call_id(self):
        params = {"CallSid": "CA1"}
        signature = RequestValidator("test-auth-token").compute_signature(
            BASE + "/twilio/voice?call_id=original", params
        )
        response = self.client.post("/twilio/voice?call_id=changed", data=params,
                                    headers={"X-Twilio-Signature": signature})
        self.assertEqual(response.status_code, 403)

    def test_signed_call_status_callback_accepted_unsigned_rejected(self):
        params = {"CallSid": "CAtest", "CallStatus": "in-progress"}
        signature = RequestValidator("test-auth-token").compute_signature(BASE + "/twilio/status", params)
        with self.assertLogs("src.api.server", level="INFO") as logs:
            response = self.client.post("/twilio/status", data=params,
                                        headers={"X-Twilio-Signature": signature})
        self.assertEqual(response.status_code, 200)
        self.assertIn("in-progress", " ".join(logs.output))
        self.assertEqual(self.client.post("/twilio/status", data=params).status_code, 403)

    def test_status_requires_admin_auth(self):
        self.assertEqual(self.client.get("/calls/CA" + "0" * 32).status_code, 403)

    def test_outbound_call_uses_api_key_and_public_webhook(self):
        sent = {}

        class FakeResponse:
            is_error = False

            def json(self):
                return {"sid": "CA123"}

        class FakeHTTPClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, **kwargs):
                sent.update(url=url, **kwargs)
                return FakeResponse()

        with patch.dict(os.environ, {
            "TWILIO_ACCOUNT_SID": "AC123",
            "TWILIO_API_KEY_SID": "SK123",
            "TWILIO_API_KEY_SECRET": "key-secret",
            "TWILIO_FROM_NUMBER": "+15557654321",
        }), patch("src.api.server.httpx.AsyncClient", return_value=FakeHTTPClient()):
            response = self.client.post("/calls", json={"to": "+15551234567"},
                                        headers={"X-Admin-Token": "admin-secret"})
        self.assertEqual(response.json(), {"call_sid": "CA123", "status": "queued"})
        self.assertEqual(sent["auth"], ("SK123", "key-secret"))
        self.assertEqual(sent["data"]["Url"], BASE + "/twilio/voice")


if __name__ == "__main__":
    unittest.main()
