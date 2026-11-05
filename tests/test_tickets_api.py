import pytest
from fastapi.testclient import TestClient

from app.main import app

CLASSIFICATIONS = {
    "Login roto": {"category": "bug", "priority": "P1", "tags": []},
    "Exportar a PDF": {"category": "feature_request", "priority": "P2", "tags": []},
    "Pago falla": {"category": "bug", "priority": "P1", "tags": []},
    "Cómo cambio la contraseña": {"category": "question", "priority": "P3", "tags": []},
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


def create(client, title):
    response = client.post("/tickets", json={"title": title, "description": "Descripción válida"})
    assert response.status_code == 201
    return response.json()


def test_get_tickets_combines_status_and_priority_filters(client):
    login = create(client, "Login roto")
    payment = create(client, "Pago falla")
    create(client, "Exportar a PDF")  # P2, open: no cumple priority=P1
    create(client, "Cómo cambio la contraseña")  # P3, open: no cumple priority=P1
    client.patch(f"/tickets/{payment['id']}", json={"status": "in_progress"})  # P1 pero no open

    response = client.get("/tickets", params={"status": "open", "priority": "P1"})

    assert response.status_code == 200
    assert [t["id"] for t in response.json()] == [login["id"]]


def test_get_tickets_filter_with_no_match_returns_empty_list(client):
    create(client, "Login roto")

    response = client.get("/tickets", params={"status": "closed"})

    assert response.status_code == 200
    assert response.json() == []


def test_get_tickets_with_unknown_status_is_never_a_server_error(client):
    # El SPEC no define error para GET /tickets: un valor desconocido no coincide con nada.
    create(client, "Login roto")

    response = client.get("/tickets", params={"status": "zombie"})

    assert response.status_code < 500
    assert response.status_code == 200
    assert response.json() == []


def test_patch_ticket_updates_status_and_timestamps(client, monkeypatch):
    clock = ["2026-01-01T10:00:00Z"]
    monkeypatch.setattr("app.db._now", lambda: clock[0])
    ticket = create(client, "Login roto")

    clock[0] = "2026-01-01T11:30:00Z"
    patched = client.patch(f"/tickets/{ticket['id']}", json={"status": "in_progress"})

    assert patched.status_code == 200
    assert patched.json()["status"] == "in_progress"

    listed = client.get("/tickets").json()
    assert len(listed) == 1
    after = listed[0]
    assert after["status"] == "in_progress"
    assert after["created_at"] == ticket["created_at"]
    assert after["updated_at"] != ticket["updated_at"]
    assert after["updated_at"].startswith("2026-01-01T11:30:00")


def test_patch_nonexistent_ticket_returns_404(client):
    response = client.patch("/tickets/9999", json={"status": "closed"})

    assert response.status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"status": "zombie"},
        {"priority": "P9"},
        {},
    ],
)
def test_patch_with_invalid_or_empty_body_returns_422(client, body):
    ticket = create(client, "Login roto")

    response = client.patch(f"/tickets/{ticket['id']}", json=body)

    assert response.status_code == 422
