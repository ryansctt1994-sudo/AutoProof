import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

DB_PATH = Path(os.getenv("DATABASE_PATH", Path(__file__).resolve().parents[1] / "autoproof.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  theorem TEXT NOT NULL,
  binders TEXT NOT NULL DEFAULT '',
  statement TEXT NOT NULL,
  natural_language TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  tactic TEXT NOT NULL,
  verdict TEXT NOT NULL,
  edited_tactic TEXT,
  note TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS tree_nodes (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  parent_id TEXT,
  kind TEXT NOT NULL,
  label TEXT NOT NULL,
  status TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS tree_nodes_by_session ON tree_nodes (session_id);
CREATE INDEX IF NOT EXISTS feedback_by_session ON feedback (session_id);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """One connection per unit of work: committed on success, always closed."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring databases created by v0.1 up to the current schema, keeping their rows."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
    if "statement" not in columns:
        conn.execute("ALTER TABLE sessions ADD COLUMN statement TEXT")
        conn.execute("ALTER TABLE sessions ADD COLUMN binders TEXT NOT NULL DEFAULT ''")
        conn.execute("UPDATE sessions SET statement = theorem WHERE statement IS NULL")
    # Session goals are the proof obligations. Any later change would let a proof
    # be checked against a different claim than the one the user saw, so forbid it.
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS sessions_are_immutable BEFORE UPDATE ON sessions "
        "BEGIN SELECT RAISE(ABORT, 'proof sessions are immutable'); END"
    )


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def create_session(session_id: str, title: str, binders: str, statement: str, root: dict[str, Any]) -> None:
    signature = f"{binders} : {statement}".strip() if binders else statement
    with connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, title, theorem, binders, statement) VALUES (?,?,?,?,?)",
            (session_id, title, signature, binders, statement),
        )
        conn.execute(
            "INSERT INTO tree_nodes (id,session_id,parent_id,kind,label,status,payload) VALUES (?,?,?,?,?,?,?)",
            (root["id"], session_id, None, root["kind"], root["label"], root["status"], json.dumps(root.get("payload", {}))),
        )


def get_session(session_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id, title, binders, statement, created_at FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
    return dict(row) if row else None


def add_node(node: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO tree_nodes (id,session_id,parent_id,kind,label,status,payload) VALUES (?,?,?,?,?,?,?)",
            (node["id"], node["session_id"], node.get("parent_id"), node["kind"], node["label"], node["status"], json.dumps(node.get("payload", {}))),
        )


def latest_node_id(session_id: str) -> str | None:
    """The most recent node, so tactic attempts form one navigable branch."""
    with connect() as conn:
        row = conn.execute("SELECT id FROM tree_nodes WHERE session_id = ? ORDER BY rowid DESC LIMIT 1", (session_id,)).fetchone()
    return row["id"] if row else None


def save_feedback(item: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO feedback (session_id,tactic,verdict,edited_tactic,note) VALUES (?,?,?,?,?)",
            (item["session_id"], item["tactic"], item["verdict"], item.get("edited_tactic"), item.get("note")),
        )


def get_tree(session_id: str) -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM tree_nodes WHERE session_id = ? ORDER BY rowid", (session_id,)).fetchall()
    nodes = []
    edges = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item["payload"])
        nodes.append(item)
        if item["parent_id"]:
            edges.append({"id": f"{item['parent_id']}-{item['id']}", "source": item["parent_id"], "target": item["id"]})
    return {"nodes": nodes, "edges": edges}
