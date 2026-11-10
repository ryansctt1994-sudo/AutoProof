"""AP-MATH-001: candidate identity is stable and tampering fails closed.

The positive fixture is deliberately a *mocked recorded verdict*. The tests
exercise candidate binding only; they do not establish a real Lean run or
cross-repository verification.
"""
from copy import deepcopy

import pytest

from app.handoff import HandoffRefused, export_candidate
from app.lean import CHECKER_VERSION, GOAL_NAME, build_source, sha256, signature_text


def fixture():
    statement, binders, proof = "n + 0 = n", "(n : Nat)", "simp"
    source = build_source(statement, binders, proof)
    session = {"id": "session-123", "statement": statement, "binders": binders}
    node = {
        "id": "attempt-123",
        "session_id": session["id"],
        "kind": "tactic",
        "status": "complete",
        "payload": {
            "ok": True, "status": "verified", "mode": "lean",
            "expected": GOAL_NAME, "checker_version": CHECKER_VERSION,
            "lean_version": "Lean (fixture only)", "axioms": {GOAL_NAME: []},
            "statement_sha256": sha256(signature_text(statement, binders)),
            "proof_sha256": sha256(proof), "source_sha256": sha256(source),
            "proof": proof,
        },
    }
    return session, node


def test_handoff_is_deterministic_bound_and_non_authoritative():
    session, node = fixture()
    one = export_candidate(session, node)
    two = export_candidate(deepcopy(session), deepcopy(node))
    assert one == two
    assert one["candidate"]["generated_lean_source"] == build_source(session["statement"], session["binders"], "simp")
    assert one["candidate"]["session_id"] == session["id"]
    assert one["candidate"]["proof_sha256"] == sha256("simp")
    assert one["candidate_sha256"].startswith("sha256:")
    assert one["authority"] == "NONE"
    assert one["mathbuild_acceptance"] == "NOT_EVALUATED"
    assert one["independent_reproduction"] is False


@pytest.mark.parametrize(("path", "value"), [
    (("session", "statement"), "1 = 2"),
    (("session", "binders"), "(m : Nat)"),
    (("node", "session_id"), "other-session"),
    (("node", "status"), "pending"),
    (("payload", "proof"), "sorry"),
    (("payload", "proof_sha256"), "0" * 64),
    (("payload", "source_sha256"), "0" * 64),
    (("payload", "statement_sha256"), "0" * 64),
    (("payload", "expected"), "other_goal"),
    (("payload", "status"), "incomplete"),
    (("payload", "ok"), False),
    (("payload", "mode"), "demo"),
    (("payload", "lean_version"), None),
    (("payload", "checker_version"), ""),
    (("payload", "axioms"), {}),
    (("payload", "axioms"), {GOAL_NAME: ["user_axiom"]}),
])
def test_tampered_record_refused(path, value):
    session, node = fixture()
    if path[0] == "session":
        session[path[1]] = value
    elif path[0] == "node":
        node[path[1]] = value
    else:
        node["payload"][path[1]] = value
    with pytest.raises(HandoffRefused):
        export_candidate(session, node)


def test_missing_record_refused():
    with pytest.raises(HandoffRefused):
        export_candidate({}, {})
