"""Casos límite pedidos: entradas vacías o enormes, unicode, XSS, SQL, IDs raros, doble envío,
valores inválidos en PATCH, LLM caído y concurrencia. Ver SPEC «Casos límite»."""

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app

GOOD = {"category": "bug", "priority": "P2", "tags": []}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr("app.classifier.classify_ticket", lambda title, description: dict(GOOD))
    return TestClient(app)


def post(client, title="Ticket", description="Descripción válida", **extra):
    return client.post(
        "/tickets", json={"title": title, "description": description, **extra}
    )


def stored(client):
    return client.get("/tickets").json()


# 1) Title vacío o solo espacios ---------------------------------------------------------------


@pytest.mark.parametrize("title", ["", " ", "     ", "\t\n", " ", "​", "　"])
def test_empty_or_whitespace_only_title_is_rejected(client, title):
    assert post(client, title=title).status_code == 422
    assert client.post("/ui/tickets", data={"title": title, "description": "x"}).headers.get(
        "HX-Trigger"
    ) is None
    assert stored(client) == []


def test_missing_or_null_title_is_rejected(client):
    assert client.post("/tickets", json={"description": "x"}).status_code == 422
    assert client.post("/tickets", json={"title": None, "description": "x"}).status_code == 422


# 2) Title de 5000 caracteres ------------------------------------------------------------------


def test_5000_char_title_is_rejected_and_nothing_is_stored(client):
    response = post(client, title="x" * 5000)

    assert response.status_code == 422
    assert stored(client) == []


def test_5000_char_title_is_rejected_by_the_board_too(client):
    response = client.post("/ui/tickets", data={"title": "x" * 5000, "description": "x"})

    assert response.status_code == 200 and 'role="alert"' in response.text
    assert stored(client) == []


@pytest.mark.parametrize(("length", "status"), [(200, 201), (201, 422)])
def test_title_limit_is_counted_in_characters(client, length, status):
    assert post(client, title="ñ" * length).status_code == status
    assert post(client, title="日" * length).status_code == status


# 3) Emojis y unicode no latino ----------------------------------------------------------------

UNICODE_TITLES = [
    "🔥 El servidor está caído 🔥",
    "日本語のタイトルです",
    "عنوان باللغة العربية",
    "Заголовок на русском",
    "ΑΒΓ ελληνικά",
    "हिन्दी शीर्षक",
    "Ñandú — acentos áéíóú ü",
    "🔥",  # solo un emoji
    "👨‍👩‍👧‍👦 familia",
    "🇪🇸",
]


@pytest.mark.parametrize("title", UNICODE_TITLES)
def test_unicode_titles_round_trip_exactly(client, title):
    created = post(client, title=title, description="Descripción 🎉 日本語").json()

    assert created["title"] == title
    assert created["description"] == "Descripción 🎉 日本語"
    assert stored(client)[0]["title"] == title
    assert title in client.get("/ui/tickets").text


def test_unicode_assignees_and_case_insensitive_filter(client):
    post(client, assignees=["José", "李雷", "Zoë", "ÁLVARO"])

    for needle in ("josé", "李雷", "ZOË", "álvaro"):
        assert len(client.get("/tickets", params={"assignee": needle}).json()) == 1


@pytest.mark.parametrize("title", ["!!!", "...", "+++", "$$$", "—"])
def test_titles_with_only_punctuation_or_symbols_are_still_rejected(client, title):
    assert post(client, title=title).status_code == 422


# 4) HTML/JS en la descripción y otros campos (XSS) --------------------------------------------

XSS = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    '"><svg onload=alert(1)>',
    "</td></tr></table><script>alert(1)</script>",
]


def test_user_data_never_lands_in_a_link_or_a_script_context(client):
    """`javascript:…` no tiene caracteres HTML que escapar: solo es peligroso en un href/src."""
    post(client, title="javascript:alert(1)", description="javascript:alert(1)",
         assignees=["javascript:alert(1)"])

    for page in (client.get("/").text, client.get("/", params={"assignee": "javascript:x"}).text):
        assert not re.search(r'(href|src|action|formaction)\s*=\s*["\']?\s*javascript:', page, re.I)
        assert not re.search(r"\son\w+\s*=", page.split("<script>", 1)[0], re.I)


@pytest.mark.parametrize("payload", XSS)
def test_html_in_description_is_stored_verbatim_and_never_rendered(client, payload):
    created = post(client, description=payload, assignees=[payload[:60]]).json()
    assert created["description"] == payload  # la API devuelve el texto tal cual (es JSON)

    for page in (client.get("/").text, client.get("/ui/tickets").text):
        assert payload not in page
        assert "<script>alert(1)" not in page and "<img src=x" not in page
        assert "<svg onload" not in page


@pytest.mark.parametrize("payload", XSS)
def test_reflected_filter_values_in_the_url_are_escaped(client, payload):
    post(client)

    page = client.get("/", params={"assignee": payload, "status": payload}).text

    assert payload not in page
    assert "<script>alert(1)" not in page and "<img src=x" not in page


def test_attribute_injection_through_an_assignee_name_is_impossible(client):
    post(client, assignees=['" onfocus="alert(1)" autofocus="'])

    page = client.get("/").text

    assert 'onfocus="alert(1)"' not in page
    assert "&#34; onfocus=&#34;alert(1)&#34;" in page


def test_header_injection_through_filters_is_impossible(client):
    response = client.get("/ui/tickets", params={"assignee": "Ana\r\nX-Evil: 1"})

    assert "X-Evil" not in response.headers
    pushed = response.headers["HX-Push-Url"]
    assert "\r" not in pushed and "\n" not in pushed


# 5) Inyección SQL básica ----------------------------------------------------------------------

SQLI = [
    "'); DROP TABLE tickets;--",
    "' OR '1'='1",
    '" OR 1=1 --',
    "Robert'); DELETE FROM tickets;--",
    "1; UPDATE tickets SET status='closed'",
    "%' OR 1=1 --",
    "\\",
]


@pytest.mark.parametrize("payload", SQLI)
def test_sql_in_ticket_fields_is_stored_as_plain_text(client, payload):
    post(client, title="Normal")
    created = post(client, title=payload + " x", description=payload, assignees=[payload[:60]])

    assert created.status_code == 201
    tickets = stored(client)
    assert len(tickets) == 2  # la tabla sigue existiendo y no se borró nada
    assert {t["status"] for t in tickets} == {"open"}
    assert tickets[0]["description"] == payload


@pytest.mark.parametrize("payload", SQLI)
def test_sql_in_filters_matches_nothing_instead_of_everything(client, payload):
    post(client, assignees=["Ana"])

    for param in ("category", "priority", "status", "assignee"):
        response = client.get("/tickets", params={param: payload})
        assert response.status_code == 200
        assert response.json() == []
    assert len(stored(client)) == 1
    assert client.get("/", params={"status": payload}).status_code == 200


@pytest.mark.parametrize("payload", SQLI)
def test_sql_in_the_page_and_in_patch_bodies_is_harmless(client, payload):
    post(client)

    assert client.get("/ui/tickets", params={"page": payload}).status_code == 200
    assert client.get("/", params={"page": payload}).status_code == 200
    assert client.patch("/tickets/1", json={"status": payload}).status_code == 422
    assert client.patch("/tickets/1", json={"assignees": [payload[:60]]}).status_code == 200
    assert len(stored(client)) == 1


# 6) LLM caído ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("caído"),
        TimeoutError("timeout"),
        ConnectionError("sin red"),
        ValueError("raro"),
    ],
)
def test_llm_failure_falls_back_in_the_api_and_in_the_board(client, monkeypatch, failure):
    def broken(title, description):
        raise failure

    monkeypatch.setattr("app.classifier.classify_ticket", broken)

    api = post(client, title="Con el LLM caído")
    ui = client.post("/ui/tickets", data={"title": "Desde el tablero", "description": "x"})

    assert api.status_code == 201
    assert (api.json()["category"], api.json()["priority"], api.json()["tags"]) == (
        "question", "P3", [],
    )
    assert ui.status_code == 200 and ui.headers["HX-Trigger"] == "ticket-created"
    assert len(stored(client)) == 2


@pytest.mark.parametrize(
    "garbage",
    [None, "texto", 42, [], {}, {"category": "URGENT", "priority": "P1", "tags": []},
     {"category": "bug", "priority": "P9", "tags": []},
     {"category": "bug", "priority": "P1", "tags": "x"},
     {"category": "bug", "priority": "P1", "tags": [1, 2]}],
)
def test_llm_garbage_output_falls_back(client, monkeypatch, garbage):
    monkeypatch.setattr("app.classifier.classify_ticket", lambda title, description: garbage)

    created = post(client).json()

    assert (created["category"], created["priority"]) == ("question", "P3")


# 7) Mismo ticket enviado dos veces seguidas ----------------------------------------------------


def test_the_same_ticket_sent_twice_creates_two_independent_tickets(client):
    first = post(client, title="Duplicado", description="Mismo texto")
    second = post(client, title="Duplicado", description="Mismo texto")

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert len(stored(client)) == 2  # no hay deduplicación: cada envío es un ticket


def test_the_create_button_is_disabled_while_the_request_is_in_flight(client):
    page = client.get("/").text

    assert 'hx-disabled-elt="find button[type=submit]"' in page


# 8) IDs malformados -----------------------------------------------------------------------------


@pytest.mark.parametrize("ticket_id", ["abc", "1.5", "1e3", "NaN", "--1", "0x10", "%00", "ñ"])
def test_malformed_ids_are_422_not_500(client, ticket_id):
    post(client)

    for url in (f"/tickets/{ticket_id}", f"/ui/tickets/{ticket_id}"):
        response = (
            client.patch(url, json={"status": "closed"})
            if url.startswith("/tickets")
            else client.post(url, data={"set_status": "closed"})
        )
        assert response.status_code in (404, 405, 422), (url, response.status_code)
        assert response.status_code != 500


@pytest.mark.parametrize(
    "ticket_id",
    [
        "-1",
        "0",
        "99999999999999999999",
        "9223372036854775808",
        "9223372036854775807",
        "2147483648",
        "-99999999999999999999",  # negativo enorme: también desbordaría un INTEGER de SQLite
        "-9223372036854775809",
    ],
)
def test_well_formed_but_unknown_ids_are_404_not_500(client, ticket_id):
    post(client)

    assert client.patch(f"/tickets/{ticket_id}", json={"status": "closed"}).status_code == 404
    board = client.post(f"/ui/tickets/{ticket_id}", data={"set_status": "closed"})
    assert board.status_code == 200 and "no existe" in board.text
    assert stored(client)[0]["status"] == "open"


# 9) PATCH con valores inválidos ---------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [{"status": "inventado"}, {"status": ""}, {"status": None}, {"status": 5}, {"status": ["open"]},
     {"priority": "P9"}, {"priority": "p1"}, {"priority": 1}, {"assignees": "Ana"},
     {"assignees": [None]}, {"assignees": [{"a": 1}]}, {}, {"unknown": "x"}, [], "texto", None],
)
def test_patch_with_invalid_values_is_422_and_changes_nothing(client, body):
    post(client, assignees=["Ana"])

    response = client.patch("/tickets/1", json=body)

    assert response.status_code == 422
    ticket = stored(client)[0]
    assert (ticket["status"], ticket["priority"], ticket["assignees"]) == ("open", "P2", ["Ana"])


def test_patch_with_a_body_that_is_not_json_is_422(client):
    post(client)

    for content in (b"no es json", b"{", b"", b"\xff\xfe"):
        response = client.patch(
            "/tickets/1", content=content, headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422


def test_patch_cannot_change_immutable_fields(client):
    post(client, title="Original")

    response = client.patch(
        "/tickets/1",
        json={"status": "closed", "title": "Hackeado", "category": "urgent", "id": 99,
              "created_at": "2000-01-01T00:00:00Z", "tags": ["x"]},
    )

    assert response.status_code == 200
    ticket = stored(client)[0]
    assert (ticket["title"], ticket["category"], ticket["id"], ticket["tags"]) == (
        "Original", "bug", 1, [],
    )
    assert ticket["created_at"] != "2000-01-01T00:00:00Z"


# 10) 20 POSTs concurrentes ----------------------------------------------------------------------


def run_concurrently(count, work):
    barrier = threading.Barrier(count)

    def wrapped(number):
        barrier.wait()  # todos arrancan a la vez
        return work(number)

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(wrapped, range(count)))


def test_20_concurrent_posts_create_20_distinct_tickets(client, monkeypatch):
    def slow_classifier(title, description):
        time.sleep(0.05)  # fuerza que las peticiones se solapen de verdad
        return dict(GOOD)

    monkeypatch.setattr("app.classifier.classify_ticket", slow_classifier)

    responses = run_concurrently(
        20, lambda n: TestClient(app).post(
            "/tickets", json={"title": f"Concurrente {n}", "description": "x"}
        )
    )

    assert [r.status_code for r in responses] == [201] * 20
    ids = {r.json()["id"] for r in responses}
    assert len(ids) == 20  # sin ids repetidos
    assert len(stored(client)) == 20
    assert {t["title"] for t in stored(client)} == {f"Concurrente {n}" for n in range(20)}


def test_20_concurrent_mixed_writes_and_reads_do_not_fail_or_corrupt(client):
    for n in range(10):
        post(client, title=f"Base {n}")

    def work(number):
        local = TestClient(app)
        if number % 3 == 0:
            return local.post("/tickets", json={"title": f"Nuevo {number}", "description": "x"})
        if number % 3 == 1:
            return local.patch(f"/tickets/{number % 10 + 1}", json={"status": "in_progress"})
        return local.get("/tickets")

    responses = run_concurrently(20, work)

    assert all(r.status_code in (200, 201) for r in responses), [r.status_code for r in responses]
    assert len(stored(client)) == 10 + len([n for n in range(20) if n % 3 == 0])


def test_20_concurrent_board_creations_from_the_form(client):
    responses = run_concurrently(
        20, lambda n: TestClient(app).post(
            "/ui/tickets", data={"title": f"Form {n}", "description": "x"}
        )
    )

    assert [r.status_code for r in responses] == [200] * 20
    assert len(stored(client)) == 20


def test_concurrent_database_access_never_raises_locked_errors(client):
    errors = []

    def work(number):
        try:
            db.create_ticket(f"Directo {number}", "x", dict(GOOD), assignees=["Ana"])
            db.list_tickets()
            db.update_ticket(1, status="in_progress")
        except Exception as error:  # noqa: BLE001 - se recogen para el assert
            errors.append(repr(error))

    run_concurrently(20, work)

    assert errors == []
