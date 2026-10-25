"""In-process phone flow; only Twilio/Deepgram network boundaries are mocked."""

import asyncio
import base64
import json
import os
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

import httpx
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from twilio.request_validator import RequestValidator

from src.api.server import app


BASE = "https://phone.example.test"
AUDIO = b"\xff" * 320


class FluxConnection:
    def __init__(self):
        self.messages = asyncio.Queue()
        self.audio = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def send(self, audio):
        self.audio.append(audio)
        await self.messages.put(json.dumps({"type": "TurnInfo", "event": "EndOfTurn",
                                            "transcript": "I feel well today."}))

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.messages.get()


class PhoneE2ETests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "PUBLIC_BASE_URL": BASE, "TWILIO_AUTH_TOKEN": "test-auth",
            "ADMIN_TOKEN": "test-admin", "TWILIO_ACCOUNT_SID": "ACtest",
            "TWILIO_VOICE_MODE": "stream",
            "TWILIO_API_KEY_SID": "SKtest", "TWILIO_API_KEY_SECRET": "test-secret",
            "TWILIO_FROM_NUMBER": "+15555550100", "DEEPGRAM_API_KEY": "test-deepgram",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.requests = []
        self.real_client = httpx.AsyncClient

    def http_client(self, **kwargs):
        return self.real_client(transport=httpx.MockTransport(self.provider), **kwargs)

    def provider(self, request):
        self.requests.append(request)
        if request.url.host == "api.twilio.com":
            return httpx.Response(201, json={"sid": "CAaccepted"})
        self.assertEqual(request.url.host, "api.deepgram.com")
        self.assertEqual(request.headers["authorization"], "Token test-deepgram")
        self.assertEqual(request.url.params["encoding"], "mulaw")
        self.assertEqual(request.url.params["sample_rate"], "8000")
        return httpx.Response(200, content=AUDIO)

    def signature(self, path, params=None):
        base = BASE.replace("https://", "wss://") if path == "/twilio/media" else BASE
        return {"X-Twilio-Signature": RequestValidator("test-auth").compute_signature(
            base + path, params or {})}

    def test_dial_webhook_stream_transcription_and_spoken_reply(self):
        flux = FluxConnection()
        agent_inputs = []
        async def reply_stream(agent, history, user):
            agent_inputs.append([dict(item) for item in history])
            yield "I'm glad you feel well today.[END]"
        with patch("src.api.server.httpx.AsyncClient", side_effect=self.http_client), patch(
            "src.core.voice_pipeline.websockets.connect", return_value=flux
        ) as connect, patch("src.core.agent.HearthAgent.reply_stream", reply_stream):
            dial = self.client.post("/calls", json={"to": "+15555550101"},
                                    headers={"X-Admin-Token": "test-admin"})
            self.assertEqual(dial.status_code, 200)
            self.assertEqual(dial.json()["call_sid"], "CAaccepted")
            params = {"CallSid": "CAaccepted"}
            # Builder 3 attaches its internal ID to the voice URL.
            voice_path = "/twilio/voice?call_id=internal-1"
            voice = self.client.post(voice_path, data=params,
                                     headers=self.signature(voice_path, params))
            self.assertEqual(voice.status_code, 200)
            stream = ElementTree.fromstring(voice.text).find("Connect/Stream")
            self.assertEqual(stream.attrib["url"], "wss://phone.example.test/twilio/media")
            with self.client.websocket_connect("/twilio/media", headers=self.signature("/twilio/media")) as ws:
                ws.send_json({"event": "start", "start": {"streamSid": "MZtest", "mediaFormat": {
                    "encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}}})
                greeting = ws.receive_json()
                self.assertEqual(greeting["event"], "media")
                self.assertEqual(base64.b64decode(greeting["media"]["payload"]), AUDIO)
                ws.send_json({"event": "media", "media": {
                    "payload": base64.b64encode(AUDIO * 2).decode()}})
                self.assertEqual(ws.receive_json()["event"], "clear")
                reply = ws.receive_json()
                self.assertEqual(reply["streamSid"], "MZtest")
                self.assertEqual(base64.b64decode(reply["media"]["payload"]), AUDIO)
                ws.send_json({"event": "stop"})
            self.assertEqual(flux.audio, [AUDIO * 2])
            self.assertEqual(connect.call_args.kwargs["additional_headers"],
                             {"Authorization": "Token test-deepgram"})
        self.assertTrue(flux.closed)
        tts = [json.loads(r.content)["text"] for r in self.requests if r.url.host == "api.deepgram.com"]
        self.assertEqual(len(tts), 2)
        self.assertEqual(tts[1], "I'm glad you feel well today.")
        self.assertEqual(agent_inputs[0][-1], {"role": "user", "content": "I feel well today."})

    def test_unsigned_media_stream_rejected_before_provider_connection(self):
        with patch("src.core.voice_pipeline.websockets.connect") as connect:
            with self.assertRaises(WebSocketDisconnect) as error:
                with self.client.websocket_connect("/twilio/media"):
                    pass
            self.assertEqual(error.exception.code, 1008)
            connect.assert_not_called()

    def test_rejected_dial_is_not_reported_as_connected(self):
        def reject(request):
            return httpx.Response(400, json={"code": 21211, "message": "private recipient detail"})

        def factory(**kwargs):
            return self.real_client(transport=httpx.MockTransport(reject), **kwargs)

        with patch("src.api.server.httpx.AsyncClient", side_effect=factory):
            result = self.client.post("/calls", json={"to": "+15555550101"},
                                      headers={"X-Admin-Token": "test-admin"})
        self.assertEqual(result.status_code, 502)
        self.assertNotIn("call_sid", result.json())
        self.assertEqual(result.json()["detail"]["twilio_code"], 21211)
        self.assertEqual(result.json()["detail"]["twilio_http_status"], 400)
        self.assertNotIn("private", result.text)
