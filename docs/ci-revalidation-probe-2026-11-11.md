# Non-production CI revalidation probe — 2026-11-11

This file triggers an isolated push-event CI run on the candidate AutoProof source.

Source baseline before this documentation-only commit:
5f60bcee05c13c27923bf35bbc333b426ca91549

Purpose:
- Re-execute the candidate's existing Python/Lean, frontend, browser and Docker-build CI jobs.
- Observe Actions trigger eligibility and gather a fresh run ID and actual job conclusions.
- Keep the proof-handoff candidate, its historical receipts and main branch unchanged.

This probe is not evidence of an independent reproduction, a proof of theorem validity, a release, an authorization grant, or a production-promotion decision.
The diagnostic branch must not be merged.
