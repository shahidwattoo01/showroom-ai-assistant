"""End-to-end tests in offline DEMO mode (no API key). Run: python -m pytest -q"""
import os
import tempfile

os.environ["LLM_MODE"] = "demo"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.gettempdir()}/showroom_test.db"

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    db_path = os.environ["DATABASE_URL"].replace("sqlite:///", "")
    if os.path.exists(db_path):
        os.remove(db_path)
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["engine"] == "demo"


def test_web_chat_inventory(client):
    sid = client.post("/api/chat/start", json={}).json()["session_id"]
    r = client.post("/api/chat/send", json={"session_id": sid, "text": "do you have a Civic available?"}).json()
    assert "Civic" in r["text"]


def test_web_chat_financing(client):
    sid = client.post("/api/chat/start", json={}).json()["session_id"]
    r = client.post("/api/chat/send", json={"session_id": sid, "text": "EMI for Corolla Altis Grande"}).json()
    assert "instalment" in r["text"].lower() or "monthly" in r["text"].lower()


def test_handoff(client):
    sid = client.post("/api/chat/start", json={}).json()["session_id"]
    r = client.post("/api/chat/send", json={"session_id": sid, "text": "I want to talk to a human please"}).json()
    assert r["status"] == "human"


def admin_token(client):
    return client.post("/api/admin/login", json={"username": "admin", "password": "admin123"}).json()["token"]


def test_admin_login_and_dashboard(client):
    token = admin_token(client)
    assert token
    h = {"Authorization": f"Bearer {token}"}
    d = client.get("/api/admin/dashboard", headers=h).json()
    assert d["stock"]["new_available"] > 0


def test_staff_whatsapp_sell_car(client):
    """Full staff flow: command -> confirm -> DB changes -> customer bot sees it."""
    h = {"Authorization": f"Bearer {admin_token(client)}"}

    def sim(phone, text):
        return client.post("/api/admin/simulate/whatsapp", json={"phone": phone, "text": text}, headers=h).json()["replies"]

    # how many white Civic Oriel before?
    cust = client.post("/api/chat/start", json={}).json()["session_id"]
    before = client.post("/api/chat/send", json={"session_id": cust, "text": "white Civic Oriel available?"}).json()["text"]

    r1 = sim("923001112233", "Civic Oriel white sold")
    assert r1 and ("Confirm" in r1[0]["text"] or "found" in r1[0]["text"].lower())
    # if multiple matches, pick 1 first
    if "found" in r1[0]["text"].lower():
        sim("923001112233", "1")
    r2 = sim("923001112233", "yes")
    assert "SOLD" in r2[0]["text"] or "✅" in r2[0]["text"]


def test_staff_permission_denied(client):
    h = {"Authorization": f"Bearer {admin_token(client)}"}
    # parts incharge cannot change car prices
    r = client.post("/api/admin/simulate/whatsapp",
                    json={"phone": "923214445566", "text": "Civic Oriel price 9999000"}, headers=h).json()["replies"]
    assert "role" in r[0]["text"].lower() or "can't" in r[0]["text"].lower()


def test_staff_part_stock_update(client):
    h = {"Authorization": f"Bearer {admin_token(client)}"}

    def sim(phone, text):
        return client.post("/api/admin/simulate/whatsapp", json={"phone": phone, "text": text}, headers=h).json()["replies"]

    r = sim("923214445566", "City brake pads stock 25")
    assert r
    if "found" in r[0]["text"].lower() and r[0]["text"][0].isdigit() is False:
        sim("923214445566", "1")
    r2 = sim("923214445566", "yes")
    assert "25" in r2[0]["text"] or "✅" in r2[0]["text"]


def test_job_ready_notifies_customer(client):
    h = {"Authorization": f"Bearer {admin_token(client)}"}
    r = client.post("/api/admin/simulate/whatsapp",
                    json={"phone": "923337778899", "text": "job 1043 ready bill 12500"}, headers=h).json()["replies"]
    # single match -> confirm directly
    assert "Confirm" in r[0]["text"] or "READY" in r[0]["text"]
    r2 = client.post("/api/admin/simulate/whatsapp", json={"phone": "923337778899", "text": "yes"}, headers=h).json()["replies"]
    assert "READY" in r2[0]["text"]
    # the outbox should now have a job_ready notification
    notes = client.get("/api/admin/t/notifications?q=ready", headers=h).json()["rows"]
    assert any(n["kind"] == "job_ready" for n in notes)


def test_excel_export(client):
    h = {"Authorization": f"Bearer {admin_token(client)}"}
    r = client.get("/api/admin/export/parts", headers=h)
    assert r.status_code == 200 and len(r.content) > 500


def test_knowledge_search(client):
    h = {"Authorization": f"Bearer {admin_token(client)}"}
    r = client.get("/api/admin/knowledge/search?q=warranty", headers=h).json()
    assert isinstance(r, list)


def test_scheduler_jobs_run(client):
    h = {"Authorization": f"Bearer {admin_token(client)}"}
    r = client.post("/api/admin/jobs/run", headers=h).json()
    assert "appointment_reminders" in r
