"""Modelos Pydantic y valores permitidos del dominio."""

from datetime import datetime
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, StringConstraints, model_validator

Category = Literal["bug", "feature_request", "question", "urgent"]
Priority = Literal["P1", "P2", "P3"]
Status = Literal["open", "in_progress", "closed"]

ALLOWED_CATEGORIES = set(get_args(Category))
ALLOWED_PRIORITIES = set(get_args(Priority))
ALLOWED_STATUSES = set(get_args(Status))

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Description = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)
]


class TicketCreate(BaseModel):
    title: Title
    description: Description


class TicketUpdate(BaseModel):
    """Solo `status` y `priority` son modificables; al menos uno es obligatorio."""

    status: Status | None = None
    priority: Priority | None = None

    @model_validator(mode="after")
    def _require_at_least_one_field(self) -> "TicketUpdate":
        if self.status is None and self.priority is None:
            raise ValueError("Hay que indicar al menos `status` o `priority`.")
        return self


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
