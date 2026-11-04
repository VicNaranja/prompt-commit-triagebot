import logging
from typing import Annotated

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query

# Se importa el módulo (no la función) para que los tests puedan sustituir
# `app.classifier.classify_ticket`.
from app import classifier, db
from app.models import Ticket, TicketCreate, TicketUpdate

load_dotenv()

logger = logging.getLogger(__name__)

app = FastAPI(title="TriageBot")


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


@app.post("/tickets", status_code=201, response_model=Ticket)
def create_ticket(payload: TicketCreate) -> dict:
    classification = _classify(payload.title, payload.description)
    return db.create_ticket(
        title=payload.title,
        description=payload.description,
        category=classification["category"],
        priority=classification["priority"],
        tags=classification["tags"],
    )


@app.get("/tickets", response_model=list[Ticket])
def list_tickets(
    category: Annotated[str | None, Query()] = None,
    priority: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
) -> list[dict]:
    return db.list_tickets(category=category, priority=priority, status=status)


@app.patch("/tickets/{ticket_id}", response_model=Ticket)
def update_ticket(ticket_id: int, payload: TicketUpdate) -> dict:
    changes = payload.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(status_code=422, detail="El cuerpo debe incluir status o priority")
    ticket = db.update_ticket(ticket_id, changes)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")
    return ticket
