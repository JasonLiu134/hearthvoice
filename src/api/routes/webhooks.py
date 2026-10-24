from html import escape
from typing import Literal
from urllib.parse import parse_qsl

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData

from src.services.twilio_client import with_query
from src.orchestration.smoke import handle_smoke_voice

internal_router = APIRouter()
provider_router = APIRouter()


class Triage(BaseModel):
    status: Literal["NORMAL", "LOW_RISK", "HIGH_RISK"]
    meds_taken: bool | None = Field(default=None, strict=True)
    current_pain_level: int | None = Field(default=None, ge=0, le=10, strict=True)
    symptoms_flagged: list[str] = Field(default_factory=list)
    summary: str | None = None


class Assessment(BaseModel):
    event_id: str = Field(min_length=1)
    call_id: str = Field(min_length=1)
    triage: Triage


class CallEvent(BaseModel):
    call_id: str
    provider_call_id: str
    status: str


class MessageEvent(BaseModel):
    alert_id: str
    provider_message_id: str
    status: str


@internal_router.post("/events/assessments")
def assessment(payload: Assessment, request: Request):
    return request.app.state.service.assessment(
        payload.event_id, payload.call_id, payload.triage.model_dump())


@internal_router.post("/events/calls")
def call_event(payload: CallEvent, request: Request):
    return request.app.state.service.call_event(**payload.model_dump())


@internal_router.post("/events/messages")
def message_event(payload: MessageEvent, request: Request):
    return request.app.state.service.message_event(**payload.model_dump())


async def verified_form(request):
    settings = request.app.state.settings
    if settings.provider != "twilio":
        raise HTTPException(404, "Twilio webhooks are disabled in mock mode")
    from twilio.request_validator import RequestValidator

    if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
        raise HTTPException(415, "Expected URL-encoded Twilio form")
    form = FormData(parse_qsl((await request.body()).decode("utf-8"), keep_blank_values=True))
    url = settings.public_base_url.rstrip("/") + request.url.path
    if request.url.query:
        url += "?" + request.url.query
    if not RequestValidator(settings.twilio_auth_token).validate(
        url, form, request.headers.get("X-Twilio-Signature", "")
    ):
        raise HTTPException(403, "Invalid Twilio signature")
    return form


@provider_router.post("/webhooks/twilio/calls")
async def twilio_call(request: Request, call_id: str | None = None):
    form = await verified_form(request)
    service = request.app.state.service
    provider_id, status = form.get("CallSid", ""), form.get("CallStatus", "")
    if call_id is None:
        calls = service.snapshot()["calls"]
        call_id = next((c["call_id"] for c in calls.values()
                        if c["provider_call_id"] == provider_id), None)
        if call_id is None:
            raise HTTPException(404, "Unknown call")
    await run_in_threadpool(service.call_event, call_id, provider_id, status)
    return {"ok": True}


@provider_router.post("/webhooks/twilio/messages")
async def twilio_message(request: Request, alert_id: str):
    form = await verified_form(request)
    await run_in_threadpool(request.app.state.service.message_event,
        alert_id, form.get("MessageSid", ""), form.get("MessageStatus", ""))
    return {"ok": True}


@provider_router.post("/webhooks/twilio/inbound")
async def twilio_inbound(request: Request):
    form = await verified_form(request)
    try:
        call = await run_in_threadpool(
            request.app.state.service.inbound, form.get("From"), form.get("CallSid"))
    except LookupError:
        return Response("<Response><Say>This number is not registered for check-ins.</Say><Hangup/></Response>",
                        media_type="application/xml")
    url = with_query(request.app.state.settings.effective_voice_url, call_id=call["call_id"])
    return Response("<Response><Redirect method=\"POST\">" + escape(url) + "</Redirect></Response>",
                    media_type="application/xml")


@provider_router.post("/webhooks/twilio/smoke/voice")
async def smoke_voice(request: Request, call_id: str, stage: str = "prompt"):
    if not request.app.state.settings.smoke_test_enabled:
        raise HTTPException(404, "Scripted test voice is disabled")
    form = await verified_form(request)
    xml = await run_in_threadpool(
        handle_smoke_voice, request.app.state.service,
        request.app.state.settings.effective_voice_url,
        call_id, form.get("CallSid", ""), stage, form.get("Digits", ""))
    return Response(xml, media_type="application/xml")
