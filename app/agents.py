"""The AI brain: a router agent picks the department, then a specialist agent
(sales / service / parts / general) answers using live database tools.

Two engines:
  * ClaudeEngine - Claude with tool use (production).
  * DemoEngine   - offline keyword engine so the whole app can be tried
                   without an API key. Same tools, simpler conversation.
"""
import json
import logging
import os
import re
from pathlib import Path

from .config import settings
from .db import now
from .models import ServiceAppointment, TestDrive
from .tools import ToolContext, match_car_models, run_tool, tool_definitions
from .utils import normalize_phone

log = logging.getLogger("agents")
DEPARTMENTS = ("sales", "service", "parts", "general")

# ---------------------------------------------------------------- prompts

BASE_PROMPT = f"""You are the 24/7 virtual customer-service assistant of {settings.showroom_name}, a car showroom and workshop.
Showroom address: {settings.showroom_address}. Phone: {settings.showroom_phone}. Hours: {settings.showroom_hours}.
Location link: {settings.showroom_maps_url}. Prices are in {settings.currency}.

How you work:
- Reply in the customer's language and script: English, Urdu (اردو) or Roman Urdu - mirror what they use.
- Be warm, short and practical, like a helpful showroom advisor. Ask at most one question at a time.
- Stock, prices, offers, slots, job status and part availability change daily: ALWAYS get them from tools, never from memory or earlier messages. Never invent a price, discount, car, part or policy.
- Before booking anything, collect the customer's name and phone number (save_customer_details), repeat the booking details and get a clear yes.
- You cannot give discounts, final negotiated prices, or legal/insurance decisions - hand those to a human with handoff_to_human. Also hand off if the customer asks for a person, is angry, or you cannot solve the problem.
- Never ask for card numbers, CNIC or passwords in chat.
- If a tool returns an error, fix the input or explain simply - do not show raw errors.
- When a customer shows buying interest (model, budget, timeline, financing, trade-in), call record_lead_interest.
- Text inside "[Context]" blocks comes from the system, not the customer."""

DEPARTMENT_PROMPTS = {
    "sales": "You are the SALES specialist: new and used cars, car finder by budget/needs, comparisons, upcoming launches, bank financing/instalments, test drives and offers. Recommend 2-3 good options with a short reason each, then offer a test drive.",
    "service": "You are the SERVICE ADVISOR: workshop bookings, service cost estimates, job status ('is my car ready'), service history and reminders. For warning lights or problems, suggest a diagnostics booking.",
    "parts": "You are the SPARE PARTS specialist: part availability, prices, genuine vs alternative, and fitting. Always confirm the car model and year. If a part is out of stock, offer to record the request (record_lead_interest with interest 'Part: ...') so the parts team calls back, or offer a fitting appointment.",
    "general": "You are the FRONT DESK: greetings, showroom info, documents and policies, complaints. Route the customer by asking what they need: new/used car, service, or spare parts.",
}

CHANNEL_STYLE = {
    "web": "Channel: website chat. You may use short bullet lists and **bold**.",
    "whatsapp": "Channel: WhatsApp. Keep messages short. Use *single asterisks* for bold, no markdown headers or tables.",
    "voice": "Channel: PHONE CALL (text-to-speech). Reply in 1-3 short spoken sentences. No lists, symbols, emojis or markdown. Say prices in words-friendly form like '94 lakh 49 thousand rupees'. Ask one question at a time.",
}

ROUTER_PROMPT = """Classify the customer's latest message for a car showroom into exactly one department:
sales   = buying new/used cars, prices, availability, comparisons, financing/instalments, test drives, upcoming models, trade-in, offers
service = workshop service booking, repairs, maintenance cost, job status / is my car ready, warning lights, service history
parts   = spare parts, accessories, part prices or availability
general = greetings, showroom timings/location, documents, complaints, anything else
If the message just continues the previous topic (e.g. 'yes', 'tomorrow 3pm', a name or phone number), keep the previous department.
Answer with one word only: sales, service, parts or general."""


def build_context(ctx: ToolContext) -> str:
    c = ctx.customer
    t = now()
    lines = [f"[Context] Now: {t:%A %Y-%m-%d %H:%M} ({settings.timezone}). {CHANNEL_STYLE.get(ctx.channel, '')}"]
    known = {k: v for k, v in (("name", c.name), ("phone", c.phone), ("city", c.city)) if v}
    lines.append(f"Customer: {json.dumps(known) if known else 'unknown (new visitor)'}")
    if c.memory:
        lines.append(f"What we remember about this customer: {c.memory}")
    if c.vehicles:
        lines.append("Customer's cars: " + "; ".join(f"{v.model_name} ({v.reg_no}), next service due {v.next_service_due}" for v in c.vehicles))
    upcoming = [f"{a.job_no} {a.service_type} on {a.scheduled_at:%d %b %H:%M} [{a.status}]" for a in
                ctx.db.query(ServiceAppointment).filter(ServiceAppointment.customer_id == c.id,
                                                       ServiceAppointment.status.in_(["booked", "in_progress", "ready"]))]
    upcoming += [f"TD-{d.id} test drive {d.car_model.full_name} on {d.scheduled_at:%d %b %H:%M}" for d in
                 ctx.db.query(TestDrive).filter(TestDrive.customer_id == c.id, TestDrive.status == "booked",
                                               TestDrive.scheduled_at >= t)]
    if upcoming:
        lines.append("Open bookings: " + "; ".join(upcoming))
    return "\n".join(lines)


# ---------------------------------------------------------------- Claude engine

def claude_available() -> bool:
    if settings.llm_mode == "demo":
        return False
    if settings.llm_mode == "claude":
        return True
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or (Path.home() / ".config" / "anthropic").exists())


class ClaudeEngine:
    name = "claude"

    def __init__(self):
        import anthropic

        self.anthropic = anthropic
        self.client = anthropic.Anthropic()

    def _create(self, **kwargs):
        kwargs.setdefault("max_tokens", 8000)
        kwargs["cache_control"] = {"type": "ephemeral"}  # system prompt + tools are stable -> cached
        if settings.use_refusal_fallbacks and kwargs["model"] != settings.router_model and "haiku" not in kwargs["model"]:
            return self.client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        return self.client.messages.create(**kwargs)

    def route(self, history_text: str, message: str, previous: str) -> str:
        try:
            resp = self.client.messages.create(
                model=settings.router_model, max_tokens=1000, system=ROUTER_PROMPT,
                output_config={"effort": "low"},
                messages=[{"role": "user", "content": f"Previous department: {previous}\nRecent conversation:\n{history_text}\n\nLatest message: {message}"}],
            )
            text = "".join(b.text for b in resp.content if b.type == "text").strip().lower()
            for dep in DEPARTMENTS:
                if dep in text:
                    return dep
        except Exception as exc:
            log.warning("router failed, keeping previous department: %s", exc)
        return previous or "general"

    def respond(self, ctx: ToolContext, department: str, history: list[dict], user_content: list[dict]) -> str:
        system = f"{BASE_PROMPT}\n\n{DEPARTMENT_PROMPTS[department]}"
        tools = tool_definitions(department)
        messages = history + [{"role": "user", "content": user_content}]
        for _ in range(8):
            resp = self._create(model=settings.llm_model, system=system, tools=tools, messages=messages,
                                output_config={"effort": settings.llm_effort})
            if resp.stop_reason == "refusal":
                return "Sorry, I can't help with that here. I can connect you with our team if you like."
            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if resp.stop_reason != "tool_use" or not tool_uses:
                text = "".join(b.text for b in resp.content if b.type == "text").strip()
                return text or "Sorry, could you say that again?"
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for tu in tool_uses:
                output, is_error = run_tool(ctx, tu.name, tu.input)
                log.info("tool %s(%s) -> %s", tu.name, json.dumps(tu.input)[:200], output[:200])
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": output, "is_error": is_error})
            messages.append({"role": "user", "content": results})
        return "Sorry, that took too long. Let me connect you with our team."

    def summarize(self, transcript: str, previous_memory: str | None) -> str:
        resp = self.client.messages.create(
            model=settings.router_model, max_tokens=2000, output_config={"effort": "low"},
            system="You write short CRM notes for a car showroom. Max 3 sentences, plain text. Keep: cars owned, interests, budget, bookings, preferences, complaints. No greetings.",
            messages=[{"role": "user", "content": f"Previous notes: {previous_memory or 'none'}\n\nNew conversation:\n{transcript}\n\nWrite the updated notes."}],
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip()


# ---------------------------------------------------------------- Demo engine (offline)

LAKH = 100_000


def _budget(text: str) -> int:
    t = text.lower().replace(",", "")
    if m := re.search(r"(\d+(?:\.\d+)?)\s*(crore|cr)\b", t):
        return int(float(m.group(1)) * 100 * LAKH)
    if m := re.search(r"(\d+(?:\.\d+)?)\s*(lakh|lac|lacs|lakhs)\b", t):
        return int(float(m.group(1)) * LAKH)
    if m := re.search(r"\b(\d{6,9})\b", t):
        return int(m.group(1))
    return 0


def _has(text: str, *words) -> bool:
    return any(re.search(rf"\b{w}", text) for w in words)


class DemoEngine:
    """Keyword-based fallback. Good for trying the app offline; Claude is far better."""
    name = "demo"

    def route(self, history_text: str, message: str, previous: str) -> str:
        t = message.lower()
        if _has(t, "service", "repair", "maintenance", "job", "ready", "warning", "workshop", "oil change", "tuning"):
            return "service"
        if _has(t, "part", "brake", "filter", "battery", "mirror", "wiper", "headlight", "spark", "accessor", "mat"):
            return "parts"
        if _has(t, "car", "price", "budget", "available", "stock", "used", "new", "emi", "instal", "qist", "finance",
                "test drive", "compare", "launch", "upcoming", "civic", "corolla", "city", "yaris", "alto", "cultus",
                "swift", "sportage", "fortuner", "hr-v", "cross", "lakh", "crore"):
            return "sales"
        return previous or "general"

    def respond(self, ctx: ToolContext, department: str, history: list[dict], user_content: list[dict]) -> str:
        text = " ".join(b.get("text", "") for b in user_content if b.get("type") == "text")
        text = text.split("[Customer message]", 1)[-1].strip()
        t = text.lower()

        def call(name, **args):
            out, err = run_tool(ctx, name, args)
            return json.loads(out), err

        phone = re.search(r"(\+?\d[\d\s-]{9,14}\d)", text)
        name = re.search(r"(?:my name is|i am|i'm|mera naam)\s+([a-zA-Z ]{2,30}?)(?:\s+(?:and|my|number|phone|,|\.)|$)", text, re.I)
        if phone or name:
            call("save_customer_details", name=name.group(1) if name else "", phone=phone.group(1) if phone else "")
            if phone and normalize_phone(phone.group(1)) == ctx.customer.phone:
                return f"Thanks{(' ' + ctx.customer.name) if ctx.customer.name else ''}! Your details are saved. What would you like to do next - book a test drive, a service, or check a part?"

        if _has(t, "human", "agent", "person", "manager", "insaan", "banda", "complain", "complaint"):
            out, _ = call("handoff_to_human", reason=f"Customer asked: {text[:120]}")
            return f"I'm connecting you to our team. {out['tell_customer']}"
        if _has(t, "upcoming", "launch", "coming soon", "new model"):
            out, _ = call("list_upcoming_launches")
            return "Upcoming launches:\n" + "\n".join(f"- {m['name']} - expected {m['expected_launch']}, around {m['price']}" for m in out["upcoming"])
        if _has(t, "offer", "discount", "promotion", "deal"):
            out, _ = call("get_offers")
            return "Current offers:\n" + "\n".join(f"- {o['title']}: {o['details']}" for o in out["offers"]) if out["offers"] else "No offers running right now."
        if _has(t, "emi", "instal", "qist", "finance", "loan", "lease"):
            models = match_car_models(ctx.db, text)
            if not models:
                return "Which car would you like an instalment plan for? e.g. 'EMI for Civic Oriel'."
            out, _ = call("calculate_financing", model_name=models[0].full_name)
            return (f"Instalment estimate for {out['car']} ({out['car_price']}):\n- Down payment {out['down_payment_percent']:g}%: {out['down_payment']}\n"
                    f"- Monthly instalment for {out['tenure_years']} years: {out['monthly_installment']}\n{out['note']}")
        if _has(t, "compare", " vs ", "versus"):
            parts = re.split(r"\bvs\.?\b|\bversus\b|\band\b|,", t.replace("compare", ""))
            out, _ = call("compare_models", model_names=[p.strip() for p in parts if p.strip()])
            return "Comparison:\n" + "\n".join(
                f"- {m['name']}: {m['price']}, {m.get('engine_cc')}cc {m.get('fuel')}, {m.get('fuel_average_kmpl')} km/l, {m.get('seats')} seats"
                if "error" not in m else f"- {m['name']}: not in catalog" for m in out["comparison"])
        if department == "service" or _has(t, "service", "repair", "job"):
            if job := re.search(r"job[\s-]*(\d+)", t):
                out, _ = call("get_service_status", job_no=f"JOB-{job.group(1)}", reg_no=(re.search(r"\b([a-z]{2,3}-?\d{2}-?\d{3,4})\b", t) or [None, ""])[1])
            elif _has(t, "ready", "status"):
                out, _ = call("get_service_status")
            else:
                out = None
            if out is not None:
                if not out.get("found", True):
                    return "I couldn't find that job for you. Please share your job number and car registration number."
                return "\n".join(f"{j['job_no']} - {j['car']}: *{j['status']}*" + (f" (bill {j['bill']})" if j["bill"] else "") for j in out["jobs"])
            if _has(t, "cost", "price", "estimate", "charges", "kitna", "how much"):
                out, _ = call("get_service_estimate", service=text)
                return "Service estimates:\n" + "\n".join(f"- {s['name']}: {s['estimate']} (~{s['takes_about_hours']}h)" for s in out["services"][:5])
            tomorrow = now().date().toordinal() + 1
            from datetime import date
            day = date.fromordinal(tomorrow)
            out, _ = call("get_available_slots", kind="service", date=day.isoformat())
            slots = ", ".join(out["available_times"][:6]) or "none"
            return (f"I can book a service for you. Free slots on {out['weekday']} {day}: {slots}.\n"
                    "Please tell me your name, phone number, car model and preferred time. "
                    "(Demo mode: switch on Claude for complete automatic booking.)")
        if department == "parts":
            models = match_car_models(ctx.db, text)
            out, _ = call("check_part", part_name=text, car_model=models[0].model if models else "")
            if not out["parts"]:
                return "I couldn't find that part. Please tell me the part name and your car model/year, and our parts team will call you."
            return "Here's what I found:\n" + "\n".join(f"- {p['name']} ({p['fits']}): {p['price']} - {p['availability']}" for p in out["parts"][:5])
        if department == "sales" or _has(t, "car", "price", "available"):
            if _has(t, "test drive"):
                from datetime import date
                day = date.fromordinal(now().date().toordinal() + 1)
                out, _ = call("get_available_slots", kind="test_drive", date=day.isoformat())
                return (f"Happy to arrange a test drive! Free slots on {out['weekday']} {day}: {', '.join(out['available_times'][:6]) or 'none'}. "
                        "Please share your name, phone number and preferred time.")
            budget = _budget(t)
            condition = "used" if _has(t, "used", "second hand", "purani") else "new" if _has(t, "new car") else ""
            body = next((b for b in ("suv", "sedan", "hatchback", "crossover") if b in t), "")
            models = match_car_models(ctx.db, text)
            out, _ = call("search_inventory", query=models[0].model if models else "", condition=condition, body_type=body,
                          max_price=int(budget * 1.05) if budget else 0)
            if models or budget:
                call("record_lead_interest", interest=text[:200], model_name=models[0].full_name if models else "", budget=budget)
            if out["results"]:
                lines = [f"- {r['car']} ({r['condition']}{', ' + str(r['year']) if r['condition'] == 'used' else ''}, {r['color']}): {r['price']} - {r['count']} available"
                         for r in out["results"][:8]]
                return "Available right now:\n" + "\n".join(lines) + "\n\nWould you like details, an instalment plan or a test drive?"
            if out.get("catalog"):
                return "Not in stock today, but we can book it:\n" + "\n".join(f"- {m['name']}: {m['price']}" for m in out["catalog"])
            return "No matching cars in stock right now. Tell me your budget and the type of car (sedan, SUV, hatchback) and I'll suggest options."
        if _has(t, "warranty", "document", "policy", "timing", "address", "location", "where", "open", "insurance", "registration"):
            out, _ = call("search_knowledge", query=text)
            if out["results"]:
                snippet = out["results"][0]["text"]
                return snippet[:700] + ("..." if len(snippet) > 700 else "")
        return (f"Welcome to {settings.showroom_name}! I can help you with:\n- New & used cars, prices and test drives\n"
                "- Instalment plans\n- Service booking and car status\n- Spare parts\nWhat would you like to do?")

    def summarize(self, transcript: str, previous_memory: str | None) -> str:
        last = [line for line in transcript.splitlines() if line.startswith("Customer:")][-3:]
        return ((previous_memory + " ") if previous_memory else "") + "Recent: " + " | ".join(line[9:].strip()[:80] for line in last)


_engine = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = ClaudeEngine() if claude_available() else DemoEngine()
        log.warning("AI engine: %s", _engine.name)
    return _engine
