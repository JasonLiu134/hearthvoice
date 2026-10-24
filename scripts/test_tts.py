"""Synthesize a local WAV sample: python -m scripts.test_tts --text 'Hello'."""

import argparse
import asyncio
import os
import time
import wave
from pathlib import Path

from dotenv import load_dotenv

from src.core.tts import AuraOptions, AuraTTSClient, AuraTTSError


async def synthesize(text: str, output: Path, timeout: float) -> None:
    options = AuraOptions(
        model=os.environ.get("DEEPGRAM_TTS_MODEL", "aura-2-thalia-en"),
        encoding="linear16", sample_rate=24000,
    )
    started = time.monotonic()
    async with asyncio.timeout(timeout):
        async with AuraTTSClient(os.environ["DEEPGRAM_API_KEY"], options) as tts:
            connected = time.monotonic()
            with wave.open(str(output), "wb") as wav:
                wav.setparams((1, 2, options.sample_rate, 0, "NONE", "not compressed"))

                async def receive():
                    total = 0
                    partial_sample = b""
                    async for event in tts.events():
                        if event.kind == "audio":
                            if total == 0:
                                print(f"Text-to-first-audio: {(time.monotonic() - connected) * 1000:.0f} ms")
                            total += len(event.audio)
                            pcm = partial_sample + event.audio
                            complete = len(pcm) - len(pcm) % 2
                            wav.writeframesraw(pcm[:complete])
                            partial_sample = pcm[complete:]
                        elif event.kind == "warning":
                            raise AuraTTSError(f"TTS warning: {event.code}")
                        elif event.kind == "flushed":
                            if not total:
                                raise AuraTTSError("Provider completed without audio")
                            if partial_sample:
                                raise AuraTTSError("Provider returned an incomplete PCM sample")
                            return
                    raise AuraTTSError("Connection closed before synthesis completed")

                reader = asyncio.create_task(receive())
                try:
                    await tts.send_text(text)
                    await tts.flush()
                    await reader
                finally:
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)
    print(f"Saved {output} (total including connection: {time.monotonic() - started:.2f}s)")


def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    parser = argparse.ArgumentParser(description="Generate a WAV using Deepgram credits; no phone call.")
    parser.add_argument("--text", default="Hello, this is HearthVoice. How are you feeling today?")
    parser.add_argument("--output", type=Path, default=Path("/private/tmp/hearthvoice-tts.wav"))
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    if not os.environ.get("DEEPGRAM_API_KEY", "").strip():
        parser.error("Set DEEPGRAM_API_KEY in the project .env or environment")
    asyncio.run(synthesize(args.text, args.output, args.timeout))


if __name__ == "__main__":
    main()
