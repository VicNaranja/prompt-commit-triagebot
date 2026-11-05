import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

# Se importa el módulo (no la función) para que los tests puedan sustituir
# `app.classifier.classify_ticket`.
from app import classifier, db
from app.deadlines import format_madrid
from app.models import (
    ALLOWED_CATEGORIES,
    ALLOWED_PRIORITIES,
    CATEGORIES,
    CATEGORY_LABELS,
    STATUS_LABELS,
    STATUSES,
    Ticket,
    TicketCreate,
    TicketUpdate,
)
from app.pagination import PAGE_SIZE, clamp_page, last_page, page_window, parse_page
from app.timefmt import format_ago

load_dotenv()

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.filters["madrid"] = format_madrid
templates.env.filters["ago"] = lambda value: format_ago(value, db._now())
templates.env.globals.update(
    CATEGORIES=CATEGORIES,
    CATEGORY_LABELS=CATEGORY_LABELS,
    STATUSES=STATUSES,
    STATUS_LABELS=STATUS_LABELS,
)

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
    return db.create_ticket(
        payload.title, payload.description, classification, assignees=payload.assignees
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/tickets", status_code=201, response_model=Ticket)
def create_ticket(payload: TicketCreate) -> dict[str, Any]:
    return _create_classified_ticket(payload)


@app.get("/tickets", response_model=list[Ticket])
def list_tickets(
    response: Response,
    category: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    assignee: str | None = None,
    unassigned: bool = False,
    overdue: bool | None = None,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    """Lista de tickets. Sin `limit` devuelve todos; `X-Total-Count` trae el total filtrado."""
    filters = {
        "category": category,
        "priority": priority,
        "status": status,
        "assignee": assignee,
        "unassigned": unassigned,
        "overdue": overdue,
    }
    response.headers["X-Total-Count"] = str(db.count_tickets(**filters))
    return db.list_tickets(**filters, limit=limit, offset=offset)


@app.patch("/tickets/{ticket_id}", response_model=Ticket)
def update_ticket(ticket_id: int, payload: TicketUpdate) -> dict[str, Any]:
    ticket = db.update_ticket(
        ticket_id,
        status=payload.status,
        priority=payload.priority,
        assignees=payload.assignees,
    )
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")
    return ticket


# --- Frontend (Jinja2 + HTMX) -------------------------------------------------------------------
# Rutas HTML separadas de la API JSON: HTMX envía formularios, no JSON, y un 4xx no se pinta.


@dataclass
class Filters:
    """Filtros activos del tablero. Se reenvían en cada petición HTMX para conservarlos."""

    category: str | None = None
    priority: str | None = None
    status: str | None = None
    assignee: str | None = None
    unassigned: bool = False
    overdue: bool = False  # casilla «Solo vencidos»: marcada = solo vencidos, sin marcar = todos


def _parse_names(raw: str | None) -> list[str] | None:
    """Convierte «Ana, Luis» en ["Ana", "Luis"]. Campo ausente → None; vacío → []."""
    if raw is None:
        return None
    return [name.strip() for name in raw.split(",") if name.strip()]


def _board_url(filters: Filters, page: int = 1) -> str:
    """Dirección del tablero con sus filtros y página: `/`, `/?status=open&page=2`…"""
    params: list[tuple[str, str]] = []
    for name in ("category", "priority", "status", "assignee"):
        value = getattr(filters, name)
        if value:
            params.append((name, value))
    if filters.unassigned:
        params.append(("unassigned", "true"))
    if filters.overdue:
        params.append(("overdue", "true"))
    if page > 1:
        params.append(("page", str(page)))
    return f"/?{urlencode(params)}" if params else "/"


def _board(
    filters: Filters, requested_page: int, error: str | None = None, oob: bool = False
) -> tuple[dict[str, Any], int]:
    """Datos del tablero para la plantilla y la página realmente mostrada (ya corregida)."""
    query = {**asdict(filters), "overdue": filters.overdue or None}
    total = db.count_tickets(**query)
    page = clamp_page(requested_page, total)
    tickets = db.list_tickets(**query, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)
    pages = last_page(total)
    first = (page - 1) * PAGE_SIZE + 1 if tickets else 0

    def link(number: int, label: str | None = None) -> dict[str, Any]:
        return {
            "number": number,
            "label": label or str(number),
            "href": _board_url(filters, number),
            "hx_get": f"/ui/tickets?page={number}",
            "current": number == page,
        }

    context = {
        "tickets": tickets,
        "error": error,
        "assignee_names": db.list_assignee_names(),
        "oob": oob,
        "filters": filters,
        "page": page,
        "pages": pages,
        "total": total,
        "range_start": first,
        "range_end": first + len(tickets) - 1 if tickets else 0,
        "page_links": [link(n) if n else None for n in page_window(page, pages)],
        "previous": link(page - 1) if page > 1 else None,
        "next": link(page + 1) if page < pages else None,
    }
    return context, page


def _render_table(
    request: Request,
    filters: Filters,
    page: int = 1,
    error: str | None = None,
    trigger: str | None = None,
    history: str = "replace",
) -> HTMLResponse:
    """Pinta la tabla (respuesta parcial de HTMX).

    `trigger` es un evento para el formulario (cabecera `HX-Trigger`). La dirección del navegador
    se actualiza con la cabecera `HX-Push-Url` (nueva entrada del historial: filtrar o paginar)
    o `HX-Replace-Url` (sin entrada nueva: editar o crear).
    """
    context, shown_page = _board(filters, page, error, oob=True)
    response = templates.TemplateResponse(request, "_tickets_table.html", context)
    header = "HX-Push-Url" if history == "push" else "HX-Replace-Url"
    response.headers[header] = _board_url(filters, shown_page)
    if trigger:
        response.headers["HX-Trigger"] = trigger
    return response


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    category: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    assignee: str | None = None,
    unassigned: bool = False,
    overdue: bool = False,
    page: str | None = None,
) -> HTMLResponse:
    """Tablero completo. Acepta los filtros y la página en la URL: `/?status=open&page=2`."""
    filters = Filters(category, priority, status, assignee, unassigned, overdue)
    context, _ = _board(filters, parse_page(page))
    return templates.TemplateResponse(request, "index.html", context)


@app.get("/ui/tickets", response_class=HTMLResponse)
def ui_list_tickets(
    request: Request,
    category: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    assignee: str | None = None,
    unassigned: bool = False,
    overdue: bool = False,
    page: str | None = None,
) -> HTMLResponse:
    filters = Filters(category, priority, status, assignee, unassigned, overdue)
    return _render_table(request, filters, parse_page(page), history="push")


def _create_error_message(error: ValidationError) -> str:
    fields = {item["loc"][0] for item in error.errors() if item["loc"]}
    messages = []
    if fields & {"title", "description"}:
        messages.append(
            "El título (máx. 200, con al menos una letra, un número o un emoji) y la descripción "
            "(máx. 5000) no pueden estar vacíos."
        )
    if "assignees" in fields:
        messages.append(
            "Los responsables no pueden estar vacíos ni superar 60 caracteres, y como máximo "
            "puede haber 10."
        )
    return " ".join(messages)


@app.post("/ui/tickets", response_class=HTMLResponse)
def ui_create_ticket(
    request: Request,
    title: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    assignees: Annotated[str, Form()] = "",
    category: Annotated[str | None, Form()] = None,
    priority: Annotated[str | None, Form()] = None,
    status: Annotated[str | None, Form()] = None,
    assignee: Annotated[str | None, Form()] = None,
    unassigned: Annotated[bool, Form()] = False,
    overdue: Annotated[bool, Form()] = False,
) -> HTMLResponse:
    # `category`, `priority`, `status`, `assignee`, `unassigned` y `overdue` son los filtros
    # activos del tablero, no datos del ticket (el responsable del ticket va en `assignees`).
    # Al crear se vuelve a la página 1, donde aparece el ticket más reciente.
    filters = Filters(category, priority, status, assignee, unassigned, overdue)
    try:
        payload = TicketCreate(
            title=title, description=description, assignees=_parse_names(assignees) or []
        )
    except ValidationError as error:
        return _render_table(request, filters, error=_create_error_message(error))
    _create_classified_ticket(payload)
    # El formulario se vacía solo cuando el ticket se ha creado (no si hubo un error).
    return _render_table(request, filters, trigger="ticket-created")


@app.post("/ui/tickets/{ticket_id}", response_class=HTMLResponse)
def ui_update_ticket(
    request: Request,
    ticket_id: int,
    set_status: Annotated[str | None, Form()] = None,
    set_priority: Annotated[str | None, Form()] = None,
    set_assignees: Annotated[str | None, Form()] = None,
    category: Annotated[str | None, Form()] = None,
    priority: Annotated[str | None, Form()] = None,
    status: Annotated[str | None, Form()] = None,
    assignee: Annotated[str | None, Form()] = None,
    unassigned: Annotated[bool, Form()] = False,
    overdue: Annotated[bool, Form()] = False,
    page: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    # `page` es la página que está viendo el usuario: se mantiene al editar desde la fila.
    filters = Filters(category, priority, status, assignee, unassigned, overdue)
    error = None
    try:
        update = TicketUpdate(
            status=set_status or None,
            priority=set_priority or None,
            assignees=_parse_names(set_assignees),
        )
    except ValidationError:
        error = (
            "Valor no válido: revisa estado, prioridad y responsables "
            "(como máximo 10, de hasta 60 caracteres cada uno)."
        )
    else:
        updated = db.update_ticket(
            ticket_id,
            status=update.status,
            priority=update.priority,
            assignees=update.assignees,
        )
        if updated is None:
            error = f"El ticket {ticket_id} no existe."
    return _render_table(request, filters, parse_page(page), error=error)
