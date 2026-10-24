"""Local microphone conversation: python -m scripts.voice_chat (macOS)."""

import argparse
import asyncio
import os
from pathlib import Path
import re
import shutil
import sys

from dotenv import load_dotenv

from src.core.agent import END_MARKER, HearthAgent
from src.core.stt import FluxSTTClient
from src.core.tts import AuraOptions, AuraTTSClient


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
    player = await asyncio.create_subprocess_exec(
        "ffplay", "-nodisp", "-autoexit", "-loglevel", "error", "-sync", "audio",
        "-probesize", "32", "-analyzeduration", "0",
        "-f", "s16le", "-sample_rate", "24000", "-ch_layout", "mono", "-i", "pipe:0",
        stdin=asyncio.subprocess.PIPE,
    )
    response = ""
    try:
        async with asyncio.timeout(90):
            async with AuraTTSClient(key, AuraOptions(model=model, encoding="linear16", sample_rate=24000)) as tts:
                async def receive():
                    received = False
                    async for event in tts.events():
                        if event.kind == "audio":
                            try:
                                if not received:
                                    # Each reply opens a new output device. Give it 200 ms
                                    # of silence to settle before the first spoken sample.
                                    player.stdin.write(bytes(24000 * 2 * 200 // 1000))
                                received = True
                                player.stdin.write(event.audio)
                                await player.stdin.drain()
                            except (BrokenPipeError, ConnectionResetError) as exc:
                                raise RuntimeError("Audio player exited; check ffplay output above") from exc
                        elif event.kind == "warning":
                            raise RuntimeError(f"Deepgram TTS warning: {event.code}")
                        elif event.kind == "flushed":
                            if not received:
                                raise RuntimeError("Deepgram returned no speech audio")
                            return
                    raise RuntimeError("TTS connection closed before reply completed")

                async def generate():
                    nonlocal response
                    pending = ""
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
                    tail = pending.replace(END_MARKER, "").strip()
                    if tail:
                        print(tail, end="", flush=True)
                        await tts.send_text(tail)
                    print()
                    await tts.flush()

                async with asyncio.TaskGroup() as tasks:
                    tasks.create_task(receive())
                    tasks.create_task(generate())
            player.stdin.close()
            await player.wait()
            if player.returncode:
                raise RuntimeError("Audio playback failed; check your output device")
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
    )
    try:
        async with FluxSTTClient(key) as stt:
            async def capture():
                while audio := await microphone.stdout.read(640):
                    # Send silence during replies so speaker output isn't a new turn.
                    await stt.send_audio(audio if listening.is_set() else b"\xff" * len(audio))
                raise RuntimeError("Microphone stopped. Check macOS microphone permission and --input-device.")

            async def transcribe():
                async for event in stt.events():
                    if event.kind == "end" and listening.is_set():
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
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This microphone command currently supports macOS only")
    for executable in ("ffmpeg", "ffplay"):
        if not shutil.which(executable):
            parser.error(f"Install {executable}: conda install -c conda-forge ffmpeg")
    for key in ("DEEPGRAM_API_KEY", "MODEL_URL", "MODEL_NAME"):
        if not os.getenv(key, "").strip():
            parser.error(f"Set {key} in the project .env")
    try:
        asyncio.run(conversation(args))
    except KeyboardInterrupt:
        print("\nGoodbye!")


if __name__ == "__main__":
    main()
