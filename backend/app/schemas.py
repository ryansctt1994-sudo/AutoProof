from typing import Literal, Optional

from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    """A proof obligation. The statement is fixed for the life of the session."""
    title: str = Field(default="Untitled proof", max_length=120)
    statement: str = Field(min_length=1, max_length=2_000)
    binders: str = Field(default="", max_length=2_000)
    natural_language: Optional[str] = Field(default=None, max_length=2_000)


class SuggestRequest(BaseModel):
    session_id: Optional[str] = Field(default=None, max_length=64)
    goal: Optional[str] = Field(default=None, max_length=10_000)
    context: str = Field(default="", max_length=20_000)


class ApplyTacticRequest(BaseModel):
    """Only the proof is user input. The goal comes from the stored session."""
    session_id: str = Field(min_length=1, max_length=64)
    tactic: str = Field(min_length=1, max_length=200, description="Label for the proof step in the tree")
    proof: str = Field(min_length=1, max_length=40_000, description="Tactic block placed under `by`")


class FeedbackRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    tactic: str = Field(min_length=1, max_length=2_000)
    verdict: Literal["approved", "edited", "rejected"]
    edited_tactic: Optional[str] = Field(default=None, max_length=2_000)
    note: Optional[str] = Field(default=None, max_length=4_000)


class CounterexampleRequest(BaseModel):
    session_id: Optional[str] = Field(default=None, max_length=64)
    statement: Optional[str] = Field(default=None, max_length=10_000)
    binders: str = Field(default="", max_length=2_000)
    bounds: int = Field(default=12, ge=1, le=100)
