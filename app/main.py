import logging
from pathlib import Path
from typing import Annotated, Any

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

# Se importa el módulo (no la función) para que los tests puedan sustituir
# `app.classifier.classify_ticket`.
from app import classifier, db
from app.models import (
    ALLOWED_CATEGORIES,
    ALLOWED_PRIORITIES,
    Ticket,
    TicketCreate,
    TicketUpdate,
)

load_dotenv()

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

app = FastAPI(title="TriageBot")


def _fallback_classification() -> dict[str, Any]:
    return {"category": "question", "priority": "P3", "tags": []}


def _is_valid_classification(result: Any) -> bool:
    return (
        isinstance(result, dict)
        and result.get("category") in ALLOWED_CATEGORIES
        and result.get("priority") in ALLOWED_PRIORITIES
        and isinstance(result.get("tags"), list)
        and all(isinstance(tag, str) for tag in result["tags"])
    )


def _classify(title: str, description: str) -> dict[str, Any]:
    """Clasifica el ticket; ante cualquier fallo o salida inválida devuelve el fallback."""
    try:
        result = classifier.classify_ticket(title, description)
    except Exception:
        logger.exception("El clasificador ha fallado; se aplica el fallback")
        return _fallback_classification()
    if not _is_valid_classification(result):
        logger.warning("El clasificador devolvió una salida inválida; se aplica el fallback")
        return _fallback_classification()
    return {"category": result["category"], "priority": result["priority"], "tags": result["tags"]}


def _create_classified_ticket(payload: TicketCreate) -> dict[str, Any]:
    classification = _classify(payload.title, payload.description)
    return db.create_ticket(payload.title, payload.description, classification)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/tickets", status_code=201, response_model=Ticket)
def create_ticket(payload: TicketCreate) -> dict[str, Any]:
    return _create_classified_ticket(payload)


@app.get("/tickets", response_model=list[Ticket])
def list_tickets(
    category: str | None = None,
    priority: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    return db.list_tickets(category=category, priority=priority, status=status)


@app.patch("/tickets/{ticket_id}", response_model=Ticket)
def update_ticket(ticket_id: int, payload: TicketUpdate) -> dict[str, Any]:
    ticket = db.update_ticket(ticket_id, status=payload.status, priority=payload.priority)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")
    return ticket


# --- Frontend (Jinja2 + HTMX) -------------------------------------------------------------------
# Rutas HTML separadas de la API JSON: HTMX envía formularios, no JSON, y un 4xx no se pinta.


def _render_table(
    request: Request,
    category: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    tickets = db.list_tickets(category=category, priority=priority, status=status)
    return templates.TemplateResponse(
        request, "_tickets_table.html", {"tickets": tickets, "error": error}
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    tickets = db.list_tickets()
    return templates.TemplateResponse(request, "index.html", {"tickets": tickets, "error": None})


@app.get("/ui/tickets", response_class=HTMLResponse)
def ui_list_tickets(
    request: Request,
    category: str | None = None,
    priority: str | None = None,
    status: str | None = None,
) -> HTMLResponse:
    return _render_table(request, category, priority, status)


@app.post("/ui/tickets", response_class=HTMLResponse)
def ui_create_ticket(
    request: Request,
    title: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    category: Annotated[str | None, Form()] = None,
    priority: Annotated[str | None, Form()] = None,
    status: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    # `category`, `priority` y `status` son los filtros activos del tablero, no datos del ticket.
    try:
        payload = TicketCreate(title=title, description=description)
    except ValidationError:
        error = "El título (máx. 200) y la descripción (máx. 5000) no pueden estar vacíos."
        return _render_table(request, category, priority, status, error=error)
    _create_classified_ticket(payload)
    return _render_table(request, category, priority, status)


@app.post("/ui/tickets/{ticket_id}", response_class=HTMLResponse)
def ui_update_ticket(
    request: Request,
    ticket_id: int,
    set_status: Annotated[str | None, Form()] = None,
    set_priority: Annotated[str | None, Form()] = None,
    category: Annotated[str | None, Form()] = None,
    priority: Annotated[str | None, Form()] = None,
    status: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    error = None
    try:
        update = TicketUpdate(status=set_status or None, priority=set_priority or None)
    except ValidationError:
        error = "Valor de estado o prioridad no válido."
    else:
        if db.update_ticket(ticket_id, status=update.status, priority=update.priority) is None:
            error = f"El ticket {ticket_id} no existe."
    return _render_table(request, category, priority, status, error=error)
