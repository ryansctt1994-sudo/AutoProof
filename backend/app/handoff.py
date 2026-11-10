"""Advisory, identity-bound proof handoff. Never a MathBuild acceptance receipt.

This module checks consistency with an AutoProof session record. It does not
re-run Lean, authenticate the recording service, or establish canonical
inclusion in MathBuild. The receiving verifier must re-check the proof under
the pinned MathBuild Lake environment and obtain its own evidence.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .lean import GOAL_NAME, STANDARD_AXIOMS, build_source, sha256, signature_text

SCHEMA_VERSION = "autoproof-candidate/1"


class HandoffRefused(ValueError):
    """The stored proof attempt cannot be exported as a verified candidate."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise HandoffRefused(reason)


def _digest(data: dict[str, Any]) -> str:
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def export_candidate(session: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    """Bind one *reported* verified attempt to its immutable stored goal.

    This is not independently signed, not a sandbox guarantee, and never
    authorizes changes to canonical source, build environment or policy.
    """
    _require(isinstance(session, dict) and isinstance(node, dict), "Invalid record type.")
    _require(node.get("session_id") == session.get("id"), "Session and attempt differ.")
    _require(isinstance(session.get("id"), str) and bool(session["id"]), "Session ID missing.")
    _require(isinstance(node.get("id"), str) and bool(node["id"]), "Attempt ID missing.")
    _require(node.get("kind") == "tactic", "Only tactic attempts may be exported.")
    _require(node.get("status") == "complete", "Attempt was not recorded complete.")

    payload = node.get("payload")
    _require(isinstance(payload, dict), "Proof result missing.")
    statement, binders, proof = session.get("statement"), session.get("binders"), payload.get("proof")
    _require(all(isinstance(x, str) for x in (statement, binders, proof)), "Source fields missing.")
    _require(bool(proof.strip()), "Proof is empty.")

    _require(payload.get("ok") is True and payload.get("status") == "verified"
             and payload.get("mode") == "lean", "No verified Lean result.")
    _require(payload.get("expected") == GOAL_NAME, "Verifier checked another goal.")
    _require(isinstance(payload.get("checker_version"), str) and bool(payload["checker_version"]),
             "Checker identity missing.")
    _require(isinstance(payload.get("lean_version"), str) and bool(payload["lean_version"]),
             "Lean identity missing.")

    source = build_source(statement, binders, proof)
    signature = signature_text(statement, binders)
    _require(payload.get("statement_sha256") == sha256(signature), "Statement digest mismatch.")
    _require(payload.get("proof_sha256") == sha256(proof), "Proof digest mismatch.")
    _require(payload.get("source_sha256") == sha256(source), "Checked-source digest mismatch.")

    axiom_map = payload.get("axioms")
    _require(isinstance(axiom_map, dict) and set(axiom_map) == {GOAL_NAME},
             "Axiom audit missing or ambiguous.")
    axiom_list = axiom_map[GOAL_NAME]
    _require(isinstance(axiom_list, list) and all(isinstance(a, str) for a in axiom_list)
             and set(axiom_list) <= STANDARD_AXIOMS, "Non-standard or invalid axioms.")

    candidate = {
        "schema_version": SCHEMA_VERSION,
        "source_system": "AutoProof",
        "session_id": session["id"],
        "attempt_id": node["id"],
        "goal_name": GOAL_NAME,
        "statement": statement,
        "binders": binders,
        "statement_sha256": sha256(signature),
        "proof_text": proof,
        "proof_sha256": sha256(proof),
        "generated_lean_source": source,
        "source_sha256": sha256(source),
        "checker_version": payload["checker_version"],
        "lean_version_reported": payload["lean_version"],
        "axioms_reported": sorted(axiom_list),
        "verdict_reported": "verified",
    }
    return {
        "candidate": candidate,
        "candidate_sha256": _digest(candidate),
        "authority": "NONE",
        "mathbuild_acceptance": "NOT_EVALUATED",
        "independent_reproduction": False,
    }
