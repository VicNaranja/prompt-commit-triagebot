"""Cálculo de la fecha límite (`due_at`) de un ticket según su prioridad.

Módulo puro (sin base de datos ni FastAPI). El equipo trabaja en España peninsular: «hoy» y
«mañana» se entienden en hora de Madrid, con horario de verano. Los datos entran y salen en UTC.
"""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")
UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# P1: hoy mismo · P2: mañana · P3: pasado mañana (días que se suman al día de creación).
DAYS_TO_DUE = {"P1": 0, "P2": 1, "P3": 2}
END_OF_DAY = time(23, 59, 59)


def compute_due_at(created_at: str, priority: str) -> str:
    """Fecha límite en UTC: las 23:59:59 (hora de Madrid) del día de creación + días por prioridad.

    El «día de creación» es el día en Madrid, no el día UTC: un ticket creado a las 23:30 UTC en
    invierno ya es del día siguiente para el equipo.
    """
    created = datetime.strptime(created_at, UTC_FORMAT).replace(tzinfo=UTC)
    due_day = created.astimezone(MADRID).date() + timedelta(days=DAYS_TO_DUE[priority])
    due_local = datetime.combine(due_day, END_OF_DAY, tzinfo=MADRID)
    return due_local.astimezone(UTC).strftime(UTC_FORMAT)


def format_madrid(utc_value: str) -> str:
    """Muestra un instante UTC en hora de Madrid como «05/01 23:59»."""
    moment = datetime.strptime(utc_value, UTC_FORMAT).replace(tzinfo=UTC)
    return moment.astimezone(MADRID).strftime("%d/%m %H:%M")
