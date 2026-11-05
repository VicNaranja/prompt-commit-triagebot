"""Responsables en el tablero HTML/HTMX: crear, editar en la fila y filtrar."""

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


def create_via_api(client, title, assignees=()):
    response = client.post(
        "/tickets",
        json={"title": title, "description": "Descripción válida", "assignees": list(assignees)},
    )
    assert response.status_code == 201, response.text
    return response.json()


def stored(client):
    return client.get("/tickets").json()


# --- Crear desde el formulario -----------------------------------------------------------------


def test_create_form_accepts_comma_separated_assignees(client):
    response = client.post(
        "/ui/tickets",
        data={"title": "Ticket uno", "description": "Algo falla", "assignees": "Ana, Luis ,  Eva"},
    )

    assert response.status_code == 200
    assert 'role="alert"' not in response.text
    assert stored(client)[0]["assignees"] == ["Ana", "Luis", "Eva"]


@pytest.mark.parametrize("raw", ["", " ", ",", " , ,, "])
def test_create_form_ignores_empty_assignee_entries(client, raw):
    response = client.post(
        "/ui/tickets", data={"title": "Ticket uno", "description": "Algo falla", "assignees": raw}
    )

    assert response.status_code == 200
    assert 'role="alert"' not in response.text
    assert stored(client)[0]["assignees"] == []


def test_create_form_with_too_long_assignee_shows_error_and_creates_nothing(client):
    response = client.post(
        "/ui/tickets",
        data={"title": "Ticket uno", "description": "Algo falla", "assignees": "x" * 61},
    )

    assert response.status_code == 200
    assert 'role="alert"' in response.text
    assert "responsables" in response.text.lower()
    assert stored(client) == []


# --- Editar en la fila -------------------------------------------------------------------------


def test_row_edit_replaces_assignees(client):
    ticket = create_via_api(client, "Ticket uno", ["Ana", "Luis"])

    response = client.post(f"/ui/tickets/{ticket['id']}", data={"set_assignees": "Eva, Luis"})

    assert response.status_code == 200
    assert 'role="alert"' not in response.text
    assert stored(client)[0]["assignees"] == ["Eva", "Luis"]


def test_row_edit_with_empty_text_removes_all_assignees(client):
    ticket = create_via_api(client, "Ticket uno", ["Ana", "Luis"])

    client.post(f"/ui/tickets/{ticket['id']}", data={"set_assignees": ""})

    assert stored(client)[0]["assignees"] == []


def test_changing_status_from_the_row_does_not_touch_assignees(client):
    ticket = create_via_api(client, "Ticket uno", ["Ana", "Luis"])

    client.post(f"/ui/tickets/{ticket['id']}", data={"set_status": "closed"})

    assert stored(client)[0]["assignees"] == ["Ana", "Luis"]


def test_row_edit_with_too_long_assignee_shows_error_and_changes_nothing(client):
    ticket = create_via_api(client, "Ticket uno", ["Ana"])

    # Con un cambio de estado válido a la vez: si falla un campo, no se aplica ninguno.
    response = client.post(
        f"/ui/tickets/{ticket['id']}", data={"set_assignees": "x" * 61, "set_status": "closed"}
    )

    assert response.status_code == 200
    assert 'role="alert"' in response.text
    assert stored(client)[0]["assignees"] == ["Ana"]
    assert stored(client)[0]["status"] == "open"


def test_table_shows_each_row_with_an_editable_assignees_field(client):
    create_via_api(client, "Ticket uno", ["Ana", "Luis"])

    page = client.get("/")

    assert 'name="set_assignees"' in page.text
    assert 'value="Ana, Luis"' in page.text


# --- Filtros -----------------------------------------------------------------------------------


def test_ui_list_filters_by_assignee_and_unassigned(client):
    create_via_api(client, "Ticket de Ana", ["Ana"])
    create_via_api(client, "Ticket de Luis", ["Luis"])
    create_via_api(client, "Ticket libre")

    by_ana = client.get("/ui/tickets", params={"assignee": "ana"}).text
    free = client.get("/ui/tickets", params={"unassigned": "true"}).text

    assert "Ticket de Ana" in by_ana
    assert "Ticket de Luis" not in by_ana and "Ticket libre" not in by_ana
    assert "Ticket libre" in free
    assert "Ticket de Ana" not in free and "Ticket de Luis" not in free


def test_ui_list_ignores_empty_assignee_filter(client):
    create_via_api(client, "Ticket de Ana", ["Ana"])
    create_via_api(client, "Ticket libre")

    page = client.get("/ui/tickets", params={"assignee": ""}).text

    assert "Ticket de Ana" in page and "Ticket libre" in page


def test_create_keeps_the_assignee_filter_active(client):
    create_via_api(client, "Ticket de Ana", ["Ana"])

    response = client.post(
        "/ui/tickets",
        data={
            "title": "Ticket de Luis",
            "description": "Algo falla",
            "assignees": "Luis",
            "assignee": "Ana",
        },
    )

    assert "Ticket de Ana" in response.text
    assert "Ticket de Luis" not in response.text
    assert len(stored(client)) == 2


def test_row_edit_keeps_the_unassigned_filter_active(client):
    taken = create_via_api(client, "Ticket asignado", ["Ana"])
    create_via_api(client, "Ticket libre")

    response = client.post(
        f"/ui/tickets/{taken['id']}", data={"set_status": "closed", "unassigned": "true"}
    )

    assert "Ticket libre" in response.text
    assert "Ticket asignado" not in response.text


def test_index_has_assignee_filter_with_autocomplete_of_existing_names(client):
    create_via_api(client, "Ticket uno", ["Ana", "Luis"])
    create_via_api(client, "Ticket dos", ["ana", "Eva"])

    page = client.get("/")

    assert 'name="assignee"' in page.text
    assert 'name="unassigned"' in page.text
    assert 'list="assignee-options"' in page.text
    assert 'id="assignee-options"' in page.text
    for name in ("Ana", "Eva", "Luis"):
        assert f'<option value="{name}">' in page.text
    assert page.text.count('<option value="Ana">') == 1  # "ana" no se repite


def test_partial_refreshes_the_autocomplete_options_out_of_band(client):
    response = client.post(
        "/ui/tickets",
        data={"title": "Ticket uno", "description": "Algo falla", "assignees": "Nuria"},
    )

    assert 'hx-swap-oob="true"' in response.text
    assert '<option value="Nuria">' in response.text


# --- Escapado ----------------------------------------------------------------------------------


def test_assignee_names_are_escaped_in_chips_input_and_autocomplete(client):
    payload = "<script>alert(1)</script>"
    create_via_api(client, "Ticket uno", [payload])

    for response in (client.get("/"), client.get("/ui/tickets")):
        assert response.status_code == 200
        assert payload not in response.text
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
