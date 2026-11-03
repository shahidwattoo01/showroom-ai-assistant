"""Background jobs: reminders, daily backups, lead re-scoring, closing idle chats."""
import asyncio
import logging
import shutil
from datetime import timedelta

from .config import settings
from .conversation import close_stale_conversations
from .db import now, session_scope
from .leads import score_lead
from .models import CustomerVehicle, Lead, Notification, ServiceAppointment, TestDrive
from .notify import notify_customer

log = logging.getLogger("scheduler")


def send_appointment_reminders(db) -> int:
    t = now()
    tomorrow_start = (t + timedelta(days=1)).replace(hour=0, minute=0, second=0)
    tomorrow_end = tomorrow_start + timedelta(days=1)
    count = 0
    for a in db.query(ServiceAppointment).filter(ServiceAppointment.status == "booked", ServiceAppointment.reminder_sent.is_(False),
                                                 ServiceAppointment.scheduled_at >= tomorrow_start,
                                                 ServiceAppointment.scheduled_at < tomorrow_end):
        notify_customer(db, a.customer, "appointment_reminder",
                        f"Reminder: your {a.service_type} for {a.car_model} is tomorrow at {a.scheduled_at:%I:%M %p} "
                        f"at {settings.showroom_name}, {settings.showroom_address}. Job no: {a.job_no}. Reply to reschedule.")
        a.reminder_sent = True
        count += 1
    for d in db.query(TestDrive).filter(TestDrive.status == "booked", TestDrive.reminder_sent.is_(False),
                                        TestDrive.scheduled_at >= tomorrow_start, TestDrive.scheduled_at < tomorrow_end):
        where = settings.showroom_address if d.location == "showroom" else f"your address ({d.address})"
        notify_customer(db, d.customer, "test_drive_reminder",
                        f"Reminder: your test drive of the {d.car_model.full_name} is tomorrow at {d.scheduled_at:%I:%M %p} at {where}. "
                        "Please bring your driving licence. Reply to reschedule.")
        d.reminder_sent = True
        count += 1
    return count


def send_service_due_reminders(db) -> int:
    soon = now().date() + timedelta(days=3)
    count = 0
    for cv in db.query(CustomerVehicle).filter(CustomerVehicle.next_service_due.isnot(None), CustomerVehicle.next_service_due <= soon):
        if cv.reminder_sent_for == cv.next_service_due:
            continue
        already_booked = db.query(ServiceAppointment).filter(ServiceAppointment.customer_id == cv.customer_id,
                                                             ServiceAppointment.status.in_(["booked", "in_progress"])).count()
        if not already_booked:
            notify_customer(db, cv.customer, "service_due",
                            f"Hi {cv.customer.name or ''}! Your {cv.model_name} ({cv.reg_no}) is due for its periodic service on "
                            f"{cv.next_service_due:%d %b}. Reply here to book a slot at {settings.showroom_name}.")
            count += 1
        cv.reminder_sent_for = cv.next_service_due
    return count


def rescore_leads(db):
    for lead in db.query(Lead).filter(Lead.status.notin_(["won", "lost"])):
        score_lead(db, lead)


def backup_database() -> str | None:
    """Daily copy of the SQLite file. (Cloud PostgreSQL / Supabase has its own automatic backups.)"""
    if not settings.is_sqlite:
        return None
    src = settings.database_url.replace("sqlite:///", "")
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    target = settings.backup_dir / f"showroom-{now():%Y-%m-%d}.db"
    if target.exists():
        return None
    shutil.copy2(src, target)
    backups = sorted(settings.backup_dir.glob("showroom-*.db"))
    for old in backups[:-settings.backup_keep_days]:
        old.unlink()
    return str(target)


def run_jobs_once() -> dict:
    with session_scope() as db:
        result = {"appointment_reminders": send_appointment_reminders(db), "service_due_reminders": send_service_due_reminders(db)}
        rescore_leads(db)
        close_stale_conversations(db)
    result["backup"] = backup_database()
    return result


async def scheduler_loop():
    await asyncio.sleep(5)
    while True:
        try:
            result = await asyncio.to_thread(run_jobs_once)
            if any(result.values()):
                log.info("scheduler: %s", result)
        except Exception:
            log.exception("scheduler run failed")
        await asyncio.sleep(settings.scheduler_interval_minutes * 60)


def outbox_count(db) -> int:
    return db.query(Notification).count()
