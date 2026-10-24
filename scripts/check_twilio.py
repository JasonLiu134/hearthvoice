"""Read-only Twilio account/number checks. Never places calls or sends messages."""

import json
import os
from pathlib import Path
import re

import httpx
from dotenv import dotenv_values, load_dotenv


def redact(text, secrets):
    text = str(text)
    for value in sorted((v for v in secrets if v and len(v) >= 4), key=len, reverse=True):
        text = text.replace(value, "[redacted]")
    text = re.sub(r"https?://\S+", "[URL]", text)
    text = re.sub(r"\+[1-9]\d{7,14}", "[phone]", text)
    text = re.sub(r"\b[A-Za-z0-9_\-]{24,}\b", "[identifier]", text)
    return " ".join(text.split())[:500]


def main():
    env_file = Path(__file__).resolve().parents[1] / ".env"
    file_values = dotenv_values(env_file)
    keys = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER")
    overrides = [key for key in keys if key in os.environ and os.environ[key] != file_values.get(key)]
    load_dotenv(env_file)
    values = {key: os.environ.get(key, "").strip() for key in keys}
    report = {"read_only": True, "shell_overrides_differing_from_env_file": overrides}
    patterns = {"TWILIO_ACCOUNT_SID": r"AC[0-9a-fA-F]{32}",
                "TWILIO_AUTH_TOKEN": r"[0-9a-fA-F]{32}",
                "TWILIO_FROM_NUMBER": r"\+[1-9]\d{7,14}"}
    report["format_checks"] = {key: bool(re.fullmatch(pattern, values[key]))
                               for key, pattern in patterns.items()}
    if not all(report["format_checks"].values()):
        report["next_step"] = "Correct invalid credential/number formats in .env."
        print(json.dumps(report, indent=2))
        return 1

    secret_values = list(values.values()) + [v for k, v in file_values.items()
                                            if any(word in k for word in ("TOKEN", "SECRET", "KEY", "SID"))]

    def get(client, path, **kwargs):
        response = client.get(path, **kwargs)
        info = {"http_status": response.status_code}
        try:
            payload = response.json()
        except ValueError:
            info["response_format"] = "non-JSON"
            return info, None
        if not isinstance(payload, dict):
            info["response_format"] = "unexpected JSON"
            return info, None
        if response.is_error:
            info["code"] = payload.get("code")
            info["description"] = redact(payload.get("message") or payload.get("detail")
                                         or payload.get("title") or "No description", secret_values)
            return info, None
        return info, payload

    try:
        with httpx.Client(base_url="https://api.twilio.com", timeout=15,
                          auth=(values["TWILIO_ACCOUNT_SID"], values["TWILIO_AUTH_TOKEN"])) as client:
            path = "/2010-04-01/Accounts/" + values["TWILIO_ACCOUNT_SID"]
            info, account = get(client, path + ".json")
            report["account"] = info
            if account is not None:
                info.update(status=account.get("status"), type=account.get("type"))
                info, numbers = get(client, path + "/IncomingPhoneNumbers.json",
                                    params={"PhoneNumber": values["TWILIO_FROM_NUMBER"]})
                report["from_number"] = info
                if numbers is not None:
                    matches = numbers.get("incoming_phone_numbers", [])
                    info["listed_in_account_phone_numbers"] = bool(matches)
                    if not matches:
                        info["interpretation"] = (
                            "No match in IncomingPhoneNumbers. This does not establish whether a "
                            "trial-provided sender is valid. Check the Voice trial Console request."
                            if account.get("type") == "Trial" else
                            "No match in this account's IncomingPhoneNumbers."
                        )
                    if matches:
                        info["capabilities"] = matches[0].get("capabilities", {})
    except httpx.HTTPError as exc:
        report["connection_error"] = type(exc).__name__
        report["next_step"] = "Check network access to api.twilio.com; no call was attempted."
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
