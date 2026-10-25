import unittest

import httpx

from scripts.configure_phone import configure_number


class ConfigurePhoneTests(unittest.TestCase):
    def test_only_owned_voice_number_gets_agent_webhook(self):
        requests = []
        def handler(request):
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(200, json={"incoming_phone_numbers": [{
                    "sid": "PNtest", "phone_number": "+15555550100", "capabilities": {"voice": True}}]})
            return httpx.Response(200, json={"voice_url": "https://voice.test/twilio/voice", "voice_method": "POST",
                                             "status_callback": "https://voice.test/twilio/status", "status_callback_method": "POST"})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            configure_number(client, "ACtest", "+15555550100", "https://voice.test")
        self.assertEqual(len(requests), 2)
        self.assertTrue(requests[1].url.path.endswith("/IncomingPhoneNumbers/PNtest.json"))
        self.assertIn(b"VoiceUrl=https%3A%2F%2Fvoice.test%2Ftwilio%2Fvoice", requests[1].content)
        self.assertIn(b"VoiceMethod=POST", requests[1].content)
        self.assertIn(b"StatusCallback=https%3A%2F%2Fvoice.test%2Ftwilio%2Fstatus", requests[1].content)

    def test_unowned_number_is_not_modified(self):
        methods = []
        def handler(request):
            methods.append(request.method)
            return httpx.Response(200, json={"incoming_phone_numbers": []})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(ValueError):
                configure_number(client, "ACtest", "+15555550100", "https://voice.test")
        self.assertEqual(methods, ["GET"])

    def test_missing_completion_callback_confirmation_is_an_error(self):
        def handler(request):
            if request.method == "GET":
                return httpx.Response(200, json={"incoming_phone_numbers": [{
                    "sid": "PNtest", "phone_number": "+15555550100", "capabilities": {"voice": True}}]})
            return httpx.Response(200, json={"voice_url": "https://voice.test/twilio/voice", "voice_method": "POST"})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaisesRegex(ValueError, "completion webhook"):
                configure_number(client, "ACtest", "+15555550100", "https://voice.test")
