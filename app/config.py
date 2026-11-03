"""All settings come from environment variables (.env file).

Change DATABASE_URL to move from the local SQLite file to a cloud database
(PostgreSQL / Supabase) without touching any code.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _bool(name: str, default: bool) -> bool:
    value = _env(name)
    if not value:
        return default
    return value.lower() in ("1", "true", "yes", "on")


class Settings:
    # --- Database ---------------------------------------------------------
    # Local default: SQLite file. Cloud: postgresql+psycopg://user:pass@host:5432/db
    database_url = _env("DATABASE_URL", f"sqlite:///{(BASE_DIR / 'data' / 'showroom.db').as_posix()}")
    seed_demo_data = _bool("SEED_DEMO_DATA", True)
    backup_dir = Path(_env("BACKUP_DIR", str(BASE_DIR / "backups")))
    backup_keep_days = int(_env("BACKUP_KEEP_DAYS", "14"))

    # --- AI ---------------------------------------------------------------
    # "auto" = Claude when credentials exist, otherwise offline demo mode.
    llm_mode = _env("LLM_MODE", "auto")  # auto | claude | demo
    llm_model = _env("LLM_MODEL", "claude-opus-5-5")
    router_model = _env("ROUTER_MODEL", "claude-haiku-5-5")
    llm_effort = _env("LLM_EFFORT", "low")  # low keeps chat replies fast
    use_refusal_fallbacks = _bool("USE_REFUSAL_FALLBACKS", True)

    # --- Showroom info (used in prompts and greetings) ----------------------
    showroom_name = _env("SHOWROOM_NAME", "Demo Motors")
    showroom_address = _env("SHOWROOM_ADDRESS", "Plot 12, Main Boulevard, Gulberg III, Lahore")
    showroom_phone = _env("SHOWROOM_PHONE", "+92 42 111 000 111")
    showroom_hours = _env("SHOWROOM_HOURS", "Mon-Sat 9:00 AM - 6:00 PM, Sunday closed")
    showroom_maps_url = _env("SHOWROOM_MAPS_URL", "https://maps.google.com/?q=Gulberg+III+Lahore")
    timezone = _env("TIMEZONE", "Asia/Karachi")
    currency = _env("CURRENCY", "PKR")
    default_country_code = _env("DEFAULT_COUNTRY_CODE", "92")

    # Booking capacity
    work_start_hour = int(_env("WORK_START_HOUR", "9"))
    work_end_hour = int(_env("WORK_END_HOUR", "17"))
    closed_weekdays = [int(d) for d in _env("CLOSED_WEEKDAYS", "6").split(",") if d.strip()]  # 6 = Sunday
    service_slot_capacity = int(_env("SERVICE_SLOT_CAPACITY", "3"))
    test_drive_slot_capacity = int(_env("TEST_DRIVE_SLOT_CAPACITY", "2"))

    # Financing defaults (demo values - change to your partner bank's rates)
    finance_annual_rate = float(_env("FINANCE_ANNUAL_RATE", "18"))
    finance_min_down_percent = float(_env("FINANCE_MIN_DOWN_PERCENT", "30"))
    finance_max_years = int(_env("FINANCE_MAX_YEARS", "5"))

    # --- Admin / security ---------------------------------------------------
    secret_key = _env("SECRET_KEY", "change-me-in-production")
    admin_username = _env("ADMIN_USERNAME", "admin")
    admin_password = _env("ADMIN_PASSWORD", "admin123")
    public_base_url = _env("PUBLIC_BASE_URL", "http://localhost:8000")

    # --- WhatsApp (Meta Cloud API) ------------------------------------------
    whatsapp_token = _env("WHATSAPP_TOKEN")
    whatsapp_phone_number_id = _env("WHATSAPP_PHONE_NUMBER_ID")
    whatsapp_verify_token = _env("WHATSAPP_VERIFY_TOKEN", "demo-verify-token")
    whatsapp_app_secret = _env("WHATSAPP_APP_SECRET")
    whatsapp_api_version = _env("WHATSAPP_API_VERSION", "v21.0")

    # --- Speech-to-text / text-to-speech for WhatsApp voice notes -----------
    # Any Whisper-compatible HTTP API (OpenAI, Groq, self-hosted). Optional.
    stt_api_url = _env("STT_API_URL")  # e.g. https://api.groq.com/openai/v1/audio/transcriptions
    stt_api_key = _env("STT_API_KEY")
    stt_model = _env("STT_MODEL", "whisper-large-v3")
    tts_api_url = _env("TTS_API_URL")  # e.g. https://api.openai.com/v1/audio/speech
    tts_api_key = _env("TTS_API_KEY")
    tts_model = _env("TTS_MODEL", "tts-1")
    tts_voice = _env("TTS_VOICE", "alloy")
    voice_note_replies = _bool("VOICE_NOTE_REPLIES", False)

    # --- Phone calls (Twilio) -----------------------------------------------
    twilio_auth_token = _env("TWILIO_AUTH_TOKEN")
    call_language = _env("CALL_LANGUAGE", "en-IN")
    call_voice = _env("CALL_VOICE", "Polly.Raveena")
    call_transfer_number = _env("CALL_TRANSFER_NUMBER")  # human fallback number

    # --- Background jobs ----------------------------------------------------
    scheduler_enabled = _bool("SCHEDULER_ENABLED", True)
    scheduler_interval_minutes = int(_env("SCHEDULER_INTERVAL_MINUTES", "10"))
    staff_undo_minutes = int(_env("STAFF_UNDO_MINUTES", "10"))

    @property
    def whatsapp_enabled(self) -> bool:
        return bool(self.whatsapp_token and self.whatsapp_phone_number_id)

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


settings = Settings()
