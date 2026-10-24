import base64
import json
import unittest

from src.core.stt import (
    FluxAudioBuffer,
    FluxOptions,
    FluxSTTError,
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


if __name__ == "__main__":
    unittest.main()
