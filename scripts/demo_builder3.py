"""Offline demo: python3 scripts/demo_builder3.py (no third-party packages)."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.orchestration.service import Orchestrator
from src.orchestration.store import SQLiteStore
from src.services.twilio_client import MockProvider


def main():
    now = datetime(2026, 10, 24, 16, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteStore(str(Path(directory) / "demo.sqlite3"))
        provider = MockProvider()
        service = Orchestrator(store, provider, lambda: now)
        for senior_id in ("concern-demo", "missed-demo"):
            service.put_profile({
                "senior_id": senior_id, "name": "Fictional Demo",
                "phone": "+12025550101" if senior_id == "concern-demo" else "+12025550103",
                "caregiver_phone": "+12025550102", "timezone": "America/Los_Angeles",
                "local_call_time": "09:00",
            })
        concern = service.start_check_in("concern-demo")
        service.assessment("demo-assessment", concern["call_ids"][0], {
            "status": "HIGH_RISK", "meds_taken": False, "current_pain_level": 7,
            "symptoms_flagged": ["increased pain", "missed medication"],
            "summary": "Fictional missed-medication and pain scenario.",
        })
        check = service.start_check_in("missed-demo")
        call = service.state["calls"][check["call_ids"][-1]]
        service.call_event(call["call_id"], call["provider_call_id"], "no-answer")
        now += timedelta(minutes=5)
        service.tick()
        check = service.state["check_ins"][check["check_in_id"]]
        call = service.state["calls"][check["call_ids"][-1]]
        service.call_event(call["call_id"], call["provider_call_id"], "no-answer")
        persisted = store.load()
        print(json.dumps({
            "mode": "mock: no real calls or texts sent",
            "call_attempts": len(provider.calls),
            "persisted_alerts": [{"kind": a["kind"], "status": a["status"]}
                                 for a in persisted["alerts"].values()],
            "sms_bodies": [message["body"] for message in provider.messages],
        }, indent=2))
        store.close()


if __name__ == "__main__":
    main()
