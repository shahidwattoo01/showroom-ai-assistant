"""Channel-independent conversation handling. Web chat, WhatsApp and phone calls
all call `handle_customer_message`, so the customer gets the same brain and the
same memory everywhere (linked by phone number)."""
import logging
import re
from datetime import timedelta

from sqlalchemy.orm import Session

from .agents import get_engine
from .channels import whatsapp_api
from .config import settings
from .db import now
from .models import Conversation, Customer, Message, Staff
from .tools import ToolContext
from .utils import normalize_phone

log = logging.getLogger("conversation")

QUICK_BUTTONS = ["New/Used Cars", "Book Service", "Spare Parts"]
BUTTON_TEXT = {
    "new/used cars": "I want to know about new and used cars",
    "book service": "I want to book a service for my car",
    "spare parts": "I need a spare part",
}
GREETING = re.compile(r"^\s*(hi+|hello+|hey+|salam|assalam[a-z ]*|aoa|slam|hy|good (morning|afternoon|evening)|السلام علیکم|سلام)\W*\s*$", re.I)
SUMMARY_EVERY = 8
HISTORY_LIMIT = 20


def get_or_create_conversation(db: Session, channel: str, external_id: str, phone: str | None = None,
                               name: str | None = None) -> Conversation:
    conv = (db.query(Conversation).filter(Conversation.channel == channel, Conversation.external_id == external_id,
                                          Conversation.status != "closed").order_by(Conversation.id.desc()).first())
    if conv:
        return conv
    customer = None
    phone = normalize_phone(phone)
    if phone:
        customer = db.query(Customer).filter(Customer.phone == phone).first()
    if not customer:
        customer = Customer(phone=phone, name=name)
        db.add(customer)
        db.flush()
    elif name and not customer.name:
        customer.name = name
    conv = Conversation(customer_id=customer.id, channel=channel, external_id=external_id)
    db.add(conv)
    db.flush()
    return conv


def _history(conv: Conversation) -> list[dict]:
    """Earlier turns as plain text, alternating user/assistant, starting with user."""
    out: list[dict] = []
    for m in conv.messages[-HISTORY_LIMIT - 1:-1]:  # last message is the current one
        if m.role == "system" or not m.text:
            continue
        role = "user" if m.role == "customer" else "assistant"
        text = m.text if m.role != "staff" else f"[Staff reply] {m.text}"
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n" + text
        else:
            out.append({"role": role, "content": text})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def _transcript(conv: Conversation, limit: int = 30) -> str:
    names = {"customer": "Customer", "bot": "Assistant", "staff": "Staff"}
    return "\n".join(f"{names.get(m.role, m.role)}: {m.text}" for m in conv.messages[-limit:] if m.text and m.role != "system")


def update_memory(db: Session, conv: Conversation):
    try:
        conv.customer.memory = get_engine().summarize(_transcript(conv), conv.customer.memory)[:1500]
        conv.summary = conv.customer.memory
    except Exception as exc:
        log.warning("memory summary failed: %s", exc)


def add_message(db: Session, conv: Conversation, role: str, text: str, meta: dict | None = None) -> Message:
    msg = Message(conversation_id=conv.id, role=role, text=text, meta=meta)
    db.add(msg)
    conv.last_message_at = now()
    db.flush()
    db.refresh(conv)
    return msg


def handle_customer_message(db: Session, conv: Conversation, text: str, images: list[dict] | None = None) -> dict:
    """Process one customer message. Returns {"text": str|None, "buttons": [...], "status": str}."""
    text = (text or "").strip()
    text = BUTTON_TEXT.get(text.lower(), text)
    is_new = len(conv.messages) == 0
    add_message(db, conv, "customer", text or "[image]", {"images": len(images or [])} if images else None)

    if conv.status == "human":
        conv.unread = (conv.unread or 0) + 1
        return {"text": None, "buttons": [], "status": "human"}

    customer = conv.customer
    if is_new and GREETING.match(text):
        hello = f"Assalam-o-Alaikum{(' ' + customer.name) if customer.name else ''}! Welcome to {settings.showroom_name}. "
        reply = {"text": hello + "How can I help you today?", "buttons": [] if conv.channel == "voice" else QUICK_BUTTONS}
        add_message(db, conv, "bot", reply["text"], {"department": "general"})
        return {**reply, "status": conv.status}

    engine = get_engine()
    ctx = ToolContext(db=db, conversation=conv, customer=customer)
    from .agents import build_context

    try:
        recent = "\n".join(f"{m.role}: {m.text[:150]}" for m in conv.messages[-6:-1] if m.text)
        department = engine.route(recent, text, conv.department or "general")
        conv.department = department
        content = [{"type": "text", "text": build_context(ctx)}]
        content += images or []
        content.append({"type": "text", "text": f"[Customer message]\n{text or 'Please look at this image.'}"})
        answer = engine.respond(ctx, department, _history(conv), content)
    except Exception as exc:
        log.exception("AI engine failed")
        department = conv.department
        answer = ("Sorry, I'm having a technical problem right now. I've asked our team to contact you."
                  if conv.channel != "voice" else "Sorry, I'm having a technical problem. Our team will call you back.")
        conv.status = "human"
        conv.handoff_reason = f"AI error: {str(exc)[:200]}"
    conv = ctx.conversation
    add_message(db, conv, "bot", answer, {"department": department, "engine": engine.name})

    customer_msgs = sum(1 for m in conv.messages if m.role == "customer")
    if customer_msgs % SUMMARY_EVERY == 0 or conv.status == "human":
        update_memory(db, conv)
    return {"text": answer, "buttons": [], "status": conv.status}


def staff_reply(db: Session, conv: Conversation, staff: Staff, text: str) -> Message:
    """A human replies from the admin panel; delivered on the customer's channel."""
    msg = add_message(db, conv, "staff", text, {"staff": staff.name})
    conv.unread = 0
    if conv.channel == "whatsapp" and conv.customer.phone and settings.whatsapp_enabled:
        whatsapp_api.send_text(conv.customer.phone, text)
    return msg


def close_stale_conversations(db: Session, hours: int = 24):
    cutoff = now() - timedelta(hours=hours)
    for conv in db.query(Conversation).filter(Conversation.status == "bot", Conversation.last_message_at < cutoff):
        if conv.messages and not conv.customer.memory:
            update_memory(db, conv)
        conv.status = "closed"
