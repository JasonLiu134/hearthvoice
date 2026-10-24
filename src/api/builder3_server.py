"""Standalone Builder 3 app; preserves Builder 1's src.api.server:app."""

import asyncio
from contextlib import asynccontextmanager
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from src.api.routes.calls import router as calls_router
from src.api.routes.webhooks import internal_router, provider_router
from src.cron.scheduler import run_scheduler
from src.orchestration.config import Settings
from src.orchestration.service import Orchestrator
from src.orchestration.store import SQLiteStore
from src.services.twilio_client import MockProvider, TwilioProvider


def create_app(settings=None, service=None):
    settings = settings or Settings.from_env()
    settings.validate()

    @asynccontextmanager
    async def lifespan(app):
        owned_store = None
        if service is None:
            provider = MockProvider() if settings.provider == "mock" else TwilioProvider(
                settings.twilio_account_sid, settings.twilio_auth_token,
                settings.twilio_from_number, settings.public_base_url, settings.effective_voice_url)
            owned_store = SQLiteStore(settings.database_path)
            app.state.service = Orchestrator(owned_store, provider)
        else:
            app.state.service = service
        stop = asyncio.Event()
        task = asyncio.create_task(run_scheduler(app.state.service, stop)) if settings.scheduler_enabled else None
        try:
            yield
        finally:
            if task:
                stop.set()
                await task
            if owned_store:
                owned_store.close()

    app = FastAPI(title="HearthVoice Builder 3 test", lifespan=lifespan)
    app.state.settings = settings

    def authenticate(authorization: str = Header(default="")):
        if not secrets.compare_digest(authorization.encode(), ("Bearer " + settings.api_token).encode()):
            raise HTTPException(401, "Invalid bearer token")

    app.include_router(calls_router, dependencies=[Depends(authenticate)])
    app.include_router(internal_router, dependencies=[Depends(authenticate)])
    app.include_router(provider_router)

    @app.exception_handler(KeyError)
    async def missing_record(request, exc):
        return JSONResponse({"detail": "Record not found"}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid_request(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.get("/health")
    def health():
        return {"status": "ok", "provider": settings.provider,
                "smoke_test_enabled": settings.smoke_test_enabled}

    return app
