"""Signed phone webhook -> summary -> caller SMS -> Twilio boundary (all external I/O mocked)."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from src.api.server import app
from src.core import trial_voice


class CallSummaryFlowTests(unittest.TestCase):
    def test_completed_conversation_notifies_caller_once_and_tracks_delivery(self):
        base = "https://summary.example.test"
        provider = Mock()
        provider.send_sms.return_value = "SMfirst"
        async def reply(agent, history, user, **kwargs):
            yield "Thank you. Goodbye.[END]"
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            "PUBLIC_BASE_URL": base, "TWILIO_AUTH_TOKEN": "test-token", "ADMIN_TOKEN": "test-admin",
            "SUPABASE_CALL_LOGGING": "true", "SUPABASE_USER_ID": "u1",
            "PATIENT_PHONE_NUMBER": "+15551234567", "CALL_LOG_DIRECTORY": folder,
            "SMS_ALERTS_ENABLED": "false", "POST_CALL_SMS_ENABLED": "true",
            "POST_CALL_SMS_DATABASE": str(Path(folder) / "messages.sqlite3"),
        }), patch("src.services.database._upload"), patch("src.services.database._client", side_effect=AssertionError("Unexpected contacts lookup")), patch("src.services.call_summary_sms._provider", return_value=("twilio", provider)), patch(
            "src.core.agent.HearthAgent.reply_stream", reply
        ), patch("src.core.agent.HearthAgent.summarize", new_callable=AsyncMock,
                 return_value={"summary": "Patient reported sleeping well."}), patch(
                     "src.core.agent.HearthAgent.close", new_callable=AsyncMock), TestClient(app) as client:
            def post(path, params):
                sig = RequestValidator("test-token").compute_signature(base + path, params)
                return client.post(path, data=params, headers={"X-Twilio-Signature": sig})
            params = {"CallSid": "CAsummary", "From": "+15551234567"}
            self.assertEqual(post("/twilio/trial/voice", params).status_code, 200)
            self.assertEqual(post("/twilio/trial/turn?turn=1", {**params, "SpeechResult": "I slept well"}).status_code, 200)
            provider.send_sms.assert_not_called()
            for _ in range(2):
                self.assertEqual(post("/twilio/status", {**params, "CallStatus": "completed"}).status_code, 200)
            self.assertEqual(provider.send_sms.call_count, 1)
            self.assertEqual({c.args[0] for c in provider.send_sms.call_args_list},
                             {"+15551234567"})
            self.assertIn("Patient reported sleeping well.", provider.send_sms.call_args.args[1])
            self.assertEqual(client.get("/calls/CAsummary/messages").status_code, 403)
            history = client.get("/calls/CAsummary/messages", headers={"X-Admin-Token": "test-admin"}).json()["messages"]
            self.assertEqual(len(history), 1)
            self.assertNotIn("phone", history[0])
            delivery = next(item for item in history if item["provider_sid"] == "SMfirst")
            path = "/webhooks/twilio/call-summary?delivery_id=" + delivery["id"]
            body = {"MessageSid": "SMfirst", "MessageStatus": "delivered"}
            self.assertEqual(client.post(path, data=body).status_code, 403)
            self.assertEqual(post(path, body).status_code, 200)
            history = client.get("/calls/CAsummary/messages", headers={"X-Admin-Token": "test-admin"}).json()["messages"]
            self.assertEqual(next(item for item in history if item["provider_sid"] == "SMfirst")["status"], "delivered")
        trial_voice.sessions.pop("CAsummary", None)


class FinalStreamSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_completion_waits_for_final_stream_transcript(self):
        import asyncio
        from types import SimpleNamespace
        from src.api.server import active_sessions, finalize_phone_call
        stream = SimpleNamespace(caller="+15551234567", transcript=[], finished=asyncio.Event())
        with patch.dict(active_sessions, {"CAwaiting": stream}), patch(
            "src.services.database.finish_call", new_callable=AsyncMock
        ) as finalize:
            task = asyncio.create_task(finalize_phone_call({"CallSid": "CAwaiting", "CallStatus": "completed"}))
            await asyncio.sleep(0)
            finalize.assert_not_awaited()
            stream.transcript.append({"role": "user", "content": "Final patient sentence"})
            stream.finished.set()
            await task
            finalize.assert_awaited_once_with("CAwaiting", "+15551234567", stream.transcript, "completed")
