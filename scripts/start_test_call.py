"""Start one real test call through the running local API; requires --place-call."""

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--to", required=True, help="Consenting test recipient, in +country-number format")
    parser.add_argument("--caregiver", required=True, help="Consenting test SMS recipient")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--place-call", action="store_true", help="Actually request a call")
    parser.add_argument("--retry-rejected-call", action="store_true",
                        help="Retry a definitively rejected call that has no provider ID")
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    token = os.getenv("API_TOKEN")
    if not token:
        parser.error("Export API_TOKEN first")

    def request(path, payload=None, method=None):
        headers = {"Authorization": "Bearer " + token}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode()
        with urlopen(Request(args.api_url.rstrip("/") + path, data=data,
                             headers=headers, method=method), timeout=45) as response:
            return json.load(response)

    if not args.place_call:
        print("Prepared only. Add --place-call to place a call to the supplied test number.")
        return
    try:
        health = request("/health")
        if health.get("provider") != "twilio" or not health.get("smoke_test_enabled"):
            parser.error("Run the Builder 3 app with PROVIDER=twilio and SMOKE_TEST_ENABLED=true")
        # Reuse a stable test profile so inbound numbers remain unambiguous.
        senior_id = "builder3-test"
        request("/profiles/" + senior_id, {
            "name": "Fictional Builder 3 Test", "phone": args.to,
            "caregiver_phone": args.caregiver, "timezone": "UTC", "local_call_time": "00:00",
            "enabled": False,  # Manual only: scheduler must never dial this test profile.
        }, "PUT")
        check = request("/calls", {"senior_id": senior_id,
                                  "retry_rejected": args.retry_rejected_call}, "POST")
        history = request("/history")
        calls = [history["calls"][cid] for cid in check["call_ids"]]
        print(json.dumps({"check_in": check, "calls": calls}, indent=2))
        print("One check-in per UTC date: rerunning reuses today's call rather than dialing again.")
    except HTTPError as exc:
        print("API request failed with HTTP " + str(exc.code), file=sys.stderr)
        return 1
    except (URLError, TimeoutError):
        print("API request failed or timed out. Check /history before retrying; the call may exist.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
