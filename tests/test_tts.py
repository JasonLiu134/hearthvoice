import asyncio
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import AsyncMock, patch

from scripts.test_tts import synthesize
from src.core.tts import AuraEvent, AuraOptions, AuraTTSClient, AuraTTSError


class AuraTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.socket = AsyncMock()
        self.connection = AsyncMock()
        self.connection.__aenter__.return_value = self.socket
        patcher = patch("src.core.tts.websockets.connect", return_value=self.connection)
        self.connect = patcher.start()
        self.addCleanup(patcher.stop)

    def messages(self, *items):
        async def receive():
            for item in items:
                yield json.dumps(item) if isinstance(item, dict) else item
        self.socket.__aiter__.side_effect = receive

    async def test_text_and_audio_stream_concurrently_before_flush_completes(self):
        spoken = asyncio.Event()

        async def send(raw):
            if json.loads(raw)["type"] == "Speak":
                spoken.set()

        async def receive():
            await spoken.wait()
            yield json.dumps({"type": "Metadata"})
            yield b"\xff" * 160
            yield json.dumps({"type": "Flushed", "sequence_id": 0})

        self.socket.send.side_effect = send
        self.socket.__aiter__.side_effect = receive
        async with AuraTTSClient("test-key") as tts:
            async def first_audio():
                async for event in tts.events():
                    if event.kind == "audio":
                        return event.audio

            reader = asyncio.create_task(first_audio())
            await tts.send_text("Hello")
            self.assertEqual(await asyncio.wait_for(reader, 1), b"\xff" * 160)
            await tts.flush()
        sent = [json.loads(call.args[0]) for call in self.socket.send.await_args_list]
        self.assertEqual(sent, [{"type": "Speak", "text": "Hello"}, {"type": "Flush"}])
        self.assertEqual(self.connect.call_args.kwargs["additional_headers"], {"Authorization": "Token test-key"})
        self.assertIn("encoding=mulaw&sample_rate=8000", self.connect.call_args.args[0])
        self.assertNotIn("container", self.connect.call_args.args[0])
        self.connection.__aexit__.assert_awaited_once()

    async def test_clear_discards_old_audio_until_ack_and_allows_next_reply(self):
        self.messages(b"stale", {"type": "Flushed"}, {"type": "Cleared"}, b"new")
        async with AuraTTSClient("test-key") as tts:
            await tts.send_text("Old reply")
            await tts.clear()
            await tts.clear()
            with self.assertRaises(RuntimeError):
                await tts.send_text("Too soon")
            with self.assertRaises(RuntimeError):
                await tts.flush()
            stream = tts.events()
            self.assertEqual(await anext(stream), AuraEvent("cleared"))
            await tts.send_text("New reply")
            self.assertEqual(await anext(stream), AuraEvent("audio", audio=b"new"))
            await stream.aclose()
        sent = [json.loads(call.args[0]) for call in self.socket.send.await_args_list]
        self.assertEqual([item["type"] for item in sent], ["Speak", "Clear", "Speak"])

    async def test_provider_error_and_warnings_are_not_audio(self):
        self.messages({"type": "Warning", "code": "FLUSH_WARNING"}, {"type": "Error", "code": "BAD_REQUEST"})
        with self.assertRaisesRegex(AuraTTSError, "BAD_REQUEST"):
            async with AuraTTSClient("test-key") as tts:
                stream = tts.events()
                self.assertEqual(await anext(stream), AuraEvent("warning", code="FLUSH_WARNING"))
                await anext(stream)
        self.connection.__aexit__.assert_awaited_once()

    async def test_failed_clear_does_not_leave_unacknowledgeable_pending_state(self):
        async with AuraTTSClient("test-key") as tts:
            self.socket.send.side_effect = OSError("Send failed")
            with self.assertRaises(OSError):
                await tts.clear()
            self.socket.send.side_effect = None
            await tts.clear()
            self.assertEqual(self.socket.send.await_count, 2)
            with self.assertRaises(RuntimeError):
                await tts.send_text("Wait for clear confirmation")

    async def test_cancellation_closes_socket(self):
        waiting = asyncio.Event()

        async def receive():
            waiting.set()
            await asyncio.Future()
            yield b""

        self.socket.__aiter__.side_effect = receive

        async def run():
            async with AuraTTSClient("test-key") as tts:
                async for _ in tts.events():
                    pass

        task = asyncio.create_task(run())
        await asyncio.wait_for(waiting.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.connection.__aexit__.assert_awaited_once()

    async def test_second_reader_is_rejected(self):
        self.messages(b"one", b"two")
        async with AuraTTSClient("test-key") as tts:
            stream = tts.events()
            await anext(stream)
            with self.assertRaises(RuntimeError):
                await anext(tts.events())
            await stream.aclose()

    async def test_invalid_usage_and_transport_failure(self):
        with self.assertRaises(ValueError):
            AuraTTSClient(" ")
        with self.assertRaises(ValueError):
            AuraOptions(sample_rate=24000)
        tts = AuraTTSClient("test-key")
        with self.assertRaises(RuntimeError):
            await tts.send_text("Hello")
        async with tts:
            with self.assertRaises(ValueError):
                await tts.send_text(" ")
            with self.assertRaises(RuntimeError):
                await tts.__aenter__()
            self.socket.send.side_effect = OSError("Connection lost")
            with self.assertRaises(OSError):
                await tts.send_text("Hello")

    async def test_local_command_writes_playable_pcm_wav(self):
        # WebSocket boundaries need not coincide with 16-bit PCM samples.
        self.messages(b"\x01", b"\x00" + b"\x01\x00" * 239, {"type": "Flushed"})
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "sample.wav"
            with patch.dict("os.environ", {"DEEPGRAM_API_KEY": "test-key"}), patch("builtins.print"):
                await synthesize("Hello", output, 1)
            with wave.open(str(output), "rb") as wav:
                self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, 24000))
                self.assertEqual(wav.readframes(240), b"\x01\x00" * 240)

    async def test_local_command_rejects_premature_close(self):
        self.messages(b"\x00\x00")
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict("os.environ", {"DEEPGRAM_API_KEY": "test-key"}), patch("builtins.print"):
                with self.assertRaisesRegex(AuraTTSError, "before synthesis completed"):
                    await synthesize("Hello", Path(directory) / "sample.wav", 1)


if __name__ == "__main__":
    unittest.main()
