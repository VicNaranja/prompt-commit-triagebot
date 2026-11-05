"""Tests de aceptación de TriageBot.

Los 5 primeros son los tests obligatorios del bootcamp; al final hay casos adicionales.

Ninguno llama al LLM real: cada test sustituye `app.classifier.classify_ticket` por una
función falsa con `monkeypatch`, así que no necesitan API key ni conexión.
"""

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Test client with an isolated database path when the app supports DATABASE_URL."""
    # Cada test usa su propia base de datos SQLite en una carpeta temporal, para no
    # mezclar datos entre tests ni tocar la base de datos real.
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    return TestClient(app)


def fake_classification(category="bug", priority="P1", tags=None):
    """Clasificación falsa que devuelve el clasificador sustituido (en lugar del LLM)."""
    return {
        "category": category,
        "priority": priority,
        "tags": tags if tags is not None else ["login", "customer-impact"],
    }


def test_post_ticket_creates_ticket_with_classification(client, monkeypatch):
    """Crear un ticket válido devuelve 201 con el ticket completo y ya clasificado."""
    # El clasificador falso devuelve bug / P1 / ["login", "customer-impact"].
    monkeypatch.setattr(
        "app.classifier.classify_ticket",
        lambda title, description: fake_classification(),
    )

    response = client.post(
        "/tickets",
        json={
            "title": "La app no carga",
            "description": "Desde esta mañana aparece una pantalla en blanco al iniciar sesión",
        },
    )

    # La respuesta trae el id generado, los datos enviados, la clasificación,
    # el estado inicial "open" y las fechas de creación y actualización.
    assert response.status_code == 201
    data = response.json()
    assert isinstance(data["id"], int)
    assert data["title"] == "La app no carga"
    assert data["description"].startswith("Desde esta mañana")
    assert data["category"] == "bug"
    assert data["priority"] == "P1"
    assert data["tags"] == ["login", "customer-impact"]
    assert data["status"] == "open"
    assert "created_at" in data
    assert "updated_at" in data


def test_created_ticket_is_persisted_and_listed(client, monkeypatch):
    """Un ticket creado queda guardado y aparece después en GET /tickets."""
    monkeypatch.setattr(
        "app.classifier.classify_ticket",
        lambda title, description: fake_classification(category="feature_request", priority="P2", tags=["export"]),
    )

    created = client.post(
        "/tickets",
        json={"title": "Exportar a PDF", "description": "Necesitamos exportar informes a PDF"},
    )
    assert created.status_code == 201

    # Se pide la lista en una petición aparte: si aparece, es que se guardó en SQLite.
    listed = client.get("/tickets")

    assert listed.status_code == 200
    tickets = listed.json()
    assert len(tickets) >= 1
    assert any(ticket["title"] == "Exportar a PDF" for ticket in tickets)
    assert any(ticket["category"] == "feature_request" for ticket in tickets)


def test_post_ticket_rejects_invalid_input(client):
    """Título o descripción vacíos o demasiado largos se rechazan con 422."""
    invalid_payloads = [
        {"title": "", "description": "Descripción válida"},  # título vacío
        {"title": "   ", "description": "Descripción válida"},  # título solo con espacios
        {"title": "x" * 201, "description": "Descripción válida"},  # título > 200 caracteres
        {"title": "Título válido", "description": ""},  # descripción vacía
        {"title": "Título válido", "description": "x" * 5001},  # descripción > 5000 caracteres
    ]

    for payload in invalid_payloads:
        response = client.post("/tickets", json=payload)
        assert response.status_code == 422


def test_classifier_failure_uses_safe_fallback(client, monkeypatch):
    """Si el clasificador falla, la API no se cae y aplica el fallback seguro."""

    # Simula que el LLM no está disponible: el clasificador lanza una excepción.
    def broken_classifier(title, description):
        raise RuntimeError("Anthropic is unavailable")

    monkeypatch.setattr("app.classifier.classify_ticket", broken_classifier)

    response = client.post(
        "/tickets",
        json={
            "title": "No entiendo cómo cambiar mi contraseña",
            "description": "El cliente pregunta cómo resetear la contraseña desde el portal",
        },
    )

    # El ticket se crea igualmente, con el fallback question / P3 / sin tags.
    assert response.status_code == 201
    data = response.json()
    assert data["category"] == "question"
    assert data["priority"] == "P3"
    assert data["tags"] == []
    assert data["status"] == "open"


def test_update_ticket_and_filter_by_status_priority_category(client, monkeypatch):
    """Se puede cambiar estado y prioridad con PATCH y filtrar por categoría, prioridad y estado."""
    # 1. Se crea un ticket clasificado como urgent / P1.
    monkeypatch.setattr(
        "app.classifier.classify_ticket",
        lambda title, description: fake_classification(category="urgent", priority="P1", tags=["demo"]),
    )

    created = client.post(
        "/tickets",
        json={
            "title": "Esto urge porque tenemos demo el viernes",
            "description": "El cliente necesita una solución antes de una demo crítica",
        },
    )
    assert created.status_code == 201
    ticket_id = created.json()["id"]

    # 2. Se cambia a mano el estado a in_progress y la prioridad a P2.
    patched = client.patch(f"/tickets/{ticket_id}", json={"status": "in_progress", "priority": "P2"})
    assert patched.status_code == 200
    assert patched.json()["status"] == "in_progress"
    assert patched.json()["priority"] == "P2"

    # 3. Filtrando por los tres campos a la vez sale exactamente ese ticket, con los valores
    #    nuevos (en una base de datos vacía al empezar el test).
    filtered = client.get("/tickets", params={"category": "urgent", "priority": "P2", "status": "in_progress"})
    assert filtered.status_code == 200
    tickets = filtered.json()
    assert len(tickets) == 1
    assert tickets[0]["id"] == ticket_id


# =========================================================================================
# Casos adicionales (añadidos por el equipo con autorización del profesor).
# Cubren casos de la SPEC que los 5 tests obligatorios de arriba no prueban.
# =========================================================================================

# Clasificación que devuelve el clasificador falso según el título del ticket.
CLASSIFICATIONS = {
    "Pantalla en blanco": {"category": "bug", "priority": "P1", "tags": ["login"]},
    "Exportar a PDF": {"category": "feature_request", "priority": "P3", "tags": ["export"]},
    "Cambiar contraseña": {"category": "question", "priority": "P2", "tags": []},
}
DEFAULT_CLASSIFICATION = {"category": "bug", "priority": "P2", "tags": []}


@pytest.fixture()
def tickets_client(client, monkeypatch):
    """El `client` de arriba, con un clasificador falso que clasifica según el título."""
    monkeypatch.setattr(
        "app.classifier.classify_ticket",
        lambda title, description: CLASSIFICATIONS.get(title, DEFAULT_CLASSIFICATION),
    )
    return client


@pytest.fixture()
def fixed_time(monkeypatch):
    """Permite fijar la hora que usa la base de datos: `fixed_time("2026-01-01T10:00:00Z")`."""

    def set_time(timestamp: str) -> None:
        monkeypatch.setattr(db, "_now", lambda: timestamp)

    return set_time


def create(tickets_client, title, description="Descripción del ticket"):
    """Crea un ticket y devuelve el JSON de la respuesta."""
    response = tickets_client.post("/tickets", json={"title": title, "description": description})
    assert response.status_code == 201
    return response.json()


# --- POST /tickets ------------------------------------------------------------------------


def test_post_accepts_title_and_description_at_max_length(tickets_client):
    """Los límites exactos se aceptan: título de 200 y descripción de 5000 caracteres."""
    response = tickets_client.post("/tickets", json={"title": "x" * 200, "description": "y" * 5000})

    assert response.status_code == 201
    assert len(response.json()["title"]) == 200
    assert len(response.json()["description"]) == 5000


def test_post_rejects_description_with_only_spaces(tickets_client):
    """Una descripción solo con espacios cuenta como vacía: 422."""
    response = tickets_client.post(
        "/tickets", json={"title": "Título válido", "description": "   "}
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"description": "Falta el título"},
        {"title": "Falta la descripción"},
        {},
    ],
)
def test_post_rejects_missing_fields(tickets_client, payload):
    """Si falta el título, la descripción o ambos: 422."""
    response = tickets_client.post("/tickets", json=payload)

    assert response.status_code == 422


def test_post_stores_title_and_description_trimmed(tickets_client):
    """El título y la descripción se guardan sin espacios al principio ni al final."""
    created = create(tickets_client, "   Pantalla en blanco   ", "  Al iniciar sesión  ")

    assert created["title"] == "Pantalla en blanco"
    assert created["description"] == "Al iniciar sesión"
    # También en lo que queda guardado, no solo en la respuesta del POST.
    assert tickets_client.get("/tickets").json()[0]["title"] == "Pantalla en blanco"


# --- GET /tickets -------------------------------------------------------------------------


def test_get_returns_empty_list_when_there_are_no_tickets(tickets_client):
    """Sin tickets, la lista está vacía."""
    response = tickets_client.get("/tickets")

    assert response.status_code == 200
    assert response.json() == []


def test_get_orders_by_created_at_newest_first(tickets_client, fixed_time):
    """Orden por created_at, el más reciente primero, aunque se haya creado después."""
    fixed_time("2026-01-02T10:00:00Z")
    newest = create(tickets_client, "Creado primero pero con fecha más reciente")
    fixed_time("2026-01-01T10:00:00Z")
    oldest = create(tickets_client, "Creado después pero con fecha más antigua")

    ids = [ticket["id"] for ticket in tickets_client.get("/tickets").json()]

    assert ids == [newest["id"], oldest["id"]]


def test_get_breaks_created_at_ties_by_id_highest_first(tickets_client, fixed_time):
    """Si dos tickets tienen el mismo created_at, va primero el de id mayor."""
    fixed_time("2026-01-01T10:00:00Z")
    first = create(tickets_client, "Primero")
    second = create(tickets_client, "Segundo")

    ids = [ticket["id"] for ticket in tickets_client.get("/tickets").json()]

    assert ids == [second["id"], first["id"]]


@pytest.mark.parametrize(
    ("params", "expected_title"),
    [
        ({"category": "bug"}, "Pantalla en blanco"),
        ({"category": "feature_request"}, "Exportar a PDF"),
        ({"priority": "P2"}, "Cambiar contraseña"),
        ({"priority": "P3"}, "Exportar a PDF"),
    ],
)
def test_get_filters_by_single_field_and_excludes_the_rest(tickets_client, params, expected_title):
    """Cada filtro por separado devuelve solo los tickets que cumplen y excluye los demás."""
    for title in CLASSIFICATIONS:
        create(tickets_client, title)

    tickets = tickets_client.get("/tickets", params=params).json()

    assert [ticket["title"] for ticket in tickets] == [expected_title]


def test_get_filters_by_status(tickets_client):
    """El filtro por estado excluye los tickets con otro estado."""
    open_ticket = create(tickets_client, "Pantalla en blanco")
    closed_ticket = create(tickets_client, "Exportar a PDF")
    tickets_client.patch(f"/tickets/{closed_ticket['id']}", json={"status": "closed"})

    open_ids = [
        ticket["id"] for ticket in tickets_client.get("/tickets", params={"status": "open"}).json()
    ]
    closed_ids = [
        ticket["id"]
        for ticket in tickets_client.get("/tickets", params={"status": "closed"}).json()
    ]

    assert open_ids == [open_ticket["id"]]
    assert closed_ids == [closed_ticket["id"]]


@pytest.mark.parametrize(
    "params",
    [{"category": "URGENT"}, {"priority": "P9"}, {"status": "inventado"}],
)
def test_get_with_unknown_filter_value_returns_empty_list(tickets_client, params):
    """Un valor de filtro que no existe no da error: 200 con lista vacía."""
    create(tickets_client, "Pantalla en blanco")

    response = tickets_client.get("/tickets", params=params)

    assert response.status_code == 200
    assert response.json() == []


# --- PATCH /tickets/{ticket_id} -----------------------------------------------------------


def test_patch_with_empty_body_returns_422(tickets_client):
    """Un PATCH sin nada que cambiar ({}) se rechaza con 422."""
    ticket = create(tickets_client, "Pantalla en blanco")

    response = tickets_client.patch(f"/tickets/{ticket['id']}", json={})

    assert response.status_code == 422


def test_patch_unknown_ticket_returns_404(tickets_client):
    """Un PATCH a un ticket que no existe devuelve 404."""
    response = tickets_client.patch("/tickets/9999", json={"status": "closed"})

    assert response.status_code == 404


@pytest.mark.parametrize(
    "payload",
    [{"status": "CLOSED"}, {"status": "cerrado"}, {"priority": "P4"}, {"priority": "p1"}],
)
def test_patch_rejects_values_outside_the_enum(tickets_client, payload):
    """Un estado o prioridad fuera de los valores permitidos devuelve 422 y no cambia nada."""
    ticket = create(tickets_client, "Pantalla en blanco")

    response = tickets_client.patch(f"/tickets/{ticket['id']}", json=payload)

    assert response.status_code == 422
    stored = tickets_client.get("/tickets").json()[0]
    assert stored["status"] == "open"
    assert stored["priority"] == "P1"


def test_patch_only_status_keeps_priority(tickets_client):
    """Cambiar solo el estado no toca la prioridad."""
    ticket = create(tickets_client, "Pantalla en blanco")  # P1

    patched = tickets_client.patch(
        f"/tickets/{ticket['id']}", json={"status": "in_progress"}
    ).json()

    assert patched["status"] == "in_progress"
    assert patched["priority"] == "P1"


def test_patch_only_priority_keeps_status(tickets_client):
    """Cambiar solo la prioridad no toca el estado."""
    ticket = create(tickets_client, "Pantalla en blanco")  # open

    patched = tickets_client.patch(f"/tickets/{ticket['id']}", json={"priority": "P3"}).json()

    assert patched["priority"] == "P3"
    assert patched["status"] == "open"


def test_patch_updates_updated_at_but_not_created_at(tickets_client, fixed_time):
    """Al cambiar un ticket se actualiza updated_at; created_at se mantiene."""
    fixed_time("2026-01-01T10:00:00Z")
    ticket = create(tickets_client, "Pantalla en blanco")
    fixed_time("2026-01-01T12:30:00Z")

    patched = tickets_client.patch(f"/tickets/{ticket['id']}", json={"status": "closed"}).json()

    assert patched["created_at"] == "2026-01-01T10:00:00Z"
    assert patched["updated_at"] == "2026-01-01T12:30:00Z"


def test_patch_ignores_immutable_fields(tickets_client):
    """Los campos inmutables (category, title...) no cambian aunque vengan en el PATCH."""
    ticket = create(tickets_client, "Pantalla en blanco")  # bug

    patched = tickets_client.patch(
        f"/tickets/{ticket['id']}",
        json={"status": "closed", "category": "question", "title": "Otro título"},
    ).json()

    assert patched["status"] == "closed"
    assert patched["category"] == "bug"
    assert patched["title"] == "Pantalla en blanco"


def test_patch_with_only_immutable_fields_returns_422(tickets_client):
    """Un PATCH que solo trae campos inmutables no tiene nada que cambiar: 422."""
    ticket = create(tickets_client, "Pantalla en blanco")

    response = tickets_client.patch(f"/tickets/{ticket['id']}", json={"category": "question"})

    assert response.status_code == 422
