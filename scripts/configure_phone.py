"""Connect an owned Twilio number to this backend; does not place calls."""

import argparse
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv


def configure_number(client, account, number, base):
    url = urlsplit(base)
    if url.scheme != "https" or not url.hostname or url.path or url.query or url.fragment or url.username:
        raise ValueError("PUBLIC_BASE_URL must be an HTTPS origin without a trailing slash")
    prefix = f"https://api.twilio.com/2010-04-01/Accounts/{account}"
    response = client.get(prefix + "/IncomingPhoneNumbers.json", params={"PhoneNumber": number})
    response.raise_for_status()
    matches = [n for n in response.json().get("incoming_phone_numbers", [])
               if n.get("phone_number") == number and n.get("capabilities", {}).get("voice")]
    if len(matches) != 1:
        raise ValueError("TWILIO_FROM_NUMBER must match one owned, voice-capable number")
    response = client.post(prefix + "/IncomingPhoneNumbers/" + matches[0]["sid"] + ".json",
                           data={"VoiceUrl": base + "/twilio/voice", "VoiceMethod": "POST",
                                 "StatusCallback": base + "/twilio/status", "StatusCallbackMethod": "POST"})
    response.raise_for_status()
    result = response.json()
    expected = {"voice_url": base + "/twilio/voice", "voice_method": "POST",
                "status_callback": base + "/twilio/status", "status_callback_method": "POST"}
    if any(result.get(key) != value for key, value in expected.items()):
        raise ValueError("Twilio did not confirm the voice and completion webhook configuration")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    required = ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "PUBLIC_BASE_URL"]
    for key in required:
        if not os.getenv(key, "").strip():
            parser.error(f"Set {key} in .env")
    try:
        with httpx.Client(auth=(os.environ[required[0]], os.environ[required[1]]), timeout=15) as client:
            configure_number(client, os.environ[required[0]], os.environ[required[2]], os.environ[required[3]])
    except httpx.HTTPStatusError as exc:
        parser.exit(1, f"Twilio configuration failed: HTTP {exc.response.status_code}\n")
    except httpx.RequestError:
        parser.exit(1, "Twilio configuration request failed; check number settings before retrying.\n")
    except ValueError as exc:
        parser.error(str(exc))
    print("Number voice webhook configured successfully. No call was placed.")
    print("Restart the API after changing .env, then call the purchased number.")


if __name__ == "__main__":
    main()
