"""WhatsApp webhook (Meta Cloud API). One number serves both customers and staff:
staff phone numbers (Staff table) go to the command bot, everyone else to the AI agent.
"""
import base64
import hashlib
import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import PlainTextResponse

from .. import speech
from ..config import settings
from ..conversation import get_or_create_conversation, handle_customer_message
from ..db import session_scope
from ..models import Staff
from ..utils import normalize_phone
from . import whatsapp_api
from .staff import handle_staff_message

log = logging.getLogger("whatsapp")
router = APIRouter(prefix="/webhooks/whatsapp", tags=["whatsapp"])


def process_incoming(phone: str, text: str, name: str | None = None, images: list[dict] | None = None,
                     voice_note: bool = False) -> list[dict]:
    """Core handler shared by the real webhook and the admin-panel simulator.
    Returns the replies that should be sent back to `phone`."""
    phone = normalize_phone(phone)
    with session_scope() as db:
        staff = db.query(Staff).filter(Staff.phone == phone, Staff.active.is_(True)).first()
        if staff and not text.lower().startswith("#customer"):
            return handle_staff_message(db, staff, text)
        if staff:  # staff can test the customer bot by prefixing "#customer"
            text = text[len("#customer"):].strip()
        conv = get_or_create_conversation(db, "whatsapp", phone, phone=phone, name=name)
        reply = handle_customer_message(db, conv, text, images=images)
        if not reply["text"]:
            return []
        return [{"text": reply["text"], "buttons": reply.get("buttons") or [], "voice": voice_note}]


def _deliver(phone: str, replies: list[dict]):
    for reply in replies:
        try:
            if reply.get("voice") and speech.tts_enabled():
                whatsapp_api.send_audio(phone, speech.synthesize(reply["text"]))
            whatsapp_api.send_reply(phone, reply)
        except Exception:
            log.exception("failed to send WhatsApp reply to %s", phone)


def _handle_event(msg: dict, name: str | None):
    phone = msg["from"]
    kind = msg.get("type")
    whatsapp_api.mark_read(msg.get("id", ""))
    text, images, voice = "", None, False
    try:
        if kind == "text":
            text = msg["text"]["body"]
        elif kind == "interactive":
            inter = msg["interactive"]
            text = (inter.get("button_reply") or inter.get("list_reply") or {}).get("title", "")
        elif kind == "button":
            text = msg["button"].get("text", "")
        elif kind == "audio":
            voice = True
            if not speech.stt_enabled():
                _deliver(phone, [{"text": "Sorry, I can't listen to voice notes yet. Please type your message. 🙏"}])
                return
            data, mime = whatsapp_api.download_media(msg["audio"]["id"])
            text = speech.transcribe(data, mime)
        elif kind == "image":
            data, mime = whatsapp_api.download_media(msg["image"]["id"])
            images = [{"type": "image", "source": {"type": "base64", "media_type": mime.split(";")[0],
                                                   "data": base64.standard_b64encode(data).decode()}}]
            text = msg["image"].get("caption", "")
        elif kind == "location":
            loc = msg["location"]
            text = f"[Customer shared location: {loc.get('latitude')},{loc.get('longitude')} {loc.get('address', '')}]"
        else:
            _deliver(phone, [{"text": "Sorry, I can only read text, voice notes and pictures."}])
            return
        replies = process_incoming(phone, text, name=name, images=images, voice_note=voice)
        _deliver(phone, replies)
    except Exception:
        log.exception("WhatsApp message handling failed")


@router.get("")
def verify(request: Request):
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == settings.whatsapp_verify_token:
        return PlainTextResponse(params.get("hub.challenge", ""))
    raise HTTPException(403, "verification failed")


@router.post("")
async def receive(request: Request, background: BackgroundTasks):
    body = await request.body()
    if settings.whatsapp_app_secret:
        expected = "sha256=" + hmac.new(settings.whatsapp_app_secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, request.headers.get("X-Hub-Signature-256", "")):
            raise HTTPException(401, "bad signature")
    payload = await request.json()
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {c.get("wa_id"): c.get("profile", {}).get("name") for c in value.get("contacts", [])}
            for msg in value.get("messages", []):
                # answer Meta immediately (it retries slow webhooks); do the AI work in the background
                background.add_task(_handle_event, msg, names.get(msg.get("from")))
    return {"ok": True}
