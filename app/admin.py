"""Admin panel API: login, generic table editing, live chats, Excel import/export,
knowledge documents, WhatsApp simulator and dashboard numbers."""
import io
import re
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from openpyxl import Workbook, load_workbook
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .agents import get_engine
from .channels.whatsapp import process_incoming
from .config import settings
from .conversation import staff_reply, update_memory
from .db import get_db, now
from .knowledge import KNOWLEDGE_DIR, SUPPORTED, kb
from .leads import score_lead
from .models import (AuditLog, CarModel, Conversation, Customer, CustomerVehicle, Lead, Notification, Offer, Part,
                     ServiceAppointment, ServiceType, Staff, TestDrive, Vehicle)
from .notify import notify_customer
from .scheduler import run_jobs_once
from .tools import match_car_models
from .utils import hash_password, make_token, money, normalize_phone, read_token, verify_password

router = APIRouter(prefix="/api/admin", tags=["admin"])
ALL = ("owner", "sales", "service", "parts")


def F(name, type="text", label=None, options=None, ref=None, readonly=False, list=True):
    return {"name": name, "type": type, "label": label or name.replace("_", " ").capitalize(), "options": options,
            "ref": ref, "readonly": readonly, "list": list}


TABLES = {
    "vehicles": {"model": Vehicle, "label": "Stock (vehicles)", "roles": ("owner", "sales"), "key": "chassis_no",
                 "search": ["color", "chassis_no", "reg_no"], "fields": [
                     F("car_model_id", "ref", "Car", ref="car_models"), F("condition", "select", options=["new", "used"]),
                     F("year", "int"), F("color"), F("price", "int"), F("mileage_km", "int", "Mileage km"),
                     F("chassis_no"), F("reg_no", label="Reg no"), F("status", "select", options=["available", "booked", "sold"]),
                     F("arrived_at", "date"), F("notes", "textarea", list=False)]},
    "car_models": {"model": CarModel, "label": "Car catalog", "roles": ("owner", "sales"), "key": None,
                   "search": ["make", "model", "variant"], "fields": [
                       F("make"), F("model"), F("variant"), F("body_type", "select", options=["sedan", "hatchback", "suv", "crossover", "pickup", "van"]),
                       F("fuel", "select", options=["petrol", "hybrid", "diesel", "electric"]), F("transmission"),
                       F("engine_cc", "int", "Engine cc"), F("seats", "int"), F("fuel_avg_kmpl", "number", "km/l"), F("price", "int"),
                       F("features", "textarea", list=False), F("is_upcoming", "bool", "Upcoming"), F("launch_date", "date"),
                       F("active", "bool")]},
    "parts": {"model": Part, "label": "Spare parts", "roles": ("owner", "parts"), "key": "part_no",
              "search": ["part_no", "name", "category", "compatible_models"], "fields": [
                  F("part_no", label="Part no"), F("name"), F("category"), F("compatible_models", "textarea", "Fits"),
                  F("price", "int"), F("stock", "int"), F("min_stock", "int", "Min stock"), F("is_genuine", "bool", "Genuine")]},
    "service_appointments": {"model": ServiceAppointment, "label": "Service jobs", "roles": ("owner", "service"), "key": "job_no",
                             "search": ["job_no", "reg_no", "car_model", "service_type"], "fields": [
                                 F("job_no", label="Job no"), F("customer_id", "ref", "Customer", ref="customers"), F("car_model", label="Car"),
                                 F("reg_no", label="Reg no"), F("service_type"), F("scheduled_at", "datetime", "When"),
                                 F("status", "select", options=["booked", "in_progress", "ready", "delivered", "cancelled"]),
                                 F("bill_amount", "int", "Bill"), F("complaint", "textarea", list=False), F("source", readonly=True)]},
    "test_drives": {"model": TestDrive, "label": "Test drives", "roles": ("owner", "sales"), "key": None, "search": ["address"],
                    "fields": [F("customer_id", "ref", "Customer", ref="customers"), F("car_model_id", "ref", "Car", ref="car_models"),
                               F("scheduled_at", "datetime", "When"), F("location", "select", options=["showroom", "home"]),
                               F("address", "textarea"), F("status", "select", options=["booked", "done", "cancelled", "no_show"])]},
    "leads": {"model": Lead, "label": "Leads", "roles": ("owner", "sales"), "key": None, "search": ["interest", "notes", "timeline"],
              "fields": [F("customer_id", "ref", "Customer", ref="customers"), F("interest", "textarea"),
                         F("car_model_id", "ref", "Car", ref="car_models", list=False), F("budget", "int"), F("timeline"),
                         F("wants_financing", "bool", "Financing"), F("has_trade_in", "bool", "Trade-in", list=False),
                         F("source", readonly=True), F("status", "select", options=["new", "contacted", "test_drive", "negotiation", "won", "lost"]),
                         F("score", "int", readonly=True), F("score_label", readonly=True, label="Temp"), F("notes", "textarea", list=False)]},
    "customers": {"model": Customer, "label": "Customers", "roles": ALL, "key": "phone", "search": ["name", "phone", "email", "city"],
                  "fields": [F("name"), F("phone"), F("email"), F("city"), F("memory", "textarea", "AI memory", list=False),
                             F("notes", "textarea", list=False)]},
    "customer_vehicles": {"model": CustomerVehicle, "label": "Customer cars", "roles": ("owner", "service"), "key": None,
                          "search": ["reg_no", "model_name"], "fields": [
                              F("customer_id", "ref", "Customer", ref="customers"), F("reg_no", label="Reg no"), F("model_name", label="Car"),
                              F("year", "int"), F("last_service_date", "date"), F("last_service_km", "int", "Last service km"),
                              F("next_service_due", "date", "Next service due")]},
    "offers": {"model": Offer, "label": "Offers", "roles": ("owner", "sales"), "key": None, "search": ["title", "description"],
               "fields": [F("title"), F("description", "textarea"), F("valid_from", "date"), F("valid_to", "date"), F("active", "bool")]},
    "service_types": {"model": ServiceType, "label": "Service price list", "roles": ("owner", "service"), "key": None, "search": ["name"],
                      "fields": [F("name"), F("description", "textarea"), F("price_min", "int"), F("price_max", "int"),
                                 F("duration_hours", "number", "Hours")]},
    "staff": {"model": Staff, "label": "Staff", "roles": ("owner",), "key": "username", "search": ["name", "username", "phone"],
              "fields": [F("name"), F("username"), F("phone", label="WhatsApp phone"), F("role", "select", options=list(ALL)),
                         F("active", "bool"), F("password", "password", "New password", list=False)]},
    "notifications": {"model": Notification, "label": "Outbox", "roles": ALL, "key": None, "search": ["to", "text", "kind"],
                      "readonly": True, "fields": [F("created_at", "datetime", "Time"), F("kind"), F("to"), F("text", "textarea"),
                                                   F("status"), F("error", list=False)]},
    "audit_log": {"model": AuditLog, "label": "Audit log", "roles": ("owner",), "key": None, "search": ["actor", "entity"],
                  "readonly": True, "fields": [F("created_at", "datetime", "Time"), F("actor"), F("action"), F("entity"),
                                               F("entity_id", "int", "ID"), F("old", "json"), F("new", "json"), F("undone", "bool")]},
}


# ---------------------------------------------------------------- auth

def current_staff(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> Staff:
    token = authorization.removeprefix("Bearer ").strip()
    staff_id = read_token(token) if token else None
    staff = db.get(Staff, staff_id) if staff_id else None
    if not staff or not staff.active:
        raise HTTPException(401, "Please log in")
    return staff


def _table(name: str, staff: Staff, write: bool = False) -> dict:
    spec = TABLES.get(name)
    if not spec:
        raise HTTPException(404, "unknown table")
    if staff.role != "owner" and staff.role not in spec["roles"]:
        raise HTTPException(403, "Your role cannot access this")
    if write and spec.get("readonly"):
        raise HTTPException(400, "read-only table")
    return spec


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    staff = db.query(Staff).filter(Staff.username == body.username.strip(), Staff.active.is_(True)).first()
    if not staff or not verify_password(body.password, staff.password_hash):
        raise HTTPException(401, "Wrong username or password")
    return {"token": make_token(staff.id), "staff": {"name": staff.name, "role": staff.role}}


@router.get("/me")
def me(staff: Staff = Depends(current_staff)):
    tables = [{"name": n, "label": s["label"]} for n, s in TABLES.items() if staff.role == "owner" or staff.role in s["roles"]]
    return {"name": staff.name, "role": staff.role, "tables": tables, "engine": get_engine().name,
            "whatsapp": settings.whatsapp_enabled, "database": "sqlite (local)" if settings.is_sqlite else "postgresql (cloud)"}


# ---------------------------------------------------------------- generic CRUD

def _coerce(field: dict, value):
    if value in ("", None):
        return False if field["type"] == "bool" else None
    t = field["type"]
    if t in ("int", "ref"):
        return int(float(str(value).replace(",", "")))
    if t == "number":
        return float(value)
    if t == "bool":
        return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "y")
    if t == "date":
        if isinstance(value, datetime):
            return value.date()
        return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    if t == "datetime":
        return value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", ""))
    return str(value).strip()


def _labels(db: Session, rows: list) -> dict:
    cm_ids = {getattr(r, "car_model_id", None) for r in rows} - {None}
    cu_ids = {getattr(r, "customer_id", None) for r in rows} - {None}
    out = {"car_models": {}, "customers": {}}
    if cm_ids:
        out["car_models"] = {c.id: c.full_name for c in db.query(CarModel).filter(CarModel.id.in_(cm_ids))}
    if cu_ids:
        out["customers"] = {c.id: f"{c.name or 'Unknown'} ({c.phone or 'no phone'})" for c in db.query(Customer).filter(Customer.id.in_(cu_ids))}
    return out


def _row(obj, spec) -> dict:
    d = obj.to_dict()
    d.pop("password_hash", None)
    return d


@router.get("/meta/{name}")
def meta(name: str, staff: Staff = Depends(current_staff)):
    spec = _table(name, staff)
    return {"name": name, "label": spec["label"], "fields": spec["fields"], "readonly": spec.get("readonly", False)}


@router.get("/options/{name}")
def options(name: str, q: str = "", staff: Staff = Depends(current_staff), db: Session = Depends(get_db)):
    if name == "car_models":
        return [{"id": c.id, "label": c.full_name + (" (upcoming)" if c.is_upcoming else "")} for c in db.query(CarModel).order_by(CarModel.make, CarModel.model)]
    if name == "customers":
        query = db.query(Customer)
        if q:
            query = query.filter(or_(Customer.name.ilike(f"%{q}%"), Customer.phone.ilike(f"%{q}%")))
        return [{"id": c.id, "label": f"{c.name or 'Unknown'} ({c.phone or 'no phone'})"} for c in query.order_by(Customer.id.desc()).limit(200)]
    raise HTTPException(404)


@router.get("/t/{name}")
def list_rows(name: str, q: str = "", filter_field: str = "", filter_value: str = "", limit: int = 500,
              staff: Staff = Depends(current_staff), db: Session = Depends(get_db)):
    spec = _table(name, staff)
    model = spec["model"]
    query = db.query(model)
    if q:
        conds = [getattr(model, f).ilike(f"%{q}%") for f in spec["search"]]
        if name == "vehicles":
            ids = [c.id for c in match_car_models(db, q, limit=50)]
            if ids:
                conds.append(Vehicle.car_model_id.in_(ids))
        query = query.filter(or_(*conds))
    if filter_field and filter_value and hasattr(model, filter_field):
        query = query.filter(getattr(model, filter_field) == filter_value)
    order = {"leads": Lead.score.desc(), "service_appointments": ServiceAppointment.scheduled_at.desc(),
             "test_drives": TestDrive.scheduled_at.desc()}.get(name, model.id.desc())
    rows = query.order_by(order).limit(min(limit, 2000)).all()
    return {"rows": [_row(r, spec) for r in rows], "labels": _labels(db, rows)}


def _apply(db: Session, staff: Staff, name: str, spec: dict, obj, data: dict) -> dict:
    old = {}
    for field in spec["fields"]:
        key = field["name"]
        if key not in data or field["readonly"]:
            continue
        if field["type"] == "password":
            if data[key]:
                obj.password_hash = hash_password(data[key])
            continue
        value = _coerce(field, data[key])
        if key == "phone":
            value = normalize_phone(value)
        if getattr(obj, key) != value:
            before = getattr(obj, key)
            old[key] = before.isoformat() if isinstance(before, (date, datetime)) else before
            setattr(obj, key, value)
    return old


def _after_change(db: Session, name: str, obj, old: dict):
    if name == "service_appointments" and old.get("status") and obj.status == "ready" and obj.customer:
        bill = f" Bill: {money(obj.bill_amount)}." if obj.bill_amount else ""
        notify_customer(db, obj.customer, "job_ready", f"Your {obj.car_model} ({obj.reg_no}) is ready for pickup at "
                        f"{settings.showroom_name}.{bill} Job no: {obj.job_no}.")
    if name == "service_appointments" and obj.status == "delivered" and obj.customer_id:
        cv = db.query(CustomerVehicle).filter_by(customer_id=obj.customer_id, reg_no=obj.reg_no).first()
        if cv:
            cv.last_service_date = obj.scheduled_at.date()
            cv.next_service_due = obj.scheduled_at.date() + timedelta(days=180)
    if name == "leads":
        score_lead(db, obj)


@router.post("/t/{name}")
def create_row(name: str, data: dict, staff: Staff = Depends(current_staff), db: Session = Depends(get_db)):
    spec = _table(name, staff, write=True)
    obj = spec["model"]()
    if name == "service_appointments" and not data.get("job_no"):
        data["job_no"] = f"JOB-{1100 + (db.query(func.max(ServiceAppointment.id)).scalar() or 0)}"
    try:
        _apply(db, staff, name, spec, obj, data)
        if name == "service_appointments":
            obj.source = "admin"
        db.add(obj)
        db.flush()
        _after_change(db, name, obj, {})
        db.add(AuditLog(actor=f"admin:{staff.username}", action="create", entity=name, entity_id=obj.id, new=_row(obj, spec)))
        db.commit()
    except (ValueError, TypeError) as exc:
        db.rollback()
        raise HTTPException(400, f"Invalid value: {exc}")
    except Exception as exc:
        db.rollback()
        raise HTTPException(400, f"Could not save: {str(exc).splitlines()[0][:200]}")
    return _row(obj, spec)


@router.put("/t/{name}/{row_id}")
def update_row(name: str, row_id: int, data: dict, staff: Staff = Depends(current_staff), db: Session = Depends(get_db)):
    spec = _table(name, staff, write=True)
    obj = db.get(spec["model"], row_id)
    if not obj:
        raise HTTPException(404)
    try:
        old = _apply(db, staff, name, spec, obj, data)
        db.flush()
        _after_change(db, name, obj, old)
        if old:
            db.add(AuditLog(actor=f"admin:{staff.username}", action="update", entity=name, entity_id=obj.id, old=old,
                            new={k: _row(obj, spec).get(k) for k in old}))
        db.commit()
    except (ValueError, TypeError) as exc:
        db.rollback()
        raise HTTPException(400, f"Invalid value: {exc}")
    except Exception as exc:
        db.rollback()
        raise HTTPException(400, f"Could not save: {str(exc).splitlines()[0][:200]}")
    return _row(obj, spec)


@router.delete("/t/{name}/{row_id}")
def delete_row(name: str, row_id: int, staff: Staff = Depends(current_staff), db: Session = Depends(get_db)):
    spec = _table(name, staff, write=True)
    obj = db.get(spec["model"], row_id)
    if not obj:
        raise HTTPException(404)
    if name == "staff" and obj.id == staff.id:
        raise HTTPException(400, "You cannot delete yourself")
    snapshot = _row(obj, spec)
    try:
        db.delete(obj)
        db.add(AuditLog(actor=f"admin:{staff.username}", action="delete", entity=name, entity_id=row_id, old=snapshot))
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(400, "Cannot delete - other records use this row. Mark it inactive / change its status instead.")
    return {"deleted": row_id}


# ---------------------------------------------------------------- dashboard

@router.get("/dashboard")
def dashboard(staff: Staff = Depends(current_staff), db: Session = Depends(get_db)):
    t = now()
    day = t.replace(hour=0, minute=0, second=0)
    week = day - timedelta(days=6)
    convs = db.query(Conversation).filter(Conversation.created_at >= week).all()
    handled_by_bot = sum(1 for c in convs if not c.handoff_reason)
    by_channel = {}
    for c in convs:
        by_channel[c.channel] = by_channel.get(c.channel, 0) + 1
    asked = (db.query(CarModel.make, CarModel.model, func.count(Lead.id)).join(Lead, Lead.car_model_id == CarModel.id)
             .group_by(CarModel.make, CarModel.model).order_by(func.count(Lead.id).desc()).limit(5).all())
    return {
        "today": {
            "chats": db.query(Conversation).filter(Conversation.created_at >= day).count(),
            "waiting_for_human": db.query(Conversation).filter(Conversation.status == "human").count(),
            "test_drives": db.query(TestDrive).filter(TestDrive.scheduled_at >= day, TestDrive.scheduled_at < day + timedelta(days=1),
                                                      TestDrive.status == "booked").count(),
            "service_jobs": db.query(ServiceAppointment).filter(ServiceAppointment.scheduled_at >= day,
                                                                ServiceAppointment.scheduled_at < day + timedelta(days=1)).count(),
            "cars_ready": db.query(ServiceAppointment).filter(ServiceAppointment.status == "ready").count(),
        },
        "stock": {
            "new_available": db.query(Vehicle).filter(Vehicle.status == "available", Vehicle.condition == "new").count(),
            "used_available": db.query(Vehicle).filter(Vehicle.status == "available", Vehicle.condition == "used").count(),
            "booked": db.query(Vehicle).filter(Vehicle.status == "booked").count(),
            "parts_low": db.query(Part).filter(Part.stock <= Part.min_stock).count(),
        },
        "leads": {label: db.query(Lead).filter(Lead.score_label == label, Lead.status.notin_(["won", "lost"])).count()
                  for label in ("hot", "warm", "cold")},
        "week": {"chats": len(convs), "bot_resolved_percent": round(100 * handled_by_bot / len(convs)) if convs else None,
                 "by_channel": by_channel},
        "most_wanted_models": [{"model": f"{m[0]} {m[1]}", "leads": m[2]} for m in asked],
        "hot_leads": [{"id": l.id, "customer": l.customer.name if l.customer else None, "phone": l.customer.phone if l.customer else None,
                       "interest": l.interest, "score": l.score} for l in
                      db.query(Lead).filter(Lead.score_label == "hot", Lead.status.notin_(["won", "lost"])).order_by(Lead.score.desc()).limit(8)],
    }


# ---------------------------------------------------------------- chats (human takeover)

@router.get("/chats")
def chats(status: str = "", staff: Staff = Depends(current_staff), db: Session = Depends(get_db)):
    q = db.query(Conversation)
    if status:
        q = q.filter(Conversation.status == status)
    rows = q.order_by((Conversation.status == "human").desc(), Conversation.last_message_at.desc()).limit(100).all()
    return [{"id": c.id, "channel": c.channel, "status": c.status, "department": c.department, "unread": c.unread,
             "customer": c.customer.name or c.customer.phone or f"Visitor #{c.customer_id}", "phone": c.customer.phone,
             "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
             "last_text": (c.messages[-1].text[:90] if c.messages else ""), "handoff_reason": c.handoff_reason} for c in rows]


@router.get("/chats/{conv_id}")
def chat_detail(conv_id: int, staff: Staff = Depends(current_staff), db: Session = Depends(get_db)):
    c = db.get(Conversation, conv_id)
    if not c:
        raise HTTPException(404)
    c.unread = 0
    db.commit()
    return {"id": c.id, "channel": c.channel, "status": c.status, "department": c.department, "summary": c.summary,
            "handoff_reason": c.handoff_reason,
            "customer": {"id": c.customer.id, "name": c.customer.name, "phone": c.customer.phone, "memory": c.customer.memory},
            "messages": [{"id": m.id, "role": m.role, "text": m.text, "time": m.created_at.isoformat(), "meta": m.meta} for m in c.messages]}


class ReplyIn(BaseModel):
    text: str


@router.post("/chats/{conv_id}/reply")
def chat_reply(conv_id: int, body: ReplyIn, staff: Staff = Depends(current_staff), db: Session = Depends(get_db)):
    c = db.get(Conversation, conv_id)
    if not c:
        raise HTTPException(404)
    c.status = "human"  # replying takes the chat over from the bot
    try:
        staff_reply(db, c, staff, body.text.strip())
    except Exception as exc:
        db.rollback()
        raise HTTPException(502, f"Could not deliver: {exc}")
    db.commit()
    return {"ok": True}


@router.post("/chats/{conv_id}/{action}")
def chat_action(conv_id: int, action: str, staff: Staff = Depends(current_staff), db: Session = Depends(get_db)):
    c = db.get(Conversation, conv_id)
    if not c:
        raise HTTPException(404)
    if action == "takeover":
        c.status = "human"
    elif action == "release":
        c.status, c.handoff_reason = "bot", None
    elif action == "close":
        update_memory(db, c)
        c.status = "closed"
    elif action == "summarize":
        update_memory(db, c)
    else:
        raise HTTPException(400, "unknown action")
    db.commit()
    return {"status": c.status, "summary": c.summary}


# ---------------------------------------------------------------- Excel import / export

def _excel_columns(name: str, spec: dict) -> list[str]:
    cols = [f["name"] for f in spec["fields"] if f["type"] != "password"]
    if "car_model_id" in cols:
        cols[cols.index("car_model_id")] = "car"
    if "customer_id" in cols:
        cols[cols.index("customer_id")] = "customer_phone"
    return ["id", *cols]


@router.get("/export/{name}")
def export_table(name: str, template: bool = False, staff: Staff = Depends(current_staff), db: Session = Depends(get_db)):
    spec = _table(name, staff)
    cols = _excel_columns(name, spec)
    wb = Workbook()
    ws = wb.active
    ws.title = name[:30]
    ws.append(cols)
    if not template:
        for obj in db.query(spec["model"]).order_by(spec["model"].id).all():
            d = _row(obj, spec)
            if "car" in cols:
                d["car"] = obj.car_model.full_name if getattr(obj, "car_model", None) else ""
            if "customer_phone" in cols:
                d["customer_phone"] = obj.customer.phone if getattr(obj, "customer", None) else ""
            ws.append([str(d.get(c)) if isinstance(d.get(c), (dict, list)) else d.get(c) for c in cols])
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 18
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"{name}{'-template' if template else ''}-{now():%Y%m%d}.xlsx"
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/import/{name}")
async def import_table(name: str, file: UploadFile = File(...), staff: Staff = Depends(current_staff), db: Session = Depends(get_db)):
    spec = _table(name, staff, write=True)
    wb = load_workbook(io.BytesIO(await file.read()), data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise HTTPException(400, "Empty sheet")
    header = [str(h).strip().lower() if h else "" for h in rows[0]]
    fields = {f["name"]: f for f in spec["fields"]}
    created = updated = 0
    errors = []
    for line_no, values in enumerate(rows[1:], start=2):
        if not any(v not in (None, "") for v in values):
            continue
        data = dict(zip(header, values))
        try:
            obj = None
            if data.get("id"):
                obj = db.get(spec["model"], int(data["id"]))
            key = spec.get("key")
            if not obj and key and data.get(key):
                key_value = normalize_phone(str(data[key])) if key == "phone" else str(data[key]).strip()
                obj = db.query(spec["model"]).filter(getattr(spec["model"], key) == key_value).first()
            if "car" in data and data["car"]:
                matches = [c for c in db.query(CarModel) if c.full_name.lower() == str(data["car"]).strip().lower()] \
                          or match_car_models(db, str(data["car"]))
                if not matches:
                    raise ValueError(f"car '{data['car']}' not in catalog")
                data["car_model_id"] = matches[0].id
            if "customer_phone" in data and data["customer_phone"]:
                phone = normalize_phone(str(data["customer_phone"]))
                cust = db.query(Customer).filter(Customer.phone == phone).first()
                if not cust:
                    cust = Customer(phone=phone)
                    db.add(cust)
                    db.flush()
                data["customer_id"] = cust.id
            is_new = obj is None
            if is_new:
                obj = spec["model"]()
                db.add(obj)
            _apply(db, staff, name, spec, obj, {k: v for k, v in data.items() if k in fields})
            db.flush()
            created += is_new
            updated += not is_new
        except Exception as exc:
            db.rollback()
            errors.append(f"Row {line_no}: {str(exc).splitlines()[0][:150]}")
            continue
        db.commit()
    db.add(AuditLog(actor=f"admin:{staff.username}", action="import", entity=name, new={"created": created, "updated": updated, "errors": len(errors)}))
    db.commit()
    return {"created": created, "updated": updated, "errors": errors[:50]}


# ---------------------------------------------------------------- knowledge base

@router.get("/knowledge")
def knowledge_files(staff: Staff = Depends(current_staff)):
    return kb.files()


@router.post("/knowledge")
async def knowledge_upload(file: UploadFile = File(...), staff: Staff = Depends(current_staff)):
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", file.filename or "document.txt")
    if not any(name.lower().endswith(ext) for ext in SUPPORTED):
        raise HTTPException(400, "Upload .pdf, .md or .txt files")
    (KNOWLEDGE_DIR / name).write_bytes(await file.read())
    kb.rebuild()
    return kb.files()


@router.delete("/knowledge/{filename}")
def knowledge_delete(filename: str, staff: Staff = Depends(current_staff)):
    path = (KNOWLEDGE_DIR / filename).resolve()
    if path.parent != KNOWLEDGE_DIR.resolve() or not path.exists():
        raise HTTPException(404)
    path.unlink()
    kb.rebuild()
    return kb.files()


@router.get("/knowledge/search")
def knowledge_search(q: str, staff: Staff = Depends(current_staff)):
    return kb.search(q)


# ---------------------------------------------------------------- simulator & jobs

class SimIn(BaseModel):
    phone: str
    text: str
    name: str | None = None


@router.post("/simulate/whatsapp")
def simulate_whatsapp(body: SimIn, staff: Staff = Depends(current_staff)):
    """Send a message 'as if' it came from a WhatsApp number - works without Meta setup.
    Use a staff phone (e.g. 923001112233) to test staff commands."""
    return {"replies": process_incoming(body.phone, body.text, name=body.name)}


@router.post("/jobs/run")
def run_jobs(staff: Staff = Depends(current_staff)):
    return run_jobs_once()
