import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Test client with an isolated database path (mismo patrón que test_acceptance.py)."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    return TestClient(app)


@pytest.mark.parametrize("title", ["!!!", "...", "?¿?", "- - -"])
def test_post_ticket_returns_422_when_title_has_no_alphanumeric_chars(client, monkeypatch, title):
    classifier_calls = []

    def fake_classifier(title, description):
        classifier_calls.append((title, description))
        return {"category": "bug", "priority": "P1", "tags": []}

    monkeypatch.setattr("app.classifier.classify_ticket", fake_classifier)

    response = client.post(
        "/tickets",
        json={"title": title, "description": "Descripción válida"},
    )

    assert response.status_code == 422
    # El error debe ser del campo `title`, no de otro campo.
    assert response.json()["detail"][0]["loc"] == ["body", "title"]
    # Una petición rechazada no debe llegar al clasificador.
    assert classifier_calls == []
