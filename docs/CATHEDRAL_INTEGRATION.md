# Cathedral Formal Workbench Integration

AutoProof is an interactive proof-construction workspace. It is not the final authority for Cathedral Forge promotion decisions.

## Authority model

AutoProof distinguishes three classes of output:

| Output | Meaning | Evidence authority |
| --- | --- | --- |
| Model or deterministic suggestion | Candidate tactic or explanation | None |
| Demo plausibility | Interface-only heuristic when Lean is unavailable | None / E0 |
| Lean verification | Result returned by an installed Lean process | Formal-check evidence only |

A Lean-verified theorem proves the formal proposition presented to Lean. It does not by itself prove that the proposition faithfully represents the Cathedral runtime rule.

## Canonical pipeline

```text
Cathedral claim
  -> registered language / CGIR normalization
  -> generated Lean proposition
  -> AutoProof proof session
  -> pinned Lean checker
  -> runtime-correspondence gate
  -> Chronicle receipt
  -> Cathedral promotion decision
```

## Required invariants

- **INV-CF-PROOF-001**: Model-generated tactics, confidence values, explanations, and proof plans carry no formal authority until checked by Lean.
- **INV-CF-PROOF-002**: Demo mode may never produce `PASS`, `VERIFIED`, `PROVED`, or another evidence-bearing status.
- **INV-CF-PROOF-003**: A Lean theorem establishes the formal proposition only. Runtime correctness requires a separate correspondence gate.
- **INV-CF-COMP-001**: Adding the formal layer may tighten a prior decision but may not bypass or weaken an existing Cathedral gate.

## Chronicle receipt target

A future Chronicle adapter should record at least:

```json
{
  "claim_id": "CGIR-CLAIM-001",
  "session_id": "...",
  "runtime_rule_id": "...",
  "translation_version": "...",
  "lean_source_sha256": "...",
  "lean_version": "...",
  "toolchain_manifest_sha256": "...",
  "proof_status": "LEAN_VERIFIED",
  "runtime_correspondence": "PENDING",
  "external_reproduction": "PENDING",
  "authority": "NONE"
}
```

`authority` remains `NONE` until the applicable Cathedral promotion gate is satisfied.

## First recommended vertical slice

Use AutoProof to repair and document the surfaced CGIR bare-conjunction miss class:

1. preserve the minimal failing input,
2. display the runtime recognition result,
3. display the normalized CGIR,
4. generate the Lean proposition,
5. construct and verify the corrected theorem,
6. add the runtime regression fixture,
7. compare runtime and Lean verdicts,
8. anchor the result and limitations in Chronicle,
9. package the slice for independent reproduction.

## Current non-claims

- AutoProof is not an autonomous theorem prover.
- Demo mode is not a proof checker.
- Bounded counterexample failure is not proof.
- A successful Lean theorem is not end-to-end runtime verification.
- SQLite proof trees are not yet immutable provenance.
- The Lean environment is not yet pinned by this repository.
- Independent human reproduction is pending.
- Production authority is none.
