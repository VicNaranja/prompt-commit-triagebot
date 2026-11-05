"""Modelos Pydantic y valores permitidos del dominio."""

import unicodedata
from datetime import datetime
from typing import Annotated, Literal, get_args

from pydantic import AfterValidator, BaseModel, Field, StringConstraints, model_validator

Category = Literal["bug", "feature_request", "question", "urgent"]
Priority = Literal["P1", "P2", "P3"]
Status = Literal["open", "in_progress", "resolved", "closed"]

ALLOWED_CATEGORIES = set(get_args(Category))
ALLOWED_PRIORITIES = set(get_args(Priority))
ALLOWED_STATUSES = set(get_args(Status))

# Orden de aparición en los desplegables (el de `Literal`) y etiquetas visibles en pantalla.
# Los valores internos de la API no cambian; solo se traducen al mostrarlos.
CATEGORIES = get_args(Category)
STATUSES = get_args(Status)
CATEGORY_LABELS = {
    "bug": "Error",
    "feature_request": "Petición de mejora",
    "question": "Pregunta",
    "urgent": "Urgente",
}
STATUS_LABELS = {
    "open": "Abierto",
    "in_progress": "En curso",
    "resolved": "Resuelto",
    "closed": "Cerrado",
}

MAX_ASSIGNEES = 10
MAX_ASSIGNEE_LENGTH = 60


def _has_meaning(char: str) -> bool:
    """Letra o número de cualquier alfabeto (日本語, العربية…), o emoji (categoría Unicode «So»)."""
    return char.isalnum() or unicodedata.category(char) == "So"


def _require_meaningful(value: str) -> str:
    """Un título hecho solo de signos de puntuación o símbolos sueltos («!!!», «+++») no vale."""
    if not any(_has_meaning(char) for char in value):
        raise ValueError("El título debe contener al menos una letra, un número o un emoji.")
    return value


Title = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    AfterValidator(_require_meaningful),
]
Description = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)
]


AssigneeName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_ASSIGNEE_LENGTH)
]
# El máximo se aplica a la lista recibida. Los duplicados (sin distinguir mayúsculas) los
# elimina `db.py`, que conserva la primera grafía y el orden.
Assignees = Annotated[list[AssigneeName], Field(max_length=MAX_ASSIGNEES)]


class TicketCreate(BaseModel):
    title: Title
    description: Description
    assignees: Assignees = Field(default_factory=list)


class TicketUpdate(BaseModel):
    """Se pueden cambiar `status`, `priority` y `assignees`; al menos uno es obligatorio.

    `assignees` sustituye la lista completa (`[]` quita todos los responsables).
    """

    status: Status | None = None
    priority: Priority | None = None
    assignees: Assignees | None = None

    @model_validator(mode="after")
    def _require_at_least_one_field(self) -> "TicketUpdate":
        if self.status is None and self.priority is None and self.assignees is None:
            raise ValueError("Hay que indicar al menos `status`, `priority` o `assignees`.")
        return self


class Ticket(BaseModel):
    id: int
    title: str
    description: str
    category: Category
    priority: Priority
    tags: list[str]
    status: Status
    status_changed_at: datetime
    assignees: list[str]
    due_at: datetime
    overdue: bool
    created_at: datetime
    updated_at: datetime
