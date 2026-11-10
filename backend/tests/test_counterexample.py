"""Bounded counterexample search: real witnesses, honest "no witness", and explicit refusals."""
import pytest

from app.counterexample import Unsupported, parse, search


@pytest.mark.parametrize("statement, witness", [
    ("n + 1 = n", {"n": 0}),
    ("n * n = n + n", {"n": 1}),
    ("a - b + b = a", {"a": 0, "b": 1}),        # truncated subtraction
    ("a / b * b = a", {"a": 1, "b": 0}),        # x / 0 = 0 in Lean
    ("(n m : Nat) (h : n ≤ m) : n < m", {"n": 0, "m": 0}),
    ("a ∣ b → b ∣ a", {"a": 1, "b": 0}),
    ("2 ^ n > n ^ 3", {"n": 2}),
])
def test_finds_a_real_counterexample(statement, witness):
    result = search(statement)
    assert result["supported"] is True
    assert result["found"] is True
    assert result["assignment"] == witness
    assert "makes the statement false" in result["explanation"]


@pytest.mark.parametrize("statement", [
    "n + 0 = n",
    "n * 2 = n + n",
    "∀ n m : Nat, n + m = m + n",
    "min a b ≤ a ∧ min a b ≤ b",
    "n % 0 = n ∧ n / 0 = 0",
    "¬ (n < n)",
])
def test_no_witness_is_reported_as_evidence_not_proof(statement):
    result = search(statement)
    assert result["supported"] is True
    assert result["found"] is False
    assert "evidence, not a proof" in result["explanation"]


@pytest.mark.parametrize("statement", [
    "(x : Int) : x + 1 > x",   # not a natural-number statement
    "∃ n, n = 3",              # existentials are out of scope
    "n.succ = n + 1",          # unknown dotted name
    "p ∧ q",                   # non-arithmetic atoms
    "n +",                     # malformed
    "",
])
def test_unsupported_statements_are_not_reported_as_searched(statement):
    result = search(statement)
    assert result["supported"] is False
    assert result["found"] is False
    assert result["explanation"].startswith("Not searched")


def test_parser_never_evaluates_python():
    with pytest.raises(Unsupported):
        parse("__import__('os')")
    result = search("__import__('os').system('true') = 0")
    assert result["supported"] is False


def test_variable_count_is_bounded():
    result = search("a + b + c + d + e + f + g + h + i + j = j + i + h + g + f + e + d + c + b + a")
    assert result["supported"] is True
    assert result["bound"] < 12
    assert "lowered" in result["explanation"]
