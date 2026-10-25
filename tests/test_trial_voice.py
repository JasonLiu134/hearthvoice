import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch
from xml.etree import ElementTree

from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from src.api.server import app
from src.core import trial_voice

BASE = "https://trial.example.test"


class TrialVoiceTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"PUBLIC_BASE_URL": BASE, "TWILIO_AUTH_TOKEN": "test-token",
                                             "POST_CALL_SMS_ENABLED": "false", "SUPABASE_CALL_LOGGING": "false"})
        self.env.start()
        self.addCleanup(self.env.stop)
        trial_voice.sessions.clear()
        self.addCleanup(trial_voice.sessions.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def post(self, path, speech=None, sid="CAtrial"):
        params = {"CallSid": sid}
        if speech is not None:
            params["SpeechResult"] = speech
        sig = RequestValidator("test-token").compute_signature(BASE + path, params)
        return self.client.post(path, data=params, headers={"X-Twilio-Signature": sig})

    def test_signed_back_and_forth_history_retry_and_goodbye(self):
        histories = []
        async def reply(agent, history, user, **kwargs):
            self.assertEqual(kwargs["max_tokens"], 64)
            histories.append([dict(m) for m in history])
            yield "How is your garden?" if len(histories) == 1 else "Goodbye.[END]"
        with patch("src.core.agent.HearthAgent.reply_stream", reply), patch(
            "src.core.agent.HearthAgent.close", new_callable=AsyncMock
        ) as close:
            opening = self.post("/twilio/trial/voice")
            self.assertEqual(opening.status_code, 200)
            root = ElementTree.fromstring(opening.text)
            self.assertIsNone(root.find("Connect"))
            self.assertEqual(root.find("Gather").attrib["input"], "speech")
            first = self.post("/twilio/trial/turn?turn=1", "I like gardening & flowers")
            self.assertIn("How is your garden?", first.text)
            self.assertEqual(self.post("/twilio/trial/turn?turn=1", "retry").text, first.text)
            final = self.post("/twilio/trial/turn?turn=2", "Bye")
            self.assertIsNotNone(ElementTree.fromstring(final.text).find("Hangup"))
            self.assertNotIn("[END]", final.text)
            self.assertEqual(len(histories), 2)
            self.assertEqual(histories[1][-2]["content"], "How is your garden?")
            self.assertEqual(close.await_count, 2)

    def test_unsigned_or_tampered_requests_rejected(self):
        self.assertEqual(self.client.post("/twilio/trial/voice", data={"CallSid": "x"}).status_code, 403)
        params = {"CallSid": "CAtrial", "SpeechResult": "hello"}
        sig = RequestValidator("test-token").compute_signature(BASE + "/twilio/trial/turn?turn=1", params)
        result = self.client.post("/twilio/trial/turn?turn=2", data=params, headers={"X-Twilio-Signature": sig})
        self.assertEqual(result.status_code, 403)

    def test_silence_and_turn_limit_end_without_model(self):
        self.post("/twilio/trial/voice")
        with patch("src.core.agent.HearthAgent.reply_stream") as model:
            for turn in range(1, 5):
                result = self.post(f"/twilio/trial/turn?turn={turn}", "")
            model.assert_not_called()
        self.assertIsNotNone(ElementTree.fromstring(result.text).find("Hangup"))
        self.assertEqual(len(trial_voice.sessions["CAtrial"].history), 0)

    def test_timeout_returns_spoken_fallback_and_closes_model(self):
        async def slow(agent, history, user, **kwargs):
            await asyncio.sleep(10)
            yield "too late"
        self.post("/twilio/trial/voice")
        with patch("src.core.agent.HearthAgent.reply_stream", slow), patch(
            "src.core.agent.HearthAgent.close", new_callable=AsyncMock
        ) as close, patch("src.core.trial_voice.MODEL_TIMEOUT", 0.01):
            result = self.post("/twilio/trial/turn?turn=1", "Hello")
        self.assertEqual(result.status_code, 200)
        self.assertIn("trouble responding", result.text)
        close.assert_awaited_once()
        self.assertEqual(trial_voice.sessions["CAtrial"].history, [])

    def test_expired_session_and_call_isolation(self):
        self.post("/twilio/trial/voice", sid="CAone")
        self.post("/twilio/trial/voice", sid="CAtwo")
        trial_voice.sessions["CAone"].expires = 0
        result = self.post("/twilio/trial/turn?turn=1", "Hello", sid="CAone")
        self.assertIn("expired", result.text)
        self.assertEqual(trial_voice.sessions["CAtwo"].next_turn, 1)

    def test_xml_escapes_agent_text(self):
        root = ElementTree.fromstring(trial_voice.twiml("A < B & C", BASE, 1))
        self.assertEqual(root.find("Say").text, "A < B & C")

    def test_purchased_number_common_webhook_uses_trial_mode(self):
        with patch.dict(os.environ, {"TWILIO_VOICE_MODE": "trial"}):
            response = self.post("/twilio/voice")
        self.assertEqual(response.status_code, 200)
        root = ElementTree.fromstring(response.text)
        self.assertIsNotNone(root.find("Gather"))
        self.assertIsNone(root.find("Connect"))

    def test_call_transcript_reaches_supabase_and_finalizes_from_signed_callback(self):
        import json
        from pathlib import Path
        import tempfile
        async def reply(agent, history, user, **kwargs):
            yield "Goodbye.[END]"
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "SUPABASE_CALL_LOGGING": "true", "SUPABASE_USER_ID": "u1",
            "PATIENT_PHONE_NUMBER": "+15551234567", "CALL_LOG_DIRECTORY": directory,
        }), patch("src.services.database._upload") as upload, patch(
            "src.core.agent.HearthAgent.reply_stream", reply
        ), patch("src.core.agent.HearthAgent.summarize", new_callable=AsyncMock,
                 return_value={"summary": "Patient said hello."}), patch(
                     "src.core.agent.HearthAgent.close", new_callable=AsyncMock):
            params = {"CallSid": "CAlogged", "From": "+15551234567"}
            sig = RequestValidator("test-token").compute_signature(BASE + "/twilio/trial/voice", params)
            result = self.client.post("/twilio/trial/voice", data=params,
                                      headers={"X-Twilio-Signature": sig})
            self.assertEqual(result.status_code, 200)
            self.post("/twilio/trial/turn?turn=1", "Hello", sid="CAlogged")
            params["CallStatus"] = "completed"
            sig = RequestValidator("test-token").compute_signature(BASE + "/twilio/status", params)
            result = self.client.post("/twilio/status", data=params,
                                      headers={"X-Twilio-Signature": sig})
            self.assertEqual(result.status_code, 200)
            row = json.loads(next(Path(directory).glob("*.json")).read_text())
            self.assertEqual(row["status"], "completed")
            self.assertIn("Patient: Hello\nAgent: Goodbye.", row["transcript"])
            self.assertEqual(row["summary"], "Patient said hello.")
            self.assertGreaterEqual(upload.call_count, 3)
