import asyncio
import base64
import json
import unittest
from unittest.mock import AsyncMock, patch

from src.core.voice_pipeline import CallSession, SpeechConfig, reply_to


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


CONFIG = SpeechConfig("deepgram", "eleven", "voice")


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
        assert kwargs["params"] == {"output_format": "ulaw_8000"}
        assert kwargs["json"]["model_id"] == "eleven_flash_v2_5"
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

    def test_short_scripted_replies(self):
        self.assertIn("eaten", reply_to("fine", 0))
        self.assertIn("medication", reply_to("yes", 1))


if __name__ == "__main__":
    unittest.main()
