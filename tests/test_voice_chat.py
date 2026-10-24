import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from scripts.voice_chat import speak_reply, stop_process


class VoiceChatTests(unittest.IsolatedAsyncioTestCase):
    async def test_reply_streams_audio_and_removes_split_end_marker(self):
        flushed = asyncio.Event()
        spoken = []

        class TTS:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def send_text(self, text):
                spoken.append(text)

            async def flush(self):
                flushed.set()

            async def events(self):
                yield SimpleNamespace(kind="audio", audio=b"\x01\x02")
                yield SimpleNamespace(kind="audio", audio=b"\x03\x04")
                await flushed.wait()
                yield SimpleNamespace(kind="flushed")

        class Agent:
            async def reply_stream(self, history, user):
                for chunk in ["Hello. ", "How are you?", "[EN", "D]"]:
                    yield chunk

        player = SimpleNamespace(
            stdin=SimpleNamespace(write=unittest.mock.Mock(), drain=AsyncMock(), close=unittest.mock.Mock()),
            returncode=0, wait=AsyncMock(),
        )
        with patch("scripts.voice_chat.AuraTTSClient", return_value=TTS()), patch(
            "scripts.voice_chat.asyncio.create_subprocess_exec", AsyncMock(return_value=player)
        ) as spawn:
            result = await speak_reply(Agent(), [], {}, "key", "aura-2-thalia-en")
        command = spawn.call_args.args
        self.assertNotIn("-ac", command)
        self.assertEqual(command[command.index("-ch_layout") + 1], "mono")
        self.assertEqual(command[command.index("-sample_rate") + 1], "24000")
        self.assertEqual(result, "Hello. How are you?")
        self.assertEqual(spoken, ["Hello.", "How are you?"])
        self.assertEqual([call.args[0] for call in player.stdin.write.call_args_list],
                         [bytes(9600), b"\x01\x02", b"\x03\x04"])
        player.stdin.close.assert_called_once()

    async def test_stop_process_kills_if_termination_times_out(self):
        process = SimpleNamespace(returncode=None, terminate=unittest.mock.Mock(),
                                  kill=unittest.mock.Mock(), wait=AsyncMock(side_effect=[TimeoutError, None]))
        await stop_process(process)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()

    async def test_stt_failure_stops_microphone_and_closes_agent(self):
        from scripts.voice_chat import conversation

        class STT:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def events(self):
                raise RuntimeError("provider disconnected")
                yield

        async def blocked_read(size):
            await asyncio.Event().wait()

        mic = SimpleNamespace(stdout=SimpleNamespace(read=blocked_read), returncode=None,
                              terminate=unittest.mock.Mock(), wait=AsyncMock())
        agent = SimpleNamespace(client=SimpleNamespace(close=AsyncMock()))
        with patch.dict("os.environ", {"DEEPGRAM_API_KEY": "key"}), patch(
            "scripts.voice_chat.FluxSTTClient", return_value=STT()
        ), patch("scripts.voice_chat.HearthAgent", return_value=agent), patch(
            "scripts.voice_chat.asyncio.create_subprocess_exec", AsyncMock(return_value=mic)
        ):
            with self.assertRaises(ExceptionGroup):
                await conversation(SimpleNamespace(name="Test", input_device="default", voice="aura-2-thalia-en"))
        mic.terminate.assert_called_once()
        agent.client.close.assert_awaited_once()
