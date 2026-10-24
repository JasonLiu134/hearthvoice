"""Local microphone conversation: python -m scripts.voice_chat (macOS)."""

import argparse
import asyncio
import logging
import os
from pathlib import Path
import re
import shutil
import sys
import time

from dotenv import load_dotenv

from src.core.agent import END_MARKER, HearthAgent
from src.core.stt import FluxSTTClient
from src.core.tts import AuraOptions, AuraTTSClient

LOG = logging.getLogger("hearthvoice.voice")


def configure_log(path):
    """Record stage metadata, excluding conversation text and credentials."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    handler = logging.StreamHandler(os.fdopen(fd, "a", buffering=1))
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.handlers.clear()
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    LOG.propagate = False


def audio_diagnostics():
    return getattr(LOG.handlers[0], "stream", None) if LOG.handlers else None


async def stop_process(process):
    if process.returncode is None:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 3)
        except TimeoutError:
            process.kill()
            await process.wait()


async def speak_reply(agent, history, user, key, model):
    """Stream sentence-sized agent text into Aura and PCM directly to ffplay."""
    started = time.monotonic()
    LOG.info("reply START")
    player = await asyncio.create_subprocess_exec(
        "ffplay", "-nodisp", "-autoexit", "-loglevel", "error", "-sync", "audio",
        "-probesize", "32", "-analyzeduration", "0",
        "-f", "s16le", "-sample_rate", "24000", "-ch_layout", "mono", "-i", "pipe:0",
        stdin=asyncio.subprocess.PIPE,
        stderr=audio_diagnostics(),
    )
    response = ""
    try:
        async with asyncio.timeout(90):
            async with AuraTTSClient(key, AuraOptions(model=model, encoding="linear16", sample_rate=24000)) as tts:
                LOG.info("tts CONNECTED")
                async def receive():
                    received = False
                    total = 0
                    async for event in tts.events():
                        if event.kind == "audio":
                            try:
                                if not received:
                                    LOG.info("tts FIRST_AUDIO elapsed_ms=%d", (time.monotonic() - started) * 1000)
                                    # Each reply opens a new output device. Give it 750 ms
                                    # of silence to settle before the first spoken sample.
                                    player.stdin.write(bytes(24000 * 2 * 750 // 1000))
                                received = True
                                total += len(event.audio)
                                player.stdin.write(event.audio)
                                await player.stdin.drain()
                            except (BrokenPipeError, ConnectionResetError) as exc:
                                raise RuntimeError("Audio player exited; check ffplay output above") from exc
                        elif event.kind == "warning":
                            raise RuntimeError(f"Deepgram TTS warning: {event.code}")
                        elif event.kind == "flushed":
                            if not received:
                                raise RuntimeError("Deepgram returned no speech audio")
                            LOG.info("tts SUCCESS audio_bytes=%d", total)
                            return
                    raise RuntimeError("TTS connection closed before reply completed")

                async def generate():
                    nonlocal response
                    pending = ""
                    LOG.info("agent START")
                    print("Hearth: ", end="", flush=True)
                    async for chunk in agent.reply_stream(history, user):
                        response += chunk
                        pending += chunk
                        # Keep incomplete sentences (and split [END] markers) buffered.
                        while match := re.search(r"[.!?](?:\s|$)", pending):
                            phrase, pending = pending[:match.end()], pending[match.end():]
                            phrase = phrase.replace(END_MARKER, "").strip()
                            if phrase:
                                print(phrase, end=" ", flush=True)
                                await tts.send_text(phrase)
                                LOG.info("tts TEXT_SENT chars=%d", len(phrase))
                    tail = pending.replace(END_MARKER, "").strip()
                    if tail:
                        print(tail, end="", flush=True)
                        await tts.send_text(tail)
                        LOG.info("tts TEXT_SENT chars=%d", len(tail))
                    print()
                    LOG.info("agent SUCCESS chars=%d", len(response))
                    await tts.flush()
                    LOG.info("tts FLUSH_SENT")

                async with asyncio.TaskGroup() as tasks:
                    tasks.create_task(receive())
                    tasks.create_task(generate())
            player.stdin.close()
            await player.wait()
            if player.returncode:
                raise RuntimeError("Audio playback failed; check your output device")
            LOG.info("playback PROCESS_SUCCESS exit_code=0 elapsed_ms=%d", (time.monotonic() - started) * 1000)
    except BaseException as exc:
        LOG.error("reply FAIL error_type=%s player_exit=%s", type(exc).__name__, player.returncode)
        raise
    finally:
        await stop_process(player)
    return response.replace(END_MARKER, "").strip()


async def conversation(args):
    key = os.environ["DEEPGRAM_API_KEY"]
    user = {"preferred_name": args.name}
    agent = HearthAgent()
    history = []
    listening = asyncio.Event()
    listening.set()
    turns = asyncio.Queue(maxsize=1)
    microphone = await asyncio.create_subprocess_exec(
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-f", "avfoundation", "-i", f":{args.input_device}",
        "-ac", "1", "-ar", "8000", "-c:a", "pcm_mulaw", "-f", "mulaw", "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=audio_diagnostics(),
    )
    try:
        async with FluxSTTClient(key) as stt:
            LOG.info("stt CONNECTED microphone STARTED")
            async def capture():
                while audio := await microphone.stdout.read(640):
                    # Send silence during replies so speaker output isn't a new turn.
                    await stt.send_audio(audio if listening.is_set() else b"\xff" * len(audio))
                raise RuntimeError("Microphone stopped. Check macOS microphone permission and --input-device.")

            async def transcribe():
                async for event in stt.events():
                    if event.kind == "end" and listening.is_set():
                        LOG.info("stt TURN_SUCCESS chars=%d", len(event.transcript))
                        listening.clear()
                        await turns.put(event.transcript)
                raise RuntimeError("STT connection closed")

            async def respond():
                while True:
                    text = await turns.get()
                    print(f"You: {text}")
                    history.append({"role": "user", "content": text})
                    reply = await speak_reply(agent, history, user, key, args.voice)
                    history.append({"role": "assistant", "content": reply})
                    # Bound context for the small local model.
                    del history[:-12]
                    await asyncio.sleep(0.4)
                    listening.set()
                    print("Listening… (Ctrl+C to quit)")
                    LOG.info("microphone LISTENING")

            print("Listening… Speak, then pause. Ctrl+C to quit.")
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(capture())
                tasks.create_task(transcribe())
                tasks.create_task(respond())
    finally:
        await stop_process(microphone)
        await agent.client.close()


def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    parser = argparse.ArgumentParser(description="Talk to the local llama.cpp agent using Deepgram STT/TTS (macOS).")
    parser.add_argument("--name", default="friend")
    parser.add_argument("--input-device", default="default", help="AVFoundation microphone name or index")
    parser.add_argument("--voice", default=os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-thalia-en"))
    parser.add_argument("--log-file", type=Path, default=Path("/tmp/hearthvoice-voice.log"))
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This microphone command currently supports macOS only")
    for executable in ("ffmpeg", "ffplay"):
        if not shutil.which(executable):
            parser.error(f"Install {executable}: conda install -c conda-forge ffmpeg")
    for key in ("DEEPGRAM_API_KEY", "MODEL_URL", "MODEL_NAME"):
        if not os.getenv(key, "").strip():
            parser.error(f"Set {key} in the project .env")
    configure_log(args.log_file)
    LOG.info("session START")
    print(f"Live diagnostics: tail -f {args.log_file}")
    try:
        asyncio.run(conversation(args))
    except KeyboardInterrupt:
        LOG.info("session STOP user_interrupt")
        print("\nGoodbye!")
    except Exception as exc:
        def record_failure(error):
            if isinstance(error, BaseExceptionGroup):
                for child in error.exceptions:
                    record_failure(child)
            else:
                LOG.error("session FAIL error_type=%s", type(error).__name__)
        record_failure(exc)
        print(f"Voice session failed ({type(exc).__name__}). Check {args.log_file} for stage and audio diagnostics.", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
