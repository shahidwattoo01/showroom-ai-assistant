"""Business tools the AI agents call. Every answer about stock, prices, slots and
bookings comes from the live database through these functions - never from the
model's memory - so daily inventory changes show up instantly.
"""
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from .config import settings
from .db import now
from .knowledge import kb
from .leads import rescore_customer, score_lead
from .models import (CarModel, Conversation, Customer, CustomerVehicle, Lead, Offer, Part, ServiceAppointment,
                     ServiceType, TestDrive, Vehicle)
from .notify import notify_staff
from .utils import money, normalize_phone


@dataclass
class ToolContext:
    db: Session
    conversation: Conversation
    customer: Customer

    @property
    def channel(self) -> str:
        return self.conversation.channel


class ToolError(Exception):
    pass


# ---------------------------------------------------------------- helpers

def _words(text: str | None) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def match_car_models(db: Session, text: str, include_upcoming: bool = True, limit: int = 5) -> list[CarModel]:
    """Fuzzy match free text like 'civic oriel' or 'corolla 1.8' to catalog variants."""
    want = _words(text)
    if not want:
        return []
    scored = []
    q = db.query(CarModel).filter(CarModel.active.is_(True))
    if not include_upcoming:
        q = q.filter(CarModel.is_upcoming.is_(False))
    for cm in q:
        make, model, variant = _words(cm.make), _words(cm.model), _words(cm.variant)
        score = 3 * len(want & model) + len(want & make) + 2 * len(want & variant)
        if want & model or (want & make and not (want - make)):
            scored.append((score, cm))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        return []
    best = scored[0][0]
    return [cm for s, cm in scored if s == best][:limit] if best >= 5 else [cm for _, cm in scored[:limit]]


def _parse_dt(value: str) -> datetime:
    value = (value or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %I:%M %p", "%Y-%m-%d %I %p"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ToolError(f"Could not understand date/time '{value}'. Use format YYYY-MM-DD HH:MM (24h).")


def _require_contact(ctx: ToolContext):
    if not ctx.customer.phone or not ctx.customer.name:
        missing = [f for f, v in (("name", ctx.customer.name), ("phone number", ctx.customer.phone)) if not v]
        raise ToolError(f"Customer {' and '.join(missing)} needed before booking. Ask the customer, then call save_customer_details.")


def _slot_ok(dt: datetime) -> str | None:
    if dt.weekday() in settings.closed_weekdays:
        return "The showroom is closed on that day."
    if not (settings.work_start_hour <= dt.hour < settings.work_end_hour):
        return f"Bookings are possible between {settings.work_start_hour}:00 and {settings.work_end_hour}:00."
    if dt < now():
        return "That time is in the past."
    return None


def _booked_count(db: Session, kind: str, start: datetime) -> int:
    end = start + timedelta(hours=1)
    if kind == "service":
        return db.query(ServiceAppointment).filter(ServiceAppointment.scheduled_at >= start, ServiceAppointment.scheduled_at < end,
                                                   ServiceAppointment.status != "cancelled").count()
    return db.query(TestDrive).filter(TestDrive.scheduled_at >= start, TestDrive.scheduled_at < end,
                                      TestDrive.status != "cancelled").count()


def _capacity(kind: str) -> int:
    return settings.service_slot_capacity if kind == "service" else settings.test_drive_slot_capacity


def _model_info(db: Session, cm: CarModel) -> dict:
    units = db.query(Vehicle).filter(Vehicle.car_model_id == cm.id, Vehicle.status == "available", Vehicle.condition == "new").all()
    return {
        "name": cm.full_name, "body_type": cm.body_type, "fuel": cm.fuel, "transmission": cm.transmission,
        "engine_cc": cm.engine_cc, "seats": cm.seats, "fuel_average_kmpl": cm.fuel_avg_kmpl,
        "price": money(cm.price), "features": cm.features,
        "upcoming": cm.is_upcoming, "expected_launch": cm.launch_date.isoformat() if cm.launch_date else None,
        "new_units_in_stock": len(units), "colors_in_stock": sorted({u.color for u in units}),
    }


def _open_lead(ctx: ToolContext) -> Lead:
    lead = (ctx.db.query(Lead).filter(Lead.customer_id == ctx.customer.id, Lead.status.notin_(["won", "lost"]))
            .order_by(Lead.id.desc()).first())
    if not lead:
        lead = Lead(customer_id=ctx.customer.id, source=ctx.channel, status="new")
        ctx.db.add(lead)
    return lead


# ---------------------------------------------------------------- sales tools

def search_inventory(ctx: ToolContext, query: str = "", condition: str = "", body_type: str = "", fuel: str = "",
                     min_price: int = 0, max_price: int = 0, min_seats: int = 0, color: str = "") -> dict:
    db = ctx.db
    q = db.query(Vehicle).join(CarModel).filter(Vehicle.status == "available")
    if condition in ("new", "used"):
        q = q.filter(Vehicle.condition == condition)
    if body_type:
        q = q.filter(func.lower(CarModel.body_type) == body_type.lower())
    if fuel:
        q = q.filter(func.lower(CarModel.fuel) == fuel.lower())
    if min_price:
        q = q.filter(Vehicle.price >= min_price)
    if max_price:
        q = q.filter(Vehicle.price <= max_price)
    if min_seats:
        q = q.filter(CarModel.seats >= min_seats)
    if color:
        q = q.filter(func.lower(Vehicle.color).contains(color.lower()))
    units = q.all()
    if query:
        ids = {cm.id for cm in match_car_models(db, query, include_upcoming=False, limit=20)}
        units = [u for u in units if u.car_model_id in ids]
    groups: dict[tuple, dict] = {}
    for u in units:
        key = (u.car_model_id, u.condition, u.color, u.year if u.condition == "used" else None)
        g = groups.setdefault(key, {"car": u.car_model.full_name, "condition": u.condition, "color": u.color,
                                    "year": u.year, "count": 0, "prices": [], "mileage_km": u.mileage_km if u.condition == "used" else None,
                                    "body_type": u.car_model.body_type, "fuel": u.car_model.fuel})
        g["count"] += 1
        g["prices"].append(u.price)
    results = []
    for g in sorted(groups.values(), key=lambda g: min(g["prices"])):
        prices = g.pop("prices")
        g["price"] = money(min(prices)) if min(prices) == max(prices) else f"{money(min(prices))} - {money(max(prices))}"
        results.append(g)
    out = {"results": results[:15], "total_matching_units": len(units)}
    if not results and query:
        catalog = match_car_models(db, query)
        if catalog:
            out["note"] = "Not in stock right now, but these are in our catalog (can be ordered / booked):"
            out["catalog"] = [_model_info(db, cm) for cm in catalog]
    return out


def get_model_details(ctx: ToolContext, model_name: str) -> dict:
    matches = match_car_models(ctx.db, model_name)
    if not matches:
        raise ToolError(f"No model matching '{model_name}' in our catalog.")
    return {"models": [_model_info(ctx.db, cm) for cm in matches]}


def compare_models(ctx: ToolContext, model_names: list[str]) -> dict:
    rows = []
    for name in model_names[:4]:
        matches = match_car_models(ctx.db, name)
        rows.append(_model_info(ctx.db, matches[0]) if matches else {"name": name, "error": "not in catalog"})
    return {"comparison": rows}


def list_upcoming_launches(ctx: ToolContext) -> dict:
    ups = ctx.db.query(CarModel).filter(CarModel.is_upcoming.is_(True), CarModel.active.is_(True)).order_by(CarModel.launch_date).all()
    return {"upcoming": [_model_info(ctx.db, cm) for cm in ups],
            "note": "Prices of upcoming cars are expected prices and may change at launch."}


def calculate_financing(ctx: ToolContext, car_price: int = 0, model_name: str = "", down_payment_percent: float = 0,
                        years: int = 0, annual_rate_percent: float = 0) -> dict:
    if not car_price and model_name:
        matches = match_car_models(ctx.db, model_name)
        if not matches:
            raise ToolError(f"No model matching '{model_name}'.")
        car_price, model_name = matches[0].price, matches[0].full_name
    if not car_price:
        raise ToolError("Need a car price or model name.")
    down_pct = max(down_payment_percent or settings.finance_min_down_percent, settings.finance_min_down_percent)
    years = min(max(years or settings.finance_max_years, 1), settings.finance_max_years)
    rate = annual_rate_percent or settings.finance_annual_rate
    principal = car_price * (1 - down_pct / 100)
    r, n = rate / 100 / 12, years * 12
    monthly = principal * r / (1 - (1 + r) ** -n) if r else principal / n
    return {
        "car": model_name or None, "car_price": money(car_price), "down_payment_percent": down_pct,
        "down_payment": money(car_price * down_pct / 100), "financed_amount": money(principal),
        "tenure_years": years, "annual_markup_rate_percent": rate, "monthly_installment": money(monthly),
        "total_paid_over_tenure": money(monthly * n + car_price * down_pct / 100),
        "note": f"Estimate only. Minimum down payment {settings.finance_min_down_percent:g}%, max {settings.finance_max_years} years. "
                "Final rate depends on the bank, insurance and registration charges.",
    }


def get_available_slots(ctx: ToolContext, kind: str, date: str) -> dict:
    kind = "service" if kind == "service" else "test_drive"
    try:
        day = datetime.strptime(date.strip(), "%Y-%m-%d")
    except ValueError:
        raise ToolError("date must be YYYY-MM-DD")
    if day.weekday() in settings.closed_weekdays:
        return {"date": date, "slots": [], "note": "Showroom closed that day."}
    slots = []
    for hour in range(settings.work_start_hour, settings.work_end_hour):
        start = day.replace(hour=hour)
        if start < now():
            continue
        left = _capacity(kind) - _booked_count(ctx.db, kind, start)
        if left > 0:
            slots.append(start.strftime("%H:%M"))
    return {"kind": kind, "date": date, "weekday": day.strftime("%A"), "available_times": slots}


def book_test_drive(ctx: ToolContext, model_name: str, date_time: str, location: str = "showroom", address: str = "") -> dict:
    _require_contact(ctx)
    matches = match_car_models(ctx.db, model_name, include_upcoming=False)
    if not matches:
        raise ToolError(f"No model matching '{model_name}' available for test drive.")
    cm = matches[0]
    dt = _parse_dt(date_time)
    if problem := _slot_ok(dt):
        raise ToolError(problem)
    if _booked_count(ctx.db, "test_drive", dt.replace(minute=0)) >= _capacity("test_drive"):
        raise ToolError("That slot is full. Call get_available_slots and offer another time.")
    if location == "home" and not address:
        raise ToolError("Home test drive needs the customer's address.")
    td = TestDrive(customer_id=ctx.customer.id, car_model_id=cm.id, scheduled_at=dt,
                   location="home" if location == "home" else "showroom", address=address or None)
    ctx.db.add(td)
    lead = _open_lead(ctx)
    lead.car_model_id = lead.car_model_id or cm.id
    lead.interest = lead.interest or cm.full_name
    if lead.status in ("new", "contacted"):
        lead.status = "test_drive"
    ctx.db.flush()
    score_lead(ctx.db, lead)
    notify_staff(ctx.db, f"New test drive: {ctx.customer.name} ({ctx.customer.phone}) - {cm.full_name} on "
                         f"{dt:%a %d %b %I:%M %p} at {td.location}.")
    return {"booked": True, "booking_id": f"TD-{td.id}", "car": cm.full_name, "when": dt.strftime("%A %d %B %Y, %I:%M %p"),
            "location": td.location, "address": settings.showroom_address if td.location == "showroom" else address}


def record_lead_interest(ctx: ToolContext, interest: str, model_name: str = "", budget: int = 0, timeline: str = "",
                         wants_financing: bool = False, has_trade_in: bool = False, notes: str = "") -> dict:
    lead = _open_lead(ctx)
    lead.interest = interest
    if model_name and (m := match_car_models(ctx.db, model_name)):
        lead.car_model_id = m[0].id
    lead.budget = budget or lead.budget
    lead.timeline = timeline or lead.timeline
    lead.wants_financing = lead.wants_financing or wants_financing
    lead.has_trade_in = lead.has_trade_in or has_trade_in
    if notes:
        lead.notes = f"{lead.notes}\n{notes}" if lead.notes else notes
    ctx.db.flush()
    score_lead(ctx.db, lead)
    return {"saved": True}


def get_offers(ctx: ToolContext) -> dict:
    today = now().date()
    offers = ctx.db.query(Offer).filter(Offer.active.is_(True)).all()
    valid = [o for o in offers if (not o.valid_from or o.valid_from <= today) and (not o.valid_to or o.valid_to >= today)]
    return {"offers": [{"title": o.title, "details": o.description, "valid_until": o.valid_to.isoformat() if o.valid_to else None}
                       for o in valid]}


# ---------------------------------------------------------------- service tools

def get_service_estimate(ctx: ToolContext, service: str = "") -> dict:
    types = ctx.db.query(ServiceType).all()
    want = _words(service)
    matched = [t for t in types if want & _words(f"{t.name} {t.description}")] if want else []
    rows = matched or types
    return {"services": [{"name": t.name, "includes": t.description, "estimate": f"{money(t.price_min)} - {money(t.price_max)}",
                          "takes_about_hours": t.duration_hours} for t in rows],
            "note": "Estimates; final bill depends on the car model and parts needed."}


def book_service(ctx: ToolContext, date_time: str, service_type: str, car_model: str, reg_no: str = "", complaint: str = "") -> dict:
    _require_contact(ctx)
    dt = _parse_dt(date_time)
    if problem := _slot_ok(dt):
        raise ToolError(problem)
    if _booked_count(ctx.db, "service", dt.replace(minute=0)) >= _capacity("service"):
        raise ToolError("That service slot is full. Call get_available_slots and offer another time.")
    last = ctx.db.query(func.max(ServiceAppointment.id)).scalar() or 0
    appt = ServiceAppointment(job_no=f"JOB-{1100 + last}", customer_id=ctx.customer.id, reg_no=(reg_no or "").upper() or None,
                              car_model=car_model, service_type=service_type, scheduled_at=dt, complaint=complaint or None,
                              source=ctx.channel)
    ctx.db.add(appt)
    if reg_no and not ctx.db.query(CustomerVehicle).filter_by(customer_id=ctx.customer.id, reg_no=reg_no.upper()).first():
        ctx.db.add(CustomerVehicle(customer_id=ctx.customer.id, reg_no=reg_no.upper(), model_name=car_model))
    ctx.db.flush()
    return {"booked": True, "job_no": appt.job_no, "when": dt.strftime("%A %d %B %Y, %I:%M %p"), "service": service_type,
            "car": car_model, "address": settings.showroom_address,
            "note": "Customer will get a WhatsApp reminder a day before and an alert when the car is ready."}


def get_service_status(ctx: ToolContext, job_no: str = "", reg_no: str = "") -> dict:
    q = ctx.db.query(ServiceAppointment)
    if job_no:
        q = q.filter(func.upper(ServiceAppointment.job_no) == job_no.strip().upper())
    elif reg_no:
        q = q.filter(func.upper(ServiceAppointment.reg_no) == reg_no.strip().upper())
    else:
        q = q.filter(ServiceAppointment.customer_id == ctx.customer.id)
    appts = q.order_by(ServiceAppointment.scheduled_at.desc()).limit(3).all()
    # privacy: only show other people's jobs if both job number and registration match
    visible = [a for a in appts if a.customer_id == ctx.customer.id or
               (job_no and reg_no and (a.reg_no or "").upper() == reg_no.strip().upper())]
    if not visible:
        return {"found": False, "note": "No matching job for this customer. Ask for the job number AND registration number."}
    labels = {"booked": "Booked - waiting for the car", "in_progress": "Work in progress", "ready": "Ready for pickup",
              "delivered": "Delivered", "cancelled": "Cancelled"}
    return {"jobs": [{"job_no": a.job_no, "car": a.car_model, "reg_no": a.reg_no, "service": a.service_type,
                      "scheduled": a.scheduled_at.strftime("%a %d %b %I:%M %p"), "status": labels.get(a.status, a.status),
                      "bill": money(a.bill_amount) if a.bill_amount else None} for a in visible]}


def get_service_history(ctx: ToolContext) -> dict:
    appts = (ctx.db.query(ServiceAppointment).filter(ServiceAppointment.customer_id == ctx.customer.id)
             .order_by(ServiceAppointment.scheduled_at.desc()).limit(10).all())
    cars = ctx.db.query(CustomerVehicle).filter_by(customer_id=ctx.customer.id).all()
    return {"cars": [{"reg_no": c.reg_no, "model": c.model_name, "last_service": c.last_service_date.isoformat() if c.last_service_date else None,
                      "next_service_due": c.next_service_due.isoformat() if c.next_service_due else None} for c in cars],
            "appointments": [{"job_no": a.job_no, "date": a.scheduled_at.strftime("%Y-%m-%d"), "service": a.service_type,
                              "status": a.status, "bill": money(a.bill_amount) if a.bill_amount else None} for a in appts]}


def cancel_booking(ctx: ToolContext, booking_ref: str) -> dict:
    ref = booking_ref.strip().upper()
    if ref.startswith("TD-"):
        td = ctx.db.get(TestDrive, int(ref[3:])) if ref[3:].isdigit() else None
        if not td or td.customer_id != ctx.customer.id:
            raise ToolError("Test drive booking not found for this customer.")
        td.status = "cancelled"
        return {"cancelled": ref}
    appt = ctx.db.query(ServiceAppointment).filter(func.upper(ServiceAppointment.job_no) == ref).first()
    if not appt or appt.customer_id != ctx.customer.id:
        raise ToolError("Booking not found for this customer.")
    if appt.status not in ("booked",):
        raise ToolError(f"Cannot cancel - job status is {appt.status}.")
    appt.status = "cancelled"
    return {"cancelled": ref}


# ---------------------------------------------------------------- parts tools

def check_part(ctx: ToolContext, part_name: str, car_model: str = "") -> dict:
    want = _words(part_name) - {"part", "parts", "for", "the"}
    car = _words(car_model)
    rows = []
    for p in ctx.db.query(Part).all():
        text = _words(f"{p.name} {p.category} {p.part_no}")
        hit = len(want & text)
        if not hit:
            continue
        compat = _words(p.compatible_models)
        if car and not (car & compat) and "universal" not in compat and "most" not in compat:
            continue
        rows.append((hit, p))
    rows.sort(key=lambda x: -x[0])
    def status(p: Part) -> str:
        if p.stock <= 0:
            return "Out of stock - can be ordered (usually 3-5 working days)"
        return "In stock" if p.stock > p.min_stock else f"Low stock ({p.stock} left)"
    return {"parts": [{"part_no": p.part_no, "name": p.name, "fits": p.compatible_models, "price": money(p.price),
                       "availability": status(p), "genuine": p.is_genuine} for _, p in rows[:8]],
            "note": None if rows else "No matching part found. Offer to take the request and have the parts team call back."}


# ---------------------------------------------------------------- common tools

def search_knowledge(ctx: ToolContext, query: str) -> dict:
    hits = kb.search(query)
    return {"results": hits} if hits else {"results": [], "note": "Nothing found in the showroom documents."}


def save_customer_details(ctx: ToolContext, name: str = "", phone: str = "", email: str = "", city: str = "") -> dict:
    db, cust = ctx.db, ctx.customer
    if phone:
        norm = normalize_phone(phone)
        if not norm or len(norm) < 10:
            raise ToolError("That phone number looks invalid.")
        existing = db.query(Customer).filter(Customer.phone == norm, Customer.id != cust.id).first()
        if existing:
            # returning customer: move this chat onto their existing record (keeps memory & history)
            for model in (Lead, TestDrive, ServiceAppointment, CustomerVehicle):
                db.query(model).filter(model.customer_id == cust.id).update({"customer_id": existing.id})
            db.query(Conversation).filter(Conversation.customer_id == cust.id).update({"customer_id": existing.id})
            db.flush()
            db.delete(cust)
            ctx.customer = cust = existing
            ctx.conversation.customer_id = existing.id
        cust.phone = norm
    if name:
        cust.name = name.strip().title()
    if email:
        cust.email = email.strip()
    if city:
        cust.city = city.strip().title()
    db.flush()
    rescore_customer(db, cust.id)
    return {"saved": True, "customer": {"name": cust.name, "phone": cust.phone, "returning_customer_memory": cust.memory}}


def handoff_to_human(ctx: ToolContext, reason: str, summary: str = "") -> dict:
    conv = ctx.conversation
    conv.status = "human"
    conv.handoff_reason = reason
    if summary:
        conv.summary = summary
    conv.unread = (conv.unread or 0) + 1
    who = ctx.customer.name or ctx.customer.phone or f"web visitor #{ctx.customer.id}"
    notify_staff(ctx.db, f"Customer needs a human ({conv.channel}): {who}. Reason: {reason}. Open the admin panel > Chats.",
                 roles=("owner", "sales", "service", "parts"))
    open_now = _slot_ok(now() + timedelta(minutes=1)) is None
    return {"handed_off": True, "staff_available_now": open_now,
            "tell_customer": "A team member will reply here shortly." if open_now else
            f"Our team is offline now ({settings.showroom_hours}); they will contact you first thing in working hours."}


# ---------------------------------------------------------------- registry

def _schema(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


S, I, N, B = {"type": "string"}, {"type": "integer"}, {"type": "number"}, {"type": "boolean"}

TOOLS: dict[str, dict] = {
    "search_inventory": {
        "fn": search_inventory,
        "description": "Search cars currently IN STOCK (new and used) with live availability, colours and prices. Use for any 'do you have / available / budget / which car' question.",
        "schema": _schema({
            "query": {**S, "description": "make/model/variant words, e.g. 'civic oriel' or 'corolla'. Empty for all."},
            "condition": {"type": "string", "enum": ["new", "used", ""]},
            "body_type": {"type": "string", "enum": ["sedan", "hatchback", "suv", "crossover", ""]},
            "fuel": {"type": "string", "enum": ["petrol", "hybrid", "diesel", "electric", ""]},
            "min_price": I, "max_price": {**I, "description": f"maximum price in {settings.currency}"},
            "min_seats": I, "color": S}),
    },
    "get_model_details": {"fn": get_model_details,
                          "description": "Specs, features, price and stock colours of a model from the catalog.",
                          "schema": _schema({"model_name": S}, ["model_name"])},
    "compare_models": {"fn": compare_models, "description": "Compare 2-4 models side by side (price, engine, fuel average, features).",
                       "schema": _schema({"model_names": {"type": "array", "items": S}}, ["model_names"])},
    "list_upcoming_launches": {"fn": list_upcoming_launches, "description": "Upcoming car launches with expected dates and prices.",
                               "schema": _schema({})},
    "calculate_financing": {"fn": calculate_financing,
                            "description": "Bank financing / instalment (EMI) estimate. Give car_price or model_name.",
                            "schema": _schema({"car_price": I, "model_name": S, "down_payment_percent": N, "years": I,
                                               "annual_rate_percent": N})},
    "get_available_slots": {"fn": get_available_slots, "description": "Free time slots for service or test drive on a date.",
                            "schema": _schema({"kind": {"type": "string", "enum": ["service", "test_drive"]},
                                               "date": {**S, "description": "YYYY-MM-DD"}}, ["kind", "date"])},
    "book_test_drive": {"fn": book_test_drive,
                        "description": "Book a test drive. Needs customer name+phone saved first. Confirm details with the customer before calling.",
                        "schema": _schema({"model_name": S, "date_time": {**S, "description": "YYYY-MM-DD HH:MM (24h)"},
                                           "location": {"type": "string", "enum": ["showroom", "home"]}, "address": S},
                                          ["model_name", "date_time"])},
    "record_lead_interest": {"fn": record_lead_interest,
                             "description": "Save what a buyer wants (model, budget, timeline, financing, trade-in) so sales can follow up. Call whenever a customer shows buying interest.",
                             "schema": _schema({"interest": S, "model_name": S, "budget": I,
                                                "timeline": {**S, "description": "e.g. 'this month', 'in 3 months'"},
                                                "wants_financing": B, "has_trade_in": B, "notes": S}, ["interest"])},
    "get_offers": {"fn": get_offers, "description": "Current promotions and offers.", "schema": _schema({})},
    "get_service_estimate": {"fn": get_service_estimate, "description": "Price estimate and duration for service work.",
                             "schema": _schema({"service": {**S, "description": "e.g. 'oil change', 'AC', 'brakes'; empty lists all"}})},
    "book_service": {"fn": book_service,
                     "description": "Book a workshop service appointment. Needs customer name+phone saved first. Confirm details before calling.",
                     "schema": _schema({"date_time": {**S, "description": "YYYY-MM-DD HH:MM (24h)"}, "service_type": S,
                                        "car_model": S, "reg_no": S, "complaint": S}, ["date_time", "service_type", "car_model"])},
    "get_service_status": {"fn": get_service_status, "description": "Live status of a car in the workshop (is it ready?).",
                           "schema": _schema({"job_no": S, "reg_no": S})},
    "get_service_history": {"fn": get_service_history, "description": "This customer's cars, past services and next service due date.",
                            "schema": _schema({})},
    "cancel_booking": {"fn": cancel_booking, "description": "Cancel the customer's service (JOB-xxxx) or test drive (TD-x) booking.",
                       "schema": _schema({"booking_ref": S}, ["booking_ref"])},
    "check_part": {"fn": check_part, "description": "Spare part / accessory availability and price, optionally for a specific car.",
                   "schema": _schema({"part_name": S, "car_model": S}, ["part_name"])},
    "search_knowledge": {"fn": search_knowledge,
                         "description": "Search showroom documents: warranty, policies, service schedule, documents needed, FAQs, brochures.",
                         "schema": _schema({"query": S}, ["query"])},
    "save_customer_details": {"fn": save_customer_details, "description": "Save the customer's name / phone / email / city.",
                              "schema": _schema({"name": S, "phone": S, "email": S, "city": S})},
    "handoff_to_human": {"fn": handoff_to_human,
                         "description": "Transfer to a human staff member: customer asks for a person, is angry/complaining, price negotiation or discount requests, or you cannot help.",
                         "schema": _schema({"reason": S, "summary": {**S, "description": "short summary for staff"}}, ["reason"])},
}

COMMON = ["search_knowledge", "get_offers", "save_customer_details", "handoff_to_human"]
DEPARTMENT_TOOLS = {
    "sales": ["search_inventory", "get_model_details", "compare_models", "list_upcoming_launches", "calculate_financing",
              "get_available_slots", "book_test_drive", "record_lead_interest", *COMMON],
    "service": ["get_service_estimate", "get_available_slots", "book_service", "get_service_status", "get_service_history",
                "cancel_booking", "check_part", *COMMON],
    "parts": ["check_part", "record_lead_interest", "get_service_estimate", "get_available_slots", "book_service", *COMMON],
    "general": ["search_inventory", "get_model_details", "list_upcoming_launches", "get_service_status", "check_part",
                "get_available_slots", "cancel_booking", *COMMON],
}


def tool_definitions(department: str) -> list[dict]:
    return [{"name": n, "description": TOOLS[n]["description"], "input_schema": TOOLS[n]["schema"]}
            for n in DEPARTMENT_TOOLS.get(department, DEPARTMENT_TOOLS["general"])]


def run_tool(ctx: ToolContext, name: str, args: dict) -> tuple[str, bool]:
    """Returns (json_result, is_error)."""
    spec = TOOLS.get(name)
    if not spec:
        return json.dumps({"error": f"unknown tool {name}"}), True
    try:
        result = spec["fn"](ctx, **(args or {}))
        ctx.db.flush()
        return json.dumps(result, default=str, ensure_ascii=False), False
    except ToolError as exc:
        return json.dumps({"error": str(exc)}), True
    except TypeError as exc:
        return json.dumps({"error": f"bad arguments: {exc}"}), True
