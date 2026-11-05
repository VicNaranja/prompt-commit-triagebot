"""Fecha límite y vencidos en la API y el tablero (ver SPEC «Fecha límite»)."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import app

# Creado el lunes 05/01/2026 a las 10:00 UTC (11:00 en Madrid).
CREATED = "2026-01-05T10:00:00Z"
P1_DUE = "2026-01-05T22:59:59Z"
P2_DUE = "2026-01-06T22:59:59Z"
P3_DUE = "2026-01-07T22:59:59Z"


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
def classification(monkeypatch):
    """Prioridad que devuelve el clasificador falso; se puede cambiar en cada test."""
    state = {"priority": "P1"}
    monkeypatch.setattr(
        "app.classifier.classify_ticket",
        lambda title, description: {"category": "bug", "priority": state["priority"], "tags": []},
    )
    return state


@pytest.fixture()
def client(tmp_path, monkeypatch, clock, classification):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    return TestClient(app)


def create(client, title="Ticket"):
    response = client.post("/tickets", json={"title": title, "description": "Descripción válida"})
    assert response.status_code == 201, response.text
    return response.json()


def patch(client, ticket_id, **body):
    response = client.patch(f"/tickets/{ticket_id}", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def listed(client, **params):
    response = client.get("/tickets", params=params)
    assert response.status_code == 200
    return response.json()


# --- due_at según la prioridad -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("priority", "expected"), [("P1", P1_DUE), ("P2", P2_DUE), ("P3", P3_DUE)]
)
def test_new_ticket_gets_due_at_from_its_priority(client, classification, priority, expected):
    classification["priority"] = priority

    ticket = create(client)

    assert ticket["due_at"] == expected
    assert listed(client)[0]["due_at"] == expected


def test_changing_priority_recalculates_due_at_from_created_at_not_from_now(client, clock):
    ticket = create(client)  # P1
    clock.value = "2026-01-20T09:00:00Z"  # semanas después

    updated = patch(client, ticket["id"], priority="P3")

    assert updated["due_at"] == P3_DUE  # desde created_at, no desde el 20/01
    assert listed(client)[0]["due_at"] == P3_DUE


def test_changing_status_or_assignees_does_not_change_due_at(client, clock):
    ticket = create(client)
    clock.value = "2026-01-05T15:00:00Z"

    patch(client, ticket["id"], status="in_progress")
    patch(client, ticket["id"], assignees=["Ana"])

    assert listed(client)[0]["due_at"] == P1_DUE


def test_reopening_does_not_restart_the_deadline(client, clock):
    ticket = create(client)
    patch(client, ticket["id"], status="resolved")
    clock.value = "2026-01-12T09:00:00Z"

    reopened = patch(client, ticket["id"], status="open")

    assert reopened["due_at"] == P1_DUE


# --- overdue -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        ("2026-01-05T10:00:00Z", False),
        ("2026-01-05T22:59:58Z", False),
        ("2026-01-05T22:59:59Z", False),  # justo en el límite todavía no está vencido
        ("2026-01-05T23:00:00Z", True),
        ("2026-02-01T00:00:00Z", True),
    ],
)
def test_ticket_is_overdue_only_after_its_due_date(client, clock, now, expected):
    create(client)  # P1, vence 2026-01-05T22:59:59Z
    clock.value = now

    assert listed(client)[0]["overdue"] is expected


@pytest.mark.parametrize("status", ["resolved", "closed"])
def test_resolved_and_closed_tickets_are_never_overdue(client, clock, status):
    ticket = create(client)
    patch(client, ticket["id"], status=status)
    clock.value = "2026-03-01T00:00:00Z"

    assert listed(client)[0]["overdue"] is False


@pytest.mark.parametrize("status", ["open", "in_progress"])
def test_open_and_in_progress_tickets_past_due_are_overdue(client, clock, status):
    ticket = create(client)
    patch(client, ticket["id"], status=status)
    clock.value = "2026-03-01T00:00:00Z"

    assert listed(client)[0]["overdue"] is True


def test_reopened_ticket_past_its_deadline_is_overdue_again(client, clock):
    ticket = create(client)
    patch(client, ticket["id"], status="closed")
    clock.value = "2026-03-01T00:00:00Z"
    assert listed(client)[0]["overdue"] is False

    patch(client, ticket["id"], status="open")

    assert listed(client)[0]["overdue"] is True


def test_post_and_patch_responses_include_the_overdue_flag(client):
    ticket = create(client)

    assert ticket["overdue"] is False
    assert patch(client, ticket["id"], status="in_progress")["overdue"] is False


# --- filtro overdue ----------------------------------------------------------------------------


def make_board(client, classification, clock):
    """Tres tickets (P1, P2, P3) y la hora a las 23:30 UTC del día 05: solo el P1 está vencido."""
    tickets = {}
    for priority in ("P1", "P2", "P3"):
        classification["priority"] = priority
        tickets[priority] = create(client, f"Ticket {priority}")
    clock.value = "2026-01-05T23:30:00Z"
    return tickets


def ids(tickets):
    return sorted(t["id"] for t in tickets)


def test_filter_overdue_true_returns_only_overdue_tickets(client, classification, clock):
    board = make_board(client, classification, clock)

    assert ids(listed(client, overdue="true")) == [board["P1"]["id"]]


def test_filter_overdue_false_returns_only_not_overdue_tickets(client, classification, clock):
    board = make_board(client, classification, clock)

    assert ids(listed(client, overdue="false")) == sorted([board["P2"]["id"], board["P3"]["id"]])


def test_overdue_filter_excludes_resolved_even_if_past_due(client, classification, clock):
    board = make_board(client, classification, clock)
    patch(client, board["P1"]["id"], status="resolved")

    assert listed(client, overdue="true") == []


def test_overdue_filter_combines_with_assignee(client, classification, clock):
    board = make_board(client, classification, clock)
    patch(client, board["P1"]["id"], assignees=["Ana"])  # vencido
    patch(client, board["P2"]["id"], assignees=["Ana"])  # de Ana también, pero no vencido

    assert ids(listed(client, overdue="true", assignee="Ana")) == [board["P1"]["id"]]
    assert listed(client, overdue="true", assignee="Luis") == []


def test_overdue_filter_with_non_boolean_value_returns_422(client):
    assert client.get("/tickets", params={"overdue": "quizás"}).status_code == 422


# --- Migración de una base de datos anterior ---------------------------------------------------


def test_existing_database_without_due_at_is_migrated_and_backfilled(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,"
        " description TEXT NOT NULL, category TEXT NOT NULL, priority TEXT NOT NULL,"
        " tags TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'open',"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    for priority in ("P1", "P2", "P3"):
        conn.execute(
            "INSERT INTO tickets (title, description, category, priority, created_at, updated_at)"
            " VALUES (?, 'd', 'bug', ?, ?, ?)",
            (f"Antiguo {priority}", priority, CREATED, CREATED),
        )
    conn.commit()
    conn.close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setattr("app.db._now", Clock(CREATED))
    client = TestClient(app)

    tickets = {t["title"]: t for t in client.get("/tickets").json()}

    assert tickets["Antiguo P1"]["due_at"] == P1_DUE
    assert tickets["Antiguo P2"]["due_at"] == P2_DUE
    assert tickets["Antiguo P3"]["due_at"] == P3_DUE
    assert all(t["assignees"] == [] for t in tickets.values())
    # Y sigue funcionando después de migrar (una segunda conexión no vuelve a migrar).
    assert len(client.get("/tickets").json()) == 3


# --- Pantalla ----------------------------------------------------------------------------------


def test_board_shows_due_date_in_madrid_time(client):
    create(client)

    page = client.get("/").text

    assert ">Vence<" in page
    assert "05/01 23:59" in page


def test_board_marks_overdue_tickets_and_only_those(client, classification, clock):
    board = make_board(client, classification, clock)

    page = client.get("/ui/tickets").text

    assert page.count(">Vencido<") == 1
    patch(client, board["P1"]["id"], status="resolved")
    assert ">Vencido<" not in client.get("/ui/tickets").text


def test_ui_filter_only_overdue(client, classification, clock):
    make_board(client, classification, clock)

    page = client.get("/ui/tickets", params={"overdue": "true"}).text

    assert "Ticket P1" in page
    assert "Ticket P2" not in page and "Ticket P3" not in page


def test_index_has_the_only_overdue_checkbox(client):
    page = client.get("/").text

    assert 'name="overdue"' in page
    assert "Solo vencidos" in page


def test_overdue_filter_is_kept_when_creating_and_editing_from_the_board(
    client, classification, clock
):
    board = make_board(client, classification, clock)

    created = client.post(
        "/ui/tickets",
        data={"title": "Nuevo", "description": "Algo falla", "overdue": "true"},
    )
    edited = client.post(
        f"/ui/tickets/{board['P2']['id']}", data={"set_status": "in_progress", "overdue": "true"}
    )

    for response in (created, edited):
        assert "Ticket P1" in response.text
        assert "Ticket P2" not in response.text
        assert "Ticket P3" not in response.text
