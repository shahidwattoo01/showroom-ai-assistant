"""Outgoing WhatsApp messages through the Meta WhatsApp Cloud API.

When WHATSAPP_TOKEN is not configured every send is a no-op that returns
False, and the app works in "simulator" mode (see the admin panel).
"""
import httpx

from ..config import settings

GRAPH = "https://graph.facebook.com"


def _url(path: str) -> str:
    return f"{GRAPH}/{settings.whatsapp_api_version}/{path}"


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.whatsapp_token}"}


def _post(payload: dict) -> bool:
    if not settings.whatsapp_enabled:
        return False
    payload = {"messaging_product": "whatsapp", **payload}
    resp = httpx.post(_url(f"{settings.whatsapp_phone_number_id}/messages"), json=payload, headers=_headers(), timeout=20)
    if resp.status_code >= 400:
        raise RuntimeError(f"WhatsApp API {resp.status_code}: {resp.text[:300]}")
    return True


def send_text(to: str, text: str) -> bool:
    # WhatsApp limit is 4096 characters per message
    ok = True
    for i in range(0, max(len(text), 1), 4000):
        ok = _post({"to": to, "type": "text", "text": {"body": text[i:i + 4000]}}) and ok
    return ok


def send_buttons(to: str, text: str, buttons: list[str]) -> bool:
    """Up to 3 tap buttons (WhatsApp limit). Button titles max 20 chars."""
    buttons = buttons[:3]
    return _post({
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": text[:1024]},
            "action": {"buttons": [{"type": "reply", "reply": {"id": f"btn_{i}", "title": b[:20]}} for i, b in enumerate(buttons)]},
        },
    })


def send_reply(to: str, reply: dict) -> bool:
    if reply.get("buttons"):
        return send_buttons(to, reply["text"], reply["buttons"])
    return send_text(to, reply["text"])


def mark_read(message_id: str):
    try:
        _post({"status": "read", "message_id": message_id})
    except Exception:
        pass


def download_media(media_id: str) -> tuple[bytes, str]:
    """Returns (bytes, mime_type) for an incoming image / voice note."""
    meta = httpx.get(_url(media_id), headers=_headers(), timeout=20).json()
    resp = httpx.get(meta["url"], headers=_headers(), timeout=60)
    resp.raise_for_status()
    return resp.content, meta.get("mime_type", "application/octet-stream")


def upload_media(data: bytes, mime_type: str, filename: str) -> str:
    resp = httpx.post(
        _url(f"{settings.whatsapp_phone_number_id}/media"),
        headers=_headers(),
        data={"messaging_product": "whatsapp", "type": mime_type},
        files={"file": (filename, data, mime_type)},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def send_audio(to: str, data: bytes, mime_type: str = "audio/ogg") -> bool:
    if not settings.whatsapp_enabled:
        return False
    media_id = upload_media(data, mime_type, "reply.ogg")
    return _post({"to": to, "type": "audio", "audio": {"id": media_id}})
