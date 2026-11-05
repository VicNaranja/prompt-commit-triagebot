"""«Desde cuándo» está el estado: status_changed_at (SPEC «Estados») y su formato."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.timefmt import format_ago

CREATED = "2026-01-05T10:00:00Z"  # 11:00 en Madrid


class Clock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


@pytest.fixture()
def clock(monkeypatch):
    fake = Clock(CREATED)
    monkeypatch.setattr("app.db._now", fake)
    return fake


@pytest.fixture()
def client(tmp_path, monkeypatch, clock):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(
        "app.classifier.classify_ticket",
        lambda title, description: {"category": "bug", "priority": "P2", "tags": []},
    )
    return TestClient(app)


def create(client, title="Ticket"):
    response = client.post("/tickets", json={"title": title, "description": "Descripción válida"})
    assert response.status_code == 201, response.text
    return response.json()


def patch(client, ticket_id, **body):
    response = client.patch(f"/tickets/{ticket_id}", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def only_ticket(client):
    tickets = client.get("/tickets").json()
    assert len(tickets) == 1
    return tickets[0]


# --- Formato relativo --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (-30, "hace instantes"),  # reloj adelantado: nunca «hace -1 min»
        (0, "hace instantes"),
        (59, "hace instantes"),
        (60, "hace 1 min"),
        (5 * 60, "hace 5 min"),
        (59 * 60 + 59, "hace 59 min"),
        (60 * 60, "hace 1 h"),
        (3 * 3600 + 1799, "hace 3 h"),
        (23 * 3600 + 3599, "hace 23 h"),
        (24 * 3600, "hace 1 d"),
        (2 * 86400 + 5, "hace 2 d"),
        (45 * 86400, "hace 45 d"),
    ],
)
def test_format_ago(seconds, expected):
    from datetime import UTC, datetime, timedelta

    base = datetime(2026, 1, 5, 10, 0, 0, tzinfo=UTC)
    now = (base + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")

    assert format_ago("2026-01-05T10:00:00Z", now) == expected


# --- status_changed_at -------------------------------------------------------------------------


def test_new_ticket_status_changed_at_equals_created_at(client):
    ticket = create(client)

    assert ticket["status_changed_at"] == ticket["created_at"] == CREATED
    assert only_ticket(client)["status_changed_at"] == CREATED


def test_changing_the_status_updates_status_changed_at(client, clock):
    ticket = create(client)
    clock.value = "2026-01-05T12:30:00Z"

    updated = patch(client, ticket["id"], status="in_progress")

    assert updated["status_changed_at"] == "2026-01-05T12:30:00Z"
    assert updated["created_at"] == CREATED
    assert only_ticket(client)["status_changed_at"] == "2026-01-05T12:30:00Z"


def test_patch_with_the_same_status_does_not_change_status_changed_at(client, clock):
    ticket = create(client)
    clock.value = "2026-01-05T12:30:00Z"

    updated = patch(client, ticket["id"], status="open")  # ya estaba open

    assert updated["status_changed_at"] == CREATED
    assert updated["updated_at"] == "2026-01-05T12:30:00Z"  # updated_at sí cambia


def test_changing_only_priority_or_assignees_does_not_change_status_changed_at(client, clock):
    ticket = create(client)
    clock.value = "2026-01-05T12:30:00Z"

    patch(client, ticket["id"], priority="P1")
    after_assignees = patch(client, ticket["id"], assignees=["Ana"])

    assert after_assignees["status_changed_at"] == CREATED


def test_status_and_priority_together_update_it_when_the_status_changes(client, clock):
    ticket = create(client)
    clock.value = "2026-01-05T12:30:00Z"

    updated = patch(client, ticket["id"], status="resolved", priority="P1")

    assert updated["status_changed_at"] == "2026-01-05T12:30:00Z"


def test_status_and_priority_together_keep_it_when_the_status_is_the_same(client, clock):
    ticket = create(client)
    clock.value = "2026-01-05T12:30:00Z"

    updated = patch(client, ticket["id"], status="open", priority="P1")

    assert updated["status_changed_at"] == CREATED


def test_reopening_restarts_the_since_when(client, clock):
    ticket = create(client)
    clock.value = "2026-01-05T12:00:00Z"
    patch(client, ticket["id"], status="resolved")
    clock.value = "2026-01-07T09:00:00Z"

    reopened = patch(client, ticket["id"], status="open")

    assert reopened["status_changed_at"] == "2026-01-07T09:00:00Z"
    assert reopened["due_at"] == ticket["due_at"]  # el plazo no se reinicia


def test_every_real_transition_is_recorded(client, clock):
    ticket = create(client)
    moments = {
        "in_progress": "2026-01-05T11:00:00Z",
        "resolved": "2026-01-05T15:00:00Z",
        "closed": "2026-01-06T08:00:00Z",
    }

    for status, moment in moments.items():
        clock.value = moment
        assert patch(client, ticket["id"], status=status)["status_changed_at"] == moment


# --- Migración ---------------------------------------------------------------------------------


def old_database(path, with_due_at):
    conn = sqlite3.connect(path)
    due = " due_at TEXT," if with_due_at else ""
    conn.execute(
        "CREATE TABLE tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,"
        " description TEXT NOT NULL, category TEXT NOT NULL, priority TEXT NOT NULL,"
        " tags TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'open'," + due +
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO tickets (title, description, category, priority, status, created_at,"
        " updated_at) VALUES ('Antiguo', 'd', 'bug', 'P1', 'resolved', ?, ?)",
        ("2026-01-01T08:00:00Z", "2026-01-03T17:45:00Z"),
    )
    conn.commit()
    conn.close()


@pytest.mark.parametrize("with_due_at", [False, True])
def test_old_databases_get_status_changed_at_filled_from_updated_at(
    tmp_path, monkeypatch, clock, with_due_at
):
    path = tmp_path / "old.db"
    old_database(path, with_due_at)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    client = TestClient(app)

    ticket = only_ticket(client)

    assert ticket["status_changed_at"] == "2026-01-03T17:45:00Z"
    assert ticket["due_at"]  # y la migración anterior (due_at) sigue funcionando
    assert len(client.get("/tickets").json()) == 1  # una segunda conexión no rompe nada


# --- Pantalla ----------------------------------------------------------------------------------


def test_board_shows_since_when_in_madrid_time_and_relative(client, clock):
    create(client)
    clock.value = "2026-01-05T13:00:00Z"  # tres horas después

    page = client.get("/ui/tickets").text

    assert "desde 05/01 11:00" in page
    assert "hace 3 h" in page


def test_board_updates_since_when_after_changing_the_status_from_a_row(client, clock):
    ticket = create(client)
    clock.value = "2026-01-06T09:30:00Z"

    response = client.post(f"/ui/tickets/{ticket['id']}", data={"set_status": "in_progress"})

    assert "desde 06/01 10:30" in response.text
    assert "hace instantes" in response.text


def test_board_does_not_move_since_when_for_a_non_status_edit(client, clock):
    ticket = create(client)
    clock.value = "2026-01-06T10:30:00Z"

    response = client.post(f"/ui/tickets/{ticket['id']}", data={"set_assignees": "Ana"})

    assert "desde 05/01 11:00" in response.text
    assert "hace 1 d" in response.text
