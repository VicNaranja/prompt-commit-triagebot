"""Persistencia en SQLite con sqlite3.

La ruta de la base de datos se lee de `DATABASE_URL` (formato `sqlite:///<ruta>`) cada vez que
se abre una conexión, no al importar el módulo: los tests la fijan después de importar la app.
"""

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from app import dates

DEFAULT_DATABASE_URL = "sqlite:///triagebot.db"
SQLITE_PREFIX = "sqlite:///"

# Valor del filtro `technician` que pide los tickets sin asignar.
UNASSIGNED = "none"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    description TEXT NOT NULL,
    category    TEXT NOT NULL,
    priority    TEXT NOT NULL,
    tags        TEXT NOT NULL,
    status      TEXT NOT NULL,
    technician  TEXT,
    due_date    TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
)
"""


def database_path() -> str:
    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    if not url.startswith(SQLITE_PREFIX):
        raise ValueError(f"DATABASE_URL debe empezar por {SQLITE_PREFIX!r}")
    return url[len(SQLITE_PREFIX) :]


def _migrate(conn: sqlite3.Connection) -> None:
    """Añade las columnas nuevas a bases de datos creadas antes de que existieran."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(tickets)")}
    if "technician" not in columns:
        conn.execute("ALTER TABLE tickets ADD COLUMN technician TEXT")
    if "due_date" not in columns:
        conn.execute("ALTER TABLE tickets ADD COLUMN due_date TEXT")
        rows = conn.execute("SELECT id, priority, created_at FROM tickets").fetchall()
        for row in rows:
            conn.execute(
                "UPDATE tickets SET due_date = ? WHERE id = ?",
                (dates.due_date_for(row["priority"], row["created_at"]), row["id"]),
            )


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(database_path())
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(_SCHEMA)
        _migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    ticket = dict(row)
    ticket["tags"] = json.loads(ticket["tags"])
    return ticket


def create_ticket(
    title: str, description: str, category: str, priority: str, tags: list[str]
) -> dict[str, Any]:
    now = _now()
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO tickets
                (title, description, category, priority, tags, status, technician, due_date,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'open', NULL, ?, ?, ?)
            """,
            (
                title,
                description,
                category,
                priority,
                json.dumps(tags),
                dates.due_date_for(priority, now),
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _row_to_dict(row)


def get_ticket(ticket_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_tickets(
    category: str | None = None,
    priority: str | None = None,
    statuses: list[str] | None = None,
    technician: str | None = None,
    due: str | None = None,
) -> list[dict[str, Any]]:
    """Lista tickets, del más reciente al más antiguo.

    - `statuses`: uno o varios estados (se combinan con OR).
    - `technician`: nombre exacto, o `UNASSIGNED` para los que no tienen técnico.
    - `due`: `overdue`, `today` o `tomorrow` (respecto a hoy en España). Otro valor: lista vacía.
    """
    clauses: list[str] = []
    params: list[Any] = []
    for column, value in (("category", category), ("priority", priority)):
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)
    if statuses:
        clauses.append(f"status IN ({', '.join('?' for _ in statuses)})")
        params.extend(statuses)
    if technician == UNASSIGNED:
        clauses.append("technician IS NULL")
    elif technician is not None:
        clauses.append("technician = ?")
        params.append(technician)
    if due is not None:
        today = dates.today()
        due_filters = {
            "overdue": ("due_date < ?", today),
            "today": ("due_date = ?", today),
            "tomorrow": ("due_date = ?", today + timedelta(days=1)),
        }
        if due not in due_filters:
            return []
        clause, day = due_filters[due]
        clauses.append(clause)
        params.append(day.isoformat())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM tickets {where} ORDER BY created_at DESC, id DESC", params
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def update_ticket(ticket_id: int, changes: dict[str, Any]) -> dict[str, Any] | None:
    """Actualiza `status`, `priority` y/o `technician`. Devuelve None si el ticket no existe.

    Si cambia la prioridad, recalcula la fecha límite desde la fecha de creación.
    """
    allowed = {
        key: value for key, value in changes.items() if key in ("status", "priority", "technician")
    }
    with connect() as conn:
        current = conn.execute(
            "SELECT created_at FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        if current is None:
            return None
        if "priority" in allowed:
            allowed["due_date"] = dates.due_date_for(allowed["priority"], current["created_at"])
        assignments = ", ".join(f"{column} = ?" for column in allowed)
        conn.execute(
            f"UPDATE tickets SET {assignments}, updated_at = ? WHERE id = ?",
            [*allowed.values(), _now(), ticket_id],
        )
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    return _row_to_dict(row)
