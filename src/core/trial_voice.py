"""Bounded, single-process Gather/Say conversations for inbound trial calls."""

import asyncio
from contextlib import aclosing
from dataclasses import dataclass, field
import logging
import time
from xml.etree.ElementTree import Element, SubElement, tostring

from src.core.agent import END_MARKER, HearthAgent

log = logging.getLogger(__name__)
MAX_TURNS = 4  # Stay below the trial's ten action/redirect hops.
MODEL_TIMEOUT = 3.0  # Trial TwiML fetch deadline is five seconds.


@dataclass
class Conversation:
    caller: str = ""
    transcript: list = field(default_factory=list)
    history: list = field(default_factory=list)
    responses: dict = field(default_factory=dict)
    patient_turns: dict = field(default_factory=dict)
    assessments_queued: set = field(default_factory=set)
    next_turn: int = 1
    expires: float = field(default_factory=lambda: time.monotonic() + 600)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


sessions: dict[str, Conversation] = {}


def twiml(text, base, next_turn=None):
    root = Element("Response")
    SubElement(root, "Say").text = text
    if next_turn is None:
        SubElement(root, "Hangup")
    else:
        SubElement(root, "Gather", input="speech", language="en-US", method="POST",
                   action=f"{base}/twilio/trial/turn?turn={next_turn}",
                   speechTimeout="auto", timeout="5", actionOnEmptyResult="true")
    return tostring(root, encoding="unicode")


def prune():
    for sid in list(sessions):
        if sessions[sid].expires < time.monotonic() and not sessions[sid].lock.locked():
            del sessions[sid]


def start(call_sid, base, caller=""):
    prune()
    if call_sid not in sessions:
        if len(sessions) >= 100:
            return twiml("The service is busy. Please call again later.", base)
        sessions[call_sid] = Conversation(caller=caller)
    greeting = HearthAgent().opening_line({}, inbound=True)
    if not sessions[call_sid].transcript:
        sessions[call_sid].transcript.append({"role": "assistant", "content": greeting})
    return twiml(greeting, base, 1)


async def turn(call_sid, number, speech, base):
    prune()
    session = sessions.get(call_sid)
    if session is None:
        return twiml("This session has expired. Please call again.", base)
    # Concurrent retries reuse the original result, never regenerate or add history.
    try:
        async with asyncio.timeout(3.8):
            async with session.lock:
                if number in session.responses:
                    return session.responses[number]
                if number != session.next_turn or number > MAX_TURNS:
                    return twiml("This conversation has ended. Goodbye.", base)
                final = number == MAX_TURNS
                if not speech.strip():
                    text = "I didn't hear anything. Please try again."
                else:
                    session.patient_turns[number] = speech[:2000]
                    agent = HearthAgent()
                    session.history.append({"role": "user", "content": speech[:2000]})
                    pieces = []
                    try:
                        async with asyncio.timeout(MODEL_TIMEOUT):
                            async with aclosing(agent.reply_stream(
                                session.history,
                                {"notes": "Keep this trial response to one short sentence, at most 20 words."},
                                max_tokens=64,
                            )) as stream:
                                async for chunk in stream:
                                    pieces.append(chunk)
                        raw = "".join(pieces)
                        final = final or END_MARKER in raw
                        text = raw.replace(END_MARKER, "").strip()[:1500]
                        if not text:
                            raise RuntimeError("Empty agent reply")
                        session.history.append({"role": "assistant", "content": text})
                        log.info("trial agent SUCCESS turn=%d", number)
                    except Exception as exc:
                        session.history.pop()
                        log.warning("trial agent FAIL turn=%d error_type=%s", number, type(exc).__name__)
                        text = "I'm having trouble responding right now. Please try again."
                    finally:
                        await agent.close()
                if number == MAX_TURNS:
                    text += " Thank you for trying Hearth. Goodbye."
                if speech.strip():
                    session.transcript.append({"role": "user", "content": speech[:2000]})
                session.transcript.append({"role": "assistant", "content": text})
                xml = twiml(text, base, None if final else number + 1)
                session.responses[number] = xml
                session.next_turn = MAX_TURNS + 1 if final else number + 1
                return xml
    except TimeoutError:
        return twiml("The service is taking too long. Please call again later.", base)
