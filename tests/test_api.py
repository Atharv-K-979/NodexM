import json
import time

import pytest
from fastapi.testclient import TestClient

from nodexm import jobs, pipeline
from nodexm.config import settings
from nodexm.routes import runs as runs_routes

from . import fakes


@pytest.fixture()
def client(monkeypatch):
    object.__setattr__(settings, "gemini_api_key", "server-key")
    monkeypatch.setattr(pipeline, "run_searcher", fakes.fake_search)
    monkeypatch.setattr(pipeline, "read", fakes.fake_read)
    monkeypatch.setattr(pipeline, "search_available", lambda key: True)
    monkeypatch.setattr(jobs, "LLMClient", lambda key, usage: _llm(usage))
    monkeypatch.setattr(runs_routes, "LLMClient", lambda key: fakes.FakeLLM(
        report="## Summary\nRewritten: SMEs pay compliance costs of 6,000 euros per high-risk system per year [1]."))
    from main import app

    with TestClient(app) as c:
        yield c
    object.__setattr__(settings, "gemini_api_key", "")


def _llm(usage):
    llm = fakes.FakeLLM()
    llm.usage = usage
    return llm


_n = 0


def signup(client) -> dict:
    global _n
    _n += 1
    r = client.post("/api/auth/register", json={"email": f"api{_n}@test.dev", "password": "longpassword", "name": "Ana"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def wait_done(client, headers, run_id, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/api/runs/{run_id}", headers=headers).json()
        if run["status"] not in ("queued", "running"):
            return run
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def test_auth_flow(client):
    assert client.get("/api/threads").status_code == 401
    assert client.get("/api/threads", headers={"Authorization": "Bearer nope"}).status_code == 401
    headers = signup(client)
    me = client.get("/api/auth/me", headers=headers).json()
    assert me["user"]["name"] == "Ana" and me["allowance"]["quick_left"] == 2
    email = me["user"]["email"]
    assert client.post("/api/auth/register", json={"email": email, "password": "longpassword"}).status_code == 409
    assert client.post("/api/auth/login", json={"email": email, "password": "wrong-password"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": email.upper(), "password": "longpassword"}).status_code == 200


def test_keys_are_masked_and_switch_to_own_key_mode(client):
    headers = signup(client)
    r = client.put("/api/settings/keys", headers=headers, json={"gemini_api_key": "AIzaSyExampleKey1234"})
    body = r.json()
    assert body["user"]["keys"]["gemini"] == "AIza…1234"
    assert body["allowance"]["own_keys"] and body["allowance"]["unlimited"]
    r = client.put("/api/settings/keys", headers=headers, json={"gemini_api_key": ""})
    assert r.json()["user"]["keys"]["gemini"] == ""


def test_ask_stream_edit_export_and_isolation(client):
    headers = signup(client)
    r = client.post("/api/threads", headers=headers, json={"question": "What do SMEs pay under the AI Act?",
                                                           "mode": "quick"})
    assert r.status_code == 201, r.text
    thread_id, run_id = r.json()["thread_id"], r.json()["run_id"]

    events = []
    with client.stream("GET", f"/api/runs/{run_id}/events", headers=headers) as s:
        for line in s.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
                if events[-1]["type"] == "done":
                    break
    assert any(e["type"] == "trace" and e["data"]["node"] == "verifier" for e in events)

    run = wait_done(client, headers, run_id)
    assert run["status"] == "completed", run["error"]
    assert run["draft"]["content_md"].startswith("## Summary")
    disputed = [c for c in run["claims"] if c["status"] == "disputed"]
    assert disputed and disputed[0]["evidence"][0]["quote"]
    assert {s["number"] for s in run["sources"]} == {1, 2, 3, 4}

    thread = client.get(f"/api/threads/{thread_id}", headers=headers).json()
    assert thread["title"].startswith("What do SMEs pay") and len(thread["runs"]) == 1

    # someone else cannot see it
    other = signup(client)
    assert client.get(f"/api/threads/{thread_id}", headers=other).status_code == 404
    assert client.get(f"/api/runs/{run_id}", headers=other).status_code == 404
    assert client.get("/api/threads", headers=other).json() == []

    draft = client.post(f"/api/runs/{run_id}/draft/approve", headers=headers).json()
    assert draft["status"] == "approved"
    draft = client.patch(f"/api/runs/{run_id}/draft", headers=headers,
                         json={"content_md": run["draft"]["content_md"] + "\nEdited."}).json()
    assert draft["status"] == "pending_review" and draft["version"] == 2
    draft = client.post(f"/api/runs/{run_id}/draft/rewrite-section", headers=headers,
                        json={"heading": "Summary", "instruction": "shorter"}).json()
    assert "Rewritten:" in draft["content_md"] and "Where sources disagree" in draft["content_md"]

    export = client.get(f"/api/runs/{run_id}/export?format=md", headers=headers)
    assert export.status_code == 200 and "attachment" in export.headers["content-disposition"]

    # follow-up in the same thread
    r = client.post(f"/api/threads/{thread_id}/runs", headers=headers, json={"question": "And for large firms?",
                                                                             "mode": "quick"})
    assert r.status_code == 201
    wait_done(client, headers, r.json()["id"])
    assert len(client.get(f"/api/threads/{thread_id}", headers=headers).json()["runs"]) == 2

    # free-run allowance: 2 quick used, a third quick is forced to deep, then the daily cap applies
    allowance = client.get("/api/usage", headers=headers).json()["allowance"]
    assert allowance["quick_left"] == 0 and allowance["deep_left"] == 1

    assert client.delete(f"/api/threads/{thread_id}", headers=headers).status_code == 204
    assert client.get(f"/api/runs/{run_id}", headers=headers).status_code == 404


def test_watch_lifecycle(client):
    headers = signup(client)
    w = client.post("/api/watches", headers=headers, json={"topic": "AI Act SME costs", "cadence": "weekly",
                                                           "mode": "quick"}).json()
    assert w["next_run_at"] and w["cadence_label"].startswith("Every Monday")
    run = client.post(f"/api/watches/{w['id']}/run", headers=headers).json()
    wait_done(client, headers, run["id"])
    detail = client.get(f"/api/watches/{w['id']}", headers=headers).json()
    assert detail["thread_id"] and detail["ledger"] and detail["runs"][0]["status"] == "completed"
    paused = client.patch(f"/api/watches/{w['id']}", headers=headers, json={"active": False}).json()
    assert paused["active"] is False and paused["next_run_at"] is None
    assert client.delete(f"/api/watches/{w['id']}", headers=headers).status_code == 204
    # the answer stays visible in its thread
    assert client.get(f"/api/threads/{detail['thread_id']}", headers=headers).json()["runs"]


def test_change_feedback_and_feed(client):
    from sqlmodel import Session

    from nodexm.db import engine
    from nodexm.models import ChangeEvent, LedgerClaim

    headers = signup(client)
    w = client.post("/api/watches", headers=headers, json={"topic": "Acme pricing", "cadence": "manual"}).json()
    run = client.post(f"/api/watches/{w['id']}/run", headers=headers).json()
    wait_done(client, headers, run["id"])
    with Session(engine) as s:
        lc = LedgerClaim(watch_id=w["id"], text="Acme Pro costs $49", status="disputed", first_seen_run=run["id"],
                         last_seen_run=run["id"])
        s.add(lc)
        s.flush()
        e = ChangeEvent(watch_id=w["id"], run_id=run["id"], ledger_claim_id=lc.id, kind="status_changed",
                        from_status="agreed", to_status="disputed", claim_text=lc.text, confirmation="new_source",
                        evidence_before=[{"domain": "acme.com", "quote": "$49", "stance": "supports"}],
                        evidence_after=[{"domain": "x.example", "quote": "$59", "stance": "contradicts"}])
        s.add(e)
        s.commit()
        change_id = e.id

    feed = client.get("/api/watches/changes", headers=headers).json()
    assert feed["changes"][0]["watch_topic"] == "Acme pricing"
    assert feed["changes"][0]["before"][0]["domain"] == "acme.com"
    assert feed["quality"]["precision"] is None

    r = client.post(f"/api/watches/{w['id']}/changes/{change_id}/feedback", headers=headers, json={"verdict": "real"})
    assert r.status_code == 200 and r.json()["quality"] == {**r.json()["quality"], "reviewed": 1, "precision": 1.0}
    assert client.get(f"/api/watches/{w['id']}", headers=headers).json()["changes"][0]["feedback"] == "real"

    other = signup(client)
    assert client.post(f"/api/watches/{w['id']}/changes/{change_id}/feedback", headers=other,
                       json={"verdict": "noise"}).status_code == 404
    assert client.get("/api/watches/changes", headers=other).json()["changes"] == []
