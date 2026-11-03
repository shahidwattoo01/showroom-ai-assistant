"""Website chat widget API (text + browser voice)."""
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .config import settings
from .conversation import QUICK_BUTTONS, get_or_create_conversation, handle_customer_message
from .db import get_db
from .models import Conversation, Message

router = APIRouter(prefix="/api/chat", tags=["web chat"])


class StartIn(BaseModel):
    session_id: str | None = None


class SendIn(BaseModel):
    session_id: str
    text: str


def _conv(db: Session, session_id: str) -> Conversation | None:
    return (db.query(Conversation).filter(Conversation.channel == "web", Conversation.external_id == session_id)
            .order_by(Conversation.id.desc()).first())


@router.post("/start")
def start(body: StartIn, db: Session = Depends(get_db)):
    session_id = body.session_id if body.session_id and len(body.session_id) >= 16 else secrets.token_urlsafe(18)
    conv = _conv(db, session_id)
    history = [{"id": m.id, "role": m.role, "text": m.text} for m in conv.messages] if conv and conv.status != "closed" else []
    return {"session_id": session_id, "showroom": settings.showroom_name, "history": history,
            "welcome": f"Assalam-o-Alaikum! 👋 Welcome to {settings.showroom_name}. Ask me about new & used cars, prices, "
                       "instalments, test drives, service booking or spare parts - in English or Urdu.",
            "buttons": QUICK_BUTTONS}


@router.post("/send")
def send(body: SendIn, db: Session = Depends(get_db)):
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "empty message")
    if len(text) > 2000:
        text = text[:2000]
    conv = get_or_create_conversation(db, "web", body.session_id)
    try:
        reply = handle_customer_message(db, conv, text)
        db.commit()
    except Exception:
        db.rollback()
        raise
    last_id = db.query(Message.id).filter(Message.conversation_id == conv.id).order_by(Message.id.desc()).first()[0]
    return {**reply, "last_id": last_id}


@router.get("/poll")
def poll(session_id: str, after: int = 0, db: Session = Depends(get_db)):
    """Staff replies (human takeover) arrive here."""
    conv = _conv(db, session_id)
    if not conv:
        return {"messages": [], "status": "new"}
    msgs = db.query(Message).filter(Message.conversation_id == conv.id, Message.id > after, Message.role == "staff").all()
    return {"messages": [{"id": m.id, "role": m.role, "text": m.text} for m in msgs], "status": conv.status}
