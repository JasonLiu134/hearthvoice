"""Background Supabase call logging with durable local snapshots.

A single configured patient is supported. SDK/network work runs off the event loop.
The local copy is retained after upload so failed writes can be replayed.
"""
import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import threading
from uuid import NAMESPACE_URL, uuid5

log = logging.getLogger(__name__)
_lock = threading.RLock()
TERMINAL = {"completed", "busy", "failed", "no-answer", "canceled"}


def now():
    return datetime.now(timezone.utc).isoformat()


def enabled():
    return os.getenv("SUPABASE_CALL_LOGGING", "false").lower() == "true"


def _directory():
    path = Path(os.getenv("CALL_LOG_DIRECTORY", "data/call-logs"))
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def _client():
    from supabase import create_client
    from supabase.lib.client_options import SyncClientOptions
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"],
                         options=SyncClientOptions(postgrest_client_timeout=10))


def _upload(row):
    client = _client()
    try:
        # Twilio uses hyphens; the deployed public.call_status enum uses underscores.
        payload = dict(row)
        # The deployed schema requires this field even for on-demand inbound calls.
        # Use the observed start as their effective schedule; keep real schedules intact.
        if payload.get("scheduled_for") is None and payload.get("started_at"):
            payload["scheduled_for"] = payload["started_at"]
        if "status" in row:
            payload["status"] = {"in-progress": "in_progress", "busy": "failed",
                                 "no-answer": "failed", "canceled": "failed"}.get(row["status"], row["status"])
        client.table("calls").upsert(payload, on_conflict="id").execute()
    finally:
        client.postgrest.session.close()


def _save(sid, caller, history, status, summary):
    with _lock:
        call_id = str(uuid5(NAMESPACE_URL, "twilio:" + sid))
        path = _directory() / (call_id + ".json")
        row = json.loads(path.read_text()) if path.exists() else None
        if row is None:
            patient = os.getenv("PATIENT_PHONE_NUMBER", "").strip()
            user_id = os.getenv("SUPABASE_USER_ID", "").strip()
            if not patient or caller != patient or not user_id:
                log.warning("call log SKIP: patient mapping missing or caller unmatched")
                return
            row = dict(id=call_id, user_id=user_id, scheduled_for=None,
                       status="in-progress", started_at=now(), ended_at=None,
                       summary="", audio_path="", transcript="")
        transcript = "\n".join(
            ("Patient: " if item["role"] == "user" else "Agent: ") + item["content"]
            for item in history or [] if item.get("role") in {"user", "assistant"})
        # Delayed/retried background snapshots cannot truncate the conversation.
        if len(transcript) > len(row["transcript"]):
            row["transcript"] = transcript
        if status in TERMINAL and row["status"] not in TERMINAL:
            row.update(status=status, ended_at=now())
        if summary:
            row["summary"] = summary
        temporary = path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(row, handle)
        temporary.replace(path)
        try:
            _upload(row)
            log.info("Supabase call log SUCCESS")
        except Exception as exc:
            code = getattr(exc, "code", None)
            log.error("Supabase call log FAIL error_type=%s code=%s; local snapshot retained",
                      type(exc).__name__, code if isinstance(code, str) and code.isalnum() else "unknown")


async def save_call(sid, caller="", history=None, status=None):
    if not enabled() or not sid:
        return
    try:
        await asyncio.to_thread(_save, sid, caller, history, status, None)
    except Exception as exc:
        log.error("Local call log FAIL error_type=%s", type(exc).__name__)


async def finish_call(sid, caller="", history=None, status="completed"):
    await save_call(sid, caller, history, status)
    from src.services.call_summary_sms import enabled as sms_enabled, notify_caller
    if status not in TERMINAL or not sid or not (enabled() or sms_enabled()):
        return
    agent = None
    try:
        path = _directory() / (str(uuid5(NAMESPACE_URL, "twilio:" + sid)) + ".json")
        try:
            row = await asyncio.to_thread(lambda: json.loads(path.read_text()))
        except FileNotFoundError:
            # SMS can use the signed call participant and current session history,
            # even if Supabase logging is disabled or the caller has no profile.
            row = {"id": str(uuid5(NAMESPACE_URL, "twilio:" + sid)), "status": status,
                   "summary": "", "transcript": "\n".join(
                       ("Patient: " if item["role"] == "user" else "Agent: ") + item["content"]
                       for item in history or [] if item.get("role") in {"user", "assistant"})}
        if not row["transcript"]:
            return
        if not row["summary"]:
            from src.core.agent import HearthAgent
            agent = HearthAgent()
            async with asyncio.timeout(20):
                assessment = await agent.summarize([{
                    "role": "user", "content": "Full call transcript:\n" + row["transcript"]}])
            summary = assessment.get("summary", "")
            if isinstance(summary, str) and summary.strip():
                if enabled():
                    await asyncio.to_thread(_save, sid, caller, history, status, summary.strip())
                row["summary"] = summary.strip()
        # Existing summaries still need contact notification on callback retries.
        # This worker has its own durable per-recipient claims; no blind resends.
        await asyncio.to_thread(notify_caller, row, caller)
    except FileNotFoundError:
        pass  # Unmapped callers are intentionally not logged.
    except Exception as exc:
        log.error("Call finalization FAIL error_type=%s", type(exc).__name__)
    finally:
        if agent is not None:
            await agent.close()


def replay():
    """Explicitly replay retained snapshots; upsert prevents duplicate call rows."""
    with _lock:
        for path in _directory().glob("*.json"):
            _upload(json.loads(path.read_text()))


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    replay()
