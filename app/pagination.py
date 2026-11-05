"""Paginación del tablero. Módulo puro, sin base de datos ni FastAPI."""

import math

PAGE_SIZE = 20
_SHOW_ALL_UP_TO = 7  # hasta 7 páginas se muestran todos los números


def parse_page(raw: str | None) -> int:
    """Número de página de la URL. Cualquier valor no válido (vacío, texto, 0, negativo) → 1."""
    try:
        page = int((raw or "").strip())
    except ValueError:
        return 1
    return page if page >= 1 else 1


def last_page(total: int) -> int:
    return max(1, math.ceil(total / PAGE_SIZE))


def clamp_page(page: int, total: int) -> int:
    """Lleva la página al rango existente: nunca menor que 1 ni mayor que la última."""
    return min(max(page, 1), last_page(total))


def page_window(current: int, last: int) -> list[int | None]:
    """Números de página a mostrar (`None` es «…»): primera, última y vecinas de la actual."""
    if last <= _SHOW_ALL_UP_TO:
        return list(range(1, last + 1))
    wanted = sorted({1, last, *(n for n in (current - 1, current, current + 1) if 1 <= n <= last)})
    window: list[int | None] = []
    previous = 0
    for number in wanted:
        if number - previous > 1:
            window.append(None)
        window.append(number)
        previous = number
    return window
