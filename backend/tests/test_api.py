"""HTTP-level tests: session integrity, status mapping, and the feedback and tree contracts."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import store
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def session(client):
    response = client.post("/api/sessions", json={"statement": "n + 0 = n", "binders": "(n : Nat)"})
    assert response.status_code == 201
    return response.json()


def test_health_reports_verification_off_by_default(client):
    body = client.get("/api/health").json()
    assert body["verification"] == {"enabled": False, "lean_version": None}


def test_session_exposes_the_fixed_signature(client, session):
    assert session["signature"] == "theorem autoproof_goal (n : Nat) : n + 0 = n"
    assert session["name"] == "autoproof_goal"
    read_back = client.get(f"/api/sessions/{session['id']}").json()
    assert read_back["statement"] == "n + 0 = n"


@pytest.mark.parametrize("payload", [
    {"statement": "True := by sorry"},
    {"statement": "True", "binders": "n : Nat"},
    {"statement": "   "},
])
def test_invalid_sessions_are_refused(client, payload):
    assert client.post("/api/sessions", json=payload).status_code == 422


def test_unknown_session_returns_404_everywhere(client):
    assert client.get("/api/sessions/does-not-exist").status_code == 404
    assert client.get("/api/sessions/does-not-exist/tree").status_code == 404
    assert client.post("/api/apply-tactic", json={"session_id": "nope", "tactic": "simp", "proof": "simp"}).status_code == 404
    assert client.post("/api/feedback", json={"session_id": "nope", "tactic": "simp", "verdict": "approved"}).status_code == 404
    assert client.post("/api/suggest", json={"session_id": "nope"}).status_code == 404
    assert client.post("/api/counterexample", json={"session_id": "nope"}).status_code == 404


def test_apply_tactic_with_verification_off_is_pending_not_verified(client, session):
    body = client.post("/api/apply-tactic", json={"session_id": session["id"], "tactic": "simp", "proof": "simp"}).json()
    assert body["status"] == "disabled"
    assert body["ok"] is False
    tree = client.get(f"/api/sessions/{session['id']}/tree").json()
    statuses = {node["label"]: node["status"] for node in tree["nodes"] if node["kind"] == "tactic"}
    assert statuses == {"simp": "pending"}


def test_session_statement_cannot_be_changed(session):
    with store.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE sessions SET statement = 'False' WHERE id = ?", (session["id"],))
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE sessions SET binders = '' WHERE id = ?", (session["id"],))
    assert store.get_session(session["id"])["statement"] == "n + 0 = n"


def test_request_cannot_smuggle_a_different_statement(client, session):
    """Extra fields are ignored: the goal always comes from the stored session."""
    client.post("/api/apply-tactic", json={
        "session_id": session["id"], "tactic": "simp", "proof": "simp",
        "statement": "1 = 2", "theorem": "autoproof_goal : 1 = 2",
    })
    assert store.get_session(session["id"])["statement"] == "n + 0 = n"
    tree = client.get(f"/api/sessions/{session['id']}/tree").json()
    assert all("1 = 2" not in str(node["payload"]) for node in tree["nodes"])


def test_feedback_edited_requires_the_edited_proof(client, session):
    response = client.post("/api/feedback", json={"session_id": session["id"], "tactic": "simp", "verdict": "edited"})
    assert response.status_code == 422


def test_feedback_is_stored_and_described_honestly(client, session):
    response = client.post("/api/feedback", json={
        "session_id": session["id"], "tactic": "simp", "verdict": "approved", "note": "looks right",
    })
    assert response.status_code == 201
    assert "not yet used to rank" in response.json()["message"]
    with store.connect() as conn:
        rows = conn.execute("SELECT verdict, note FROM feedback WHERE session_id = ?", (session["id"],)).fetchall()
    assert [tuple(row) for row in rows] == [("approved", "looks right")]


def test_counterexample_uses_the_session_statement(client, session):
    body = client.post("/api/counterexample", json={"session_id": session["id"]}).json()
    assert body["supported"] is True and body["found"] is False
    assert "evidence, not a proof" in body["explanation"]


def test_counterexample_for_raw_statement_finds_a_witness(client):
    body = client.post("/api/counterexample", json={"statement": "n + 1 = n"}).json()
    assert body["found"] is True
    assert body["assignment"] == {"n": 0}


def test_counterexample_reports_unsupported_statements(client):
    body = client.post("/api/counterexample", json={"statement": "∃ n, n = 3"}).json()
    assert body["supported"] is False
    assert body["found"] is False
    assert body["explanation"].startswith("Not searched")


def test_suggestions_are_marked_advisory(client, session):
    body = client.post("/api/suggest", json={"session_id": session["id"]}).json()
    assert body["advisory"] is True
    assert body["tactics"]


def test_tree_lists_nodes_in_creation_order(client, session):
    for label in ["first", "second"]:
        client.post("/api/apply-tactic", json={"session_id": session["id"], "tactic": label, "proof": "simp"})
    tree = client.get(f"/api/sessions/{session['id']}/tree").json()
    labels = [node["label"] for node in tree["nodes"]]
    assert labels == ["Initial goal", "first", "second"]
    assert tree["nodes"][2]["parent_id"] == tree["nodes"][1]["id"]


def test_verified_proof_is_recorded_complete(client, session, trusted, lean_required):
    body = client.post("/api/apply-tactic", json={"session_id": session["id"], "tactic": "simp", "proof": "simp"}).json()
    assert body["status"] == "verified"
    tree = client.get(f"/api/sessions/{session['id']}/tree").json()
    node = next(n for n in tree["nodes"] if n["id"] == body["node_id"])
    assert node["status"] == "complete"
    assert node["payload"]["proof"] == "simp"


def test_incomplete_and_rejected_proofs_are_recorded_as_errors(client, session, trusted, lean_required):
    sorry = client.post("/api/apply-tactic", json={"session_id": session["id"], "tactic": "sorry", "proof": "sorry"}).json()
    wrong = client.post("/api/apply-tactic", json={"session_id": session["id"], "tactic": "bad", "proof": "exact absurd"}).json()
    assert sorry["status"] == "incomplete"
    assert wrong["status"] == "rejected"
    tree = client.get(f"/api/sessions/{session['id']}/tree").json()
    statuses = {n["id"]: n["status"] for n in tree["nodes"]}
    assert statuses[sorry["node_id"]] == "error"
    assert statuses[wrong["node_id"]] == "error"


def test_frontend_cannot_bypass_the_goal_by_sending_a_theorem(client, session, trusted, lean_required):
    """A proof for a different statement is still checked against the stored goal and fails."""
    body = client.post("/api/apply-tactic", json={
        "session_id": session["id"], "tactic": "x", "proof": "exact (by decide : 2 + 2 = 5)",
    }).json()
    assert body["status"] == "rejected"
