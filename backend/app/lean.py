import hashlib
import os
import shutil
import subprocess
import tempfile


DEMO_TOKENS = ("simp", "rfl", "omega", "exact", "ring", "decide")


def _lean_version(command: str) -> str:
    try:
        result = subprocess.run(
            [command, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return (result.stdout or result.stderr).strip()
    except Exception:
        return "unknown"


def check_lean(code: str) -> dict:
    """Validate Lean source without granting authority to demo-mode heuristics.

    Only a real Lean process can produce ``LEAN_VERIFIED``. Demo mode remains
    useful for interface exploration, but it is advisory and evidence-free.
    """
    command = os.getenv("LEAN_COMMAND", "lean")
    source_sha256 = hashlib.sha256(code.encode("utf-8")).hexdigest()

    if shutil.which(command):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".lean", delete=False, encoding="utf-8"
        ) as source:
            source.write(code)
            path = source.name
        try:
            result = subprocess.run(
                [command, path],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            verified = result.returncode == 0
            return {
                "ok": verified,
                "verified": verified,
                "advisory": False,
                "status": "LEAN_VERIFIED" if verified else "LEAN_REJECTED",
                "authority": "PINNED_CHECKER_REQUIRED",
                "evidence_level": "FORMAL_CHECK",
                "diagnostics": (result.stderr or result.stdout).strip(),
                "mode": "lean",
                "command": command,
                "lean_version": _lean_version(command),
                "source_sha256": source_sha256,
            }
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "verified": False,
                "advisory": False,
                "status": "LEAN_TIMEOUT",
                "authority": "NONE",
                "evidence_level": "E0",
                "diagnostics": "Lean timed out after 20 seconds.",
                "mode": "lean",
                "command": command,
                "lean_version": _lean_version(command),
                "source_sha256": source_sha256,
            }
        finally:
            os.unlink(path)

    plausible = any(token in code for token in DEMO_TOKENS)
    return {
        "ok": False,
        "verified": False,
        "advisory": True,
        "status": "DEMO_PLAUSIBLE" if plausible else "DEMO_UNRESOLVED",
        "authority": "NONE",
        "evidence_level": "E0",
        "diagnostics": (
            "Demo mode only: Lean is not installed, so no proof was checked. "
            "Install a pinned Lean 4 toolchain to produce formal evidence."
        ),
        "mode": "demo",
        "command": command,
        "lean_version": None,
        "source_sha256": source_sha256,
    }
