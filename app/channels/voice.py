"""Phone-call agent using Twilio Programmable Voice.

Point your Twilio number's "A call comes in" webhook to  POST {PUBLIC_BASE_URL}/voice/incoming
Twilio does the speech recognition (<Gather input="speech">) and text-to-speech (<Say>);
this server only exchanges text with the same AI agent used for chat.
For lower latency later, swap this for a streaming pipeline (Twilio Media Streams + Pipecat/LiveKit).
"""
import base64
import hashlib
import hmac
from xml.sax.saxutils import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..config import settings
from ..conversation import add_message, get_or_create_conversation, handle_customer_message
from ..db import session_scope
from ..models import Conversation

router = APIRouter(prefix="/voice", tags=["voice"])


def _twiml(body: str) -> Response:
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>', media_type="application/xml")


def _say(text: str) -> str:
    return f'<Say voice="{settings.call_voice}" language="{settings.call_language}">{escape(text)}</Say>'


def _gather(prompt: str = "") -> str:
    inner = _say(prompt) if prompt else ""
    return (f'<Gather input="speech" action="/voice/gather" method="POST" language="{settings.call_language}" '
            f'speechTimeout="auto" actionOnEmptyResult="true">{inner}</Gather>')


async def _form(request: Request) -> dict:
    form = dict(await request.form())
    if settings.twilio_auth_token:
        url = settings.public_base_url.rstrip("/") + request.url.path
        data = url + "".join(f"{k}{form[k]}" for k in sorted(form))
        expected = base64.b64encode(hmac.new(settings.twilio_auth_token.encode(), data.encode(), hashlib.sha1).digest()).decode()
        if not hmac.compare_digest(expected, request.headers.get("X-Twilio-Signature", "")):
            raise HTTPException(401, "bad Twilio signature")
    return form


@router.post("/incoming")
async def incoming(request: Request):
    form = await _form(request)
    with session_scope() as db:
        conv = get_or_create_conversation(db, "voice", form.get("CallSid", "unknown"), phone=form.get("From"))
        name = conv.customer.name
        greeting = (f"Assalam o Alaikum{', ' + name if name else ''}! Thank you for calling {settings.showroom_name}. "
                    "I'm the virtual assistant. How can I help you today? You can ask about cars, service or spare parts.")
        add_message(db, conv, "bot", greeting, {"department": "general"})
    return _twiml(_gather(greeting) + _say("Sorry, I didn't hear anything. Goodbye!"))


@router.post("/gather")
async def gather(request: Request):
    form = await _form(request)
    speech = (form.get("SpeechResult") or "").strip()
    call_sid = form.get("CallSid", "unknown")
    if not speech:
        return _twiml(_gather("Sorry, I didn't catch that. Could you please repeat?") + _say("Goodbye!"))
    if any(w in speech.lower() for w in ("bye", "goodbye", "khuda hafiz", "allah hafiz", "that's all", "thank you bye")):
        with session_scope() as db:
            conv = db.query(Conversation).filter_by(channel="voice", external_id=call_sid).first()
            if conv:
                conv.status = "closed"
        return _twiml(_say("Thank you for calling. Allah Hafiz!") + "<Hangup/>")
    with session_scope() as db:
        conv = get_or_create_conversation(db, "voice", call_sid, phone=form.get("From"))
        reply = handle_customer_message(db, conv, speech)
        handed_off = conv.status == "human"
    text = reply["text"] or "One moment please."
    if handed_off and settings.call_transfer_number:
        return _twiml(_say(text + " Transferring your call now.") + f"<Dial>{escape(settings.call_transfer_number)}</Dial>")
    if handed_off:
        return _twiml(_say(text + " Our team will call you back. Allah Hafiz!") + "<Hangup/>")
    return _twiml(_gather(text) + _say("Are you still there? Goodbye!"))
