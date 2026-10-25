import asyncio
import base64
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from src.core.voice_pipeline import CallSession, SpeechConfig


class FakeTwilio:
    def __init__(self, incoming=()):
        self.incoming = list(incoming)
        self.sent = []

    async def receive_json(self):
        return self.incoming.pop(0)

    async def send_json(self, message):
        self.sent.append(message)


class FakeDeepgram:
    def __init__(self, incoming=()):
        self.incoming = list(incoming)
        self.sent = []

    async def send(self, message):
        self.sent.append(message)

    def __aiter__(self):
        async def messages():
            for message in self.incoming:
                yield json.dumps(message)
        return messages()


CONFIG = SpeechConfig("deepgram")


class FakeTTSResponse:
    def raise_for_status(self):
        pass

    async def aiter_bytes(self, chunk_size):
        yield b"\xff" * 320


class FakeTTSStream:
    async def __aenter__(self):
        return FakeTTSResponse()

    async def __aexit__(self, *args):
        pass


class FakeHTTPClient:
    def stream(self, method, url, **kwargs):
        assert method == "POST"
        assert url == "https://api.deepgram.com/v1/speak"
        assert kwargs["params"] == {
            "model": "aura-2-thalia-en", "encoding": "mulaw",
            "sample_rate": 8000, "container": "none",
        }
        assert kwargs["headers"] == {"Authorization": "Token deepgram"}
        assert kwargs["json"] == {"text": "Hello"}
        return FakeTTSStream()


class VoicePipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_twilio_audio_is_batched_to_flux_80ms_frames(self):
        audio = base64.b64encode(b"a" * 160).decode()
        events = [{"event": "start", "start": {"streamSid": "MZ1", "mediaFormat": {
            "encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1,
        }}}]
        events += [{"event": "media", "media": {"track": "inbound", "payload": audio}}] * 5
        events.append({"event": "stop"})
        twilio = FakeTwilio(events)
        deepgram = FakeDeepgram()
        session = CallSession(twilio, CONFIG)
        with patch.object(session, "speak", new_callable=AsyncMock):
            await session.receive_twilio(deepgram, None)
        self.assertEqual(deepgram.sent, [b"a" * 640, b"a" * 160])
        self.assertIn("/v2/listen?", CONFIG.deepgram_url)
        self.assertIn("encoding=mulaw", CONFIG.deepgram_url)

    async def test_end_of_turn_starts_reply_and_barge_in_clears_audio(self):
        twilio = FakeTwilio()
        session = CallSession(twilio, CONFIG)
        session.stream_sid = "MZ1"
        deepgram = FakeDeepgram([
            {"type": "TurnInfo", "event": "EndOfTurn", "transcript": "I feel okay"},
            {"type": "TurnInfo", "event": "StartOfTurn"},
        ])

        async def slow_speak(client, text):
            await asyncio.sleep(10)

        with patch.object(session, "speak", side_effect=slow_speak):
            await session.receive_deepgram(deepgram, None)
        self.assertEqual(session.turn, 1)
        self.assertEqual(twilio.sent, [{"event": "clear", "streamSid": "MZ1"}] * 2)

    async def test_tts_audio_is_sent_to_twilio_without_transcoding(self):
        twilio = FakeTwilio()
        session = CallSession(twilio, CONFIG)
        session.stream_sid = "MZ1"
        await session.speak(FakeHTTPClient(), "Hello")
        self.assertEqual(twilio.sent, [{
            "event": "media", "streamSid": "MZ1",
            "media": {"payload": base64.b64encode(b"\xff" * 320).decode()},
        }])

    async def test_tts_failure_does_not_forward_error_body_as_audio(self):
        twilio = FakeTwilio()
        session = CallSession(twilio, CONFIG)
        session.stream_sid = "MZ1"
        transport = httpx.MockTransport(lambda request: httpx.Response(
            401, json={"err_msg": "Invalid credentials"},
        ))
        async with httpx.AsyncClient(transport=transport) as client:
            with self.assertRaises(httpx.HTTPStatusError):
                await session.speak(client, "Hello")
        self.assertEqual(twilio.sent, [])

    async def test_agent_receives_transcript_and_streams_phrases_without_end_marker(self):
        session = CallSession(FakeTwilio(), CONFIG)
        async def reply(history, user):
            self.assertEqual(history[-1]["content"], "I am gardening")
            yield "How is your garden? "
            yield "Tell me more.[EN"
            yield "D]"
        session.agent.reply_stream = reply
        with patch.object(session, "speak", new_callable=AsyncMock) as speak:
            await session.respond(None, "I am gardening")
        self.assertEqual([c.args[1] for c in speak.call_args_list],
                         ["How is your garden?", "Tell me more."])
        self.assertEqual(session.history[-1]["role"], "assistant")

    async def test_agent_failure_speaks_fallback(self):
        session = CallSession(FakeTwilio(), CONFIG)
        async def reply(history, user):
            raise ConnectionError("model unavailable")
            yield
        session.agent.reply_stream = reply
        with patch.object(session, "speak", new_callable=AsyncMock) as speak:
            await session.respond(None, "Hello")
        self.assertIn("trouble responding", speak.call_args.args[1])

    async def test_barge_in_cancels_active_model_and_closes_generator(self):
        session = CallSession(FakeTwilio(), CONFIG)
        session.stream_sid = "MZ1"
        started, closed = asyncio.Event(), asyncio.Event()
        async def reply(history, user):
            try:
                started.set()
                await asyncio.Event().wait()
                yield "Unreachable"
            finally:
                closed.set()
        session.agent.reply_stream = reply
        session.speech_task = asyncio.create_task(session.respond(None, "Hello"))
        await started.wait()
        await session.interrupt()
        self.assertTrue(closed.is_set())
        self.assertTrue(session.speech_task.cancelled())
        self.assertEqual(session.twilio.sent[-1]["event"], "clear")


if __name__ == "__main__":
    unittest.main()
