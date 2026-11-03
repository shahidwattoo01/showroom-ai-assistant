"""Automatic outgoing messages (reminders, job-ready alerts, staff alerts).

Every message is stored in the `notifications` outbox. If WhatsApp is
configured it is also sent; otherwise it is marked "logged" so you can see in
the admin panel exactly what would have been sent.

Note: outside WhatsApp's 24-hour window Meta only allows pre-approved template
messages. Register templates for these texts before going live.
"""
from sqlalchemy.orm import Session

from .channels import whatsapp_api
from .config import settings
from .models import Customer, Notification, Staff


def notify_customer(db: Session, customer: Customer | None, kind: str, text: str) -> Notification:
    note = Notification(customer_id=customer.id if customer else None, channel="whatsapp",
                        to=customer.phone if customer else None, kind=kind, text=text)
    if not customer or not customer.phone:
        note.status, note.error = "failed", "customer has no phone number"
    elif not settings.whatsapp_enabled:
        note.status = "logged"
    else:
        try:
            whatsapp_api.send_text(customer.phone, text)
            note.status = "sent"
        except Exception as exc:
            note.status, note.error = "failed", str(exc)[:500]
    db.add(note)
    return note


def notify_staff(db: Session, text: str, roles: tuple[str, ...] = ("owner", "sales")):
    """Alert staff on WhatsApp (e.g. new test drive, customer asked for a human)."""
    for staff in db.query(Staff).filter(Staff.active.is_(True), Staff.role.in_(roles), Staff.phone.isnot(None)):
        note = Notification(channel="whatsapp", to=staff.phone, kind="staff_alert", text=text)
        if settings.whatsapp_enabled:
            try:
                whatsapp_api.send_text(staff.phone, text)
                note.status = "sent"
            except Exception as exc:
                note.status, note.error = "failed", str(exc)[:500]
        else:
            note.status = "logged"
        db.add(note)
