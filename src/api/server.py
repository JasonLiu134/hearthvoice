"""Twilio phone entry points for the HearthVoice demo."""

import logging
import os
import re
from urllib.parse import parse_qsl, urlparse
from xml.etree.ElementTree import Element, SubElement, tostring

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, WebSocket
from fastapi.responses import Response
from pydantic import BaseModel
from twilio.request_validator import RequestValidator

from src.core.voice_pipeline import CallSession, SpeechConfig

log = logging.getLogger(__name__)
app = FastAPI(title="HearthVoice")


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


def valid_twilio_signature(url: str, params: dict[str, str], signature: str) -> bool:
    return RequestValidator(require_setting("TWILIO_AUTH_TOKEN")).validate(url, params, signature)


def stream_twiml(base: str) -> str:
    root = Element("Response")
    connect = SubElement(root, "Connect")
    SubElement(connect, "Stream", url=base.replace("https://", "wss://", 1) + "/twilio/media")
    return tostring(root, encoding="unicode")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/twilio/voice")
async def voice_webhook(request: Request) -> Response:
    base = public_base_url()
    params = dict(parse_qsl((await request.body()).decode("utf-8"), keep_blank_values=True))
    if not valid_twilio_signature(
        base + "/twilio/voice", params, request.headers.get("x-twilio-signature", "")
    ):
        raise HTTPException(403, "Invalid Twilio signature")
    return Response(stream_twiml(base), media_type="application/xml")


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
    admin_token = require_setting("ADMIN_TOKEN")
    if x_admin_token != admin_token:
        raise HTTPException(403, "Invalid admin token")
    if not re.fullmatch(r"\+[1-9]\d{7,14}", call.to):
        raise HTTPException(422, "to must be an E.164 phone number")
    account_sid = require_setting("TWILIO_ACCOUNT_SID")
    api_key_sid = require_setting("TWILIO_API_KEY_SID")
    api_key_secret = require_setting("TWILIO_API_KEY_SECRET")
    from_number = require_setting("TWILIO_FROM_NUMBER")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls.json",
            auth=(api_key_sid, api_key_secret),
            data={
                "To": call.to,
                "From": from_number,
                "Url": public_base_url() + "/twilio/voice",
                "Method": "POST",
            },
        )
    if response.is_error:
        log.error("Twilio rejected outbound call: status=%s", response.status_code)
        raise HTTPException(502, "Twilio rejected outbound call")
    return {"call_sid": response.json()["sid"]}
