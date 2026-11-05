"""Rutas HTML/HTMX del tablero (`/` y `/ui/tickets...`). Devuelven HTML, no JSON."""

import pytest
from fastapi.testclient import TestClient

from app.main import app

CLASSIFICATIONS = {
    "Zeta login roto": {"category": "bug", "priority": "P1", "tags": ["login"]},
    "Omega exportar PDF": {"category": "question", "priority": "P3", "tags": []},
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Test client con BD aislada y clasificador mockeado según el título."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(
        "app.classifier.classify_ticket",
        lambda title, description: CLASSIFICATIONS[title],
    )
    return TestClient(app)


def create_via_api(client, title):
    response = client.post("/tickets", json={"title": title, "description": "Descripción válida"})
    assert response.status_code == 201
    return response.json()


def test_index_renders_page_with_forms_and_existing_tickets(client):
    create_via_api(client, "Zeta login roto")

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'id="create-form"' in response.text
    assert 'id="filters"' in response.text
    assert "Zeta login roto" in response.text


def test_ui_create_ticket_adds_it_to_the_table_and_persists_it(client):
    response = client.post(
        "/ui/tickets", data={"title": "Zeta login roto", "description": "No carga la pantalla"}
    )

    assert response.status_code == 200
    assert "Zeta login roto" in response.text
    assert 'role="alert"' not in response.text
    stored = client.get("/tickets").json()
    assert [t["title"] for t in stored] == ["Zeta login roto"]
    assert stored[0]["category"] == "bug"


@pytest.mark.parametrize(
    ("title", "description"),
    [
        ("", "Descripción válida"),
        ("!!!", "Descripción válida"),
        ("Zeta login roto", ""),
        ("x" * 201, "Descripción válida"),
    ],
)
def test_ui_create_with_invalid_input_shows_error_and_creates_nothing(client, title, description):
    response = client.post("/ui/tickets", data={"title": title, "description": description})

    # HTMX no pinta los 4xx: la ruta HTML responde 200 con el error dentro del HTML.
    assert response.status_code == 200
    assert 'role="alert"' in response.text
    assert client.get("/tickets").json() == []


def test_ui_create_keeps_the_active_filters_in_the_returned_table(client):
    create_via_api(client, "Zeta login roto")  # bug

    # Con el filtro category=bug activo, el ticket nuevo (question) se guarda pero no se muestra.
    response = client.post(
        "/ui/tickets",
        data={"title": "Omega exportar PDF", "description": "Quiero PDF", "category": "bug"},
    )

    assert response.status_code == 200
    assert "Zeta login roto" in response.text
    assert "Omega exportar PDF" not in response.text
    assert len(client.get("/tickets").json()) == 2


def test_ui_list_applies_filters(client):
    create_via_api(client, "Zeta login roto")  # bug
    create_via_api(client, "Omega exportar PDF")  # question

    only_bugs = client.get("/ui/tickets", params={"category": "bug"})
    nothing = client.get("/ui/tickets", params={"category": "urgent"})

    assert "Zeta login roto" in only_bugs.text
    assert "Omega exportar PDF" not in only_bugs.text
    assert "No hay tickets que coincidan." in nothing.text


@pytest.mark.parametrize(
    ("form", "field", "expected"),
    [
        ({"set_status": "closed"}, "status", "closed"),
        ({"set_priority": "P3"}, "priority", "P3"),
    ],
)
def test_ui_update_ticket_changes_status_or_priority(client, form, field, expected):
    ticket = create_via_api(client, "Zeta login roto")  # open, P1

    response = client.post(f"/ui/tickets/{ticket['id']}", data=form)

    assert response.status_code == 200
    assert 'role="alert"' not in response.text
    assert client.get("/tickets").json()[0][field] == expected


def test_ui_update_unknown_ticket_shows_error(client):
    response = client.post("/ui/tickets/9999", data={"set_status": "closed"})

    assert response.status_code == 200
    assert "El ticket 9999 no existe." in response.text


def test_ui_update_with_invalid_value_shows_error_and_changes_nothing(client):
    ticket = create_via_api(client, "Zeta login roto")

    response = client.post(f"/ui/tickets/{ticket['id']}", data={"set_status": "zombie"})

    assert response.status_code == 200
    assert 'role="alert"' in response.text
    assert client.get("/tickets").json()[0]["status"] == "open"
