# AutoProof Formal-Assurance Invariants

- **INV-CF-PROOF-001**: Model-generated tactics, explanations, confidence scores, and proof plans have no formal authority until checked by Lean.
- **INV-CF-PROOF-002**: Demo mode may never emit an evidence-bearing success state.
- **INV-CF-PROOF-003**: A Lean theorem establishes its formal proposition only; runtime correctness requires a separate correspondence gate.
- **INV-CF-COMP-001**: Adding a formal-assurance layer may tighten a prior decision but may not bypass, weaken, or silently replace an existing governance gate.
- **INV-CF-AUTH-001**: No component may be the sole authority for the claim that its own output or state is valid.
- **INV-CF-COVERAGE-001**: Passing the known corpus demonstrates conformity to that corpus, not completeness over the possible behavior space.

## Current enforcement

`INV-CF-PROOF-002` is enforced in `backend/app/lean.py`: when Lean is unavailable, demo mode returns `ok: false`, `verified: false`, `authority: NONE`, and `evidence_level: E0`.

The remaining invariants are architectural requirements for the next integration stages: pinned toolchains, Chronicle anchoring, runtime correspondence, replay, and independent witness reproduction.
