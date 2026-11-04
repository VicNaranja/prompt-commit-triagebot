"""Persistencia en SQLite (sqlite3 de la librería estándar).

La ruta de la base de datos se lee de `DATABASE_URL` en cada conexión, no al importar el módulo:
los tests la fijan después de importar la app.
"""

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

DEFAULT_DATABASE_URL = "sqlite:///triagebot.db"
_SQLITE_PREFIX = "sqlite:///"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    description TEXT NOT NULL,
    category    TEXT NOT NULL,
    priority    TEXT NOT NULL,
    tags        TEXT NOT NULL DEFAULT '[]',
    status      TEXT NOT NULL DEFAULT 'open',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
)
"""


def _database_path() -> str:
    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    if not url.startswith(_SQLITE_PREFIX):
        raise ValueError(f"DATABASE_URL debe empezar por '{_SQLITE_PREFIX}': {url!r}")
    return url[len(_SQLITE_PREFIX) :]


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Abre una conexión por operación, crea la tabla si falta y confirma al salir sin error."""
    conn = sqlite3.connect(_database_path())
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _row_to_ticket(row: sqlite3.Row) -> dict[str, Any]:
    ticket = dict(row)
    ticket["tags"] = json.loads(ticket["tags"])
    return ticket


def create_ticket(title: str, description: str, classification: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO tickets (title, description, category, priority, tags, status,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'open', ?, ?)",
            (
                title,
                description,
                classification["category"],
                classification["priority"],
                json.dumps(classification["tags"], ensure_ascii=False),
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _row_to_ticket(row)


def list_tickets(
    category: str | None = None,
    priority: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Lista tickets, el más reciente primero (desempate por id descendente)."""
    filters = {"category": category, "priority": priority, "status": status}
    active = {column: value for column, value in filters.items() if value}
    where = " AND ".join(f"{column} = ?" for column in active)
    query = "SELECT * FROM tickets"
    if where:
        query += f" WHERE {where}"
    query += " ORDER BY created_at DESC, id DESC"
    with connect() as conn:
        rows = conn.execute(query, tuple(active.values())).fetchall()
    return [_row_to_ticket(row) for row in rows]


def update_ticket(
    ticket_id: int, status: str | None = None, priority: str | None = None
) -> dict[str, Any] | None:
    """Actualiza estado y/o prioridad. Devuelve None si el ticket no existe."""
    changes = {"status": status, "priority": priority}
    active = {column: value for column, value in changes.items() if value}
    assignments = ", ".join(f"{column} = ?" for column in [*active, "updated_at"])
    with connect() as conn:
        cursor = conn.execute(
            f"UPDATE tickets SET {assignments} WHERE id = ?",
            (*active.values(), _now(), ticket_id),
        )
        if cursor.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    return _row_to_ticket(row)
