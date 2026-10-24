import base64
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from src.core.stt import (
    FluxAudioBuffer,
    FluxOptions,
    FluxSTTError,
    FluxSTTClient,
    FluxTurnEvent,
    decode_inbound_media,
    parse_flux_message,
    validate_twilio_start,
)


class FluxSTTTests(unittest.TestCase):
    def test_flux_connection_matches_twilio_audio(self):
        url = FluxOptions().url
        self.assertIn("/v2/listen?", url)
        self.assertIn("model=flux-general-en", url)
        self.assertIn("encoding=mulaw", url)
        self.assertIn("sample_rate=8000", url)
        with self.assertRaises(ValueError):
            FluxOptions(eot_threshold=0.2)

    def test_validates_start_and_only_decodes_caller_track(self):
        start = {"start": {"streamSid": "MZ1", "mediaFormat": {
            "encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1,
        }}}
        self.assertEqual(validate_twilio_start(start), "MZ1")
        payload = base64.b64encode(b"\xff\x7f").decode()
        self.assertEqual(decode_inbound_media({"media": {"track": "inbound", "payload": payload}}), b"\xff\x7f")
        self.assertEqual(decode_inbound_media({"media": {"track": "outbound", "payload": payload}}), b"")
        with self.assertRaises(ValueError):
            decode_inbound_media({"media": {"payload": "invalid payload"}})
        with self.assertRaises(ValueError):
            validate_twilio_start({"start": {"streamSid": "MZ1", "mediaFormat": {"sampleRate": 16000}}})

    def test_batches_80ms_and_flushes_remainder(self):
        buffer = FluxAudioBuffer()
        self.assertEqual(buffer.add(b"a" * 160), [])
        self.assertEqual(buffer.add(b"b" * 480), [b"a" * 160 + b"b" * 480])
        self.assertEqual(buffer.add(b"c" * 50), [])
        self.assertEqual(buffer.flush(), b"c" * 50)
        self.assertEqual(buffer.flush(), b"")

    def test_turn_events_and_errors(self):
        self.assertEqual(
            parse_flux_message(json.dumps({"type": "TurnInfo", "event": "StartOfTurn"})),
            FluxTurnEvent("start"),
        )
        self.assertEqual(
            parse_flux_message(json.dumps({"type": "TurnInfo", "event": "EndOfTurn", "transcript": "  Hello  "})),
            FluxTurnEvent("end", "Hello"),
        )
        self.assertIsNone(parse_flux_message(json.dumps({"type": "TurnInfo", "event": "Update"})))
        self.assertIsNone(parse_flux_message(json.dumps({"type": "TurnInfo", "event": "EndOfTurn", "transcript": "  "})))
        with self.assertRaises(FluxSTTError):
            parse_flux_message(json.dumps({"type": "Error", "code": "BAD_REQUEST"}))


class FluxClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.socket = AsyncMock()
        self.connection = AsyncMock()
        self.connection.__aenter__.return_value = self.socket
        self.connect_patch = patch("src.core.stt.websockets.connect", return_value=self.connection)
        self.connect = self.connect_patch.start()
        self.addCleanup(self.connect_patch.stop)

    async def test_streams_audio_and_events_concurrently(self):
        received_audio = asyncio.Event()

        async def send(audio):
            received_audio.set()

        async def messages():
            await received_audio.wait()
            yield json.dumps({"type": "Connected"})
            yield json.dumps({"type": "TurnInfo", "event": "StartOfTurn"})
            yield json.dumps({"type": "TurnInfo", "event": "EndOfTurn", "transcript": "I feel fine"})

        self.socket.send.side_effect = send
        self.socket.__aiter__.side_effect = messages
        async with FluxSTTClient("test-key") as client:
            async def collect():
                return [event async for event in client.events()]

            receiver = asyncio.create_task(collect())
            await client.send_audio(b"a" * 160)
            self.socket.send.assert_not_awaited()
            await client.send_audio(b"b" * 480)
            self.assertEqual(await asyncio.wait_for(receiver, 1), [
                FluxTurnEvent("start"), FluxTurnEvent("end", "I feel fine"),
            ])
            await client.send_audio(b"c" * 40)
            await client.flush_audio()
            await client.flush_audio()
        self.assertEqual([call.args[0] for call in self.socket.send.await_args_list], [
            b"a" * 160 + b"b" * 480, b"c" * 40,
        ])
        self.assertEqual(self.connect.call_args.kwargs["additional_headers"], {
            "Authorization": "Token test-key",
        })
        self.assertEqual(self.connect.call_args.args[0], FluxOptions().url)
        self.connection.__aexit__.assert_awaited_once()

    async def test_provider_error_propagates_and_closes_connection(self):
        async def messages():
            yield json.dumps({"type": "Error", "code": "BAD_REQUEST"})

        self.socket.__aiter__.side_effect = messages
        with self.assertRaisesRegex(FluxSTTError, "BAD_REQUEST"):
            async with FluxSTTClient("test-key") as client:
                async for _ in client.events():
                    self.fail("Error must not become a transcript")
        self.connection.__aexit__.assert_awaited_once()

    async def test_finish_sends_remainder_before_close_and_rejects_more_audio(self):
        async with FluxSTTClient("test-key") as client:
            await client.send_audio(b"a" * 200)
            await client.finish_audio()
            await client.finish_audio()
            with self.assertRaises(RuntimeError):
                await client.send_audio(b"b" * 640)
        sent = [call.args[0] for call in self.socket.send.await_args_list]
        self.assertEqual(sent[0], b"a" * 200)
        self.assertEqual(json.loads(sent[1]), {"type": "CloseStream"})
        self.assertEqual(len(sent), 2)

    async def test_transport_send_error_propagates_and_closes(self):
        self.socket.send.side_effect = OSError("Connection lost")
        with self.assertRaises(OSError):
            async with FluxSTTClient("test-key") as client:
                await client.send_audio(b"a" * 640)
        self.connection.__aexit__.assert_awaited_once()

    async def test_cancellation_closes_connection_without_sending_partial_audio(self):
        waiting = asyncio.Event()

        async def messages():
            waiting.set()
            await asyncio.Future()
            yield  # Make this an async generator.

        self.socket.__aiter__.side_effect = messages

        async def run():
            async with FluxSTTClient("test-key") as client:
                await client.send_audio(b"a" * 160)
                async for _ in client.events():
                    pass

        task = asyncio.create_task(run())
        await asyncio.wait_for(waiting.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.socket.send.assert_not_awaited()
        self.connection.__aexit__.assert_awaited_once()

    async def test_invalid_usage_and_connection_failure(self):
        with self.assertRaises(ValueError):
            FluxSTTClient(" ")
        client = FluxSTTClient("test-key")
        with self.assertRaises(RuntimeError):
            await client.send_audio(b"audio")
        with self.assertRaises(RuntimeError):
            await anext(client.events())
        async with client:
            with self.assertRaises(TypeError):
                await client.send_audio("base64")
            with self.assertRaises(RuntimeError):
                await client.__aenter__()
        self.connection.__aenter__.side_effect = OSError("Connection failed")
        with self.assertRaises(OSError):
            async with client:
                self.fail("Connection must not succeed")


if __name__ == "__main__":
    unittest.main()
