"""Lead scoring: hot / warm / cold buyer, recalculated whenever a lead signal changes."""
from datetime import timedelta

from sqlalchemy.orm import Session

from .db import now
from .models import Conversation, Lead, TestDrive

HOT, WARM = 60, 30
SOON_WORDS = ("today", "this week", "this month", "urgent", "asap", "immediately", "aaj", "is mahine", "jaldi")


def score_lead(db: Session, lead: Lead) -> Lead:
    score = 10  # made an enquiry
    if lead.car_model_id or (lead.interest and len(lead.interest) > 3):
        score += 10  # knows what they want
    if lead.budget:
        score += 10
    if lead.wants_financing:
        score += 10
    if lead.has_trade_in:
        score += 5
    if lead.timeline and any(w in lead.timeline.lower() for w in SOON_WORDS):
        score += 20
    if lead.customer_id:
        if db.query(TestDrive).filter(TestDrive.customer_id == lead.customer_id, TestDrive.status != "cancelled").count():
            score += 30
        chats = db.query(Conversation).filter(Conversation.customer_id == lead.customer_id).count()
        if chats > 1:
            score += 10  # came back again
        if lead.customer and lead.customer.phone:
            score += 5
    if lead.status in ("negotiation",):
        score += 15
    if lead.status in ("won", "lost"):
        score = 0 if lead.status == "lost" else 100
    # leads go cold when nobody talks to them
    if lead.updated_at and lead.updated_at < now() - timedelta(days=21) and lead.status not in ("won",):
        score -= 20
    lead.score = max(0, min(100, score))
    lead.score_label = "hot" if lead.score >= HOT else "warm" if lead.score >= WARM else "cold"
    return lead


def rescore_customer(db: Session, customer_id: int):
    for lead in db.query(Lead).filter(Lead.customer_id == customer_id, Lead.status.notin_(["won", "lost"])):
        score_lead(db, lead)
