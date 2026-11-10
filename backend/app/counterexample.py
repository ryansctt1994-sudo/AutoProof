"""Bounded counterexample search over the natural numbers.

The statement is parsed into a small expression tree (no ``eval``) and every
assignment of its variables in ``0…bound`` is tried, smallest values first.
Arithmetic follows Lean's ``Nat``: subtraction truncates at zero, ``x / 0 = 0``
and ``x % 0 = x``.

A search that finds nothing is evidence, never a proof, and a statement the
parser does not understand is reported as *not searched* rather than "no
counterexample found".
"""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass
from typing import Any, Union

MAX_EVALUATIONS = 200_000
MAX_BITS = 4096
NAT_TYPES = {"Nat", "ℕ"}
SUPPORTED = "numerals, variables, + - * / % ^, = ≠ < ≤ > ≥ ∣, ¬ ∧ ∨ → ↔, min, max, Nat.succ, Nat.pred"

Value = Union[int, bool]
Node = tuple  # ("num", 3) | ("var", "n") | ("bool", True) | (operator, *children)


class Unsupported(ValueError):
    """The statement is outside the fragment this search understands."""


_TOKEN = re.compile(
    r"\s*(?:(?P<num>\d+)|(?P<name>[A-Za-z_][A-Za-z0-9_'.]*|ℕ|ℤ|ℝ|ℚ)|(?P<op><->|↔|->|→|<=|≤|>=|≥|!=|≠|/\\|∧|\\/|∨|&&|\|\||[-+*/%^=<>()¬!,:{}\[\]∣∀∃]))"
)
_ALIASES = {"<->": "↔", "->": "→", "<=": "≤", ">=": "≥", "!=": "≠", "/\\": "∧", "&&": "∧", "\\/": "∨", "||": "∨", "!": "¬"}
_RELATIONS = {"=", "≠", "<", "≤", ">", "≥", "∣"}
_FUNCTIONS = {"min": 2, "max": 2, "Nat.succ": 1, "Nat.pred": 1, "Nat.min": 2, "Nat.max": 2}


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    text = text.strip()
    while position < len(text):
        match = _TOKEN.match(text, position)
        if not match or match.end() == position:
            raise Unsupported(f"unexpected character {text[position:position + 1]!r}")
        position = match.end()
        if match.group("num") is not None:
            tokens.append(("num", match.group("num")))
        elif match.group("name") is not None:
            name = match.group("name")
            tokens.append(("op", "∀") if name == "forall" else ("name", name))
        else:
            operator = match.group("op")
            tokens.append(("op", _ALIASES.get(operator, operator)))
    return tokens


@dataclass
class Parsed:
    tree: Node
    variables: list[str]
    hypotheses: list[Node]


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]):
        self.tokens = tokens
        self.index = 0
        self.declared: dict[str, str] = {}
        self.hypotheses: list[Node] = []

    # -- token helpers ------------------------------------------------------
    def peek(self, offset: int = 0) -> tuple[str, str]:
        index = self.index + offset
        return self.tokens[index] if index < len(self.tokens) else ("end", "")

    def take(self) -> tuple[str, str]:
        token = self.peek()
        self.index += 1
        return token

    def accept(self, value: str) -> bool:
        if self.peek() == ("op", value):
            self.index += 1
            return True
        return False

    def expect(self, value: str) -> None:
        if not self.accept(value):
            raise Unsupported(f"expected {value!r} near {self.peek()[1] or 'the end'!r}")

    # -- binders ------------------------------------------------------------
    def _declare(self, names: list[str], type_name: str | None) -> None:
        if type_name is not None and type_name not in NAT_TYPES:
            raise Unsupported(f"only natural-number variables are searched (found type {type_name})")
        for name in names:
            self.declared[name] = "Nat"

    def _binder_group(self, closing: str) -> None:
        """Parse ``n m : Nat`` (variables) or ``h : n < m`` (a hypothesis) up to the closing bracket."""
        names: list[str] = []
        while self.peek()[0] == "name":
            names.append(self.take()[1])
        if not names:
            raise Unsupported("could not read a binder")
        self.expect(":")
        kind, value = self.peek()
        is_type = kind == "name" and self.peek(1) == ("op", closing) and value not in self.declared and value not in {"True", "False"}
        if is_type:
            self._declare(names, self.take()[1])
        elif len(names) == 1:
            self.hypotheses.append(self.iff())
        else:
            raise Unsupported("could not read a hypothesis binder")
        self.expect(closing)

    def binders(self) -> None:
        while self.peek()[0] == "op" and self.peek()[1] in "({[":
            if self.peek()[1] == "(" and not self._looks_like_binder():
                return
            opening = self.take()[1]
            self._binder_group({"(": ")", "{": "}", "[": "]"}[opening])

    def _looks_like_binder(self) -> bool:
        offset = 1
        while self.peek(offset)[0] == "name":
            offset += 1
        return offset > 1 and self.peek(offset) == ("op", ":")

    def quantifiers(self) -> None:
        while self.accept("∀"):
            if self.peek() == ("op", "("):
                self.binders()
            else:
                names: list[str] = []
                while self.peek()[0] == "name":
                    names.append(self.take()[1])
                type_name = self.take()[1] if self.accept(":") and self.peek()[0] == "name" else None
                if not names:
                    raise Unsupported("could not read the quantified variables")
                self._declare(names, type_name)
            self.expect(",")

    # -- propositions -------------------------------------------------------
    def iff(self) -> Node:
        left = self.implies()
        while self.accept("↔"):
            left = ("↔", left, self.implies())
        return left

    def implies(self) -> Node:
        left = self.disjunction()
        if self.accept("→"):
            return ("→", left, self.implies())
        return left

    def disjunction(self) -> Node:
        left = self.conjunction()
        while self.accept("∨"):
            left = ("∨", left, self.conjunction())
        return left

    def conjunction(self) -> Node:
        left = self.negation()
        while self.accept("∧"):
            left = ("∧", left, self.negation())
        return left

    def negation(self) -> Node:
        if self.accept("¬"):
            return ("¬", self.negation())
        if self.peek() == ("op", "∀"):
            self.quantifiers()
            return self.iff()
        if self.peek() == ("op", "∃"):
            raise Unsupported("existential statements are not searched")
        return self.comparison()

    def comparison(self) -> Node:
        left = self.sum()
        links: list[Node] = []
        while self.peek()[0] == "op" and self.peek()[1] in _RELATIONS:
            relation = self.take()[1]
            right = self.sum()
            links.append((relation, left, right))
            left = right
        if not links:
            return left
        tree = links[0]
        for link in links[1:]:
            tree = ("∧", tree, link)
        return tree

    # -- arithmetic ---------------------------------------------------------
    def sum(self) -> Node:
        left = self.product()
        while self.peek() in {("op", "+"), ("op", "-")}:
            left = (self.take()[1], left, self.product())
        return left

    def product(self) -> Node:
        left = self.power()
        while self.peek() in {("op", "*"), ("op", "/"), ("op", "%")}:
            left = (self.take()[1], left, self.power())
        return left

    def power(self) -> Node:
        base = self.atom()
        if self.accept("^"):
            return ("^", base, self.power())
        return base

    def atom(self) -> Node:
        kind, value = self.take()
        if kind == "num":
            return ("num", int(value))
        if kind == "name":
            if value in {"True", "False"}:
                return ("bool", value == "True")
            if value in _FUNCTIONS:
                return (value.removeprefix("Nat.") if value in {"Nat.min", "Nat.max"} else value, *[self.atom() for _ in range(_FUNCTIONS[value])])
            if "." in value:
                raise Unsupported(f"unknown function or constant {value!r}")
            self.declared.setdefault(value, "Nat")
            return ("var", value)
        if (kind, value) == ("op", "("):
            inner = self.iff()
            self.expect(")")
            return inner
        raise Unsupported(f"unexpected {value or 'end of statement'!r}")


def parse(statement: str) -> Parsed:
    """Parse a proposition, optionally with a ``theorem name (binders) :`` prefix."""
    text = statement.split(":=", 1)[0].strip()
    text = re.sub(r"^(?:theorem|lemma|example)\b\s*(?:(?![(:{\[])[^\s(:{\[]+)?", "", text).strip()
    if not text:
        raise Unsupported("the statement is empty")
    parser = _Parser(_tokenize(text))
    parser.binders()
    parser.accept(":")
    parser.quantifiers()
    tree = parser.iff()
    if parser.peek()[0] != "end":
        raise Unsupported(f"unexpected {parser.peek()[1]!r}")
    for hypothesis in reversed(parser.hypotheses):
        tree = ("→", hypothesis, tree)
    return Parsed(tree=tree, variables=list(parser.declared), hypotheses=parser.hypotheses)


def _number(value: Value) -> int:
    if isinstance(value, bool):
        raise Unsupported("a proposition was used where a number was expected")
    return value


def _truth(value: Value) -> bool:
    if not isinstance(value, bool):
        raise Unsupported("a number was used where a proposition was expected")
    return value


def evaluate(node: Node, env: dict[str, int]) -> Value:
    operator = node[0]
    if operator == "num" or operator == "bool":
        return node[1]
    if operator == "var":
        return env[node[1]]
    if operator == "¬":
        return not _truth(evaluate(node[1], env))
    if operator in {"∧", "∨", "→", "↔"}:
        left = _truth(evaluate(node[1], env))
        if operator == "∧":
            return left and _truth(evaluate(node[2], env))
        if operator == "∨":
            return left or _truth(evaluate(node[2], env))
        if operator == "→":
            return (not left) or _truth(evaluate(node[2], env))
        return left == _truth(evaluate(node[2], env))
    if operator == "Nat.succ":
        return _number(evaluate(node[1], env)) + 1
    if operator == "Nat.pred":
        return max(0, _number(evaluate(node[1], env)) - 1)
    left, right = _number(evaluate(node[1], env)), _number(evaluate(node[2], env))
    if operator == "+":
        return left + right
    if operator == "-":
        return max(0, left - right)
    if operator == "*":
        return left * right
    if operator == "/":
        return left // right if right else 0
    if operator == "%":
        return left % right if right else left
    if operator == "^":
        if left > 1 and right * left.bit_length() > MAX_BITS:
            raise Unsupported("an exponent grows too large to search")
        return left**right
    if operator == "min":
        return min(left, right)
    if operator == "max":
        return max(left, right)
    if operator == "=":
        return left == right
    if operator == "≠":
        return left != right
    if operator == "<":
        return left < right
    if operator == "≤":
        return left <= right
    if operator == ">":
        return left > right
    if operator == "≥":
        return left >= right
    if operator == "∣":
        return right == 0 if left == 0 else right % left == 0
    raise Unsupported(f"unknown operator {operator!r}")


def _conclusion(tree: Node) -> Node:
    while tree[0] == "→":
        tree = tree[2]
    return tree


def _describe(tree: Node, env: dict[str, int]) -> str:
    conclusion = _conclusion(tree)
    if conclusion[0] in _RELATIONS:
        try:
            return f" The two sides evaluate to {evaluate(conclusion[1], env)} and {evaluate(conclusion[2], env)}."
        except Unsupported:
            return ""
    return ""


def search(statement: str, bound: int = 12) -> dict[str, Any]:
    """Look for an assignment in ``0…bound`` that makes the statement false."""
    result: dict[str, Any] = {"supported": False, "found": False, "assignment": None, "variables": [], "bound": bound, "checked": 0}
    try:
        parsed = parse(statement)
        variables = parsed.variables
        result["variables"] = variables
        if not variables:
            value = _truth(evaluate(parsed.tree, {}))
            return {**result, "supported": True, "found": not value, "assignment": {} if not value else None, "checked": 1,
                    "explanation": "The statement has no variables and evaluates to true." if value else "The statement has no variables and evaluates to false."}

        effective = bound
        while effective > 0 and (effective + 1) ** len(variables) > MAX_EVALUATIONS:
            effective -= 1
        if (effective + 1) ** len(variables) > MAX_EVALUATIONS:
            raise Unsupported(f"{len(variables)} variables are too many to search exhaustively")
        result["bound"] = effective

        checked = 0
        for largest in range(effective + 1):
            for values in itertools.product(range(largest + 1), repeat=len(variables)):
                if max(values) != largest:
                    continue
                env = dict(zip(variables, values))
                checked += 1
                if not _truth(evaluate(parsed.tree, env)):
                    shown = ", ".join(f"{name} = {value}" for name, value in env.items())
                    return {**result, "supported": True, "found": True, "assignment": env, "checked": checked,
                            "explanation": f"Counterexample: {shown} makes the statement false.{_describe(parsed.tree, env)}"}
        domain = f"{', '.join(variables)} ∈ 0…{effective}"
        note = f" (the bound was lowered from {bound} to keep the search small)" if effective != bound else ""
        return {**result, "supported": True, "checked": checked,
                "explanation": f"No counterexample with {domain}{note}: all {checked} assignments satisfy the statement. This is evidence, not a proof."}
    except Unsupported as error:
        return {**result, "explanation": f"Not searched: {error}. The search understands {SUPPORTED} over natural numbers."}
    except RecursionError:
        return {**result, "explanation": "Not searched: the statement is nested too deeply."}
