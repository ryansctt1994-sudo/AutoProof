"""Lean 4 runner.

Evidence rule
-------------
A submission is ``verified`` only when the Lean compiler accepted the exact
source *and* every named declaration in it depends on nothing beyond Lean's
three standard axioms. Everything else is reported as what it is:

``verified``    Lean accepted it; the axiom audit is clean.
``incomplete``  Lean accepted it, but it leans on ``sorry``, a declared
                ``axiom`` or another non-standard axiom (``native_decide`` …),
                or the axiom audit could not be completed.
``rejected``    Lean reported errors.
``timeout``     Lean did not finish in time.
``unverified``  Lean is not installed. Nothing was checked.

Threat model: this catches honest mistakes and the cheap shortcuts an LLM tends
to suggest (``sorry``, ad-hoc axioms). It is not a sandbox. Lean runs arbitrary
code at elaboration time (``#eval``, ``run_cmd``, custom elaborators), so the
API must not be exposed to untrusted users without OS-level isolation.
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

CHECKER_VERSION = "2"
STANDARD_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})

# Commands that execute code while the file is being elaborated. Blocking them
# is defence in depth, not a security boundary: macros can hide the same thing.
_EVAL_COMMANDS = ("#eval", "#exit", "run_cmd", "run_elab", "run_meta", "#synth_eval")
_DECL = re.compile(
    r"^[ \t]*(?:@\[[^\]]*\][ \t\n]*)*(?:(?:private|protected|noncomputable|unsafe|partial|nonrec)[ \t]+)*"
    r"(theorem|lemma|def|abbrev)[ \t]+(«[^»]+»|[^\s:({\[⦃]+)",
    re.MULTILINE,
)
_SCOPE = re.compile(r"^[ \t]*(namespace|section|mutual|end)\b[ \t]*([^\s]*)", re.MULTILINE)
_AXIOM_DECL = re.compile(r"^[ \t]*(?:@\[[^\]]*\][ \t\n]*)*(?:(?:private|protected)[ \t]+)*axiom\b", re.MULTILINE)
_AXIOM_LIST = re.compile(r"depends on axioms:\s*\[(.*?)\]", re.DOTALL)


def _timeout() -> int:
    try:
        return max(1, int(os.getenv("LEAN_TIMEOUT", "30")))
    except ValueError:
        return 30


def lean_command() -> list[str] | None:
    """Resolve ``LEAN_COMMAND`` (which may carry arguments, e.g. ``lake env lean``)."""
    raw = os.getenv("LEAN_COMMAND", "lean").strip()
    if not raw:
        return None
    parts = shlex.split(raw)
    executable = shutil.which(parts[0])
    return [executable, *parts[1:]] if executable else None


@lru_cache(maxsize=8)
def _version_of(command: tuple[str, ...]) -> str | None:
    try:
        result = subprocess.run([*command, "--version"], capture_output=True, text=True, timeout=20, cwd=_project_dir())
    except (OSError, subprocess.SubprocessError):
        return None
    line = (result.stdout or result.stderr).strip().splitlines()
    return line[0] if result.returncode == 0 and line else None


def lean_version() -> str | None:
    command = lean_command()
    return _version_of(tuple(command)) if command else None


def _project_dir() -> str | None:
    """Directory to run Lean from, so ``lake env lean`` can find a project (Mathlib …)."""
    value = os.getenv("LEAN_PROJECT_DIR", "").strip()
    return value if value and Path(value).is_dir() else None


def source_digest(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


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


def declared_names(code: str) -> list[str]:
    """Fully qualified names of the theorems and definitions a source declares."""
    clean = strip_comments_and_strings(code)
    events: list[tuple[int, str, str]] = []
    for match in _SCOPE.finditer(clean):
        events.append((match.start(), match.group(1), match.group(2)))
    for match in _DECL.finditer(clean):
        events.append((match.start(), "decl", match.group(2)))
    events.sort()

    scopes: list[str | None] = []  # namespace name, or None for a section
    names: list[str] = []
    for _, kind, value in events:
        if kind == "namespace":
            scopes.append(value or None)
        elif kind in {"section", "mutual"}:
            scopes.append(None)
        elif kind == "end":
            if scopes:
                scopes.pop()
        else:
            name = value.rstrip(".")  # `foo.{u}` is cut at the brace, leaving `foo.`
            if name.startswith("_root_."):
                qualified = name[len("_root_.") :]
            else:
                qualified = ".".join([scope for scope in scopes if scope] + [name])
            if qualified and qualified not in names:
                names.append(qualified)
    return names


def uses_eval(code: str) -> str | None:
    clean = strip_comments_and_strings(code)
    for command in _EVAL_COMMANDS:
        if re.search(rf"(?<![\w.]){re.escape(command)}(?![\w.])", clean):
            return command
    return None


def _parse_messages(stdout: str, source_name: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            item = None
        if not isinstance(item, dict) or "severity" not in item:
            messages.append({"line": 0, "column": 0, "severity": "information", "kind": "", "message": line.replace(source_name, "main.lean")})
            continue
        pos = item.get("pos") or {}
        messages.append({
            "line": int(pos.get("line", 0) or 0),
            "column": int(pos.get("column", 0) or 0),
            "severity": str(item.get("severity", "information")),
            "kind": str(item.get("kind", "") or ""),
            "message": str(item.get("data", "")).replace(source_name, "main.lean"),
        })
    return messages


def _limit_resources() -> None:  # pragma: no cover - runs in the child process
    os.setsid()  # own process group, so a timeout can kill Lean and anything it spawned
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))
    except (ImportError, ValueError, OSError):
        pass


def _memory_flag() -> list[str]:
    """Lean's own memory cap. (RLIMIT_AS is unusable: Lean reserves address space for threads.)"""
    try:
        megabytes = int(os.getenv("LEAN_MEMORY_MB", "4096"))
    except ValueError:
        megabytes = 4096
    return [f"--memory={megabytes}"] if megabytes > 0 else []


def _run(command: list[str], path: Path, workdir: str) -> tuple[int | None, str, str]:
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "LANG", "LC_ALL", "ELAN_HOME", "LEAN_PATH", "LEAN_SYSROOT", "LAKE_HOME", "TMPDIR"}}
    process = subprocess.Popen(
        [*command, *_memory_flag(), "--json", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
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


def _format(messages: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"{m['line']}:{m['column']}: {m['severity']}: {m['message']}" if m["line"] else m["message"] for m in messages
    )


def check_lean(code: str) -> dict[str, Any]:
    """Check a Lean source. See the module docstring for what each status means."""
    digest = source_digest(code)
    base: dict[str, Any] = {"ok": False, "source_sha256": digest, "checker_version": CHECKER_VERSION, "messages": [], "axioms": {}, "lean_version": None}
    command = lean_command()
    if not command:
        return {**base, "status": "unverified", "mode": "demo",
                "diagnostics": "Not verified: Lean 4 is not installed, so nothing was checked. Install Lean 4 or set LEAN_COMMAND to verify this proof."}
    base["lean_version"] = _version_of(tuple(command))

    if os.getenv("LEAN_ALLOW_EVAL", "").lower() not in {"1", "true", "yes"}:
        blocked = uses_eval(code)
        if blocked:
            return {**base, "status": "rejected", "mode": "lean",
                    "diagnostics": f"`{blocked}` runs code while Lean elaborates the file and is disabled here. Set LEAN_ALLOW_EVAL=1 on a trusted machine to allow it."}

    names = declared_names(code)
    user_lines = code.count("\n") + 1
    audit_first_line = user_lines + 2
    audited = code + "\n\n" + "".join(f"#print axioms {name}\n" for name in names)

    with tempfile.TemporaryDirectory(prefix="autoproof-") as workdir:
        path = Path(workdir) / "Main.lean"
        path.write_text(audited, encoding="utf-8")
        returncode, stdout, stderr = _run(command, path, workdir)
        source_name = str(path)

    if returncode is None:
        return {**base, "status": "timeout", "mode": "lean", "diagnostics": f"Lean timed out after {_timeout()} seconds."}

    parsed = _parse_messages(stdout, source_name)
    messages = [m for m in parsed if m["line"] < audit_first_line]
    audit = {m["line"]: m for m in parsed if m["line"] >= audit_first_line}
    errors = [m for m in messages if m["severity"] == "error"]
    result = {**base, "mode": "lean", "messages": messages}

    if errors or (returncode != 0 and not audit):
        diagnostics = _format(messages) or stderr.strip().replace(source_name, "main.lean") or f"Lean exited with status {returncode}."
        return {**result, "status": "rejected", "diagnostics": diagnostics}

    problems: list[str] = []
    axioms: dict[str, list[str]] = {}
    if any(m["kind"] == "hasSorry" or "declaration uses `sorry`" in m["message"] or "declaration uses 'sorry'" in m["message"] for m in messages):
        problems.append("uses `sorry`")
    if _AXIOM_DECL.search(strip_comments_and_strings(code)):
        problems.append("declares its own axiom")
    for offset, name in enumerate(names):
        report = audit.get(audit_first_line + offset)
        if report is None or report["severity"] == "error":
            problems.append(f"could not audit the axioms of `{name}`")
            continue
        listed = _AXIOM_LIST.search(report["message"])
        if listed:
            found = [item.strip() for item in listed.group(1).replace("\n", " ").split(",") if item.strip()]
        elif "does not depend on any axioms" in report["message"]:
            found = []
        else:
            problems.append(f"could not audit the axioms of `{name}`")
            continue
        axioms[name] = found
        extra = [axiom for axiom in found if axiom not in STANDARD_AXIOMS]
        if extra:
            problems.append(f"`{name}` depends on non-standard axioms: {', '.join(extra)}")

    result["axioms"] = axioms
    if problems:
        summary = "Lean accepted the file, but it is not a complete proof: " + "; ".join(dict.fromkeys(problems)) + "."
        return {**result, "status": "incomplete", "diagnostics": "\n".join(filter(None, [summary, _format(messages)]))}
    return {**result, "ok": True, "status": "verified", "diagnostics": _format(messages)}
