"""Persistencia en SQLite (sqlite3 de la librería estándar).

La ruta de la base de datos se lee de `DATABASE_URL` en cada conexión, no al importar el módulo:
los tests la fijan después de importar la app.
"""

import json
import os
import sqlite3
from collections.abc import Iterable, Iterator
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
);
CREATE TABLE IF NOT EXISTS ticket_assignees (
    ticket_id INTEGER NOT NULL,
    name      TEXT NOT NULL,
    name_key  TEXT NOT NULL,
    PRIMARY KEY (ticket_id, name_key)
);
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
    """Abre una conexión por operación, crea las tablas si faltan y confirma al salir sin error."""
    conn = sqlite3.connect(_database_path())
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _name_key(name: str) -> str:
    """Clave para comparar responsables sin distinguir mayúsculas (también con acentos)."""
    return name.strip().casefold()


def _replace_assignees(conn: sqlite3.Connection, ticket_id: int, names: Iterable[str]) -> None:
    """Sustituye la lista de responsables, conservando el orden recibido."""
    conn.execute("DELETE FROM ticket_assignees WHERE ticket_id = ?", (ticket_id,))
    conn.executemany(
        "INSERT OR IGNORE INTO ticket_assignees (ticket_id, name, name_key) VALUES (?, ?, ?)",
        [(ticket_id, name, _name_key(name)) for name in names],
    )


def _rows_to_tickets(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    """Convierte filas en tickets, añadiendo sus responsables con una sola consulta."""
    ids = [row["id"] for row in rows]
    assignees: dict[int, list[str]] = {ticket_id: [] for ticket_id in ids}
    if ids:
        placeholders = ", ".join("?" for _ in ids)
        for found in conn.execute(
            f"SELECT ticket_id, name FROM ticket_assignees WHERE ticket_id IN ({placeholders})"
            " ORDER BY rowid",
            ids,
        ):
            assignees[found["ticket_id"]].append(found["name"])
    tickets = []
    for row in rows:
        ticket = dict(row)
        ticket["tags"] = json.loads(ticket["tags"])
        ticket["assignees"] = assignees[ticket["id"]]
        tickets.append(ticket)
    return tickets


def _get_ticket(conn: sqlite3.Connection, ticket_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    return _rows_to_tickets(conn, [row])[0]


def create_ticket(
    title: str,
    description: str,
    classification: dict[str, Any],
    assignees: Iterable[str] = (),
) -> dict[str, Any]:
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
        ticket_id = cursor.lastrowid
        _replace_assignees(conn, ticket_id, assignees)
        return _get_ticket(conn, ticket_id)


def list_tickets(
    category: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    assignee: str | None = None,
    unassigned: bool = False,
) -> list[dict[str, Any]]:
    """Lista tickets, el más reciente primero (desempate por id descendente).

    Todos los filtros indicados se cumplen a la vez. `assignee` coincide con el nombre completo
    de uno de los responsables, sin distinguir mayúsculas.
    """
    filters = {"category": category, "priority": priority, "status": status}
    clauses = [f"{column} = ?" for column, value in filters.items() if value]
    params: list[Any] = [value for value in filters.values() if value]
    if assignee:
        clauses.append(
            "EXISTS (SELECT 1 FROM ticket_assignees a"
            " WHERE a.ticket_id = tickets.id AND a.name_key = ?)"
        )
        params.append(_name_key(assignee))
    if unassigned:
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM ticket_assignees a WHERE a.ticket_id = tickets.id)"
        )
    query = "SELECT * FROM tickets"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC, id DESC"
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
        return _rows_to_tickets(conn, rows)


def list_assignee_names() -> list[str]:
    """Nombres de responsables existentes (una grafía por nombre), para el autocompletado."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT name FROM ticket_assignees GROUP BY name_key ORDER BY name_key"
        ).fetchall()
    return [row["name"] for row in rows]


def update_ticket(
    ticket_id: int,
    status: str | None = None,
    priority: str | None = None,
    assignees: list[str] | None = None,
) -> dict[str, Any] | None:
    """Actualiza estado, prioridad y/o responsables. Devuelve None si el ticket no existe.

    `assignees` sustituye la lista completa; `None` la deja como está y `[]` la vacía.
    """
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
        if assignees is not None:
            _replace_assignees(conn, ticket_id, assignees)
        return _get_ticket(conn, ticket_id)
