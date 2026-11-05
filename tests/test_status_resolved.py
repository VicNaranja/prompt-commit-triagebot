"""Estado `resolved`, reapertura y etiquetas en español (ver SPEC «Estados» y §6)."""

import re

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import CATEGORIES, CATEGORY_LABELS, STATUS_LABELS, STATUSES


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(
        "app.classifier.classify_ticket",
        lambda title, description: {"category": "bug", "priority": "P1", "tags": []},
    )
    return TestClient(app)


def create(client, title="Ticket"):
    response = client.post("/tickets", json={"title": title, "description": "Descripción válida"})
    assert response.status_code == 201
    return response.json()


def set_status(client, ticket_id, status):
    return client.patch(f"/tickets/{ticket_id}", json={"status": status})


# --- API ---------------------------------------------------------------------------------------


def test_ticket_can_be_marked_as_resolved_before_closing(client):
    ticket = create(client)

    response = set_status(client, ticket["id"], "resolved")

    assert response.status_code == 200
    assert response.json()["status"] == "resolved"
    assert client.get("/tickets").json()[0]["status"] == "resolved"


def test_full_lifecycle_open_in_progress_resolved_closed(client):
    ticket = create(client)

    for status in ("in_progress", "resolved", "closed"):
        assert set_status(client, ticket["id"], status).json()["status"] == status


@pytest.mark.parametrize("previous", ["resolved", "closed"])
def test_ticket_can_be_reopened_from_resolved_or_closed(client, previous):
    ticket = create(client)
    assert set_status(client, ticket["id"], previous).json()["status"] == previous

    response = set_status(client, ticket["id"], "open")

    assert response.status_code == 200
    assert response.json()["status"] == "open"


def test_reopening_updates_updated_at_but_not_created_at(client, monkeypatch):
    clock = ["2026-01-05T10:00:00Z"]
    monkeypatch.setattr("app.db._now", lambda: clock[0])
    ticket = create(client)
    clock[0] = "2026-01-05T11:00:00Z"
    assert set_status(client, ticket["id"], "resolved").json()["status"] == "resolved"
    clock[0] = "2026-01-06T09:00:00Z"

    reopened = set_status(client, ticket["id"], "open").json()

    assert reopened["created_at"] == ticket["created_at"]
    assert reopened["updated_at"] == "2026-01-06T09:00:00Z"


def test_filter_by_resolved_status(client):
    resolved = create(client, "Resuelto")
    create(client, "Abierto")
    set_status(client, resolved["id"], "resolved")

    response = client.get("/tickets", params={"status": "resolved"})

    assert [t["id"] for t in response.json()] == [resolved["id"]]


@pytest.mark.parametrize("status", ["resuelto", "RESOLVED", "done", ""])
def test_status_outside_the_enum_is_still_rejected(client, status):
    ticket = create(client)

    response = set_status(client, ticket["id"], status)

    assert response.status_code == 422
    assert client.get("/tickets").json()[0]["status"] == "open"


# --- Pantalla ----------------------------------------------------------------------------------


def test_row_status_selector_offers_the_four_states_in_order_with_spanish_and_value(client):
    create(client)

    page = client.get("/").text

    row_select = re.search(r'<select name="set_status".*?</select>', page, re.S).group(0)
    assert re.findall(r'<option value="(\w+)"', row_select) == [
        "open",
        "in_progress",
        "resolved",
        "closed",
    ]
    assert re.findall(r">(\w[\w ()]*)</option>", row_select) == [
        "Abierto (open)",
        "En curso (in_progress)",
        "Resuelto (resolved)",
        "Cerrado (closed)",
    ]


def test_status_filter_offers_resolved(client):
    page = client.get("/").text

    filter_select = re.search(r'<select name="status".*?</select>', page, re.S).group(0)
    assert '<option value="resolved">Resuelto (resolved)</option>' in filter_select


def test_choosing_resolved_and_then_open_from_the_board_resolves_and_reopens(client):
    ticket = create(client)

    resolved = client.post(f"/ui/tickets/{ticket['id']}", data={"set_status": "resolved"})
    assert 'role="alert"' not in resolved.text
    assert client.get("/tickets").json()[0]["status"] == "resolved"

    client.post(f"/ui/tickets/{ticket['id']}", data={"set_status": "open"})
    assert client.get("/tickets").json()[0]["status"] == "open"


def test_enum_values_are_always_shown_with_their_spanish_label(client):
    for title in ("Uno", "Dos"):
        create(client, title)
    client.patch("/tickets/1", json={"status": "in_progress"})
    page = client.get("/").text

    expected = {**{v: f"{STATUS_LABELS[v]} ({v})" for v in STATUSES},
                **{v: f"{CATEGORY_LABELS[v]} ({v})" for v in CATEGORIES}}
    options = re.findall(r'<option value="(\w+)"[^>]*>([^<]+)</option>', page)
    shown = {value: text.strip() for value, text in options if value in expected}

    assert shown == expected  # todos presentes y ninguno solo en inglés
    assert ">bug<" not in page and ">in_progress<" not in page  # nunca el valor suelto


@pytest.mark.parametrize(
    ("category", "label"),
    [
        ("bug", "Error"),
        ("feature_request", "Petición de mejora"),
        ("question", "Pregunta"),
        ("urgent", "Urgente"),
    ],
)
def test_category_badge_and_filter_use_spanish_labels(client, monkeypatch, category, label):
    monkeypatch.setattr(
        "app.classifier.classify_ticket",
        lambda title, description: {"category": category, "priority": "P2", "tags": []},
    )
    create(client)

    page = client.get("/").text

    assert f">{label} ({category})</span>" in page  # insignia en la tabla
    assert f'<option value="{category}">{label} ({category})</option>' in page  # filtro
