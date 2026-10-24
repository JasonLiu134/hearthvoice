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

`src/core/test_agent.py` is not present in this checkout yet. That command needs
the agent teammate's implementation before it can run. The speech clients do
not depend on that file and can be tested independently.

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

There is no standalone recording/microphone command yet. Live STT integration
must feed the client raw mono 8 kHz mu-law audio, including captured silence,
while consuming speech-start and completed-transcript events concurrently.
Do not send a WAV header or base64 text. The client groups audio into 80 ms
frames internally. See the [STT handoff and usage example](builders/builder-1/STT.md).

The next local voice-test milestone is microphone capture into Flux, completed
transcripts into the llama.cpp agent, and the agent's spoken response into Aura.
That combined loop is not wired yet.

## 6. Run all automated checks

```bash
python -m unittest discover -s tests -v
python -m pip check
```

The speech unit tests mock provider connections. They verify protocol behavior,
audio handling, interruption, and cleanup, rather than live recognition quality
or latency. The llama.cpp server is not needed for these tests.

For phone integration, follow [Twilio setup](builders/builder-1/TWILIO.md).
