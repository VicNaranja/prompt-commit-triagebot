"""`GET /tickets/stats`: conteos por categoría, prioridad y estado (ver SPEC §4)."""

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.models import CATEGORIES, STATUSES

PRIORITIES = ("P1", "P2", "P3")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(
        "app.classifier.classify_ticket",
        lambda title, description: {"category": "bug", "priority": "P2", "tags": []},
    )
    return TestClient(app)


def make(category="bug", priority="P2", n=1):
    """Crea `n` tickets con esa categoría y prioridad directamente en la BD."""
    for _ in range(n):
        classification = {"category": category, "priority": priority, "tags": []}
        db.create_ticket("Ticket", "Descripción", classification)


def stats(client):
    response = client.get("/tickets/stats")
    assert response.status_code == 200
    return response.json()


def test_empty_database_is_all_zeros_with_every_key(client):
    assert stats(client) == {
        "total": 0,
        "by_category": dict.fromkeys(CATEGORIES, 0),
        "by_priority": dict.fromkeys(PRIORITIES, 0),
        "by_status": dict.fromkeys(STATUSES, 0),
    }


def test_shape_has_exactly_the_four_keys(client):
    assert list(stats(client)) == ["total", "by_category", "by_priority", "by_status"]


def test_counts_by_category_priority_and_status(client):
    make("bug", "P1", n=2)
    make("bug", "P2", n=3)
    make("question", "P3", n=1)
    make("urgent", "P1", n=1)

    data = stats(client)

    assert data["total"] == 7
    assert data["by_category"] == {"bug": 5, "feature_request": 0, "question": 1, "urgent": 1}
    assert data["by_priority"] == {"P1": 3, "P2": 3, "P3": 1}
    assert data["by_status"] == {"open": 7, "in_progress": 0, "resolved": 0, "closed": 0}


def test_missing_values_appear_with_zero_not_absent(client):
    make("bug", "P2")

    data = stats(client)

    assert data["by_category"]["feature_request"] == 0
    assert data["by_priority"]["P1"] == 0
    assert data["by_status"]["resolved"] == 0


@pytest.mark.parametrize("group", ["by_category", "by_priority", "by_status"])
def test_every_group_adds_up_to_total(client, group):
    make("bug", "P1", n=4)
    make("feature_request", "P2", n=3)
    make("urgent", "P3", n=2)

    data = stats(client)

    assert data["total"] == 9
    assert sum(data[group].values()) == data["total"]


def test_changing_status_moves_the_count(client):
    make()
    ticket_id = client.get("/tickets").json()[0]["id"]

    client.patch(f"/tickets/{ticket_id}", json={"status": "resolved"})
    data = stats(client)

    assert data["by_status"] == {"open": 0, "in_progress": 0, "resolved": 1, "closed": 0}
    assert data["total"] == 1


def test_changing_priority_moves_the_count(client):
    make(priority="P2")
    ticket_id = client.get("/tickets").json()[0]["id"]

    client.patch(f"/tickets/{ticket_id}", json={"priority": "P1"})

    assert stats(client)["by_priority"] == {"P1": 1, "P2": 0, "P3": 0}


def test_creating_through_the_api_is_counted(client):
    client.post("/tickets", json={"title": "Se rompe el login", "description": "Da error"})

    data = stats(client)

    assert data["total"] == 1
    assert data["by_category"]["bug"] == 1


def test_counts_all_tickets_beyond_one_page(client):
    make(n=45)

    assert stats(client)["total"] == 45


def test_does_not_depend_on_query_params(client):
    make("bug", "P1", n=2)

    # Los parámetros de `GET /tickets` no filtran las estadísticas: cuentan todo.
    response = client.get("/tickets/stats?status=closed&category=question&limit=1")

    assert response.status_code == 200
    assert response.json()["total"] == 2


def test_route_is_not_mistaken_for_a_ticket_id(client):
    # `stats` no es un entero: sigue siendo 422 en la ruta de un ticket concreto.
    assert client.patch("/tickets/stats", json={"status": "closed"}).status_code == 422
    assert client.get("/tickets/stats").status_code == 200
