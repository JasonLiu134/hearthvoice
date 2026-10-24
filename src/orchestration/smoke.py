"""Scripted test voice only: button presses create explicitly synthetic assessments."""

from xml.etree.ElementTree import Element, SubElement, tostring

from src.services.twilio_client import with_query


def handle_smoke_voice(service, voice_url, call_id, provider_call_id, stage="prompt", digits=""):
    call = service.snapshot()["calls"][call_id]
    if not provider_call_id or call["provider_call_id"] != provider_call_id:
        raise ValueError("Provider call ID does not match")
    if stage not in {"prompt", "answer"}:
        raise ValueError("Unknown test voice stage")
    response = Element("Response")
    if stage == "prompt":
        gather = SubElement(response, "Gather", {
            "input": "dtmf", "numDigits": "1", "timeout": "10", "method": "POST",
            "action": with_query(voice_url, call_id=call_id, stage="answer"),
            "actionOnEmptyResult": "true",
        })
        SubElement(gather, "Say").text = (
            "This is a HearthVoice test call. Press 1 to simulate a concern and send a test caregiver text. "
            "Press 2 to finish normally without a text. This is only a test."
        )
    elif digits in {"1", "2"}:
        triage = {"status": "HIGH_RISK" if digits == "1" else "NORMAL", "synthetic": True,
                  "summary": "SYNTHETIC telephony test selected by keypad; no health assessment performed."}
        # A repeated webhook uses the same event; a conflicting second choice is rejected.
        service.assessment("smoke:" + call_id, call_id, triage)
        SubElement(response, "Say").text = (
            "Your simulated concern was recorded. The test text delivery status is available in call history. Goodbye."
            if digits == "1" else "The normal test check-in was recorded. Goodbye."
        )
    else:
        SubElement(response, "Say").text = "No valid test choice was received. Goodbye."
    SubElement(response, "Hangup")
    return tostring(response, encoding="unicode")
