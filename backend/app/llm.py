import json
import os

import httpx


def deterministic_suggestions(goal: str) -> list[dict]:
    lower = goal.lower()
    suggestions = [
        {"tactic": "simp", "confidence": 0.91, "explanation": "Simplifies standard definitions and registered rewrite rules."},
        {"tactic": "rfl", "confidence": 0.73, "explanation": "Closes the goal if both sides reduce to the same expression."},
        {"tactic": "omega", "confidence": 0.66, "explanation": "Decides many linear arithmetic goals over naturals and integers."},
    ]
    if "=" in goal and ("+" in goal or "*" in goal):
        suggestions.insert(0, {"tactic": "ring", "confidence": 0.84, "explanation": "Normalizes polynomial expressions on both sides of the equality."})
    if "→" in goal or "forall" in lower:
        suggestions.insert(0, {"tactic": "intro h", "confidence": 0.79, "explanation": "Introduces the premise or universally quantified variable into the context."})
    return suggestions[:5]


async def suggest(goal: str, context: str) -> tuple[list[dict], str]:
    base_url = os.getenv("LLM_BASE_URL")
    model = os.getenv("LLM_MODEL")
    if not base_url or not model:
        return deterministic_suggestions(goal), "demo"
    prompt = (
        "You are a careful Lean 4 proof assistant. Given this goal and context, suggest at most five small tactics. "
        'Return strict JSON: {"tactics":[{"tactic":string,"explanation":string,"confidence":number}]}.\n'
        f"Goal: {goal}\nContext: {context}"
    )
    try:
        async with httpx.AsyncClient(timeout=18) as client:
            response = await client.post(f"{base_url.rstrip('/')}/chat/completions", json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2})
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            tactics = json.loads(content).get("tactics", [])
            if tactics:
                return tactics[:5], "llm"
    except Exception:
        pass
    return deterministic_suggestions(goal), "fallback"

