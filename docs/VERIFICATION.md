# Verification and trust model

AutoProof's job is to tell you what Lean actually established. This document states the guarantee, the way it can be violated, and what is not yet covered.

## The invariant

A proof is reported **verified** only when all of the following hold:

1. The session's goal declaration (`autoproof_goal`) is present in the file that was checked.
2. Lean reports no errors for the file, including the declaration and the proof.
3. The proof contains no `sorry`.
4. The file declares no axioms of its own.
5. `#print axioms autoproof_goal` reports only `propext`, `Classical.choice` and `Quot.sound`.

Anything else is reported as one of: `incomplete` (accepted by Lean, but not a complete proof), `rejected` (errors, a missing target, or a refused submission), `timeout`, `unverified` (Lean not installed), or `disabled` (checking is turned off on this server).

## Binding the claim to the session

Users never submit a theorem name or a statement. When a session starts, the server stores the statement and binders. Each check builds the declaration itself:

```lean
theorem autoproof_goal (n : Nat) : n + 0 = n := by
  <the user's tactic block, indented under `by`>
```

Consequences:

- A proof of some other statement cannot be checked against this session's goal. It fails to typecheck.
- A valid theorem submitted in place of a tactic block is refused, because it is not a tactic.
- A proof cannot close the block early or add commands. Every submitted line is indented under `by`, and the audit line is appended after the declaration. A comment that swallows the audit line makes the audit fail, and a failed audit is never verified.
- The stored statement cannot be changed. The database rejects any `UPDATE` on `sessions`.
- Statements and binders containing `:=`, comments, line breaks, or command keywords are refused.

## Running Lean on submitted text

Lean elaborates the file it is given, and that can run arbitrary code (`#eval`, macros, `run_cmd`, `native_decide` and others). Keyword blocking is not a sandbox, so:

- Verification is **off by default** (`AUTOPROOF_TRUSTED_LEAN` unset). In that state no submitted text is executed, and every result is `disabled`.
- With `AUTOPROOF_TRUSTED_LEAN=1`, Lean runs, with a timeout, a memory cap, its own process group, and a file-size limit. Execution commands are still blocked as defence in depth.
- Enable it only on a machine where executing the submitted text is acceptable. A public deployment needs an isolated runner (for example a container without network or secrets, one job per submission) before this flag is set.

## Test coverage of the invariant

`backend/tests/test_lean.py` and `backend/tests/test_api.py` cover:

| Case | Expected status |
| --- | --- |
| Correct tactic proof | `verified` |
| Empty proof | `rejected` (refused before Lean runs) |
| Unrelated valid theorem submitted as the proof | `rejected` |
| Target declaration missing from the checked source | `rejected` |
| Target named only inside a comment | `rejected` |
| `sorry` | `incomplete` |
| User-declared axiom used by the proof | `incomplete` |
| Axioms declared but unused | `incomplete` |
| `native_decide` on a true closed statement | `incomplete` |
| False statement | `rejected` |
| `#eval`, `#exit`, `run_cmd` | `rejected` before Lean runs |
| Comment that swallows the audit line | not `verified` |
| Lean exits non-zero with no error messages | `rejected` |
| Audit message missing or naming another declaration | `incomplete` |
| Lean times out | `timeout` |
| Lean not installed | `unverified` |
| Checking turned off | `disabled` |
| Statement containing `:=`, comments or newlines | refused (422) |

The Lean-dependent tests run in CI with `AUTOPROOF_REQUIRE_LEAN=1`, so a missing Lean fails the run instead of being skipped.

## Known limitations

- **Mathlib is not available** unless `LEAN_PROJECT_DIR` points at a project that provides it. Statements over Mathlib types are not in the tested set.
- **Only tactic proofs** of a single goal are supported. Multi-declaration files, `calc` blocks spanning several declarations, and mutual definitions are not.
- **Diagnostics are Lean's own.** Errors at the declaration header map to no editor line, and the UI says so.
- **Counterexample search** is a bounded, exhaustive check over natural numbers. A search that finds nothing is evidence, not a proof. Statements outside its fragment are reported as "not searched".
- **Suggestions** from the optional language model are advisory. They are never used to decide a verdict.
- **Feedback** is stored per session and is not yet used to rank suggestions.
- **Docker images** were not built in the environment where this document was written (the registry rate-limited the base images). CI builds them; see the pull request for the result.
