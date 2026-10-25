# Local installation and testing

Run commands from the project root. The agent uses a local llama.cpp server;
speech recognition and synthesis use hosted Deepgram APIs and require internet
access. Twilio is not needed for the standalone speech checks.

## 1. Create the Python environment

Replace `<name>` with your environment name, for example `hearthvoice`:

```bash
conda create -n <name> python=3.12
conda activate <name>
python -m pip install -r requirements.txt
```

Activate the same environment in each additional terminal tab.

## 2. Configure the environment

Create `.env` in the project root, or add these variables to your existing file:

```dotenv
MODEL_URL=http://127.0.0.1:8080/v1
MODEL_NAME=bartowski/Llama-3.2-1B-Instruct-GGUF:Q4_K_M
DEEPGRAM_API_KEY=your_deepgram_api_key
DEEPGRAM_TTS_MODEL=aura-2-thalia-en
```

Get a key from the [Deepgram Console](https://console.deepgram.com/). One key
serves both Flux STT and Aura-2 TTS. `DEEPGRAM_TTS_MODEL` is optional and defaults
to the value above. Keep `.env` out of Git; `.env.example` contains placeholders.
Preserve any existing Twilio settings if you also run the phone backend.

The TTS test command loads the root `.env` automatically. The reusable speech
clients accept a key explicitly, so their caller must load configuration.

## 3. Install and run the local agent server

Install llama.cpp into the activated Conda environment:

```bash
conda install -c conda-forge llama.cpp
```

Download and try the model using the team's command:

```bash
llama-cli -hf bartowski/Llama-3.2-1B-Instruct-GGUF:Q4_K_M
```

Exit the CLI when finished. Start the CPU server in one terminal tab and leave
it running:

```bash
llama-server -hf bartowski/Llama-3.2-1B-Instruct-GGUF:Q4_K_M --device none --n-gpu-layers 0 --ctx-size 2048 --host 127.0.0.1 --port 8080
```

In another activated terminal, the team's agent test command is:

```bash
python src/core/test_agent.py
```

The command starts a text conversation with the local agent. Use `/summary`
to summarize the conversation and `/quit` to exit. The speech clients can be
tested independently of the agent server.

## 4. Run text-to-speech

No local speech model download, GPU, or additional provider SDK is needed.
`requirements.txt` includes the WebSocket and configuration libraries.

Generate a speech sample:

```bash
python -m scripts.test_tts --text "Hello, this is HearthVoice. How are you feeling today?" --output /tmp/hearthvoice-tts.wav
```

On macOS, play it with:

```bash
afplay /tmp/hearthvoice-tts.wav
```

The command uses Deepgram credits and saves a mono 24 kHz 16-bit PCM WAV. It
prints time to first received audio and total generation time. Playback occurs
after generation, so this does not measure live playback latency. Use
`--timeout 60` to change the default 30-second deadline.

The reusable `AuraTTSClient` defaults to raw 8 kHz mu-law for phone integration;
the test command selects PCM for local WAV playback. See [TTS details](builders/builder-1/TTS.md).

## 5. Test speech-to-text

Flux STT runs on Deepgram, so there is no local model to install. Its client is
`FluxSTTClient` in `src/core/stt.py`.

Run its automated tests without an API key or API usage:

```bash
python -m unittest discover -s tests -p 'test_stt.py' -v
```

## Talk to the agent from your microphone (macOS)

Keep the llama-server command from step 3 running. Install the audio tools once
if `ffmpeg` or `ffplay` is missing:

```bash
conda install -c conda-forge ffmpeg
```

In a second terminal, activate your environment and run from the project root:

```bash
conda activate hearthvoice
python -m scripts.voice_chat --name Julie
```

Watch successes and failures in a separate terminal:

```bash
tail -f /tmp/hearthvoice-voice.log
```

Restart the voice CLI after code changes to enable logging. The log appends
session starts, STT turns, agent completion, TTS first-audio timing and byte
counts, playback exit status, and audio-tool diagnostics. Conversation text
and API keys are excluded from application log events. `PROCESS_SUCCESS`
means the player exited normally; speaker volume and routing need a live
check. Use `--log-file /path/to/file.log` to choose another location.

Allow microphone access for your terminal in macOS System Settings → Privacy &
Security → Microphone. Speak, then pause to finish your turn. Audio goes to
Deepgram Flux, the transcript goes to the local agent, and Aura streams its
spoken reply to your speakers. This uses Deepgram credits. Press Ctrl+C to stop.
The microphone is muted during replies to prevent speaker echo; interruption
is not supported yet. Wait for “Listening…” before speaking again.

If the default microphone fails, list devices and select an audio device index:

```bash
ffmpeg -f avfoundation -list_devices true -i ""
python -m scripts.voice_chat --input-device 0 --name Julie
```

The device-list command exits with an error after printing the devices; that
is expected. Use the audio-device index, not the video-device index.
This command currently supports macOS. Live microphone permissions, provider
credentials, and audio quality must be checked locally; automated tests mock
these connections. Recent conversation history is limited to six exchanges,
but long turns may still exceed the local model's configured context window.

## 6. Run all automated checks

```bash
python -m unittest discover -s tests -v
python -m pip check
```

The speech unit tests mock provider connections. They verify protocol behavior,
audio handling, interruption, and cleanup, rather than live recognition quality
or latency. The llama.cpp server is not needed for these tests.

For phone integration, follow [Twilio setup](builders/builder-1/TWILIO.md).
For automatic conversation storage, follow [Supabase setup](builders/builder-4/SUPABASE.md).
