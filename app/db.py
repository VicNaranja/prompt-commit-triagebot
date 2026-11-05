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

from app.deadlines import compute_due_at

DEFAULT_DATABASE_URL = "sqlite:///triagebot.db"
_MAX_ID = 2**63 - 1  # el mayor entero que admite una columna INTEGER de SQLite
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
    due_at      TEXT,
    status_changed_at TEXT,
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


# Un ticket está vencido si su fecha límite ya pasó y sigue sin resolverse. Se calcula al
# consultar (no se guarda). Un `due_at` vacío (no debería ocurrir tras migrar) nunca vence.
_UNRESOLVED_STATUSES = ("open", "in_progress")
_OVERDUE_SQL = (
    "(COALESCE(due_at, '9999-12-31T23:59:59Z') < ? AND status IN ("
    + ", ".join(f"'{status}'" for status in _UNRESOLVED_STATUSES)
    + "))"
)


def _migrate(conn: sqlite3.Connection) -> None:
    """Pone al día una base de datos creada con una versión anterior del esquema."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(tickets)")}
    if "due_at" not in columns:
        conn.execute("ALTER TABLE tickets ADD COLUMN due_at TEXT")
    if "status_changed_at" not in columns:
        conn.execute("ALTER TABLE tickets ADD COLUMN status_changed_at TEXT")
    # Lo más cercano que se conocía en las bases antiguas: la última modificación.
    conn.execute(
        "UPDATE tickets SET status_changed_at = updated_at WHERE status_changed_at IS NULL"
    )
    pending = conn.execute(
        "SELECT id, created_at, priority FROM tickets WHERE due_at IS NULL"
    ).fetchall()
    for row in pending:
        conn.execute(
            "UPDATE tickets SET due_at = ? WHERE id = ?",
            (compute_due_at(row["created_at"], row["priority"]), row["id"]),
        )


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Abre una conexión por operación, crea/migra las tablas y confirma al salir sin error."""
    conn = sqlite3.connect(_database_path())
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        _migrate(conn)
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
    now = _now()
    tickets = []
    for row in rows:
        ticket = dict(row)
        ticket["tags"] = json.loads(ticket["tags"])
        ticket["assignees"] = assignees[ticket["id"]]
        ticket["overdue"] = (
            ticket["due_at"] is not None
            and ticket["due_at"] < now
            and ticket["status"] in _UNRESOLVED_STATUSES
        )
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
            " due_at, status_changed_at, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)",
            (
                title,
                description,
                classification["category"],
                classification["priority"],
                json.dumps(classification["tags"], ensure_ascii=False),
                compute_due_at(now, classification["priority"]),
                now,
                now,
                now,
            ),
        )
        ticket_id = cursor.lastrowid
        _replace_assignees(conn, ticket_id, assignees)
        return _get_ticket(conn, ticket_id)


def _where(
    category: str | None,
    priority: str | None,
    status: str | None,
    assignee: str | None,
    unassigned: bool,
    overdue: bool | None,
) -> tuple[str, list[Any]]:
    """Cláusula WHERE (con su «WHERE») y parámetros: la misma para listar y para contar."""
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
    if overdue is not None:
        clauses.append(_OVERDUE_SQL if overdue else f"NOT {_OVERDUE_SQL}")
        params.append(_now())
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), params


def count_tickets(
    category: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    assignee: str | None = None,
    unassigned: bool = False,
    overdue: bool | None = None,
) -> int:
    """Cuántos tickets cumplen los filtros (mismos parámetros que `list_tickets`)."""
    where, params = _where(category, priority, status, assignee, unassigned, overdue)
    with connect() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM tickets{where}", params).fetchone()[0]


def list_tickets(
    category: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    assignee: str | None = None,
    unassigned: bool = False,
    overdue: bool | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Lista tickets, el más reciente primero (desempate por id descendente).

    Todos los filtros indicados se cumplen a la vez. `assignee` coincide con el nombre completo
    de uno de los responsables, sin distinguir mayúsculas. `overdue=True` deja solo los vencidos,
    `overdue=False` solo los no vencidos y `None` no filtra. `limit` y `offset` paginan; sin
    `limit` se devuelven todos (desde `offset`).
    """
    where, params = _where(category, priority, status, assignee, unassigned, overdue)
    query = f"SELECT * FROM tickets{where} ORDER BY created_at DESC, id DESC"
    if limit is not None or offset:
        # SQLite exige LIMIT para usar OFFSET; -1 significa «sin límite».
        query += " LIMIT ? OFFSET ?"
        params = [*params, -1 if limit is None else limit, offset]
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

    `assignees` sustituye la lista completa; `None` la deja como está y `[]` la vacía. Si cambia
    la prioridad, `due_at` se recalcula desde `created_at` (no desde el momento del cambio).
    `status_changed_at` solo se actualiza si el estado cambia de verdad.
    """
    if not 1 <= ticket_id <= _MAX_ID:
        return None  # un id fuera del rango de SQLite (p. ej. 99999999999999999999) no existe
    changes = {"status": status, "priority": priority}
    active = {column: value for column, value in changes.items() if value}
    now = _now()
    with connect() as conn:
        existing = conn.execute(
            "SELECT status, created_at FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        if existing is None:
            return None
        if priority:
            active["due_at"] = compute_due_at(existing["created_at"], priority)
        if status and status != existing["status"]:
            active["status_changed_at"] = now
        assignments = ", ".join(f"{column} = ?" for column in [*active, "updated_at"])
        cursor = conn.execute(
            f"UPDATE tickets SET {assignments} WHERE id = ?",
            (*active.values(), now, ticket_id),
        )
        if cursor.rowcount == 0:
            return None
        if assignees is not None:
            _replace_assignees(conn, ticket_id, assignees)
        return _get_ticket(conn, ticket_id)
