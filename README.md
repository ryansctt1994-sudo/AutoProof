# AutoProof

**A human-in-the-loop theorem proving workspace for Lean 4.** You write tactic proofs for a fixed goal. AutoProof asks Lean to check them, reports exactly what Lean established, and keeps the attempts in a proof tree. Suggestions and counterexample searches are aids, not verdicts.

![AutoProof interface](docs/demo.svg)

> The demo image predates v0.2 and does not show the current verdict panel.

## What it does

- **Fixed goals.** A session stores its statement server-side. You write only the tactic proof. See [docs/VERIFICATION.md](docs/VERIFICATION.md) for the invariant.
- **Honest verdicts.** `verified`, `incomplete` (for example `sorry`, or an extra axiom), `rejected`, `timeout`, `not checked`, or `checking turned off`. Only `verified` means proved.
- **Axiom audit.** Every verified proof is checked with `#print axioms`, and the axioms it depends on are shown.
- **Proof tree.** Each attempt is recorded in the session, with its status.
- **Counterexample search.** A bounded, exhaustive check over natural numbers. It reports "not searched" for statements outside its fragment.
- **Feedback.** Approve, edit, or reject a step. Stored per session; not yet used to rank suggestions.
- **Optional language model.** Any OpenAI-compatible endpoint can propose tactics. Suggestions are advisory only.

## Important: Lean checking is off by default

Lean runs code while it elaborates the file it is given, so AutoProof does **not** run Lean on submitted text unless you opt in:

```bash
AUTOPROOF_TRUSTED_LEAN=1 uvicorn app.main:app --port 8000   # from backend/, Lean 4 on PATH
```

Without the flag every proof is reported as "checking turned off". Enable it only on a machine where executing submitted text is acceptable. See [docs/VERIFICATION.md](docs/VERIFICATION.md).

## Quick start

### Local development

```bash
# terminal 1: API
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
AUTOPROOF_TRUSTED_LEAN=1 uvicorn app.main:app --reload --port 8000   # omit the flag to keep checking off

# terminal 2: web
cd frontend
npm ci
npm run dev        # http://localhost:3000
```

Lean 4 must be on `PATH` for verification. Install it with [elan](https://github.com/leanprover/elan) or from the [Lean releases](https://lean-lang.org/).

### With Docker

```bash
docker compose up --build
```

The API image does not contain Lean, so verification reports "not checked" there. Docker images are built by CI; see the pull request for the current result.

## Tests

```bash
cd backend
pip install -r requirements-dev.txt
AUTOPROOF_REQUIRE_LEAN=1 python -m pytest -q        # needs Lean 4 on PATH; omit the variable to skip Lean tests

cd ../frontend
npm run lint && npx tsc --noEmit && npm run build
# browser workflow, with the API and web app running:
AUTOPROOF_EXPECT_VERIFICATION=on node e2e/workflow.mjs   # use "off" if the API runs without AUTOPROOF_TRUSTED_LEAN
```

CI runs all of these on every push and pull request (`.github/workflows/ci.yml`).

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | Service status and whether verification is enabled |
| `POST /api/sessions` | Create a proof session with a fixed `statement` and optional `binders` |
| `GET /api/sessions/{id}` | Read a session |
| `POST /api/apply-tactic` | Check a tactic proof against the session's goal (`session_id`, `tactic` label, `proof`) |
| `GET /api/sessions/{id}/tree` | Proof tree, in creation order |
| `POST /api/suggest` | Advisory tactic suggestions |
| `POST /api/feedback` | Record an approval, edit, or rejection |
| `POST /api/counterexample` | Bounded counterexample search |

Unknown sessions return 404. Interactive API docs are at `http://localhost:8000/docs`.

**Breaking change in 0.2.0:** sessions take `statement` and `binders` instead of `theorem`, and `apply-tactic` takes `proof` instead of `code`. The API no longer accepts a full Lean file.

## Configuration

See [`.env.example`](.env.example). The key settings are `AUTOPROOF_TRUSTED_LEAN`, `LEAN_COMMAND`, `LEAN_TIMEOUT`, `LEAN_PROJECT_DIR` (to use Mathlib), `DATABASE_PATH`, and `AUTOPROOF_CORS_ORIGINS`.

## Feedback export

Feedback is stored in the database. Export it for fine-tuning with:

```bash
python models/export_feedback.py --database backend/autoproof.db --output data/feedback.jsonl
```

Exported data can contain private proofs. Review it before sharing, and keep `data/` out of version control (it is git-ignored).

## Architecture

```text
Next.js UI ──► FastAPI ──► Lean 4 CLI   (only when AUTOPROOF_TRUSTED_LEAN=1)
                 │
                 ├── SQLite: sessions (immutable goals), proof tree, feedback
                 ├── bounded counterexample search (no eval)
                 └── optional OpenAI-compatible model (advisory suggestions only)
```

## Contributing

Issues and focused pull requests are welcome. Keep user proof data private by default, add tests for backend behaviour, and preserve the accessible interaction model (visible focus, text labels, no colour-only state).
