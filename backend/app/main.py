import os
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import store
from .counterexample import search as search_counterexample
from .lean import GOAL_NAME, check_proof, lean_version, signature_text, trusted_enabled, validate_signature
from .llm import suggest
from .schemas import ApplyTacticRequest, CounterexampleRequest, FeedbackRequest, SessionCreate, SuggestRequest

NODE_STATUS = {
    "verified": "complete",
    "incomplete": "error",
    "rejected": "error",
    "timeout": "error",
    "unverified": "pending",
    "disabled": "pending",
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.init_db()
    yield


app = FastAPI(title="AutoProof API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("AUTOPROOF_CORS_ORIGINS", "http://localhost:3000").split(",") if origin.strip()],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _session_or_404(session_id: str) -> dict:
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown proof session.")
    return session


@app.get("/api/health")
async def health():
    enabled = trusted_enabled()
    return {
        "status": "ok",
        "service": "autoproof",
        "verification": {"enabled": enabled, "lean_version": lean_version() if enabled else None},
    }


@app.post("/api/sessions", status_code=201)
async def create_session(payload: SessionCreate):
    try:
        validate_signature(payload.statement, payload.binders)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    session_id, root_id = str(uuid4()), str(uuid4())
    binders, statement = payload.binders.strip(), payload.statement.strip()
    root = {
        "id": root_id, "kind": "goal", "label": "Initial goal", "status": "active",
        "payload": {"statement": statement, "binders": binders},
    }
    store.create_session(session_id, payload.title, binders, statement, root)
    return {
        "id": session_id,
        "root_id": root_id,
        "name": GOAL_NAME,
        "binders": binders,
        "statement": statement,
        "signature": signature_text(statement, binders),
        "verification_enabled": trusted_enabled(),
    }


@app.get("/api/sessions/{session_id}")
async def read_session(session_id: str):
    session = _session_or_404(session_id)
    return {**session, "name": GOAL_NAME, "signature": signature_text(session["statement"], session["binders"]), "verification_enabled": trusted_enabled()}


@app.post("/api/suggest")
async def suggest_tactics(payload: SuggestRequest):
    goal = payload.goal
    if payload.session_id:
        goal = _session_or_404(payload.session_id)["statement"]
    if not goal:
        raise HTTPException(status_code=422, detail="Provide a session_id or a goal.")
    tactics, source = await suggest(goal, payload.context)
    # Suggestions are advisory. Only a Lean check can make a proof verified.
    return {"tactics": tactics, "source": source, "advisory": True}


@app.post("/api/apply-tactic")
async def apply_tactic(payload: ApplyTacticRequest):
    session = _session_or_404(payload.session_id)
    result = check_proof(session["statement"], session["binders"], payload.proof)
    node_id = str(uuid4())
    store.add_node({
        "id": node_id,
        "session_id": session["id"],
        "parent_id": store.latest_node_id(session["id"]),
        "kind": "tactic",
        "label": payload.tactic,
        "status": NODE_STATUS.get(result["status"], "error"),
        "payload": {**result, "proof": payload.proof},
    })
    return {"node_id": node_id, **result}


@app.post("/api/feedback", status_code=201)
async def feedback(payload: FeedbackRequest):
    _session_or_404(payload.session_id)
    if payload.verdict == "edited" and not (payload.edited_tactic or "").strip():
        raise HTTPException(status_code=422, detail="An edited verdict needs the edited proof.")
    store.save_feedback(payload.model_dump())
    return {"saved": True, "message": "Feedback recorded. It is stored locally and not yet used to rank suggestions."}


@app.get("/api/sessions/{session_id}/tree")
async def proof_tree(session_id: str):
    _session_or_404(session_id)
    return store.get_tree(session_id)


@app.post("/api/counterexample")
async def counterexample(payload: CounterexampleRequest):
    if payload.session_id:
        session = _session_or_404(payload.session_id)
        statement, binders = session["statement"], session["binders"]
    else:
        if not payload.statement or not payload.statement.strip():
            raise HTTPException(status_code=422, detail="Provide a session_id or a statement.")
        statement, binders = payload.statement, payload.binders
    text = f"{binders} : {statement}" if binders.strip() else statement
    return search_counterexample(text, payload.bounds)
