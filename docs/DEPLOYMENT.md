# Deployment & Integrations

Everything here is **optional**. The app runs fully without any of it; add each
piece when you're ready. Edit `.env` and restart.

## 0. Why the cloud (showroom with one laptop)

Don't run the bot on the showroom laptop — it must stay on 24/7. Run the server
on a small cloud VM; the laptop and staff phones are just clients.

```
Showroom laptop / phones   →   Cloud server (always on)
  admin panel (browser)         FastAPI app + database + WhatsApp/voice
  WhatsApp staff commands        — survives power cuts & laptop reboots
```

Any host works (one `uvicorn`/Docker process). Put it near your users for voice
latency.

```bash
# production run
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
# (behind Nginx/Caddy for HTTPS; HTTPS is required by WhatsApp & Twilio webhooks)
```

## 1. Database → Supabase / PostgreSQL

1. Create a project at supabase.com (or any PostgreSQL).
2. Uncomment `psycopg[binary]` in `requirements.txt` and `pip install -r requirements.txt`.
3. Set:
   ```
   DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DBNAME
   ```
4. Restart. Tables are created and seeded automatically on first boot.

Supabase gives automatic backups and a spreadsheet-like table editor, so staff
can even edit rows there directly if needed.

## 2. WhatsApp → Meta Cloud API

1. Create a Meta app → add **WhatsApp** → get a **test number** (free) or connect a
   real number (must not be active on the normal WhatsApp app).
2. Set in `.env`:
   ```
   WHATSAPP_TOKEN=EAAG...                 # permanent/system-user token
   WHATSAPP_PHONE_NUMBER_ID=123456789
   WHATSAPP_VERIFY_TOKEN=any-string-you-choose
   WHATSAPP_APP_SECRET=...                # enables signature verification
   ```
3. In Meta → WhatsApp → Configuration, set the **callback URL** to
   `https://YOUR-DOMAIN/webhooks/whatsapp` and the **verify token** to the same
   string. Subscribe to the `messages` field.
4. Add staff WhatsApp numbers in the admin panel (Staff table) so their messages
   go to the command bot.

Templates: outside the 24-hour window Meta only delivers pre-approved template
messages. Register templates matching the reminder/alert texts in `app/notify.py`
and `app/scheduler.py` before relying on outbound reminders.

## 3. Phone calls → Twilio

1. Buy a voice-capable number in Twilio.
2. Set `TWILIO_AUTH_TOKEN`, `PUBLIC_BASE_URL=https://YOUR-DOMAIN`, and optionally
   `CALL_TRANSFER_NUMBER` (human fallback), `CALL_LANGUAGE`, `CALL_VOICE`.
3. In the number's settings, set **A call comes in** → Webhook →
   `https://YOUR-DOMAIN/voice/incoming` (HTTP POST).

This uses Twilio's built-in speech recognition and TTS. For low-latency,
barge-in voice later, switch to Twilio Media Streams + Pipecat/LiveKit (see
ARCHITECTURE.md) — the agent/tools don't change.

## 4. Voice notes (WhatsApp) → Whisper + TTS

```
STT_API_URL=https://api.groq.com/openai/v1/audio/transcriptions   # or OpenAI/self-hosted
STT_API_KEY=...
TTS_API_URL=https://api.openai.com/v1/audio/speech
TTS_API_KEY=...
VOICE_NOTE_REPLIES=true     # also reply with a voice note
```
Without these, voice notes get a polite "please type" reply; text/image still work.

## 5. Claude model & cost

```
LLM_MODEL=claude-opus-5-5       # best quality
ROUTER_MODEL=claude-haiku-5-5   # cheap routing / staff parsing
LLM_EFFORT=low                  # low = fast, cheap chat; raise for harder tasks
```
To cut cost at high volume, set `LLM_MODEL=claude-sonnet-5-5`. The system prompt
and tool list are prompt-cached automatically.

## 6. Backups

SQLite: a dated copy is written daily to `backups/` (keep = `BACKUP_KEEP_DAYS`).
PostgreSQL/Supabase: use the provider's automatic backups.

## 7. Embed the chat on the showroom website

```html
<script src="https://YOUR-DOMAIN/static/widget.js" data-name="Demo Motors"></script>
```
A floating chat button opens the assistant in a panel on any page.

## Go-live checklist

- [ ] `ANTHROPIC_API_KEY` set, `LLM_MODE=claude`
- [ ] `DATABASE_URL` → PostgreSQL/Supabase
- [ ] Changed `ADMIN_PASSWORD` and `SECRET_KEY`
- [ ] Real cars/parts/prices imported (Excel) or entered; demo data removed
- [ ] Knowledge docs (brochures, warranty, policies) uploaded
- [ ] Staff created with correct roles and WhatsApp numbers
- [ ] WhatsApp webhook verified; templates approved
- [ ] HTTPS enabled; `PUBLIC_BASE_URL` set
- [ ] (If phone) Twilio webhook set and a human transfer number configured
