"""One scheduler per application process; run the demo with one Uvicorn worker."""

import asyncio
import logging

logger = logging.getLogger(__name__)


async def run_scheduler(service, stop):
    while not stop.is_set():
        try:
            await asyncio.to_thread(service.tick)
        except Exception as exc:
            # Avoid logging provider payloads or personal data.
            logger.error("Scheduler tick failed (%s)", type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        except TimeoutError:
            pass
