import base64
import hashlib
import hmac
import json
import os
import re
import time

from .config import settings


def normalize_phone(raw: str | None) -> str | None:
    """'0300-1234567' / '+92 300 1234567' / 'whatsapp:+923001234567' -> '923001234567'."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 11:
        digits = settings.default_country_code + digits[1:]
    return digits


def money(amount: int | float | None) -> str:
    if amount is None:
        return "N/A"
    return f"{settings.currency} {int(round(amount)):,}"


# ---- passwords & tokens (no extra dependencies) ----------------------------

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or "$" not in stored:
        return False
    salt_hex, digest_hex = stored.split("$", 1)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 200_000)
    return hmac.compare_digest(digest.hex(), digest_hex)


def make_token(staff_id: int, hours: int = 12) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"sid": staff_id, "exp": int(time.time()) + hours * 3600}).encode()).decode()
    sig = hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def read_token(token: str) -> int | None:
    try:
        payload, sig = token.rsplit(".", 1)
        expected = hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
        if data["exp"] < time.time():
            return None
        return int(data["sid"])
    except Exception:
        return None
