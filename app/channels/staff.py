"""Staff command bot: managers update the database by WhatsApp in plain language.

  "Civic Oriel white sold"         -> marks one matching unit as sold
  "3 Corolla Altis grey arrived"   -> adds 3 units to stock
  "City brake pads stock 15"       -> sets part stock
  "Yaris price 5,600,000"          -> changes catalog price
  "job 1043 ready bill 12500"      -> job status + automatic WhatsApp to the customer
  "stock" / "today"                -> summaries (read only)

Every change is confirmed first, written to the audit log, and can be undone.
"""
import json
import logging
import re
from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..db import now
from ..models import (AuditLog, CarModel, Conversation, Lead, Part, PendingAction, ServiceAppointment, Staff, TestDrive,
                      Vehicle)
from ..notify import notify_customer
from ..tools import match_car_models
from ..utils import money

log = logging.getLogger("staff")

PERMISSIONS = {
    "vehicle_status": ("owner", "sales"), "add_vehicles": ("owner", "sales"), "model_price": ("owner", "sales"),
    "part_stock": ("owner", "parts"), "part_price": ("owner", "parts"), "job_status": ("owner", "service"),
}
YES = {"yes", "y", "confirm", "ok", "okay", "haan", "han", "ji", "done", "✅ confirm", "theek hai"}
NO = {"no", "n", "cancel", "nahi", "❌ cancel", "stop"}
CONFIRM_BUTTONS = ["✅ Confirm", "❌ Cancel"]
HELP = ("*Staff commands*\n"
        "• Civic Oriel white sold\n• 2 Corolla Altis grey arrived\n• Civic Oriel black booked / available\n"
        "• City brake pads stock 15  (or +5 / -2)\n• Oil filter Corolla price 1950\n• Yaris ATIV price 5600000\n"
        "• job 1043 in progress / ready bill 12500 / delivered\n• stock  • today  • undo  • help")

STAFF_PARSER_PROMPT = """You convert car-showroom staff WhatsApp messages (English / Roman Urdu / Urdu) into one database action by calling the staff_action tool.
Action types:
- vehicle_status: a car unit was sold / booked / became available again. Fields: model, color, condition, status.
- add_vehicles: new stock arrived. Fields: model, color, count, condition, year, price.
- part_stock: spare part quantity. Fields: part, car_model, stock (absolute) OR delta (+/-).
- part_price: spare part price change. Fields: part, car_model, price.
- model_price: catalog price of a car variant changed. Fields: model, price.
- job_status: workshop job update. Fields: job_no (digits), status (in_progress|ready|delivered|cancelled), bill_amount.
- stock_summary: they ask for current stock. today_summary: they ask for today's activity.
Prices are in rupees: convert '56 lakh' to 5600000 and '1.2 crore' to 12000000. 'bik gai'/'bech di' = sold.
Always call the tool. If the message is unclear, call it with type 'unclear' and a short question in 'question'."""

PARSER_TOOL = {
    "name": "staff_action",
    "description": "The parsed staff action.",
    "input_schema": {"type": "object", "properties": {
        "type": {"type": "string", "enum": ["vehicle_status", "add_vehicles", "part_stock", "part_price", "model_price",
                                            "job_status", "stock_summary", "today_summary", "unclear"]},
        "model": {"type": "string"}, "color": {"type": "string"},
        "condition": {"type": "string", "enum": ["new", "used", ""]},
        "status": {"type": "string"}, "count": {"type": "integer"}, "year": {"type": "integer"},
        "price": {"type": "integer"}, "part": {"type": "string"}, "car_model": {"type": "string"},
        "stock": {"type": "integer"}, "delta": {"type": "integer"}, "job_no": {"type": "string"},
        "bill_amount": {"type": "integer"}, "question": {"type": "string"}},
        "required": ["type"]},
}


# ---------------------------------------------------------------- parsing

def _num(text: str) -> int | None:
    t = text.lower().replace(",", "")
    if m := re.search(r"(\d+(?:\.\d+)?)\s*(crore|cr)\b", t):
        return int(float(m.group(1)) * 10_000_000)
    if m := re.search(r"(\d+(?:\.\d+)?)\s*(lakh|lac|lacs)\b", t):
        return int(float(m.group(1)) * 100_000)
    if m := re.search(r"\b(\d{3,10})\b", t):
        return int(m.group(1))
    return None


COLORS = ["pearl white", "white", "black", "grey", "gray", "silver", "red", "blue", "brown", "beige", "green", "maroon"]


def parse_demo(text: str) -> dict:
    t = text.lower().strip()
    if t in ("stock", "stock summary", "aaj ka stock", "inventory"):
        return {"type": "stock_summary"}
    if t in ("today", "today summary", "aaj", "report"):
        return {"type": "today_summary"}
    if m := re.search(r"job[\s#-]*(\d+)", t):
        status = ("ready" if "ready" in t else "delivered" if "deliver" in t else "cancelled" if "cancel" in t
                  else "in_progress" if ("progress" in t or "started" in t or "start" in t) else "")
        bill = re.search(r"bill\s*(\d[\d,]*)", t)
        return {"type": "job_status", "job_no": m.group(1), "status": status,
                "bill_amount": int(bill.group(1).replace(",", "")) if bill else None}
    color = next((c for c in COLORS if c in t), "")
    condition = "used" if "used" in t else ""
    if re.search(r"\b(sold|bik gai|bik gayi|bech di|sale done)\b", t):
        return {"type": "vehicle_status", "model": t, "color": color, "condition": condition, "status": "sold"}
    if re.search(r"\b(booked|book ho gai|reserved)\b", t):
        return {"type": "vehicle_status", "model": t, "color": color, "condition": condition, "status": "booked"}
    if re.search(r"\b(available again|unbooked|back in stock)\b", t):
        return {"type": "vehicle_status", "model": t, "color": color, "condition": condition, "status": "available"}
    if re.search(r"\b(arrived|received|aa gai|aa gayi|new stock)\b", t):
        count = re.match(r"\s*(\d+)\b", t)
        return {"type": "add_vehicles", "model": t, "color": color, "condition": condition or "new",
                "count": int(count.group(1)) if count else 1}
    if m := re.search(r"stock\s*([+-]?\d+)", t):
        part = t[:m.start()].strip()
        val = m.group(1)
        return {"type": "part_stock", "part": part, "car_model": part,
                **({"delta": int(val)} if val[0] in "+-" else {"stock": int(val)})}
    if "price" in t or "rate" in t:
        before = re.split(r"\b(price|rate)\b", t)[0].strip()
        price = _num(t.split("price")[-1] if "price" in t else t.split("rate")[-1])
        if match_car_models_quick(before) and not re.search(r"filter|pad|oil|battery|plug|wiper|mirror|light|mat|part", before):
            return {"type": "model_price", "model": before, "price": price}
        return {"type": "part_price", "part": before, "car_model": before, "price": price}
    return {"type": "unclear", "question": "Sorry, I didn't get that. Send *help* to see the commands."}


def match_car_models_quick(text: str) -> bool:
    from ..db import SessionLocal

    with SessionLocal() as db:
        return bool(match_car_models(db, text))


def parse_claude(text: str) -> dict:
    from ..agents import get_engine

    engine = get_engine()
    resp = engine.client.messages.create(
        model=settings.router_model, max_tokens=2000, system=STAFF_PARSER_PROMPT, tools=[PARSER_TOOL],
        output_config={"effort": "low"}, messages=[{"role": "user", "content": text}],
    )
    for block in resp.content:
        if block.type == "tool_use":
            return dict(block.input)
    return {"type": "unclear", "question": "".join(b.text for b in resp.content if b.type == "text") or "Please rephrase."}


def parse(text: str) -> dict:
    from ..agents import get_engine

    if get_engine().name == "claude":
        try:
            return parse_claude(text)
        except Exception as exc:
            log.warning("Claude staff parser failed, using rules: %s", exc)
    return parse_demo(text)


# ---------------------------------------------------------------- resolving candidates

def _vehicle_label(v: Vehicle) -> str:
    extra = f", {v.year}, {v.mileage_km:,} km" if v.condition == "used" else ""
    return f"{v.car_model.full_name}, {v.color} ({v.condition}{extra}) - chassis …{(v.chassis_no or '')[-4:]}, arrived {v.arrived_at}, {v.status}"


def _part_matches(db: Session, part: str, car: str) -> list[Part]:
    words = set(re.findall(r"[a-z0-9]+", part.lower())) - {"stock", "price", "rate", "for", "ka", "ki", "of"}
    car_words = {w for cm in match_car_models(db, car, limit=10) for w in cm.model.lower().split()} if car else set()
    out = []
    for p in db.query(Part).all():
        name_words = set(re.findall(r"[a-z0-9]+", f"{p.name} {p.category} {p.part_no}".lower()))
        hits = len(words & name_words)
        if not hits:
            continue
        compat = set(re.findall(r"[a-z0-9]+", (p.compatible_models or "").lower()))
        if car_words and not car_words & compat:
            continue
        out.append((hits, p))
    if not out:
        return []
    best = max(h for h, _ in out)
    return [p for h, p in out if h == best]


def resolve(db: Session, action: dict) -> tuple[list[dict], str | None]:
    """Returns (candidates, error). Each candidate = {"id": .., "label": ..}."""
    kind = action["type"]
    if kind == "vehicle_status":
        models = match_car_models(db, action.get("model", ""), include_upcoming=False, limit=10)
        if not models:
            return [], "I couldn't find that car model."
        q = db.query(Vehicle).filter(Vehicle.car_model_id.in_([m.id for m in models]), Vehicle.status != action.get("status"))
        if action.get("color"):
            q = q.filter(func.lower(Vehicle.color).contains(action["color"].lower().replace("gray", "grey")))
        if action.get("condition"):
            q = q.filter(Vehicle.condition == action["condition"])
        if action.get("status") == "available":
            q = q.filter(Vehicle.status == "booked")
        units = q.order_by(Vehicle.arrived_at).all()
        if not units:
            return [], f"No {action.get('color', '')} {models[0].model} in stock with a status other than '{action.get('status')}'."
        return [{"id": v.id, "label": _vehicle_label(v)} for v in units[:9]], None
    if kind in ("add_vehicles", "model_price"):
        models = match_car_models(db, action.get("model", ""), limit=9)
        if not models:
            return [], "I couldn't find that model in the catalog. Add it once in the admin panel (Car catalog)."
        return [{"id": m.id, "label": f"{m.full_name} (current price {money(m.price)})"} for m in models], None
    if kind in ("part_stock", "part_price"):
        parts = _part_matches(db, action.get("part", ""), action.get("car_model", ""))
        if not parts:
            return [], "I couldn't find that part. Check the name or add it in the admin panel (Parts)."
        return [{"id": p.id, "label": f"{p.name} [{p.part_no}] - fits {p.compatible_models} - stock {p.stock}, {money(p.price)}"}
                for p in parts[:9]], None
    if kind == "job_status":
        job = db.query(ServiceAppointment).filter(func.upper(ServiceAppointment.job_no) == f"JOB-{action.get('job_no', '').strip()}").first()
        if not job:
            return [], f"Job JOB-{action.get('job_no')} not found."
        if action.get("status") not in ("in_progress", "ready", "delivered", "cancelled", "booked"):
            return [], "Which status? in progress / ready / delivered / cancelled."
        return [{"id": job.id, "label": f"{job.job_no} - {job.car_model} ({job.reg_no}), now '{job.status}'"}], None
    return [], "Sorry, I didn't understand."


def describe(action: dict, label: str) -> str:
    kind = action["type"]
    if kind == "vehicle_status":
        return f"Mark *{label}* as *{action['status'].upper()}*?"
    if kind == "add_vehicles":
        bits = [f"{action.get('count') or 1} x {label.split(' (current')[0]}", action.get("color") or "colour not set",
                action.get("condition") or "new"]
        if action.get("price"):
            bits.append(money(action["price"]))
        return f"Add to stock: *{', '.join(bits)}*?"
    if kind == "model_price":
        return f"Change price of *{label}* to *{money(action.get('price'))}*?"
    if kind == "part_stock":
        change = f"stock {action['delta']:+d}" if action.get("delta") is not None else f"stock = {action.get('stock')}"
        return f"Update *{label}*: *{change}*?"
    if kind == "part_price":
        return f"Change price of *{label}* to *{money(action.get('price'))}*?"
    if kind == "job_status":
        bill = f", bill {money(action['bill_amount'])}" if action.get("bill_amount") else ""
        notify = " (customer will get a WhatsApp alert)" if action.get("status") == "ready" else ""
        return f"Set *{label}* to *{action['status'].replace('_', ' ').upper()}*{bill}{notify}?"
    return label


# ---------------------------------------------------------------- execute / undo

def _audit(db, staff, action, entity, entity_id, old, new):
    db.add(AuditLog(actor=f"staff:{staff.name} (whatsapp)", action=action, entity=entity, entity_id=entity_id, old=old, new=new))


def execute(db: Session, staff: Staff, action: dict, target_id: int) -> str:
    kind = action["type"]
    if kind == "vehicle_status":
        v = db.get(Vehicle, target_id)
        old = {"status": v.status}
        v.status = action["status"]
        _audit(db, staff, "update", "vehicles", v.id, old, {"status": v.status})
        left = db.query(Vehicle).filter(Vehicle.car_model_id == v.car_model_id, Vehicle.color == v.color,
                                        Vehicle.condition == v.condition, Vehicle.status == "available").count()
        return f"✅ Done. {v.car_model.full_name} {v.color} (…{(v.chassis_no or '')[-4:]}) is now {v.status.upper()}. Remaining available in this colour: {left}."
    if kind == "add_vehicles":
        cm = db.get(CarModel, target_id)
        created = []
        for i in range(max(1, min(int(action.get("count") or 1), 50))):
            v = Vehicle(car_model_id=cm.id, condition=action.get("condition") or "new", year=action.get("year") or now().year,
                        color=(action.get("color") or "Not set").title(), price=action.get("price") or cm.price,
                        chassis_no=f"TEMP-{now():%y%m%d%H%M%S}-{i}", notes="Added via WhatsApp - update chassis no. in admin panel")
            db.add(v)
            db.flush()
            created.append(v.id)
        if cm.is_upcoming:
            cm.is_upcoming = False
        _audit(db, staff, "create", "vehicles", None, None, {"ids": created})
        return f"✅ Added {len(created)} x {cm.full_name} to stock. Update chassis numbers later in the admin panel."
    if kind == "model_price":
        cm = db.get(CarModel, target_id)
        old = {"price": cm.price}
        cm.price = int(action["price"])
        _audit(db, staff, "update", "car_models", cm.id, old, {"price": cm.price})
        db.query(Vehicle).filter(Vehicle.car_model_id == cm.id, Vehicle.condition == "new", Vehicle.status == "available").update({"price": cm.price})
        return f"✅ {cm.full_name} price is now {money(cm.price)} (new units in stock updated too)."
    if kind == "part_stock":
        p = db.get(Part, target_id)
        old = {"stock": p.stock}
        p.stock = max(0, p.stock + int(action["delta"])) if action.get("delta") is not None else max(0, int(action.get("stock") or 0))
        _audit(db, staff, "update", "parts", p.id, old, {"stock": p.stock})
        warn = " ⚠️ Below minimum stock!" if p.stock <= p.min_stock else ""
        return f"✅ {p.name} [{p.part_no}] stock is now {p.stock}.{warn}"
    if kind == "part_price":
        p = db.get(Part, target_id)
        old = {"price": p.price}
        p.price = int(action["price"])
        _audit(db, staff, "update", "parts", p.id, old, {"price": p.price})
        return f"✅ {p.name} [{p.part_no}] price is now {money(p.price)}."
    if kind == "job_status":
        job = db.get(ServiceAppointment, target_id)
        old = {"status": job.status, "bill_amount": job.bill_amount}
        job.status = action["status"]
        if action.get("bill_amount"):
            job.bill_amount = int(action["bill_amount"])
        _audit(db, staff, "update", "service_appointments", job.id, old, {"status": job.status, "bill_amount": job.bill_amount})
        extra = ""
        if job.status == "ready":
            bill = f" Bill: {money(job.bill_amount)}." if job.bill_amount else ""
            note = notify_customer(db, job.customer, "job_ready",
                                   f"Your {job.car_model} ({job.reg_no}) is ready for pickup at {settings.showroom_name}.{bill} "
                                   f"Job no: {job.job_no}. Timings: {settings.showroom_hours}.")
            extra = f" Customer alert: {note.status}."
        return f"✅ {job.job_no} is now {job.status.replace('_', ' ').upper()}.{extra}"
    return "Nothing to do."


def undo_last(db: Session, staff: Staff) -> str:
    cutoff = now() - timedelta(minutes=settings.staff_undo_minutes)
    entry = (db.query(AuditLog).filter(AuditLog.actor == f"staff:{staff.name} (whatsapp)", AuditLog.undone.is_(False),
                                       AuditLog.created_at >= cutoff).order_by(AuditLog.id.desc()).first())
    if not entry:
        return f"Nothing to undo (only changes from the last {settings.staff_undo_minutes} minutes can be undone)."
    tables = {"vehicles": Vehicle, "parts": Part, "car_models": CarModel, "service_appointments": ServiceAppointment}
    model = tables[entry.entity]
    if entry.action == "create":
        for vid in entry.new.get("ids", []):
            if obj := db.get(model, vid):
                db.delete(obj)
    else:
        obj = db.get(model, entry.entity_id)
        for key, value in (entry.old or {}).items():
            setattr(obj, key, value)
    entry.undone = True
    db.add(AuditLog(actor=f"staff:{staff.name} (whatsapp)", action="undo", entity=entry.entity, entity_id=entry.entity_id,
                    old=entry.new, new=entry.old))
    return "↩️ Undone. The last change was reverted."


# ---------------------------------------------------------------- summaries

def stock_summary(db: Session) -> str:
    rows = (db.query(CarModel, Vehicle.condition, func.count(Vehicle.id)).join(Vehicle)
            .filter(Vehicle.status == "available").group_by(CarModel.id, Vehicle.condition).all())
    lines = [f"• {cm.full_name} ({cond}): {n}" for cm, cond, n in sorted(rows, key=lambda r: r[0].full_name)]
    low = db.query(Part).filter(Part.stock <= Part.min_stock).all()
    out = "*Cars available*\n" + ("\n".join(lines) or "none")
    if low:
        out += "\n\n*Parts low / out of stock*\n" + "\n".join(f"• {p.name} [{p.part_no}]: {p.stock}" for p in low[:15])
    return out


def today_summary(db: Session) -> str:
    start = now().replace(hour=0, minute=0, second=0)
    end = start + timedelta(days=1)
    chats = db.query(Conversation).filter(Conversation.created_at >= start).count()
    leads = db.query(Lead).filter(Lead.created_at >= start).count()
    hot = db.query(Lead).filter(Lead.score_label == "hot", Lead.status.notin_(["won", "lost"])).count()
    tds = db.query(TestDrive).filter(TestDrive.scheduled_at >= start, TestDrive.scheduled_at < end, TestDrive.status == "booked").count()
    jobs = db.query(ServiceAppointment).filter(ServiceAppointment.scheduled_at >= start, ServiceAppointment.scheduled_at < end).all()
    sold = db.query(AuditLog).filter(AuditLog.created_at >= start, AuditLog.entity == "vehicles",
                                     AuditLog.new["status"].as_string() == "sold").count()
    waiting = db.query(Conversation).filter(Conversation.status == "human").count()
    by_status = {}
    for j in jobs:
        by_status[j.status] = by_status.get(j.status, 0) + 1
    return (f"*Today ({start:%d %b})*\n• New chats: {chats}\n• New leads: {leads} (hot leads open: {hot})\n"
            f"• Test drives today: {tds}\n• Workshop jobs: {len(jobs)} {json.dumps(by_status) if by_status else ''}\n"
            f"• Cars marked sold: {sold}\n• Chats waiting for a human: {waiting}")


# ---------------------------------------------------------------- entry point

def handle_staff_message(db: Session, staff: Staff, text: str) -> list[dict]:
    t = (text or "").strip()
    low = t.lower()
    pending = (db.query(PendingAction).filter(PendingAction.staff_id == staff.id, PendingAction.status == "waiting")
               .order_by(PendingAction.id.desc()).first())
    if pending and pending.created_at < now() - timedelta(minutes=30):
        pending.status, pending = "expired", None

    if low in ("help", "commands", "menu", "?"):
        return [{"text": HELP}]
    if low in ("undo", "wapis", "revert"):
        return [{"text": undo_last(db, staff)}]
    if pending:
        action = dict(pending.action)
        if low in NO:
            pending.status = "cancelled"
            return [{"text": "❌ Cancelled. Nothing was changed."}]
        if action.get("selected") is None and low.isdigit():
            idx = int(low) - 1
            if not 0 <= idx < len(action["candidates"]):
                return [{"text": f"Please reply with a number from 1 to {len(action['candidates'])}."}]
            action["selected"] = idx
            pending.action = action
            return [{"text": "Confirm: " + describe(action["parsed"], action["candidates"][idx]["label"]), "buttons": CONFIRM_BUTTONS}]
        if action.get("selected") is not None and low in YES:
            target = action["candidates"][action["selected"]]["id"]
            result = execute(db, staff, action["parsed"], target)
            pending.status = "done"
            return [{"text": f"{result}\nReply *UNDO* within {settings.staff_undo_minutes} min to revert."}]
        pending.status = "cancelled"  # a new command replaces the old pending one

    parsed = parse(t)
    kind = parsed.get("type")
    if kind == "stock_summary":
        return [{"text": stock_summary(db)}]
    if kind == "today_summary":
        return [{"text": today_summary(db)}]
    if kind not in PERMISSIONS:
        return [{"text": parsed.get("question") or "Sorry, I didn't get that. Send *help* to see the commands."}]
    if staff.role not in PERMISSIONS[kind]:
        return [{"text": f"Sorry, your role ({staff.role}) can't do that. Ask the owner or the right department."}]
    if kind in ("model_price", "part_price") and not parsed.get("price"):
        return [{"text": "What is the new price? e.g. 'price 5600000' or 'price 56 lakh'."}]
    candidates, error = resolve(db, parsed)
    if error:
        return [{"text": error}]
    pa = PendingAction(staff_id=staff.id, action={"parsed": parsed, "candidates": candidates,
                                                   "selected": 0 if len(candidates) == 1 else None})
    db.add(pa)
    if len(candidates) == 1:
        return [{"text": "Confirm: " + describe(parsed, candidates[0]["label"]), "buttons": CONFIRM_BUTTONS}]
    listing = "\n".join(f"{i + 1}️⃣ {c['label']}" for i, c in enumerate(candidates))
    return [{"text": f"I found {len(candidates)} matches:\n{listing}\n\nWhich one? Reply with the number."}]
