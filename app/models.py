"""Modelos Pydantic y valores permitidos de TriageBot."""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints, field_validator

ALLOWED_CATEGORIES = {"bug", "feature_request", "question", "urgent"}
ALLOWED_PRIORITIES = {"P1", "P2", "P3"}
ALLOWED_STATUSES = {"open", "in_progress", "resolved", "closed"}

Category = Literal["bug", "feature_request", "question", "urgent"]
Priority = Literal["P1", "P2", "P3"]
Status = Literal["open", "in_progress", "resolved", "closed"]

# Orden en que se muestran los estados (columnas del kanban, desplegables).
STATUS_ORDER = ["open", "in_progress", "resolved", "closed"]

# Grupos de estados para filtrar: activos, resueltos y cerrados (SPEC §2, «Estados»).
STATUS_GROUPS = {
    "active": ["open", "in_progress"],
    "resolved": ["resolved"],
    "closed": ["closed"],
}

# Lista estática de técnicos (SPEC §2, «Técnicos»).
TECHNICIANS = [f"Técnico {number}" for number in range(1, 11)]

TITLE_MAX_LENGTH = 200
DESCRIPTION_MAX_LENGTH = 5000

Title = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TITLE_MAX_LENGTH)
]
Description = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=DESCRIPTION_MAX_LENGTH),
]


class TicketCreate(BaseModel):
    title: Title
    description: Description


class TicketUpdate(BaseModel):
    """Solo `status`, `priority` y `technician` son modificables tras la creación.

    `technician: null` deja el ticket sin asignar; para distinguirlo de «no enviado» se usa
    `model_fields_set`.
    """

    status: Status | None = None
    priority: Priority | None = None
    technician: str | None = None

    @field_validator("technician")
    @classmethod
    def technician_must_exist(cls, value: str | None) -> str | None:
        if value is not None and value not in TECHNICIANS:
            raise ValueError("El técnico no está en la lista de técnicos")
        return value


class Ticket(BaseModel):
    id: int
    title: str
    description: str
    category: Category
    priority: Priority
    tags: list[str]
    status: Status
    technician: str | None
    due_date: date
    created_at: datetime
    updated_at: datetime
