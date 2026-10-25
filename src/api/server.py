"""Twilio phone entry points for the HearthVoice demo."""

import logging
import os
import re
import asyncio
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, urlparse
from xml.etree.ElementTree import Element, SubElement, tostring

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, WebSocket
from fastapi.responses import Response
from pydantic import BaseModel
from twilio.request_validator import RequestValidator

from src.core.voice_pipeline import CallSession, SpeechConfig, active_sessions
from src.core import trial_voice
from src.services import phone_alerts, database, call_summary_sms

log = logging.getLogger(__name__)
@asynccontextmanager
async def lifespan(app):
    try:
        yield
    finally:
        phone_alerts.close()


app = FastAPI(title="HearthVoice", lifespan=lifespan)


class OutboundCall(BaseModel):
    to: str


def public_base_url() -> str:
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path:
        raise HTTPException(503, "PUBLIC_BASE_URL must be a public HTTPS origin")
    return base


def require_setting(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise HTTPException(503, f"{name} is not configured")
    return value


def twilio_credentials() -> tuple[str, str]:
    key = os.environ.get("TWILIO_API_KEY_SID", "").strip()
    secret = os.environ.get("TWILIO_API_KEY_SECRET", "").strip()
    if key or secret:
        if not (key and secret):
            raise HTTPException(503, "Configure both Twilio API key SID and secret")
        return key, secret
    return require_setting("TWILIO_ACCOUNT_SID"), require_setting("TWILIO_AUTH_TOKEN")


def authenticate_admin(token: str | None) -> None:
    import secrets
    expected = require_setting("ADMIN_TOKEN")
    if token is None or not secrets.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(403, "Invalid admin token")


def valid_twilio_signature(url: str, params: dict[str, str], signature: str) -> bool:
    return RequestValidator(require_setting("TWILIO_AUTH_TOKEN")).validate(url, params, signature)


def stream_twiml(base: str, caller: str = "") -> str:
    root = Element("Response")
    connect = SubElement(root, "Connect")
    stream = SubElement(connect, "Stream", url=base.replace("https://", "wss://", 1) + "/twilio/media")
    if caller:
        SubElement(stream, "Parameter", name="caller", value=caller)
    return tostring(root, encoding="unicode")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


async def signed_voice_form(request: Request):
    base = public_base_url()
    params = dict(parse_qsl((await request.body()).decode("utf-8"), keep_blank_values=True))
    webhook_url = base + request.url.path
    if request.url.query:
        webhook_url += "?" + request.url.query
    if not valid_twilio_signature(
        webhook_url, params, request.headers.get("x-twilio-signature", "")
    ):
        raise HTTPException(403, "Invalid Twilio signature")
    return params


@app.post("/twilio/voice")
@app.post("/twilio/trial/voice")
async def voice_webhook(request: Request, background_tasks: BackgroundTasks) -> Response:
    params = await signed_voice_form(request)
    base = public_base_url()
    caller = params.get("To" if params.get("Direction", "").startswith("outbound") else "From", "")
    if request.url.path == "/twilio/trial/voice" or os.getenv("TWILIO_VOICE_MODE") == "trial":
        sid = params.get("CallSid", "")
        if not sid:
            raise HTTPException(422, "CallSid is required")
        xml = trial_voice.start(sid, base, caller)
        session = trial_voice.sessions.get(sid)
        if session:
            background_tasks.add_task(database.save_call, sid, caller, list(session.transcript))
        return Response(xml, media_type="application/xml")
    return Response(stream_twiml(base, caller), media_type="application/xml")


@app.post("/twilio/trial/turn")
async def trial_turn(request: Request, turn: int, background_tasks: BackgroundTasks):
    params = await signed_voice_form(request)
    sid = params.get("CallSid", "")
    if not sid or not 1 <= turn <= trial_voice.MAX_TURNS:
        raise HTTPException(422, "Invalid call or turn")
    xml = await trial_voice.turn(sid, turn, params.get("SpeechResult", ""), public_base_url())
    session = trial_voice.sessions.get(sid)
    if session:
        background_tasks.add_task(database.save_call, sid, session.caller, list(session.transcript))
    if session and turn in session.patient_turns and turn not in session.assessments_queued:
        session.assessments_queued.add(turn)
        history = [{"role": "user", "content": text} for text in session.patient_turns.values()]
        background_tasks.add_task(phone_alerts.assess_call, sid, session.caller, turn, history)
    return Response(xml, media_type="application/xml")


@app.get("/alerts")
async def alerts(x_admin_token: str | None = Header(default=None)):
    authenticate_admin(x_admin_token)
    try:
        return await asyncio.to_thread(phone_alerts.alert_history)
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from None


@app.post("/webhooks/twilio/messages")
async def message_status(request: Request, alert_id: str):
    params = await signed_voice_form(request)
    try:
        target = await asyncio.to_thread(phone_alerts.service)
        await asyncio.to_thread(target.message_event, alert_id, params.get("MessageSid", ""),
                                params.get("MessageStatus", ""))
    except KeyError:
        raise HTTPException(404, "Unknown alert") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return {"ok": True}


@app.get("/calls/{call_sid}/messages")
async def call_messages(call_sid: str, x_admin_token: str | None = Header(default=None)):
    authenticate_admin(x_admin_token)
    from uuid import NAMESPACE_URL, uuid5
    call_id = str(uuid5(NAMESPACE_URL, "twilio:" + call_sid))
    records = await asyncio.to_thread(call_summary_sms.delivery_history)
    return {"messages": [item for item in records if item["call_id"] == call_id]}


@app.post("/webhooks/twilio/call-summary")
async def call_summary_message_status(request: Request, delivery_id: str):
    params = await signed_voice_form(request)
    try:
        await asyncio.to_thread(call_summary_sms.message_status, delivery_id,
                                params.get("MessageSid", ""), params.get("MessageStatus", ""))
    except KeyError:
        raise HTTPException(404, "Unknown summary message") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return {"ok": True}


@app.websocket("/twilio/media")
async def media_stream(websocket: WebSocket) -> None:
    base = public_base_url()
    url = base.replace("https://", "wss://", 1) + "/twilio/media"
    signature = websocket.headers.get("x-twilio-signature", "")
    if not (valid_twilio_signature(url, {}, signature) or
            valid_twilio_signature(url + "/", {}, signature)):
        await websocket.close(code=1008)
        return
    config = SpeechConfig(
        require_setting("DEEPGRAM_API_KEY"),
        tts_model=os.environ.get("DEEPGRAM_TTS_MODEL", "aura-2-thalia-en"),
    )
    await websocket.accept()
    try:
        await CallSession(websocket, config).run()
    except Exception:
        log.exception("Voice stream ended with an error")
        await websocket.close(code=1011)


@app.post("/calls")
async def create_call(call: OutboundCall, x_admin_token: str | None = Header(default=None)) -> dict[str, str]:
    authenticate_admin(x_admin_token)
    if not re.fullmatch(r"\+[1-9]\d{7,14}", call.to):
        raise HTTPException(422, "to must be an E.164 phone number")
    account_sid = require_setting("TWILIO_ACCOUNT_SID")
    credentials = twilio_credentials()
    from_number = require_setting("TWILIO_FROM_NUMBER")
    base = public_base_url()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls.json",
                auth=credentials,
                data={
                    "To": call.to,
                    "From": from_number,
                    "Url": base + "/twilio/voice",
                    "Method": "POST",
                    "StatusCallback": base + "/twilio/status",
                    "StatusCallbackMethod": "POST",
                    "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"],
                },
            )
    except httpx.RequestError:
        raise HTTPException(504, "Call outcome unknown; check Twilio call logs before retrying") from None
    if response.is_error:
        try:
            error = response.json()
        except ValueError:
            error = {}
        code = error.get("code") if isinstance(error, dict) else None
        # Provider messages can contain phone numbers; return only numeric metadata.
        if type(code) is not int:
            code = None
        log.error("Twilio rejected outbound call: status=%s code=%s", response.status_code, code)
        raise HTTPException(502, {"message": "Twilio rejected outbound call",
                                  "twilio_http_status": response.status_code, "twilio_code": code})
    payload = response.json()
    return {"call_sid": payload["sid"], "status": payload.get("status", "queued")}


@app.get("/calls/{call_sid}")
async def call_status(call_sid: str, x_admin_token: str | None = Header(default=None)):
    authenticate_admin(x_admin_token)
    if not re.fullmatch(r"CA[0-9a-fA-F]{32}", call_sid):
        raise HTTPException(422, "Invalid call SID")
    account = require_setting("TWILIO_ACCOUNT_SID")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"https://api.twilio.com/2010-04-01/Accounts/{account}/Calls/{call_sid}.json",
                auth=twilio_credentials(),
            )
    except httpx.RequestError:
        raise HTTPException(502, "Unable to query Twilio call status") from None
    if response.is_error:
        raise HTTPException(502, "Twilio rejected call status query")
    payload = response.json()
    return {"call_sid": payload["sid"], "status": payload["status"], "duration": payload.get("duration")}


async def finalize_phone_call(params):
    sid, status = params.get("CallSid", ""), params.get("CallStatus", "")
    caller = params.get("To" if params.get("Direction", "").startswith("outbound") else "From", "")
    stream = active_sessions.get(sid)
    if stream and status in database.TERMINAL:
        try:
            # Status callbacks can race the final stream cleanup/persisted turn.
            async with asyncio.timeout(60):
                await stream.finished.wait()
        except TimeoutError:
            await database.save_call(sid, stream.caller, list(stream.transcript), status)
            log.error("Call finalization deferred: media stream has not finished")
            return
    session = trial_voice.sessions.get(sid)
    if session:
        async with session.lock:
            caller, transcript = session.caller, list(session.transcript)
    else:
        transcript = list(stream.transcript) if stream else None
        if stream:
            caller = stream.caller
    await database.finish_call(sid, caller, transcript, status)


@app.post("/twilio/status")
async def twilio_status(request: Request, background_tasks: BackgroundTasks):
    params = dict(parse_qsl((await request.body()).decode(), keep_blank_values=True))
    if not valid_twilio_signature(public_base_url() + "/twilio/status", params,
                                  request.headers.get("x-twilio-signature", "")):
        raise HTTPException(403, "Invalid Twilio signature")
    status = params.get("CallStatus", "")
    if status in {"queued", "initiated", "ringing", "in-progress", "completed", "busy", "failed", "no-answer", "canceled"}:
        log.info("Twilio call status=%s", status)
        background_tasks.add_task(finalize_phone_call, params)
    return {"ok": True}
