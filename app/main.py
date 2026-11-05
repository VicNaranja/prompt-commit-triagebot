import logging
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

# Se importa el módulo (no la función) para que los tests puedan sustituir
# `app.classifier.classify_ticket`.
from app import classifier, dates, db
from app.models import (
    ALLOWED_PRIORITIES,
    ALLOWED_STATUSES,
    DESCRIPTION_MAX_LENGTH,
    STATUS_GROUPS,
    STATUS_ORDER,
    TECHNICIANS,
    TITLE_MAX_LENGTH,
    Ticket,
    TicketCreate,
    TicketUpdate,
)

load_dotenv()

logger = logging.getLogger(__name__)

app = FastAPI(title="TriageBot")
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")

# Textos en español para las webs.
STATUS_LABELS = {
    "open": "Abierto",
    "in_progress": "En curso",
    "resolved": "Resuelto",
    "closed": "Cerrado",
}
CATEGORY_LABELS = {
    "bug": "Error",
    "feature_request": "Petición de mejora",
    "question": "Duda",
    "urgent": "Urgente",
}


def _format_date(value: str) -> str:
    """`2026-11-05` → `05/11/2026`."""
    year, month, day = value.split("-")
    return f"{day}/{month}/{year}"


def _format_datetime(value: str) -> str:
    """Instante UTC guardado → fecha y hora de España (`05/11/2026 15:30`)."""
    return dates.parse_utc(value).astimezone(dates.MADRID).strftime("%d/%m/%Y %H:%M")


templates.env.filters["fecha"] = _format_date
templates.env.filters["fecha_hora"] = _format_datetime
templates.env.globals.update(
    STATUS_LABELS=STATUS_LABELS,
    CATEGORY_LABELS=CATEGORY_LABELS,
    STATUS_ORDER=STATUS_ORDER,
    PRIORITIES=sorted(ALLOWED_PRIORITIES),
    TECHNICIANS=TECHNICIANS,
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _classify(title: str, description: str) -> dict:
    """Clasifica el ticket sin dejar que ningún error del clasificador llegue al endpoint."""
    try:
        result = classifier.classify_ticket(title, description)
    except Exception:
        logger.warning("El clasificador lanzó una excepción; se aplica el fallback", exc_info=True)
        return classifier.fallback()
    return classifier.validate_classification(result) or classifier.fallback()


def _create(title: str, description: str) -> dict:
    """Clasifica y guarda un ticket ya validado. Lo usan la API y la web de creación."""
    classification = _classify(title, description)
    return db.create_ticket(
        title=title,
        description=description,
        category=classification["category"],
        priority=classification["priority"],
        tags=classification["tags"],
    )


@app.post("/tickets", status_code=201, response_model=Ticket)
def create_ticket(payload: TicketCreate) -> dict:
    return _create(payload.title, payload.description)


@app.get("/tickets", response_model=list[Ticket])
def list_tickets(
    category: Annotated[str | None, Query()] = None,
    priority: Annotated[str | None, Query()] = None,
    status: Annotated[list[str] | None, Query()] = None,
    technician: Annotated[str | None, Query()] = None,
    due: Annotated[str | None, Query()] = None,
) -> list[dict]:
    return db.list_tickets(
        category=category, priority=priority, statuses=status, technician=technician, due=due
    )


@app.patch("/tickets/{ticket_id}", response_model=Ticket)
def update_ticket(ticket_id: int, payload: TicketUpdate) -> dict:
    sent = payload.model_dump(include=payload.model_fields_set)
    # `technician: null` sí es un cambio (desasignar); `status`/`priority` a null se ignoran.
    changes = {
        key: value for key, value in sent.items() if value is not None or key == "technician"
    }
    if not changes:
        raise HTTPException(
            status_code=422, detail="El cuerpo debe incluir status, priority o technician"
        )
    ticket = db.update_ticket(ticket_id, changes)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")
    return ticket


# --- Web de creación de tickets (/nuevo) -------------------------------------------------
# Tres pasos: título → descripción → agradecimiento. Con HTMX cada paso sustituye al
# anterior sin recargar; sin JavaScript, los formularios devuelven la página completa.

FormText = Annotated[str, Form()]


def _render_step(request: Request, step_template: str, **context) -> HTMLResponse:
    """Devuelve solo el paso si la petición viene de HTMX, o la página completa si no."""
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, step_template, context)
    return templates.TemplateResponse(
        request, "nuevo.html", {"step_template": step_template, **context}
    )


def _text_error(value: str, max_length: int, empty_message: str, long_message: str) -> str | None:
    """Aplica las mismas reglas que la API (1..max caracteres tras quitar espacios)."""
    if not value:
        return empty_message
    if len(value) > max_length:
        return long_message
    return None


@app.get("/nuevo", response_class=HTMLResponse)
def new_ticket_page(request: Request) -> HTMLResponse:
    return _render_step(request, "_nuevo_titulo.html", title="")


@app.post("/nuevo/titulo", response_class=HTMLResponse)
def new_ticket_title(request: Request, title: FormText = "") -> HTMLResponse:
    title = title.strip()
    error = _text_error(
        title,
        TITLE_MAX_LENGTH,
        "Escriba un título para continuar.",
        f"El título no puede superar los {TITLE_MAX_LENGTH} caracteres.",
    )
    if error:
        return _render_step(request, "_nuevo_titulo.html", title=title, error=error)
    return _render_step(request, "_nuevo_descripcion.html", title=title, description="")


@app.post("/nuevo/descripcion", response_class=HTMLResponse)
def new_ticket_description(
    request: Request, title: FormText = "", description: FormText = ""
) -> HTMLResponse:
    title = title.strip()
    description = description.strip()
    title_error = _text_error(
        title,
        TITLE_MAX_LENGTH,
        "Escriba un título para continuar.",
        f"El título no puede superar los {TITLE_MAX_LENGTH} caracteres.",
    )
    if title_error:
        # Solo pasa si se llega sin pasar por el paso 1: se vuelve a pedir el título.
        return _render_step(request, "_nuevo_titulo.html", title=title, error=title_error)
    error = _text_error(
        description,
        DESCRIPTION_MAX_LENGTH,
        "Escriba una descripción para enviar el ticket.",
        f"La descripción no puede superar los {DESCRIPTION_MAX_LENGTH} caracteres.",
    )
    if error:
        return _render_step(
            request, "_nuevo_descripcion.html", title=title, description=description, error=error
        )
    _create(title, description)
    return _render_step(request, "_nuevo_gracias.html")


# --- Web de gestión de tickets (/) --------------------------------------------------------
# Filtros de la web (en español) → filtros de `db.list_tickets`.

WEB_STATUS_FILTERS = {**STATUS_GROUPS, "all": None}
WEB_DUE_FILTERS = {"overdue", "today", "tomorrow"}
WEB_ALL = "all"

# Evento HTMX que avisa a la tabla y al tablero de que hay que refrescarse.
TICKETS_CHANGED = "ticketsChanged"


def _web_filters(estado: str, prioridad: str, tecnico: str, vence: str) -> dict:
    """Traduce los filtros de la web a parámetros de `db.list_tickets`."""
    return {
        "statuses": WEB_STATUS_FILTERS.get(estado, STATUS_GROUPS["active"]),
        "priority": prioridad if prioridad in ALLOWED_PRIORITIES else None,
        "technician": tecnico if tecnico == db.UNASSIGNED or tecnico in TECHNICIANS else None,
        "due": vence if vence in WEB_DUE_FILTERS else None,
    }


def _table_context(estado: str, prioridad: str, tecnico: str, vence: str) -> dict:
    return {
        "tickets": db.list_tickets(**_web_filters(estado, prioridad, tecnico, vence)),
        "today": dates.today().isoformat(),
    }


@app.get("/", response_class=HTMLResponse)
def management_page(request: Request) -> HTMLResponse:
    filters = {"estado": "active", "prioridad": WEB_ALL, "tecnico": WEB_ALL, "vence": WEB_ALL}
    context = _table_context(**filters)
    return templates.TemplateResponse(request, "index.html", {"filters": filters, **context})


@app.get("/gestion/tabla", response_class=HTMLResponse)
def management_table(
    request: Request,
    estado: str = "active",
    prioridad: str = WEB_ALL,
    tecnico: str = WEB_ALL,
    vence: str = WEB_ALL,
) -> HTMLResponse:
    context = _table_context(estado, prioridad, tecnico, vence)
    return templates.TemplateResponse(request, "_tickets_table.html", context)


def _ticket_or_404(ticket_id: int) -> dict:
    ticket = db.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")
    return ticket


def _detail_response(request: Request, ticket: dict, error: str | None = None) -> HTMLResponse:
    context = {"ticket": ticket, "today": dates.today().isoformat(), "error": error}
    return templates.TemplateResponse(request, "_ticket_detail.html", context)


@app.get("/gestion/tickets/{ticket_id}", response_class=HTMLResponse)
def ticket_detail(request: Request, ticket_id: int) -> HTMLResponse:
    return _detail_response(request, _ticket_or_404(ticket_id))


@app.post("/gestion/tickets/{ticket_id}", response_class=HTMLResponse)
def ticket_detail_save(
    request: Request,
    ticket_id: int,
    status: FormText = "",
    priority: FormText = "",
    technician: FormText = "",
    accion: FormText = "guardar",
) -> HTMLResponse:
    ticket = _ticket_or_404(ticket_id)
    if accion == "resolver":
        changes: dict = {"status": "resolved"}
    else:
        if status not in ALLOWED_STATUSES or priority not in ALLOWED_PRIORITIES:
            return _detail_response(request, ticket, "Estado o prioridad no válidos.")
        if technician and technician not in TECHNICIANS:
            return _detail_response(request, ticket, "El técnico no está en la lista.")
        changes = {"status": status, "priority": priority, "technician": technician or None}
    db.update_ticket(ticket_id, changes)
    # Respuesta vacía: cierra la ventana. El evento hace que la tabla o el tablero se refresquen.
    return HTMLResponse("", headers={"HX-Trigger": TICKETS_CHANGED})


# --- Tablero kanban (/kanban) -------------------------------------------------------------


def _board_context(tecnico: str) -> dict:
    technician = tecnico if tecnico == db.UNASSIGNED or tecnico in TECHNICIANS else None
    tickets = db.list_tickets(technician=technician)
    columns = {status: [] for status in STATUS_ORDER}
    for ticket in tickets:
        columns[ticket["status"]].append(ticket)
    return {"columns": columns, "today": dates.today().isoformat()}


@app.get("/kanban", response_class=HTMLResponse)
def kanban_page(request: Request) -> HTMLResponse:
    context = {"tecnico": WEB_ALL, **_board_context(WEB_ALL)}
    return templates.TemplateResponse(request, "kanban.html", context)


@app.get("/kanban/tablero", response_class=HTMLResponse)
def kanban_board(request: Request, tecnico: str = WEB_ALL) -> HTMLResponse:
    return templates.TemplateResponse(request, "_kanban_board.html", _board_context(tecnico))


@app.post("/kanban/tickets/{ticket_id}/estado", response_class=HTMLResponse)
def kanban_move(
    request: Request, ticket_id: int, status: FormText = "", tecnico: FormText = WEB_ALL
) -> HTMLResponse:
    if status not in ALLOWED_STATUSES:
        raise HTTPException(status_code=422, detail="Estado no válido")
    if db.update_ticket(ticket_id, {"status": status}) is None:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")
    return templates.TemplateResponse(request, "_kanban_board.html", _board_context(tecnico))
