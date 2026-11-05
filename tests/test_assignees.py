"""Varios responsables por ticket y filtro por responsable (ver SPEC «Responsables»)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(
        "app.classifier.classify_ticket",
        lambda title, description: {"category": "bug", "priority": "P1", "tags": []},
    )
    return TestClient(app)


def create(client, title="Ticket", **extra):
    response = client.post(
        "/tickets", json={"title": title, "description": "Descripción válida", **extra}
    )
    assert response.status_code == 201, response.text
    return response.json()


def ids(response):
    assert response.status_code == 200
    return sorted(ticket["id"] for ticket in response.json())


# --- Crear con responsables --------------------------------------------------------------------


def test_create_ticket_with_several_assignees(client):
    ticket = create(client, assignees=["Ana", "Luis", "Marta"])

    assert ticket["assignees"] == ["Ana", "Luis", "Marta"]
    listed = client.get("/tickets").json()
    assert listed[0]["assignees"] == ["Ana", "Luis", "Marta"]


def test_create_ticket_without_assignees_defaults_to_empty_list(client):
    assert create(client)["assignees"] == []


def test_assignees_are_trimmed_and_deduplicated_ignoring_case_keeping_first_spelling(client):
    ticket = create(client, assignees=[" Ana ", "Luis", "ana", "LUIS", "Eva"])

    assert ticket["assignees"] == ["Ana", "Luis", "Eva"]


@pytest.mark.parametrize(
    "assignees",
    [
        [""],
        ["   "],
        ["Ana", ""],
        ["x" * 61],
        [f"Persona {n}" for n in range(11)],
        "Ana",
        [1, 2],
    ],
)
def test_create_rejects_invalid_assignees(client, assignees):
    response = client.post(
        "/tickets",
        json={"title": "Ticket", "description": "Descripción válida", "assignees": assignees},
    )

    assert response.status_code == 422
    assert client.get("/tickets").json() == []


def test_create_accepts_the_limits(client):
    ticket = create(client, assignees=["x" * 60] + [f"Persona {n}" for n in range(9)])

    assert len(ticket["assignees"]) == 10


# --- PATCH -------------------------------------------------------------------------------------


def test_patch_replaces_the_whole_assignee_list(client):
    ticket = create(client, assignees=["Ana", "Luis"])

    response = client.patch(f"/tickets/{ticket['id']}", json={"assignees": ["Eva", "Luis"]})

    assert response.status_code == 200
    assert response.json()["assignees"] == ["Eva", "Luis"]
    assert client.get("/tickets").json()[0]["assignees"] == ["Eva", "Luis"]


def test_patch_with_empty_list_removes_all_assignees(client):
    ticket = create(client, assignees=["Ana", "Luis"])

    response = client.patch(f"/tickets/{ticket['id']}", json={"assignees": []})

    assert response.status_code == 200
    assert response.json()["assignees"] == []


def test_patch_only_assignees_leaves_status_and_priority_and_updates_updated_at(
    client, monkeypatch
):
    clock = ["2026-01-05T10:00:00Z"]
    monkeypatch.setattr("app.db._now", lambda: clock[0])
    ticket = create(client)
    clock[0] = "2026-01-05T12:00:00Z"

    response = client.patch(f"/tickets/{ticket['id']}", json={"assignees": ["Ana"]})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == ticket["status"]
    assert body["priority"] == ticket["priority"]
    assert body["created_at"] == ticket["created_at"]
    assert body["updated_at"] == "2026-01-05T12:00:00Z"


def test_patch_without_assignees_does_not_touch_them(client):
    ticket = create(client, assignees=["Ana", "Luis"])

    client.patch(f"/tickets/{ticket['id']}", json={"status": "in_progress"})

    assert client.get("/tickets").json()[0]["assignees"] == ["Ana", "Luis"]


@pytest.mark.parametrize("assignees", [[""], ["x" * 61], [f"P{n}" for n in range(11)]])
def test_patch_rejects_invalid_assignees_and_changes_nothing(client, assignees):
    ticket = create(client, assignees=["Ana"])

    response = client.patch(f"/tickets/{ticket['id']}", json={"assignees": assignees})

    assert response.status_code == 422
    assert client.get("/tickets").json()[0]["assignees"] == ["Ana"]


def test_patch_assignees_of_unknown_ticket_returns_404(client):
    response = client.patch("/tickets/9999", json={"assignees": ["Ana"]})

    assert response.status_code == 404


# --- Filtros -----------------------------------------------------------------------------------


def test_filter_by_assignee_matches_any_of_the_assignees_ignoring_case(client):
    shared = create(client, "Compartido", assignees=["Ana", "Luis"])
    only_luis = create(client, "Solo Luis", assignees=["Luis"])
    create(client, "Sin nadie")

    assert ids(client.get("/tickets", params={"assignee": "Ana"})) == [shared["id"]]
    assert ids(client.get("/tickets", params={"assignee": "luis"})) == sorted(
        [shared["id"], only_luis["id"]]
    )


def test_filter_by_unknown_assignee_returns_empty_list(client):
    create(client, assignees=["Ana"])

    response = client.get("/tickets", params={"assignee": "Nadie"})

    assert response.status_code == 200
    assert response.json() == []


def test_filter_by_assignee_does_not_match_partial_names(client):
    create(client, assignees=["Ana María"])

    assert client.get("/tickets", params={"assignee": "Ana"}).json() == []


def test_filter_unassigned_returns_only_tickets_without_assignees(client):
    create(client, "Con Ana", assignees=["Ana"])
    free = create(client, "Libre")
    cleared = create(client, "Vaciado", assignees=["Luis"])
    client.patch(f"/tickets/{cleared['id']}", json={"assignees": []})

    assert ids(client.get("/tickets", params={"unassigned": "true"})) == sorted(
        [free["id"], cleared["id"]]
    )


def test_unassigned_together_with_assignee_matches_nothing(client):
    create(client, assignees=["Ana"])
    create(client, "Libre")

    response = client.get("/tickets", params={"unassigned": "true", "assignee": "Ana"})

    assert response.status_code == 200
    assert response.json() == []


def test_assignee_filter_combines_with_other_filters(client):
    open_one = create(client, "Abierto", assignees=["Ana"])
    closed_one = create(client, "Cerrado", assignees=["Ana"])
    create(client, "Abierto de Luis", assignees=["Luis"])  # abierto, pero de otro responsable
    client.patch(f"/tickets/{closed_one['id']}", json={"status": "closed"})

    result = client.get("/tickets", params={"assignee": "Ana", "status": "open"})

    assert ids(result) == [open_one["id"]]


def test_unassigned_with_non_boolean_value_returns_422(client):
    response = client.get("/tickets", params={"unassigned": "quizás"})

    assert response.status_code == 422
