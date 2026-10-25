import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from twilio.base.exceptions import TwilioRestException

from src.services.twilio_client import Rejected, TwilioProvider


class TwilioProviderTests(unittest.TestCase):
    def test_call_request_routes_voice_and_status_with_call_id(self):
        with patch("twilio.rest.Client") as client:
            client.return_value.calls.create.return_value = SimpleNamespace(sid="CAaccepted")
            provider = TwilioProvider("ACtest", "test-token", "+15555550100",
                                      "https://calls.example.test", "https://voice.example.test/twilio/voice")
            self.assertEqual(provider.start_call("+15555550101", "internal-1"), "CAaccepted")
        request = client.return_value.calls.create.call_args.kwargs
        self.assertEqual(request["to"], "+15555550101")
        self.assertEqual(request["from_"], "+15555550100")
        self.assertEqual(parse_qs(urlsplit(request["url"]).query), {"call_id": ["internal-1"]})
        self.assertEqual(urlsplit(request["status_callback"]).path, "/webhooks/twilio/calls")
        self.assertIn("answered", request["status_callback_event"])
        self.assertIn("completed", request["status_callback_event"])

    def test_rejected_call_preserves_status_without_private_provider_message(self):
        method = Mock(side_effect=TwilioRestException(400, "/calls", msg="private phone detail", code=21211))
        with self.assertRaises(Rejected) as error:
            TwilioProvider._create(method)
        self.assertEqual(error.exception.details(), {"code": 21211, "http_status": 400})
        self.assertNotIn("private", str(error.exception))
        method.assert_called_once()

    def test_unknown_call_outcome_is_not_retried(self):
        method = Mock(side_effect=TimeoutError)
        with self.assertRaises(TimeoutError):
            TwilioProvider._create(method)
        method.assert_called_once()

    def test_sms_uses_custom_summary_delivery_callback(self):
        callback = "https://calls.example.test/webhooks/twilio/call-summary?delivery_id=one"
        with patch("twilio.rest.Client") as client:
            client.return_value.messages.create.return_value = SimpleNamespace(sid="SMaccepted")
            provider = TwilioProvider("ACtest", "test-token", "+15555550100",
                                      "https://calls.example.test", "https://calls.example.test/twilio/voice")
            self.assertEqual(provider.send_sms("+15555550101", "Patient-reported update", "one",
                                               status_callback=callback), "SMaccepted")
        client.return_value.messages.create.assert_called_once_with(
            to="+15555550101", from_="+15555550100", body="Patient-reported update", status_callback=callback)
