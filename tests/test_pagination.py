"""Paginación (API y tablero) y filtros/página en la URL (ver SPEC §4 y §6)."""

import re

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.pagination import PAGE_SIZE, clamp_page, page_window, parse_page

CLASSIFICATION = {"category": "bug", "priority": "P2", "tags": []}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(
        "app.classifier.classify_ticket", lambda title, description: dict(CLASSIFICATION)
    )
    return TestClient(app)


def fill(count, **kwargs):
    """Crea `count` tickets «Ticket 001»… directamente en la BD (rápido). El 001 es el más viejo."""
    for number in range(1, count + 1):
        db.create_ticket(f"Ticket {number:03d}", "Descripción", dict(CLASSIFICATION), **kwargs)


def titles(html):
    return re.findall(r'class="font-medium \[overflow-wrap:anywhere\]">([^<]+)</span>', html)


def numbers(html):
    return [int(title.split()[1]) for title in titles(html)]


# --- Lógica pura -------------------------------------------------------------------------------


def test_page_size_is_20():
    assert PAGE_SIZE == 20


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", 1), ("7", 7), (" 3 ", 3), ("0", 1), ("-4", 1), ("abc", 1), ("", 1), (None, 1)],
)
def test_parse_page_never_fails(raw, expected):
    assert parse_page(raw) == expected


@pytest.mark.parametrize(
    ("page", "total", "expected"),
    [(1, 0, 1), (5, 0, 1), (1, 20, 1), (2, 20, 1), (2, 21, 2), (99, 45, 3), (3, 45, 3), (1, 45, 1)],
)
def test_clamp_page(page, total, expected):
    assert clamp_page(page, total) == expected


@pytest.mark.parametrize(
    ("current", "last", "expected"),
    [
        (1, 1, [1]),
        (1, 3, [1, 2, 3]),
        (4, 7, [1, 2, 3, 4, 5, 6, 7]),
        (1, 10, [1, 2, None, 10]),
        (3, 10, [1, 2, 3, 4, None, 10]),
        (5, 10, [1, None, 4, 5, 6, None, 10]),
        (8, 10, [1, None, 7, 8, 9, 10]),
        (10, 10, [1, None, 9, 10]),
    ],
)
def test_page_window(current, last, expected):
    assert page_window(current, last) == expected


# --- API: limit, offset y X-Total-Count --------------------------------------------------------


def test_api_without_limit_returns_everything_with_the_total_header(client):
    fill(25)

    response = client.get("/tickets")

    assert response.status_code == 200
    assert len(response.json()) == 25
    assert response.headers["X-Total-Count"] == "25"


def test_api_limit_and_offset_slice_the_list_and_keep_the_total(client):
    fill(25)

    first = client.get("/tickets", params={"limit": 10})
    last = client.get("/tickets", params={"limit": 10, "offset": 20})

    assert len(first.json()) == 10 and first.headers["X-Total-Count"] == "25"
    assert len(last.json()) == 5 and last.headers["X-Total-Count"] == "25"


def test_api_consecutive_pages_neither_repeat_nor_skip_tickets(client):
    fill(25)

    seen = []
    for offset in (0, 10, 20):
        page = client.get("/tickets", params={"limit": 10, "offset": offset}).json()
        seen += [ticket["id"] for ticket in page]

    assert seen == list(range(25, 0, -1))  # el más reciente primero, sin huecos ni repetidos


def test_api_total_count_respects_the_filters(client):
    fill(5)
    client.patch("/tickets/1", json={"status": "closed"})
    client.patch("/tickets/2", json={"status": "closed"})

    response = client.get("/tickets", params={"status": "closed", "limit": 1})

    assert len(response.json()) == 1
    assert response.headers["X-Total-Count"] == "2"


def test_api_offset_past_the_end_returns_empty_list_with_the_real_total(client):
    fill(3)

    response = client.get("/tickets", params={"offset": 50, "limit": 10})

    assert response.status_code == 200
    assert response.json() == []
    assert response.headers["X-Total-Count"] == "3"


def test_api_offset_without_limit_skips_the_first_tickets(client):
    fill(5)

    response = client.get("/tickets", params={"offset": 3})

    assert [t["id"] for t in response.json()] == [2, 1]


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 201}, {"limit": -1}, {"limit": "x"}, {"offset": -1}, {"offset": "x"}],
)
def test_api_invalid_limit_or_offset_returns_422(client, params):
    assert client.get("/tickets", params=params).status_code == 422


def test_api_accepts_the_limits_of_limit(client):
    fill(3)

    assert client.get("/tickets", params={"limit": 1}).status_code == 200
    assert client.get("/tickets", params={"limit": 200}).status_code == 200


# --- Tablero: 20 por página ---------------------------------------------------------------------


def test_board_shows_the_first_20_with_range_and_pager(client):
    fill(45)

    page = client.get("/ui/tickets").text

    assert numbers(page) == list(range(45, 25, -1))
    assert "<strong class=\"font-semibold text-ink\">1–20</strong>" in page
    assert "<strong class=\"font-semibold text-ink\">45</strong> tickets" in page
    assert 'aria-label="Paginación"' in page
    assert page.count('aria-current="page"') == 1


@pytest.mark.parametrize(
    ("page_number", "first", "last", "range_text"),
    [(2, 25, 6, "21–40"), (3, 5, 1, "41–45")],
)
def test_board_other_pages_show_their_slice(client, page_number, first, last, range_text):
    fill(45)

    page = client.get("/ui/tickets", params={"page": page_number}).text

    assert numbers(page) == list(range(first, last - 1, -1))
    assert f">{range_text}</strong>" in page


@pytest.mark.parametrize(
    ("raw", "expected_first"), [("99", 5), ("0", 45), ("-3", 45), ("abc", 45)]
)
def test_board_bad_page_numbers_are_corrected_without_errors(client, raw, expected_first):
    fill(45)

    response = client.get("/ui/tickets", params={"page": raw})

    assert response.status_code == 200
    assert numbers(response.text)[0] == expected_first


def test_board_with_20_or_fewer_tickets_has_no_pager(client):
    fill(20)

    page = client.get("/ui/tickets").text

    assert len(numbers(page)) == 20
    assert 'aria-label="Paginación"' not in page
    assert re.search(r"<strong[^>]*>20</strong>\s*tickets\s*<", page)


def test_board_21_tickets_need_a_second_page(client):
    fill(21)

    first = client.get("/ui/tickets").text
    second = client.get("/ui/tickets", params={"page": 2}).text

    assert len(numbers(first)) == 20 and numbers(second) == [1]
    assert "Página <strong" in first and ">2</a>" in first


def test_board_empty_result_has_no_pager_and_shows_the_empty_state(client):
    fill(30)

    page = client.get("/ui/tickets", params={"status": "closed", "page": 2}).text

    assert "No hay tickets que coincidan." in page
    assert 'aria-label="Paginación"' not in page


def test_pager_links_keep_the_active_filters(client):
    fill(30)

    page = client.get("/ui/tickets", params={"status": "open", "category": "bug"}).text

    links = re.findall(r'<a href="([^"]+)"[^>]*aria-label="Ir a la página 2"', page)
    assert links, "debe haber un enlace a la página 2"
    assert links[0] == "/?category=bug&amp;status=open&amp;page=2"


def test_pager_links_are_htmx_requests_that_include_the_filters_and_the_page(client):
    fill(30)

    page = client.get("/ui/tickets").text

    assert 'hx-get="/ui/tickets?page=2"' in page
    assert 'hx-include="#filters"' in page
    assert 'hx-target="#tickets-table"' in page


def test_pager_marks_previous_as_disabled_on_the_first_page_and_next_on_the_last(client):
    fill(30)

    first = client.get("/ui/tickets").text
    last = client.get("/ui/tickets", params={"page": 2}).text

    assert 'aria-disabled="true">← Anterior<' in first
    assert 'aria-disabled="true">Siguiente →<' in last
    assert 'aria-disabled="true">Siguiente →<' not in first
    assert 'aria-disabled="true">← Anterior<' not in last


# --- Filtros y página en la URL -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({}, "/"),
        ({"page": 1}, "/"),
        ({"page": 2}, "/?page=2"),
        ({"status": "open"}, "/?status=open"),
        ({"status": "open", "page": 2}, "/?status=open&page=2"),
        ({"category": "bug", "priority": "P2", "status": "open", "page": 2},
         "/?category=bug&priority=P2&status=open&page=2"),
        ({"assignee": "Ana María", "unassigned": "true", "overdue": "true"},
         "/?assignee=Ana+Mar%C3%ADa&unassigned=true&overdue=true"),
    ],
)
def test_filtering_or_paging_tells_the_browser_the_new_url(client, params, expected):
    fill(45)

    response = client.get("/ui/tickets", params=params)

    assert response.headers["HX-Push-Url"] == expected


def test_the_pushed_url_uses_the_corrected_page(client):
    fill(45)

    response = client.get("/ui/tickets", params={"page": 99})

    assert response.headers["HX-Push-Url"] == "/?page=3"


def test_index_applies_filters_and_page_from_the_url(client):
    fill(45)
    client.patch("/tickets/1", json={"status": "closed"})

    closed = client.get("/", params={"status": "closed"}).text
    second = client.get("/", params={"page": 2}).text

    assert numbers(closed) == [1]
    assert numbers(second) == list(range(25, 5, -1))  # sin filtro hay 45: la página 2 es 25…6


def test_index_marks_the_filter_controls_from_the_url(client):
    fill(3)

    page = client.get(
        "/",
        params={"category": "bug", "priority": "P1", "status": "closed", "assignee": "Ana",
                "unassigned": "true", "overdue": "true"},
    ).text

    assert re.search(r'<option value="bug"\s+selected', page)
    assert re.search(r'<option value="P1"\s+selected', page)
    assert re.search(r'<option value="closed"\s+selected', page)
    assert 'name="assignee"' in page and 'value="Ana"' in page
    assert re.search(r'name="unassigned" value="true"[^>]*\bchecked', page)
    assert re.search(r'name="overdue" value="true"[^>]*\bchecked', page)


def test_index_without_filters_marks_nothing(client):
    fill(3)

    page = client.get("/").text

    assert not re.search(r'<option value="[^"]*"\s+selected[^>]*>(Todas|Todos)', page)
    assert " checked" not in page


def test_index_ignores_an_invalid_page_in_the_url(client):
    fill(25)

    for raw in ("abc", "0", "99"):
        response = client.get("/", params={"page": raw})
        assert response.status_code == 200
        assert numbers(response.text)  # siempre pinta una página con tickets


def test_clear_filters_link_goes_back_to_the_plain_board(client):
    fill(30)

    page = client.get("/", params={"status": "open", "page": 2}).text

    assert '<a href="/" class="btn-ghost ml-auto">Limpiar filtros</a>' in page


# --- La página actual viaja en las acciones de la fila ------------------------------------------


def test_index_has_one_page_state_input_outside_the_swapped_table(client):
    fill(30)

    page = client.get("/", params={"page": 2}).text

    assert page.count('id="page-state"') == 1
    assert page.index('id="page-state"') < page.index('id="tickets-table"')
    assert re.search(r'id="page-state"[^>]*value="2"|value="2"[^>]*id="page-state"', page)


def test_partial_refreshes_the_page_state_out_of_band(client):
    fill(30)

    partial = client.get("/ui/tickets", params={"page": 2}).text

    assert partial.count('id="page-state"') == 1
    # El atributo oob debe estar en ESTE elemento (no basta con que lo tenga otro, como la lista).
    state = re.search(r'<input[^>]*id="page-state"[^>]*>', partial).group(0)
    assert 'hx-swap-oob="true"' in state and 'value="2"' in state


def test_row_controls_include_the_page_state(client):
    fill(3)

    page = client.get("/ui/tickets").text

    # Tres tickets × tres controles de fila (prioridad, estado y responsables).
    assert page.count('hx-include="#filters, #page-state"') >= 3 * 3


def test_editing_a_row_keeps_the_current_page(client):
    fill(45)

    response = client.post("/ui/tickets/10", data={"set_status": "in_progress", "page": "2"})

    assert numbers(response.text) == list(range(25, 5, -1))  # sigue en la página 2
    assert response.headers["HX-Replace-Url"] == "/?page=2"


def test_editing_a_row_moves_to_the_last_page_if_the_current_one_disappears(client):
    fill(21)
    client.patch("/tickets/1", json={"status": "closed"})  # ahora 20 abiertos → solo 1 página

    response = client.post(
        "/ui/tickets/2", data={"set_status": "in_progress", "status": "open", "page": "2"}
    )

    assert response.status_code == 200
    # Quedan 19 abiertos (el 2 salió del filtro): una sola página, y la URL se corrige.
    assert len(numbers(response.text)) == 19
    assert response.headers["HX-Replace-Url"] == "/?status=open"


def test_creating_a_ticket_goes_back_to_the_first_page(client):
    fill(45)

    response = client.post(
        "/ui/tickets",
        data={"title": "Recién creado", "description": "Algo falla", "page": "3"},
    )

    assert "Recién creado" in titles(response.text)[0]
    assert response.headers["HX-Replace-Url"] == "/"
    assert response.headers["HX-Trigger"] == "ticket-created"
