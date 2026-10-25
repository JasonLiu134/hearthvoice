"""Post-call summaries sent back to the phone conversation participant."""

from contextlib import contextmanager
import logging
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlencode, urlsplit
from uuid import NAMESPACE_URL, uuid5

from src.services.twilio_client import MockProvider, Rejected, TwilioProvider

log = logging.getLogger(__name__)
STATUSES = {"accepted": 0, "queued": 1, "sending": 1, "sent": 2,
            "delivered": 3, "undelivered": 3, "failed": 3, "canceled": 3}


def enabled():
    return os.getenv("POST_CALL_SMS_ENABLED", "false").lower() == "true"


def normalize_phone(value):
    # Store phones as strings. Ten-digit NANP examples can use a configurable +1 default.
    if not isinstance(value, str):
        raise ValueError("Caller phone must be a string")
    number = re.sub(r"[\s().-]", "", value.strip())
    if re.fullmatch(r"\d{10}", number) and os.getenv("SMS_DEFAULT_COUNTRY_CODE", "1") == "1":
        number = "+1" + number
    if not re.fullmatch(r"\+[1-9]\d{7,14}", number):
        raise ValueError("Caller phone must be E.164")
    return number


@contextmanager
def _store():
    path = Path(os.getenv("POST_CALL_SMS_DATABASE", "data/post-call-sms.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    db = sqlite3.connect(path, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS deliveries (
        id TEXT PRIMARY KEY, call_id TEXT NOT NULL, phone TEXT NOT NULL,
        provider TEXT NOT NULL, status TEXT NOT NULL, provider_sid TEXT,
        error_code INTEGER, http_status INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(call_id, phone))""")
    db.commit()
    try:
        with db:
            yield db
    finally:
        db.close()


def _provider():
    mode = os.getenv("POST_CALL_SMS_PROVIDER", "mock")
    if mode == "mock":
        return mode, MockProvider()
    if mode != "twilio":
        raise ValueError("POST_CALL_SMS_PROVIDER must be mock or twilio")
    def setting(name):
        value = os.getenv(name, "").strip()
        if not value:
            raise ValueError("Missing " + name)
        return value
    base = setting("PUBLIC_BASE_URL").rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("PUBLIC_BASE_URL must be an HTTPS origin")
    sender = normalize_phone(os.getenv("TWILIO_SMS_FROM_NUMBER") or setting("TWILIO_FROM_NUMBER"))
    return mode, TwilioProvider(setting("TWILIO_ACCOUNT_SID"), setting("TWILIO_AUTH_TOKEN"),
                                sender, base, base + "/twilio/voice")


def notify_caller(call, caller):
    """Run off the event loop. Claim the recipient durably before sending once.

    A timeout or restart can leave an unknown outcome. Never automatically resend
    a claimed message: Twilio may already have accepted it.
    """
    if not enabled() or call.get("status") != "completed":
        return
    summary = call.get("summary", "").strip()
    if not summary or "Patient: " not in call.get("transcript", ""):
        return
    try:
        phone = normalize_phone(caller)
    except ValueError:
        log.warning("Post-call SMS SKIP missing or invalid caller number")
        return
    mode, provider = _provider()
    body = "HearthVoice post-call update (patient-reported): " + summary[:1000]
    with _store() as db:
        # A previous process may have stopped after Twilio accepted a request.
        # 'sending' remains a claimed, non-retryable outcome until a callback.
        delivery_id = str(uuid5(NAMESPACE_URL, "call-summary:" + call["id"] + ":" + phone))
        claimed = db.execute(
            "INSERT OR IGNORE INTO deliveries(id,call_id,phone,provider,status) VALUES(?,?,?,?,?)",
            (delivery_id, call["id"], phone, mode, "sending"))
        db.commit()  # Commit before the external side effect, even across workers.
        if not claimed.rowcount:
            return
        callback = os.getenv("PUBLIC_BASE_URL", "").rstrip("/") + "/webhooks/twilio/call-summary?" + urlencode({"delivery_id": delivery_id})
        try:
            sid = provider.send_sms(phone, body, delivery_id, status_callback=callback)
            db.execute("UPDATE deliveries SET provider_sid=?,status='accepted',updated_at=CURRENT_TIMESTAMP "
                       "WHERE id=? AND status='sending' AND provider_sid IS NULL", (sid, delivery_id))
            log.info("Post-call SMS ACCEPTED provider=%s", mode)
        except Rejected as exc:
            db.execute("UPDATE deliveries SET status='failed',error_code=?,http_status=?,updated_at=CURRENT_TIMESTAMP "
                       "WHERE id=? AND status='sending'", (exc.code, exc.http_status, delivery_id))
            log.error("Post-call SMS REJECTED code=%s status=%s", exc.code, exc.http_status)
        except Exception as exc:
            db.execute("UPDATE deliveries SET status='unknown',updated_at=CURRENT_TIMESTAMP "
                       "WHERE id=? AND status='sending'", (delivery_id,))
            log.error("Post-call SMS UNKNOWN error_type=%s; not automatically retried", type(exc).__name__)
        db.commit()


def message_status(delivery_id, sid, status):
    if status not in STATUSES or not sid:
        raise ValueError("Invalid message status")
    with _store() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
        if row is None:
            raise KeyError(delivery_id)
        if row["provider"] != "twilio" or (row["provider_sid"] and row["provider_sid"] != sid):
            raise ValueError("Message SID mismatch")
        current = row["status"]
        if current in {"delivered", "undelivered", "failed", "canceled"}:
            return
        if STATUSES.get(current, -1) > STATUSES[status]:
            return
        db.execute("UPDATE deliveries SET provider_sid=?,status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                   (sid, status, delivery_id))
        log.info("Post-call SMS status=%s", status)


def delivery_history():
    with _store() as db:
        # Phone numbers and medical summary are intentionally absent from diagnostics.
        return [dict(row) for row in db.execute(
            "SELECT id,call_id,provider,status,provider_sid,error_code,http_status,created_at,updated_at "
            "FROM deliveries ORDER BY created_at,id")]
