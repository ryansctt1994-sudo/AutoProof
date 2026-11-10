"""Proof-integrity tests for the Lean checker.

The invariant under test: ``verified`` only when the goal declaration is
accepted by Lean, the proof has no errors or ``sorry``, and its axioms are
standard. Negative cases here must never come back verified.
"""
import json

import pytest

from app import lean
from app.lean import check_proof, validate_signature, verify_source

STATEMENT = "n + 0 = n"
BINDERS = "(n : Nat)"


# --- statement and binder validation (no Lean needed) -------------------------

@pytest.mark.parametrize(
    "statement, binders",
    [
        ("True := by sorry", ""),
        ("True\ntheorem evil : False", ""),
        ("True -- hidden", ""),
        ("True /- hidden -/", ""),
        ("#eval 1", ""),
        ("", ""),
        ("True", "n : Nat"),
        ("True", "(n : Nat"),
        ("True", "(n : Nat)) : False := by"),
        ("sorry", ""),
    ],
)
def test_signature_injection_is_refused(statement, binders):
    with pytest.raises(ValueError):
        validate_signature(statement, binders)


def test_plain_signature_is_accepted():
    validate_signature(STATEMENT, BINDERS)
    validate_signature("∀ x y : Nat, x + y = y + x")


# --- execution gate -----------------------------------------------------------------

def test_verification_is_disabled_by_default():
    result = check_proof(STATEMENT, BINDERS, "simp")
    assert result["status"] == "disabled"
    assert result["ok"] is False


def test_empty_proof_is_refused_before_lean_runs(trusted, monkeypatch):
    called = []
    monkeypatch.setattr(lean, "_run", lambda *a, **k: called.append(a) or (0, "", ""))
    result = check_proof(STATEMENT, BINDERS, "   \n  ")
    assert result["status"] == "rejected"
    assert result["mode"] == "refused"
    assert called == []


def test_execution_commands_are_blocked_before_lean_runs(trusted, monkeypatch, lean_required):
    called = []
    monkeypatch.setattr(lean, "_run", lambda *a, **k: called.append(a) or (0, "", ""))
    result = check_proof(STATEMENT, BINDERS, "simp\n#eval IO.println \"hi\"")
    assert result["status"] == "rejected"
    assert "#eval" in result["diagnostics"]
    assert called == []


def test_missing_lean_reports_unverified(trusted, monkeypatch):
    monkeypatch.setenv("LEAN_COMMAND", "/nonexistent/lean-binary")
    result = check_proof(STATEMENT, BINDERS, "simp")
    assert result["status"] == "unverified"
    assert result["ok"] is False


# --- real Lean: positive and negative proofs ---------------------------------------

def test_correct_proof_is_verified(trusted, lean_required):
    result = check_proof(STATEMENT, BINDERS, "simp")
    assert result["status"] == "verified", result["diagnostics"]
    assert result["ok"] is True
    assert set(result["axioms"][lean.GOAL_NAME]) <= lean.STANDARD_AXIOMS
    assert result["statement_sha256"] and result["source_sha256"]


def test_proof_is_checked_against_the_stored_statement(trusted, lean_required):
    """The same tactic is judged against the statement the session holds, not any text the user sends."""
    assert check_proof("2 + 2 = 4", "", "decide")["status"] == "verified"
    assert check_proof("2 + 2 = 5", "", "decide")["status"] == "rejected"


def test_unrelated_valid_theorem_is_refused(trusted, lean_required):
    result = check_proof(STATEMENT, BINDERS, "theorem other : True := trivial")
    assert result["status"] == "rejected"
    assert result["ok"] is False


def test_sorry_is_incomplete(trusted, lean_required):
    result = check_proof(STATEMENT, BINDERS, "sorry")
    assert result["status"] == "incomplete"
    assert result["ok"] is False
    assert "sorryAx" in result["axioms"][lean.GOAL_NAME]


def test_native_decide_is_incomplete_because_of_its_axiom(trusted, lean_required):
    result = check_proof("2 + 2 = 4", "", "native_decide")
    assert result["status"] == "incomplete"
    assert any("native_decide" in axiom for axiom in result["axioms"][lean.GOAL_NAME])


def test_false_statement_is_rejected(trusted, lean_required):
    result = check_proof("2 + 2 = 5", "", "native_decide")
    assert result["status"] == "rejected"


def test_comment_cannot_swallow_the_audit(trusted, lean_required):
    result = check_proof(STATEMENT, BINDERS, "simp\n/- unterminated")
    assert result["status"] in {"rejected", "incomplete"}
    assert result["ok"] is False


def test_closing_the_block_early_is_refused(trusted, lean_required):
    result = check_proof(STATEMENT, BINDERS, "simp\nend")
    assert result["status"] == "rejected"


# --- verify_source: the lower-level verifier -------------------------------------

def test_missing_target_declaration_is_refused(trusted, lean_required):
    result = verify_source("theorem other : True := trivial\n", expected_name=lean.GOAL_NAME)
    assert result["status"] == "rejected"
    assert "No theorem named" in result["diagnostics"]


def test_target_named_only_in_a_comment_does_not_count(trusted, lean_required):
    result = verify_source("-- theorem autoproof_goal : True := trivial\ntheorem other : True := trivial\n")
    assert result["status"] == "rejected"


def test_nonstandard_user_axiom_is_incomplete(trusted, lean_required):
    source = "axiom cheat : False\ntheorem autoproof_goal : 1 = 2 := cheat.elim\n"
    result = verify_source(source)
    assert result["status"] == "incomplete"
    assert result["axioms"][lean.GOAL_NAME] == ["cheat"]


def test_declared_axiom_is_incomplete_even_when_unused(trusted, lean_required):
    result = verify_source("axiom unused : False\ntheorem autoproof_goal : True := trivial\n")
    assert result["status"] == "incomplete"
    assert "declares its own axiom" in result["diagnostics"]


def test_lean_error_wins_over_everything(trusted, lean_required):
    result = verify_source("theorem autoproof_goal : True := by\n  exact absurd\n")
    assert result["status"] == "rejected"


# --- fail-closed paths, exercised with a stubbed Lean process --------------------

def _stub_run(monkeypatch, returncode, messages):
    payload = "\n".join(json.dumps(item) for item in messages)
    monkeypatch.setattr(lean, "_run", lambda *a, **k: (returncode, payload, ""))


def test_missing_audit_message_is_incomplete_not_verified(trusted, monkeypatch, lean_required):
    _stub_run(monkeypatch, 0, [])
    result = verify_source("theorem autoproof_goal : True := trivial\n")
    assert result["status"] == "incomplete"
    assert "could not complete the axiom audit" in result["diagnostics"]


def test_audit_for_a_different_name_is_not_accepted(trusted, monkeypatch, lean_required):
    audit_line = 2
    _stub_run(monkeypatch, 0, [{"severity": "information", "pos": {"line": audit_line, "column": 0},
                                "data": "'other' depends on axioms: [propext]", "kind": ""}])
    result = verify_source("theorem autoproof_goal : True := trivial\n")
    assert result["status"] == "incomplete"


def test_nonzero_exit_without_messages_is_rejected(trusted, monkeypatch, lean_required):
    _stub_run(monkeypatch, 3, [])
    assert verify_source("theorem autoproof_goal : True := trivial\n")["status"] == "rejected"


def test_timeout_is_reported_as_timeout(trusted, monkeypatch, lean_required):
    monkeypatch.setattr(lean, "_run", lambda *a, **k: (None, "", ""))
    result = check_proof(STATEMENT, BINDERS, "simp")
    assert result["status"] == "timeout"
    assert result["ok"] is False


def test_proof_lines_map_back_to_the_editor(trusted, lean_required):
    """Errors inside the proof map to editor lines; errors on the declaration header map to None."""
    result = check_proof(STATEMENT, BINDERS, "intro_bad_tactic_here")
    proof_lines = [m["proof_line"] for m in result["messages"] if m["severity"] == "error"]
    assert 1 in proof_lines
    assert set(proof_lines) <= {1, None}
