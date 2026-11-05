"""Fechas de TriageBot: fecha límite según prioridad y «hoy» con la hora de España."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")

# Días naturales que se suman a la fecha de creación para obtener la fecha límite.
DAYS_BY_PRIORITY = {"P1": 0, "P2": 1, "P3": 2}


def parse_utc(timestamp: str) -> datetime:
    """Convierte un instante guardado (`2026-01-01T10:00:00Z`) en un datetime con zona."""
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


def local_date(timestamp: str) -> date:
    """Fecha en España del instante dado."""
    return parse_utc(timestamp).astimezone(MADRID).date()


def due_date_for(priority: str, created_at: str) -> str:
    """Fecha límite (`AAAA-MM-DD`) contando desde la fecha de creación en España."""
    return (local_date(created_at) + timedelta(days=DAYS_BY_PRIORITY[priority])).isoformat()


def today() -> date:
    """Hoy, con la hora de España."""
    return datetime.now(MADRID).date()
