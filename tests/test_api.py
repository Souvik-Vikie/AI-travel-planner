import json

from fastapi.testclient import TestClient

from travel_agent.api import app

FORM = {"origin_city": "Delhi", "region": "india", "month": 12, "days": 5, "travellers": 2,
        "budget": 80000, "interests": ["beach"]}


def events(resp):
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def test_api_flow_reject_then_approve_then_pdf():
    c = TestClient(app)
    ev = events(c.post("/api/trips", json=FORM))
    assert any(e["type"] == "log" for e in ev)
    res = ev[-1]
    assert res["status"] == "awaiting_approval" and res["bookings"] is None
    tid = res["thread_id"]

    res = events(c.post(f"/api/trips/{tid}/decision", json={"decision": "reject", "feedback": "somewhere else"}))[-1]
    assert res["status"] == "awaiting_approval" and res["approval"]["destination"] != ev[-1]["approval"]["destination"]

    res = events(c.post(f"/api/trips/{tid}/decision", json={"decision": "approve"}))[-1]
    assert res["status"] == "done"
    assert c.get(res["pdf_url"]).headers["content-type"] == "application/pdf"
    assert c.post(f"/api/trips/{tid}/decision", json={"decision": "approve"}).status_code == 409


def test_api_rejects_bad_input():
    c = TestClient(app)
    assert c.post("/api/trips", json={**FORM, "origin_city": "Atlantis"}).status_code == 400
    assert c.post("/api/trips", json={**FORM, "month": 13}).status_code == 422
