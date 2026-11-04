"""Persistencia en SQLite con sqlite3.

La ruta de la base de datos se lee de `DATABASE_URL` (formato `sqlite:///<ruta>`) cada vez que
se abre una conexión, no al importar el módulo: los tests la fijan después de importar la app.
"""

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

DEFAULT_DATABASE_URL = "sqlite:///triagebot.db"
SQLITE_PREFIX = "sqlite:///"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    description TEXT NOT NULL,
    category    TEXT NOT NULL,
    priority    TEXT NOT NULL,
    tags        TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
)
"""


def database_path() -> str:
    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    if not url.startswith(SQLITE_PREFIX):
        raise ValueError(f"DATABASE_URL debe empezar por {SQLITE_PREFIX!r}")
    return url[len(SQLITE_PREFIX) :]


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(database_path())
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(_SCHEMA)
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
                (title, description, category, priority, tags, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'open', ?, ?)
            """,
            (title, description, category, priority, json.dumps(tags), now, now),
        )
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _row_to_dict(row)


def list_tickets(
    category: str | None = None, priority: str | None = None, status: str | None = None
) -> list[dict[str, Any]]:
    filters = {"category": category, "priority": priority, "status": status}
    clauses = [f"{column} = ?" for column, value in filters.items() if value is not None]
    params = [value for value in filters.values() if value is not None]
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM tickets {where} ORDER BY created_at DESC, id DESC", params
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def update_ticket(ticket_id: int, changes: dict[str, str]) -> dict[str, Any] | None:
    """Actualiza `status` y/o `priority`. Devuelve None si el ticket no existe."""
    allowed = {key: value for key, value in changes.items() if key in ("status", "priority")}
    with connect() as conn:
        if conn.execute("SELECT 1 FROM tickets WHERE id = ?", (ticket_id,)).fetchone() is None:
            return None
        assignments = ", ".join(f"{column} = ?" for column in allowed)
        conn.execute(
            f"UPDATE tickets SET {assignments}, updated_at = ? WHERE id = ?",
            [*allowed.values(), _now(), ticket_id],
        )
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    return _row_to_dict(row)
