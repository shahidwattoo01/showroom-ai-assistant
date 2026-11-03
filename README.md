# 🚗 Car Showroom AI Customer Service

A 24/7 AI customer-service system for a car showroom and workshop. One AI brain
serves customers over **website chat, WhatsApp, browser voice and phone calls**,
handles **sales, service, spare parts, financing and complaints**, and hands the
hard cases to a human — while staff keep the data updated from a simple admin
panel, an Excel upload, or plain WhatsApp messages.

Built so it runs **with zero setup** (offline demo mode + local SQLite) and
scales to the cloud (Claude + Supabase/PostgreSQL + WhatsApp) by editing `.env`.

---

## ✨ What it does (the 23 MVP features)

**Sales** — car finder by budget/needs · model comparison · test-drive booking ·
instalment (EMI) calculator · upcoming launches · offers.
**Service** — appointment booking with live slots · "is my car ready?" status ·
cost estimates · service history · automatic *car ready* alert.
**Parts** — availability & price check by car/year.
**Experience** — Urdu / English / Roman Urdu · WhatsApp voice notes · phone-call
agent · remembers returning customers · smooth human handoff with full history.
**Automatic** — service-due reminders · appointment reminders a day before.
**Staff/admin** — WhatsApp staff commands ("Civic white sold") · admin panel ·
Excel import/export · live chat takeover dashboard · leads with status.
**Intelligence** — hot/warm/cold lead scoring.
**Security** — staff phone allowlist + logins · automatic daily backups.

---

## 🚀 Quick start (2 minutes, no API key, no accounts)

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows   (source .venv/bin/activate on Mac/Linux)
pip install -r requirements.txt
python run.py
```

Then open:

| Page | URL | Notes |
|------|-----|-------|
| Customer chat | http://localhost:8000/ | the website chat widget, full screen |
| Admin panel | http://localhost:8000/admin | login **admin / admin123** |
| API docs | http://localhost:8000/docs | auto-generated |

It starts in **demo mode**: an offline keyword bot, a local SQLite database, and
sample cars/parts/customers. No internet or keys needed — perfect for a first look
or a demo. The admin panel's **WhatsApp Simulator** lets you test the whole
WhatsApp experience (customer *and* staff) without any Meta setup.

### Turn on real Claude AI

```bash
copy .env.example .env            # cp on Mac/Linux
```
Put your key in `.env` → `ANTHROPIC_API_KEY=sk-ant-...` (from
https://console.anthropic.com) and restart. The bot now uses Claude agents with
full tool use, far better than demo mode.

---

## 🗺️ Pipeline

```
 Customer ── web chat ─┐
          ── WhatsApp ─┤
          ── voice ────┤→ Channel layer → Router agent → Sales / Service / Parts /
          ── phone ────┘   (whatsapp.py,     (picks dept)   General specialist agent
                            voice.py,                              │
                            webchat.py)                            │ calls tools
                                                                   ▼
                              Live database  ◄───────────  tools.py (inventory, slots,
                              (single source of truth)       bookings, parts, financing)
                                     ▲                             │
         staff & admin update it ────┤                             ▼
         (admin panel / Excel /      │                     RAG knowledge base
          WhatsApp staff commands)   │                     (brochures, policies)
                                     ▼
                        Human handoff → admin chat dashboard
                        Scheduler → reminders, backups, lead scoring
```

**Key idea:** the AI never memorises stock or prices. Every answer about
inventory, slots, job status or parts is a **live database lookup through a tool**,
so hundreds of daily changes appear instantly — nothing about the AI needs
"updating". See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## 🔄 How data stays current (3 ways, use any mix)

1. **Admin panel** (`/admin`) — edit vehicles, parts, service jobs, offers; mark a
   car sold / a job ready (which auto-WhatsApps the customer).
2. **Excel import/export** — download a table, edit in Excel, upload it back.
   Great for bulk daily stock/price updates. Templates included.
3. **WhatsApp staff commands** — a manager texts `Civic Oriel white sold`,
   `City brake pads stock 15`, `job 1043 ready bill 12500`. The bot parses it,
   asks for confirmation, updates the DB, logs it, and offers **UNDO**. Only
   allow-listed staff numbers work, and each role is limited to its area.

Live data (stock/prices/slots) comes from the database; slow documents
(brochures, warranty, FAQs) come from the **Knowledge base** — upload PDFs/MD/TXT
on the admin panel's *Knowledge* page.

---

## ☁️ Going live (when the showroom has only a laptop)

Run the server in the cloud (so it's always on); the laptop just opens the admin
panel in a browser. See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for:

- **Database → Supabase/PostgreSQL**: set one line, `DATABASE_URL=postgresql+psycopg://...`
- **WhatsApp → Meta Cloud API**: set `WHATSAPP_*`, webhook `{URL}/webhooks/whatsapp`
- **Phone → Twilio**: voice webhook `{URL}/voice/incoming`
- **Voice notes → Whisper/TTS**: set `STT_*` / `TTS_*`

Everything is optional and falls back gracefully when not configured.

---

## 🧪 Tests

```bash
python -m pytest -q
```

12 end-to-end tests cover web chat, financing, handoff, admin, the full staff
WhatsApp sell-a-car flow, permissions, Excel export, knowledge search and the
scheduler — all offline.

---

## 📁 Project layout

```
app/
  config.py         settings from .env (all defaults safe)
  db.py  models.py  SQLite/PostgreSQL schema
  seed.py           sample showroom data
  agents.py         router + specialist agents (Claude engine + offline demo engine)
  tools.py          business tools the AI calls (inventory, bookings, parts, EMI...)
  knowledge.py      RAG over data/knowledge documents
  conversation.py   channel-independent chat handling + customer memory
  leads.py          hot/warm/cold lead scoring
  notify.py         outgoing messages (WhatsApp or logged outbox)
  scheduler.py      reminders, backups, lead re-scoring
  speech.py         voice-note STT/TTS
  admin.py          admin panel API (CRUD, chats, Excel, dashboard, simulator)
  webchat.py        website chat API
  channels/
    whatsapp.py     WhatsApp webhook (customers + staff on one number)
    whatsapp_api.py Meta Cloud API client
    staff.py        staff command bot (parse → confirm → update → audit → undo)
    voice.py        Twilio phone-call agent
static/             chat.html, admin.html, widget.js
data/knowledge/     sample brochures / policies for RAG
tests/              pytest suite
```

---

## ⚠️ Notes

- Demo data (prices, stock) is **illustrative**. Replace it with the real
  showroom's data via the admin panel or Excel import.
- Change `ADMIN_PASSWORD` and `SECRET_KEY` before deploying.
- The bot never asks for card numbers/CNIC/passwords; payments go through
  in-person or official payment links only.
- Human staff still handle negotiation, discounts, and legal/insurance decisions
  — the AI hands those over automatically.
