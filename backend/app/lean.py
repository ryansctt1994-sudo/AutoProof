"""Lean 4 proof checker.

Trust model
-----------
Lean runs arbitrary code while it elaborates a file (macros, elaborators,
``#eval``, ``native_decide``). Blocking some keywords is not a sandbox, so
verification is OFF unless the operator sets ``AUTOPROOF_TRUSTED_LEAN=1`` on a
machine where executing the submitted text is acceptable.

Proof-integrity invariant
-------------------------
The user never supplies a theorem name or statement. A session stores the
statement (and binders) server-side. ``check_proof`` builds one declaration::

    theorem autoproof_goal <binders> : <statement> := by
      <user's tactic proof, indented>

and asks Lean for ``#print axioms autoproof_goal``. ``verified`` is returned only
when that declaration is present, Lean reports no errors, the proof contains no
``sorry``, and the axioms it depends on are a subset of Lean's standard three.

Statuses
--------
verified     the goal declaration was accepted and uses only standard axioms
incomplete   Lean accepted the file, but it is not a complete proof (``sorry``,
             extra axioms, or the axiom audit could not be completed)
rejected     Lean reported an error, the target declaration is missing, or the
             submission was refused before Lean ran
timeout      Lean did not finish in time
unverified   Lean is not installed, so nothing was checked
disabled     verification is switched off on this server
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

CHECKER_VERSION = "3"
GOAL_NAME = "autoproof_goal"
STANDARD_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})
MAX_PROOF_LINES = 400
MAX_SIGNATURE_CHARS = 2_000

# Text that could end the generated declaration early or start a new command.
_FORBIDDEN_IN_SIGNATURE = re.compile(
    r":=|--|/-|-/|#|[\r\n]|"
    r"\b(?:theorem|lemma|example|def|abbrev|axiom|by|sorry|namespace|section|end|open|"
    r"set_option|instance|macro|syntax|notation|variable|universe|initialize|unsafe|extern|implemented_by)\b"
)
# Commands that execute code while the file is elaborated. Defence in depth only.
_EXECUTION_COMMANDS = (
    "#eval", "#exit", "run_cmd", "run_tac", "run_elab", "run_meta",
    "initialize", "builtin_initialize", "implemented_by", "extern", "unsafe",
)
_BLOCKED = re.compile(r"(?<![\w.#])(?:" + "|".join(re.escape(c) for c in _EXECUTION_COMMANDS) + r")(?!\w)")
_TRUTHY = {"1", "true", "yes", "on"}
_AXIOM_REPORT = re.compile(
    r"'(?P<name>[^']+)' (?:does not depend on any axioms|depends on axioms: \[(?P<list>[^\]]*)\])",
    re.DOTALL,
)
_AXIOM_DECL = re.compile(r"^[ \t]*(?:@\[[^\]]*\][ \t]*)*(?:(?:private|protected)[ \t]+)*axiom\b", re.MULTILINE)


# --- configuration -----------------------------------------------------------------

def trusted_enabled() -> bool:
    """Lean runs only on machines the operator has declared trusted."""
    return os.getenv("AUTOPROOF_TRUSTED_LEAN", "").strip().lower() in _TRUTHY


def _timeout() -> int:
    try:
        return max(1, int(os.getenv("LEAN_TIMEOUT", "30")))
    except ValueError:
        return 30


def lean_command() -> list[str] | None:
    """Resolve ``LEAN_COMMAND``, which may carry arguments such as ``lake env lean``."""
    raw = os.getenv("LEAN_COMMAND", "lean").strip()
    if not raw:
        return None
    parts = shlex.split(raw)
    executable = shutil.which(parts[0])
    return [executable, *parts[1:]] if executable else None


def _project_dir() -> str | None:
    value = os.getenv("LEAN_PROJECT_DIR", "").strip()
    return value if value and Path(value).is_dir() else None


@lru_cache(maxsize=8)
def _version_of(command: tuple[str, ...]) -> str | None:
    try:
        result = subprocess.run([*command, "--version"], capture_output=True, text=True, timeout=20, cwd=_project_dir())
    except (OSError, subprocess.SubprocessError):
        return None
    lines = (result.stdout or result.stderr).strip().splitlines()
    return lines[0] if result.returncode == 0 and lines else None


def lean_version() -> str | None:
    command = lean_command()
    return _version_of(tuple(command)) if command else None


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- source handling ---------------------------------------------------------------

def _balanced(text: str) -> bool:
    pairs = {")": "(", "]": "[", "}": "{", "⦄": "⦃", "⟩": "⟨"}
    stack: list[str] = []
    for char in text:
        if char in "([{⦃⟨":
            stack.append(char)
        elif char in pairs:
            if not stack or stack.pop() != pairs[char]:
                return False
    return not stack


def validate_signature(statement: str, binders: str = "") -> None:
    """Raise ValueError unless the statement and binders are plain Lean propositions."""
    if not statement or not statement.strip():
        raise ValueError("The statement is empty.")
    if len(statement) > MAX_SIGNATURE_CHARS or len(binders) > MAX_SIGNATURE_CHARS:
        raise ValueError("The statement or binders are too long.")
    for label, text in (("statement", statement), ("binders", binders)):
        match = _FORBIDDEN_IN_SIGNATURE.search(text)
        if match:
            raise ValueError(
                f"The {label} may not contain {match.group(0)!r}. "
                "Statements are Lean propositions, not commands, comments or line breaks."
            )
    if binders.strip() and binders.strip()[0] not in "({[⦃⟨":
        raise ValueError("Binders must be bracketed, for example (n : Nat).")
    if not _balanced(statement) or not _balanced(binders):
        raise ValueError("The statement or binders have unbalanced brackets.")


def signature_text(statement: str, binders: str = "") -> str:
    """The declaration header the user is proving, shown read-only in the UI."""
    head = f"theorem {GOAL_NAME}"
    if binders.strip():
        head += f" {binders.strip()}"
    return f"{head} : {statement.strip()}"


def build_source(statement: str, binders: str, proof: str) -> str:
    validate_signature(statement, binders)
    lines = [f"{signature_text(statement, binders)} := by"]
    lines += ["  " + line.rstrip() if line.strip() else "" for line in proof.splitlines()]
    return "\n".join(lines) + "\n"


def strip_comments_and_strings(code: str) -> str:
    """Blank out comments and string literals, keeping every newline in place."""
    out: list[str] = []
    i, n, depth = 0, len(code), 0
    while i < n:
        two = code[i : i + 2]
        if depth:
            if two == "/-":
                depth += 1
                out.append("  ")
                i += 2
            elif two == "-/":
                depth -= 1
                out.append("  ")
                i += 2
            else:
                out.append("\n" if code[i] == "\n" else " ")
                i += 1
        elif two == "/-":
            depth = 1
            out.append("  ")
            i += 2
        elif two == "--":
            while i < n and code[i] != "\n":
                out.append(" ")
                i += 1
        elif code[i] == '"':
            out.append(" ")
            i += 1
            while i < n and code[i] != '"':
                step = 2 if code[i] == "\\" and i + 1 < n else 1
                out.append("".join("\n" if ch == "\n" else " " for ch in code[i : i + step]))
                i += step
            if i < n:
                out.append(" ")
                i += 1
        else:
            out.append(code[i])
            i += 1
    return "".join(out)


def _declares(clean: str, name: str) -> bool:
    pattern = (
        r"^[ \t]*(?:@\[[^\]\n]*\][ \t]*)?(?:(?:private|protected|noncomputable|unsafe|partial|nonrec)[ \t]+)*"
        rf"(?:theorem|lemma)[ \t]+{re.escape(name)}(?![\w.'])"
    )
    return re.search(pattern, clean, re.MULTILINE) is not None


def _blocked_command(clean: str) -> str | None:
    match = _BLOCKED.search(clean)
    return match.group(0) if match else None


# --- running Lean -----------------------------------------------------------------

def _limit_resources() -> None:  # pragma: no cover - runs in the child process
    os.setsid()  # own process group, so a timeout can kill Lean and anything it spawns
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))
    except (ImportError, ValueError, OSError):
        pass


def _memory_flag() -> list[str]:
    """Lean's own memory cap. RLIMIT_AS is unusable because Lean reserves address space for threads."""
    try:
        megabytes = int(os.getenv("LEAN_MEMORY_MB", "4096"))
    except ValueError:
        megabytes = 4096
    return [f"--memory={megabytes}"] if megabytes > 0 else []


def _run(command: list[str], path: Path, workdir: str) -> tuple[int | None, str, str]:
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "LANG", "LC_ALL", "ELAN_HOME", "LEAN_PATH", "LEAN_SYSROOT", "LAKE_HOME", "TMPDIR"}}
    process = subprocess.Popen(
        [*command, *_memory_flag(), "--json", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        cwd=_project_dir() or workdir, env=env, preexec_fn=_limit_resources if os.name == "posix" else None,
    )
    try:
        stdout, stderr = process.communicate(timeout=_timeout())
        return process.returncode, stdout, stderr
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL) if os.name == "posix" else process.kill()
        except (OSError, ProcessLookupError):
            process.kill()
        process.communicate()
        return None, "", ""


def _parse_messages(stdout: str, source_path: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            item = None
        if not isinstance(item, dict) or "severity" not in item:
            messages.append({"line": 0, "column": 0, "severity": "information", "kind": "", "message": line.replace(source_path, "Main.lean")})
            continue
        pos = item.get("pos") or {}
        messages.append({
            "line": int(pos.get("line", 0) or 0),
            "column": int(pos.get("column", 0) or 0),
            "severity": str(item.get("severity", "information")),
            "kind": str(item.get("kind", "") or ""),
            "message": str(item.get("data", "")).replace(source_path, "Main.lean"),
        })
    return messages


def _format(messages: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"{m['line']}:{m['column']}: {m['severity']}: {m['message']}" if m["line"] else m["message"] for m in messages
    )


def _is_sorry(message: dict[str, Any]) -> bool:
    text = message["message"]
    return message["kind"] == "hasSorry" or "declaration uses `sorry`" in text or "declaration uses 'sorry'" in text


def verify_source(source: str, expected_name: str = GOAL_NAME, *, command: list[str] | None = None) -> dict[str, Any]:
    """Check a complete Lean file and audit the axioms of ``expected_name``.

    This is the low-level verifier. ``check_proof`` is the entry point for user
    submissions, because it binds the declaration to the stored statement.
    """
    if not source.endswith("\n"):
        source += "\n"
    if command is None:
        command = lean_command()
    base: dict[str, Any] = {
        "ok": False, "status": "unverified", "mode": "lean", "expected": expected_name,
        "axioms": {}, "messages": [], "lean_version": None,
        "checker_version": CHECKER_VERSION, "source_sha256": sha256(source),
    }
    if not command:
        return {**base, "diagnostics": "Not verified: Lean 4 is not installed, so nothing was checked. Install Lean 4 or set LEAN_COMMAND."}
    base["lean_version"] = _version_of(tuple(command))

    clean = strip_comments_and_strings(source)
    blocked = _blocked_command(clean)
    if blocked:
        return {**base, "status": "rejected", "diagnostics": f"`{blocked}` runs code during elaboration and is not allowed."}
    if not _declares(clean, expected_name):
        return {**base, "status": "rejected", "diagnostics": f"No theorem named `{expected_name}` is declared, so nothing was verified."}

    audit_line = source.count("\n") + 1
    audited = source + f"#print axioms {expected_name}\n"
    with tempfile.TemporaryDirectory(prefix="autoproof-") as workdir:
        path = Path(workdir) / "Main.lean"
        path.write_text(audited, encoding="utf-8")
        returncode, stdout, stderr = _run(command, path, workdir)
        source_path = str(path)

    if returncode is None:
        return {**base, "status": "timeout", "diagnostics": f"Lean timed out after {_timeout()} seconds."}

    messages = _parse_messages(stdout, source_path)
    base["messages"] = messages
    errors = [m for m in messages if m["severity"] == "error"]
    if errors:
        return {**base, "status": "rejected", "diagnostics": _format(messages)}
    if returncode != 0:
        detail = stderr.strip().replace(source_path, "Main.lean") or f"Lean exited with status {returncode}."
        return {**base, "status": "rejected", "diagnostics": detail}

    problems: list[str] = []
    if any(_is_sorry(m) for m in messages):
        problems.append("uses `sorry`")
    if _AXIOM_DECL.search(clean):
        problems.append("declares its own axiom")

    matched = None
    for message in messages:
        if message["line"] != audit_line:
            continue
        found = _AXIOM_REPORT.match(message["message"].strip())
        if found and found.group("name") == expected_name:
            matched = found
            break
    if matched is None:
        problems.append(f"could not complete the axiom audit for `{expected_name}`")
        listed: list[str] = []
    else:
        raw = matched.group("list")
        listed = [] if raw is None else [item.strip() for item in raw.replace("\n", " ").split(",") if item.strip()]
        extra = [axiom for axiom in listed if axiom not in STANDARD_AXIOMS]
        if extra:
            problems.append(f"depends on non-standard axioms: {', '.join(extra)}")

    result = {**base, "axioms": {expected_name: listed}}
    if problems:
        summary = "Lean accepted the file, but it is not a complete proof: " + "; ".join(problems) + "."
        return {**result, "status": "incomplete", "diagnostics": "\n".join(filter(None, [summary, _format(messages)]))}
    return {**result, "ok": True, "status": "verified", "diagnostics": _format(messages)}


def _refused(message: str, statement_sha: str | None = None) -> dict[str, Any]:
    return {
        "ok": False, "status": "rejected", "mode": "refused", "diagnostics": message,
        "messages": [], "axioms": {}, "lean_version": None, "expected": GOAL_NAME,
        "checker_version": CHECKER_VERSION, "source_sha256": None, "statement_sha256": statement_sha,
    }


def check_proof(statement: str, binders: str, proof: str) -> dict[str, Any]:
    """Verify a user's tactic proof against the server-stored goal. This is the only entry point for submissions."""
    statement_sha = sha256(signature_text(statement, binders)) if statement.strip() else None
    try:
        validate_signature(statement, binders)
    except ValueError as error:
        return _refused(str(error), statement_sha)
    if not proof.strip():
        return _refused("The proof is empty.", statement_sha)
    if proof.count("\n") + 1 > MAX_PROOF_LINES:
        return _refused(f"The proof is longer than {MAX_PROOF_LINES} lines.", statement_sha)

    if not trusted_enabled():
        return {
            "ok": False, "status": "disabled", "mode": "disabled", "expected": GOAL_NAME,
            "diagnostics": (
                "Lean verification is turned off on this server, so this proof was not checked. "
                "Executing Lean on submitted text is only allowed on trusted machines (AUTOPROOF_TRUSTED_LEAN=1)."
            ),
            "messages": [], "axioms": {}, "lean_version": None,
            "checker_version": CHECKER_VERSION, "source_sha256": None, "statement_sha256": statement_sha,
        }

    source = build_source(statement, binders, proof)
    result = verify_source(source, GOAL_NAME)
    result["statement_sha256"] = statement_sha
    result["proof_sha256"] = sha256(proof)
    for message in result["messages"]:
        message["proof_line"] = message["line"] - 1 if message["line"] > 1 else None
    return result
