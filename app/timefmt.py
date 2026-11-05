"""Formato de tiempo relativo («hace 3 h»). Módulo puro, sin base de datos ni FastAPI."""

from datetime import UTC, datetime

UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def format_ago(utc_value: str, now: str) -> str:
    """Tiempo transcurrido entre `utc_value` y `now` (ambos en UTC), en español y abreviado.

    Menos de 1 min → «hace instantes»; luego «hace N min» (< 1 h), «hace N h» (< 24 h) y
    «hace N d». Un instante futuro (reloj adelantado) cuenta como «hace instantes».
    """
    then = datetime.strptime(utc_value, UTC_FORMAT).replace(tzinfo=UTC)
    current = datetime.strptime(now, UTC_FORMAT).replace(tzinfo=UTC)
    seconds = int((current - then).total_seconds())
    if seconds < 60:
        return "hace instantes"
    if seconds < 3600:
        return f"hace {seconds // 60} min"
    if seconds < 86400:
        return f"hace {seconds // 3600} h"
    return f"hace {seconds // 86400} d"
