"""Tests de aceptación de TriageBot.

Los 5 primeros son los tests obligatorios del bootcamp; al final hay casos adicionales.

Ninguno llama al LLM real: cada test sustituye `app.classifier.classify_ticket` por una
función falsa con `monkeypatch`, así que no necesitan API key ni conexión.
"""

import sqlite3
from datetime import date

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


# --- Técnicos, estado «resuelto» y fecha límite (SPEC §2) ----------------------------------
# Según CLASSIFICATIONS: «Pantalla en blanco» es P1, «Cambiar contraseña» P2 y
# «Exportar a PDF» P3.


def test_new_ticket_is_unassigned_and_due_date_depends_on_priority(tickets_client, fixed_time):
    """Un ticket nuevo llega sin técnico; P1 vence el mismo día, P2 al siguiente, P3 a los dos."""
    fixed_time("2026-03-10T09:00:00Z")

    p1 = create(tickets_client, "Pantalla en blanco")
    p2 = create(tickets_client, "Cambiar contraseña")
    p3 = create(tickets_client, "Exportar a PDF")

    assert p1["technician"] is None
    assert (p1["due_date"], p2["due_date"], p3["due_date"]) == (
        "2026-03-10",
        "2026-03-11",
        "2026-03-12",
    )


def test_due_date_uses_spanish_date(tickets_client, fixed_time):
    """La fecha límite usa la fecha de España: las 23:30 UTC del día 1 ya son el día 2."""
    fixed_time("2026-01-01T23:30:00Z")

    ticket = create(tickets_client, "Pantalla en blanco")  # P1: vence el mismo día (en España)

    assert ticket["due_date"] == "2026-01-02"


def test_patch_priority_recalculates_due_date_from_creation(tickets_client, fixed_time):
    """Al cambiar la prioridad se recalcula la fecha límite, contando desde la creación."""
    fixed_time("2026-03-10T09:00:00Z")
    ticket = create(tickets_client, "Pantalla en blanco")  # P1 → 10/03
    fixed_time("2026-03-20T09:00:00Z")  # el cambio se hace días después

    patched = tickets_client.patch(f"/tickets/{ticket['id']}", json={"priority": "P3"}).json()

    assert patched["due_date"] == "2026-03-12"


def test_patch_can_set_status_resolved(tickets_client):
    """«resolved» es un estado válido."""
    ticket = create(tickets_client, "Pantalla en blanco")

    patched = tickets_client.patch(f"/tickets/{ticket['id']}", json={"status": "resolved"})

    assert patched.status_code == 200
    assert patched.json()["status"] == "resolved"


def test_patch_assigns_and_unassigns_technician(tickets_client):
    """Se asigna un técnico de la lista y con null el ticket vuelve a quedar sin asignar."""
    ticket = create(tickets_client, "Pantalla en blanco")
    url = f"/tickets/{ticket['id']}"

    assigned = tickets_client.patch(url, json={"technician": "Técnico 3"}).json()
    unassigned = tickets_client.patch(url, json={"technician": None}).json()

    assert assigned["technician"] == "Técnico 3"
    assert unassigned["technician"] is None


def test_patch_rejects_technician_not_in_list(tickets_client):
    """Un técnico que no está en la lista devuelve 422."""
    ticket = create(tickets_client, "Pantalla en blanco")

    response = tickets_client.patch(f"/tickets/{ticket['id']}", json={"technician": "Técnico 99"})

    assert response.status_code == 422


def test_get_filters_by_several_statuses(tickets_client):
    """`status` se puede repetir: open + in_progress son los activos."""
    open_ticket = create(tickets_client, "Pantalla en blanco")
    in_progress = create(tickets_client, "Cambiar contraseña")
    resolved = create(tickets_client, "Exportar a PDF")
    tickets_client.patch(f"/tickets/{in_progress['id']}", json={"status": "in_progress"})
    tickets_client.patch(f"/tickets/{resolved['id']}", json={"status": "resolved"})

    tickets = tickets_client.get("/tickets", params={"status": ["open", "in_progress"]}).json()

    assert {ticket["id"] for ticket in tickets} == {open_ticket["id"], in_progress["id"]}


def test_get_filters_by_technician_and_unassigned(tickets_client):
    """Filtro por técnico: por nombre, o `none` para los que no tienen técnico."""
    assigned = create(tickets_client, "Pantalla en blanco")
    unassigned = create(tickets_client, "Exportar a PDF")
    tickets_client.patch(f"/tickets/{assigned['id']}", json={"technician": "Técnico 1"})

    by_name = tickets_client.get("/tickets", params={"technician": "Técnico 1"}).json()
    by_none = tickets_client.get("/tickets", params={"technician": "none"}).json()

    assert [ticket["id"] for ticket in by_name] == [assigned["id"]]
    assert [ticket["id"] for ticket in by_none] == [unassigned["id"]]


@pytest.mark.parametrize(
    ("due", "expected_title"),
    [
        ("overdue", "Pantalla en blanco"),  # P1 creado el 10 → vence el 10
        ("today", "Cambiar contraseña"),  # P2 creado el 10 → vence el 11
        ("tomorrow", "Exportar a PDF"),  # P3 creado el 10 → vence el 12
    ],
)
def test_get_filters_by_due_date(tickets_client, fixed_time, monkeypatch, due, expected_title):
    """Filtro por fecha límite respecto a «hoy» (aquí, 11/03/2026)."""
    fixed_time("2026-03-10T09:00:00Z")
    for title in CLASSIFICATIONS:
        create(tickets_client, title)
    monkeypatch.setattr("app.dates.today", lambda: date(2026, 3, 11))

    tickets = tickets_client.get("/tickets", params={"due": due}).json()

    assert [ticket["title"] for ticket in tickets] == [expected_title]


def test_get_with_unknown_due_value_returns_empty_list(tickets_client):
    """Un valor desconocido en `due` devuelve una lista vacía."""
    create(tickets_client, "Pantalla en blanco")

    assert tickets_client.get("/tickets", params={"due": "ayer"}).json() == []


def test_old_database_is_migrated(client, tmp_path):
    """Una base de datos creada antes de existir technician y due_date se migra al usarla."""
    with sqlite3.connect(tmp_path / "test.db") as conn:
        conn.execute(
            "CREATE TABLE tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,"
            " description TEXT NOT NULL, category TEXT NOT NULL, priority TEXT NOT NULL,"
            " tags TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,"
            " updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO tickets (title, description, category, priority, tags, status,"
            " created_at, updated_at) VALUES ('Antiguo', 'd', 'bug', 'P2', '[]', 'open',"
            " '2026-03-10T09:00:00Z', '2026-03-10T09:00:00Z')"
        )

    tickets = client.get("/tickets").json()

    assert tickets[0]["technician"] is None
    assert tickets[0]["due_date"] == "2026-03-11"


# --- Web de gestión y tablero kanban (SPEC §6) ---------------------------------------------


def test_management_page_shows_only_active_tickets_by_default(tickets_client):
    """La web de gestión muestra por defecto solo los tickets activos."""
    create(tickets_client, "Pantalla en blanco")
    closed = create(tickets_client, "Exportar a PDF")
    tickets_client.patch(f"/tickets/{closed['id']}", json={"status": "closed"})

    page = tickets_client.get("/")

    assert page.status_code == 200
    assert "Pantalla en blanco" in page.text
    assert "Exportar a PDF" not in page.text


def test_management_table_applies_web_filters(tickets_client):
    """La tabla filtra por estado y prioridad (con los nombres de filtro de la web)."""
    create(tickets_client, "Pantalla en blanco")
    closed = create(tickets_client, "Exportar a PDF")
    tickets_client.patch(f"/tickets/{closed['id']}", json={"status": "closed"})

    closed_table = tickets_client.get("/gestion/tabla", params={"estado": "closed"}).text
    p1_table = tickets_client.get("/gestion/tabla", params={"estado": "all", "prioridad": "P1"})

    assert "Exportar a PDF" in closed_table and "Pantalla en blanco" not in closed_table
    assert "Pantalla en blanco" in p1_table.text and "Exportar a PDF" not in p1_table.text


def test_ticket_detail_and_save(tickets_client):
    """La ventana de detalle muestra el ticket y guarda estado, prioridad y técnico."""
    ticket = create(tickets_client, "Pantalla en blanco", "Descripción completa del problema")

    detail = tickets_client.get(f"/gestion/tickets/{ticket['id']}")
    saved = tickets_client.post(
        f"/gestion/tickets/{ticket['id']}",
        data={"status": "in_progress", "priority": "P2", "technician": "Técnico 2"},
    )
    stored = tickets_client.get("/tickets").json()[0]

    assert "Descripción completa del problema" in detail.text
    assert saved.status_code == 200
    assert saved.headers["HX-Trigger"] == "ticketsChanged"
    assert (stored["status"], stored["priority"], stored["technician"]) == (
        "in_progress",
        "P2",
        "Técnico 2",
    )


def test_ticket_detail_mark_as_resolved(tickets_client):
    """El botón «Marcar como resuelto» pasa el ticket a resolved."""
    ticket = create(tickets_client, "Pantalla en blanco")

    tickets_client.post(f"/gestion/tickets/{ticket['id']}", data={"accion": "resolver"})

    assert tickets_client.get("/tickets").json()[0]["status"] == "resolved"


def test_ticket_detail_unknown_ticket_returns_404(tickets_client):
    """La ventana de detalle de un ticket que no existe devuelve 404."""
    assert tickets_client.get("/gestion/tickets/9999").status_code == 404


def test_kanban_move_changes_status(tickets_client):
    """Mover una tarjeta en el kanban cambia el estado del ticket."""
    ticket = create(tickets_client, "Pantalla en blanco")

    page = tickets_client.get("/kanban")
    moved = tickets_client.post(f"/kanban/tickets/{ticket['id']}/estado", data={"status": "closed"})

    assert page.status_code == 200
    assert moved.status_code == 200
    assert tickets_client.get("/tickets").json()[0]["status"] == "closed"


def test_kanban_move_rejects_invalid_status_and_unknown_ticket(tickets_client):
    """Mover a un estado no válido da 422; mover un ticket que no existe, 404."""
    ticket = create(tickets_client, "Pantalla en blanco")

    invalid = tickets_client.post(
        f"/kanban/tickets/{ticket['id']}/estado", data={"status": "CLOSED"}
    )
    unknown = tickets_client.post("/kanban/tickets/9999/estado", data={"status": "closed"})

    assert invalid.status_code == 422
    assert unknown.status_code == 404
