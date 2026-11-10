"""Shared test setup.

DATABASE_PATH must point at a throwaway file before ``app`` is imported, because
the store resolves its path at import time.
"""
import os
import shutil
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="autoproof-tests-")
os.environ["DATABASE_PATH"] = os.path.join(_TMP, "test.db")
os.environ.pop("AUTOPROOF_TRUSTED_LEAN", None)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Every test starts with verification off and no leftover Lean settings."""
    monkeypatch.delenv("AUTOPROOF_TRUSTED_LEAN", raising=False)
    monkeypatch.delenv("LEAN_COMMAND", raising=False)
    yield


@pytest.fixture
def trusted(monkeypatch):
    monkeypatch.setenv("AUTOPROOF_TRUSTED_LEAN", "1")
    return monkeypatch


@pytest.fixture
def lean_required():
    """Skip Lean-dependent tests when Lean is absent, unless AUTOPROOF_REQUIRE_LEAN=1 (CI),
    where a missing Lean is a failure rather than a silent skip."""
    from app.lean import lean_command

    if lean_command() is None:
        if os.getenv("AUTOPROOF_REQUIRE_LEAN") == "1":
            pytest.fail("Lean 4 is required for this test run (AUTOPROOF_REQUIRE_LEAN=1) but was not found.")
        pytest.skip("Lean 4 is not installed; set AUTOPROOF_REQUIRE_LEAN=1 to make this a failure.")


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP, ignore_errors=True)
