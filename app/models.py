"""Modelos Pydantic de TriageBot."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints

ALLOWED_CATEGORIES = {"bug", "feature_request", "question", "urgent"}
ALLOWED_PRIORITIES = {"P1", "P2", "P3"}
ALLOWED_STATUSES = {"open", "in_progress", "closed"}

Category = Literal["bug", "feature_request", "question", "urgent"]
Priority = Literal["P1", "P2", "P3"]
Status = Literal["open", "in_progress", "closed"]

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Description = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)
]


class TicketCreate(BaseModel):
    title: Title
    description: Description


class TicketUpdate(BaseModel):
    """Solo `status` y `priority` son modificables tras la creación."""

    status: Status | None = None
    priority: Priority | None = None


class Ticket(BaseModel):
    id: int
    title: str
    description: str
    category: Category
    priority: Priority
    tags: list[str]
    status: Status
    created_at: datetime
    updated_at: datetime
