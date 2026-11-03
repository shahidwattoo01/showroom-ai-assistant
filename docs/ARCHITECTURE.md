# Architecture

## The one idea that makes daily updates a non-problem

A car showroom's data changes constantly — cars sold, new stock in, parts
consumed, prices revised, jobs progressing. The AI **does not learn or store any
of this**. It behaves like a good employee who always checks the computer before
answering:

- **Live data** (stock, prices, offers, free slots, job status, part quantity) →
  fetched by a **tool call** (`app/tools.py`) that runs a live DB query *at the
  moment the customer asks*. Change a row in the admin panel and the very next
  answer reflects it.
- **Slow documents** (brochures, warranty, policies, FAQs) → a **RAG** keyword
  index (`app/knowledge.py`) rebuilt whenever a file is uploaded.

So "how does it update with hundreds of daily changes?" → the **database** is
updated by normal showroom work; the AI is stateless about data and just reads.

## Request flow

```
channel adapter (webchat / whatsapp / voice)
        │  normalises to: (channel, external_id, phone, text, images)
        ▼
conversation.handle_customer_message()
        │  • finds/creates Customer (by phone) + Conversation
        │  • if status == human  → store message, stop (staff will reply)
        │  • else build [Context] (time, customer memory, open bookings)
        ▼
agents.route()                 ← cheap model (Haiku) picks department
        ▼
agents.respond()               ← main model (Opus) with that department's tools
        │  tool-use loop (max 8 hops): call tool → feed result → continue
        ▼
reply text  → stored as a bot message → returned to the channel adapter
        │
        └─ every N messages (or on handoff): summarise → customer.memory (CRM note)
```

Two interchangeable engines implement `route/respond/summarize`:

- `ClaudeEngine` — Claude via the Anthropic SDK. System prompt + tool list are
  cached (`cache_control`), routing uses `claude-haiku-5-5`, answers use
  `claude-opus-5-5`, and refusal fallbacks are enabled by default.
- `DemoEngine` — a pure-Python keyword engine using the **same tools**, so the
  entire app is demonstrable offline with no key.

`get_engine()` picks Claude when credentials exist, else demo (override with
`LLM_MODE`).

## One number, two kinds of WhatsApp users

`channels/whatsapp.py` checks the sender against the `staff` table:
- **staff number** → `channels/staff.py` command bot (parse → confirm → update →
  audit → undo), restricted by role.
- **anyone else** → the customer AI agent.

The webhook answers Meta immediately and does AI work in a background task (Meta
retries slow webhooks).

## Tools (what the agent can actually do)

Defined once in `app/tools.py` with a JSON schema each, grouped per department.
Every tool returns JSON and can raise `ToolError` (turned into a clean message).
Bookings require a saved name + phone, check slot capacity, and write an audit
trail. `handoff_to_human` flips the conversation to `human` and alerts staff.

## Data model (`app/models.py`)

`Customer` (memory!), `CarModel` (catalog + upcoming), `Vehicle` (physical stock,
new/used, status), `Part`, `ServiceType`, `CustomerVehicle`, `ServiceAppointment`,
`TestDrive`, `Lead` (+score), `Offer`, `Conversation`/`Message`,
`PendingAction` (staff confirmations), `AuditLog`, `Notification` (outbox).

Works identically on SQLite (local) and PostgreSQL (cloud) — only `DATABASE_URL`
changes.

## Background jobs (`app/scheduler.py`)

An async loop (interval = `SCHEDULER_INTERVAL_MINUTES`) sends appointment and
service-due reminders, re-scores leads, closes idle chats, and takes a daily
SQLite backup. Trigger manually from the admin panel (*Run jobs*) or
`POST /api/admin/jobs/run`.

## Safety & privacy

- Staff allowlist + role permissions; signed-token admin auth; password hashing
  (PBKDF2). WhatsApp webhook signature + Twilio signature verification when
  secrets are set.
- Every data change is written to `AuditLog`; staff changes are reversible
  (UNDO within a window).
- The bot is instructed never to collect card numbers/CNIC/passwords and never to
  invent prices/stock/policy; discounts and legal/insurance matters are handed to
  a human.

## Extending to streaming phone (later)

The current phone agent (`channels/voice.py`) uses Twilio's `<Gather>` speech +
`<Say>` TTS — simple and reliable. For sub-second, barge-in voice, replace it with
Twilio Media Streams → Pipecat/LiveKit → streaming STT/LLM/TTS, reusing the same
`tools.py` and `agents.py`.
