import os
import unittest
from unittest.mock import patch

import httpx

from scripts.call_agent import request_call
from src.api.server import twilio_credentials
from fastapi import HTTPException


class CallAgentTests(unittest.TestCase):
    def test_cli_posts_destination_and_backend_auth(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"call_sid": "CAtest", "status": "queued"})
        real_client = httpx.Client
        with patch("scripts.call_agent.httpx.Client", side_effect=lambda **kw: real_client(
            transport=httpx.MockTransport(handler), **kw)):
            result = request_call("http://backend.test", "admin-test", to="+15555550101")
        self.assertEqual(result["status"], "queued")
        self.assertEqual(requests[0].url.path, "/calls")
        self.assertEqual(requests[0].headers["x-admin-token"], "admin-test")
        self.assertIn(b'+15555550101', requests[0].content)
        self.assertEqual(len(requests), 1)

    def test_status_query_does_not_place_another_call(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"call_sid": "CAtest", "status": "in-progress"})
        real_client = httpx.Client
        with patch("scripts.call_agent.httpx.Client", side_effect=lambda **kw: real_client(
            transport=httpx.MockTransport(handler), **kw)):
            request_call("http://backend.test", "admin-test", sid="CAtest")
        self.assertEqual(requests[0].method, "GET")
        self.assertEqual(requests[0].url.path, "/calls/CAtest")

    def test_account_token_used_when_no_api_key_pair(self):
        with patch.dict(os.environ, {"TWILIO_API_KEY_SID": "", "TWILIO_API_KEY_SECRET": "",
                                     "TWILIO_ACCOUNT_SID": "ACtest", "TWILIO_AUTH_TOKEN": "test-token"}):
            self.assertEqual(twilio_credentials(), ("ACtest", "test-token"))

    def test_incomplete_api_key_pair_is_rejected(self):
        with patch.dict(os.environ, {"TWILIO_API_KEY_SID": "SKtest", "TWILIO_API_KEY_SECRET": ""}):
            with self.assertRaises(HTTPException):
                twilio_credentials()
