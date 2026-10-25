"""Dial a phone and connect it to Hearth: python -m scripts.call_agent --to +15551234567."""

import argparse
import os
from pathlib import Path
import re

import httpx
from dotenv import load_dotenv


def request_call(api_url, token, *, to=None, sid=None):
    """One request only: a timeout may still have created a real call."""
    headers = {"X-Admin-Token": token}
    with httpx.Client(timeout=20) as client:
        if sid:
            response = client.get(api_url.rstrip("/") + "/calls/" + sid, headers=headers)
        else:
            response = client.post(api_url.rstrip("/") + "/calls", headers=headers, json={"to": to})
    response.raise_for_status()
    return response.json()


def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    parser = argparse.ArgumentParser(description="Place a real Twilio call connected to the local Hearth agent.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--to", help="Destination in +country-number format; places a real call")
    mode.add_argument("--status", help="Read status of an existing CA... call SID")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    if args.to and not re.fullmatch(r"\+[1-9]\d{7,14}", args.to):
        parser.error("--to must be a phone number such as +15551234567")
    if args.status and not re.fullmatch(r"CA[0-9a-fA-F]{32}", args.status):
        parser.error("--status must be a Twilio call SID")
    token = os.getenv("ADMIN_TOKEN", "").strip()
    if not token:
        parser.error("Set ADMIN_TOKEN in .env")
    try:
        result = request_call(args.api_url, token, to=args.to, sid=args.status)
    except httpx.HTTPStatusError as exc:
        print(f"Backend HTTP {exc.response.status_code}. Check server logs and Twilio call logs before retrying.")
        try:
            detail = exc.response.json().get("detail")
        except (ValueError, AttributeError):
            detail = None
        if isinstance(detail, dict):
            code = detail.get("twilio_code")
            status = detail.get("twilio_http_status")
            if type(status) is int:
                print(f"Twilio HTTP status: {status}")
            if type(code) is int:
                print(f"Twilio error code: {code}")
                if code > 0:
                    print(f"Error reference: https://www.twilio.com/docs/errors/{code}")
        raise SystemExit(1) from None
    except httpx.RequestError:
        print("Backend unreachable or request timed out. Outcome may be unknown; check Twilio call logs before retrying.")
        raise SystemExit(1) from None
    print(f"Call SID: {result['call_sid']}\nStatus: {result['status']}")
    if args.to:
        print("Queued means accepted, not answered. Check status with --status CALL_SID.")


if __name__ == "__main__":
    main()
