"""Cálculo puro de la fecha límite (ver SPEC «Fecha límite»). Hora de Madrid, resultado en UTC."""

import pytest

from app.deadlines import compute_due_at, format_madrid


@pytest.mark.parametrize(
    ("created_at", "priority", "expected"),
    [
        # Invierno (UTC+1): 23:59:59 en Madrid = 22:59:59 UTC.
        ("2026-01-05T10:00:00Z", "P1", "2026-01-05T22:59:59Z"),
        ("2026-01-05T10:00:00Z", "P2", "2026-01-06T22:59:59Z"),
        ("2026-01-05T10:00:00Z", "P3", "2026-01-07T22:59:59Z"),
        # Verano (UTC+2): 23:59:59 en Madrid = 21:59:59 UTC (ejemplo del SPEC).
        ("2026-06-29T09:30:00Z", "P1", "2026-06-29T21:59:59Z"),
        ("2026-06-29T09:30:00Z", "P2", "2026-06-30T21:59:59Z"),
        ("2026-06-29T09:30:00Z", "P3", "2026-07-01T21:59:59Z"),
    ],
)
def test_due_date_is_end_of_day_in_madrid_plus_days_by_priority(created_at, priority, expected):
    assert compute_due_at(created_at, priority) == expected


@pytest.mark.parametrize(
    ("created_at", "expected"),
    [
        # 23:30 UTC en invierno ya son las 00:30 del día siguiente en Madrid.
        ("2026-01-05T23:30:00Z", "2026-01-06T22:59:59Z"),
        # 22:30 UTC en verano ya son las 00:30 del día siguiente en Madrid.
        ("2026-06-29T22:30:00Z", "2026-06-30T21:59:59Z"),
        # Justo antes del cambio de día local sigue siendo el mismo día.
        ("2026-01-05T22:59:59Z", "2026-01-05T22:59:59Z"),
        ("2026-01-05T23:00:00Z", "2026-01-06T22:59:59Z"),
    ],
)
def test_p1_day_of_creation_is_the_madrid_day_not_the_utc_day(created_at, expected):
    assert compute_due_at(created_at, "P1") == expected


def test_due_date_crossing_the_spring_dst_change():
    # El cambio a horario de verano fue el domingo 29/03/2026 a las 02:00 → 03:00.
    created = "2026-03-28T12:00:00Z"

    assert compute_due_at(created, "P1") == "2026-03-28T22:59:59Z"  # sábado, UTC+1
    assert compute_due_at(created, "P2") == "2026-03-29T21:59:59Z"  # domingo, UTC+2
    assert compute_due_at(created, "P3") == "2026-03-30T21:59:59Z"  # lunes, UTC+2


def test_due_date_crossing_the_autumn_dst_change():
    # El cambio a horario de invierno fue el domingo 25/10/2026 a las 03:00 → 02:00.
    created = "2026-10-24T12:00:00Z"

    assert compute_due_at(created, "P1") == "2026-10-24T21:59:59Z"  # sábado, UTC+2
    assert compute_due_at(created, "P2") == "2026-10-25T22:59:59Z"  # domingo, UTC+1


def test_format_madrid_shows_local_day_month_and_time():
    assert format_madrid("2026-01-05T22:59:59Z") == "05/01 23:59"
    assert format_madrid("2026-06-29T21:59:59Z") == "29/06 23:59"
    assert format_madrid("2026-06-29T09:30:00Z") == "29/06 11:30"
