import pytest
from fastapi.testclient import TestClient

from app import config, main, store
from app.llm import FakeLLM


@pytest.fixture
def client(kb, monkeypatch):
    monkeypatch.setattr(main, "kb", kb)
    monkeypatch.setattr(main, "llm", FakeLLM([]))
    monkeypatch.setattr(config, "ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setattr(main, "limiter", store.RateLimiter(100, 60))
    monkeypatch.setattr(main, "global_limiter", store.RateLimiter(100, 60))
    return TestClient(main.app)


def test_chat_endpoint(client, monkeypatch):
    main.llm.responses.append({"answer": "Out-patient is $18,995.", "citations": [1], "status": "answered",
                               "wants_quote": False, "medical_topic": False})
    r = client.post("/api/chat", json={"message": "adrenal price", "history": []})
    assert r.status_code == 200 and r.json()["sources"]


def test_ui_and_health(client):
    assert "PlacidWay Knowledge Chatbot" in client.get("/").text
    assert client.get("/api/health").json()["ok"] is True
    assert client.get("/api/kb").json()["chunks"] > 0


def test_lead_validation_and_storage(client):
    bad = client.post("/api/lead", json={"name": "A", "email": "nope", "consent": True})
    assert bad.status_code == 422
    no_consent = client.post("/api/lead", json={"name": "A", "email": "a@b.com", "consent": False})
    assert no_consent.status_code == 422
    ok = client.post("/api/lead", json={"name": "Ana", "email": "ana@example.com", "question": "quote", "consent": True})
    assert ok.status_code == 200 and store.read("leads")[-1]["email"] == "ana@example.com"


def test_admin_routes_need_token(client):
    assert client.post("/api/refresh").status_code == 401
    assert client.get("/api/admin/gaps").status_code == 401
    r = client.get("/api/admin/gaps", headers={"Authorization": "Bearer test-admin-token"})
    assert r.status_code == 200


def test_rate_limit(client, monkeypatch):
    monkeypatch.setattr(main, "limiter", store.RateLimiter(1, 60))
    main.llm.responses.append({"answer": "x $18,995", "citations": [1], "status": "answered",
                               "wants_quote": False, "medical_topic": False})
    client.post("/api/chat", json={"message": "adrenal price"})
    r = client.post("/api/chat", json={"message": "adrenal price"})
    assert r.status_code == 429


def test_vapi_tool_is_off_without_secret_and_checks_it(client, monkeypatch):
    monkeypatch.setattr(config, "VAPI_TOOL_SECRET", "")
    assert client.post("/api/vapi/tool", json={}).status_code == 503
    monkeypatch.setattr(config, "VAPI_TOOL_SECRET", "s3cret-value")
    assert client.post("/api/vapi/tool", json={}, headers={"x-vapi-secret": "wrong"}).status_code == 401


def test_global_limit_applies_even_with_fake_forwarded_for(client, monkeypatch):
    monkeypatch.setattr(main, "global_limiter", store.RateLimiter(1, 60))
    main.llm.responses.append({"answer": "x $18,995", "citations": [1], "status": "answered",
                               "wants_quote": False, "medical_topic": False})
    client.post("/api/chat", json={"message": "adrenal price"}, headers={"x-forwarded-for": "1.1.1.1"})
    r = client.post("/api/chat", json={"message": "adrenal price"}, headers={"x-forwarded-for": "2.2.2.2"})
    assert r.status_code == 429


def test_vapi_tool_format(client, monkeypatch):
    monkeypatch.setattr(config, "VAPI_TOOL_SECRET", "s3cret-value")
    main.llm.responses.append({"answer": "The out-patient program is $18,995.", "citations": [1],
                               "status": "answered", "wants_quote": False, "medical_topic": False})
    payload = {"message": {"type": "tool-calls", "toolCallList": [
        {"id": "call_1", "type": "function", "function": {"name": "ask_placidway",
                                                          "arguments": {"question": "adrenal price"}}}]}}
    r = client.post("/api/vapi/tool", json=payload, headers={"x-vapi-secret": "s3cret-value"})
    body = r.json()
    assert body["results"][0]["toolCallId"] == "call_1"
    assert "$18,995" in body["results"][0]["result"]
