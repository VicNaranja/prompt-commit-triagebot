# ruff: noqa: E501  (JavaScript embebido en cadenas: las líneas largas son intencionadas)
"""Comprueba el diseño en un navegador real (Edge/Chrome vía Playwright), en claro y oscuro.

Herramienta de DESARROLLO: no forma parte de la app ni de pytest, y Playwright no está en
requirements.txt. Uso (desde la raíz del repo, con el venv activado):

    pip install playwright
    python design/check_design.py [ruta.html | URL] [carpeta_de_capturas]
    python design/check_design.py --app [carpeta_de_capturas]   # la app real, con HTMX

Con --app arranca design/demo_server.py (BD temporal y clasificador falso, no toca tu BD ni el
LLM) y, ademas de lo anterior, recorre las interacciones: filtrar, crear (valido e invalido),
editar estado y responsables, escapado de HTML y persistencia, en claro y oscuro, repitiendo las
comprobaciones de contraste y desborde TRAS cada cambio (el HTML que inserta HTMX tambien cuenta).

Comprueba, para cada tema (claro/oscuro) y cada ancho (1280, 768 y 375 px):
  - errores de consola, de página y recursos que no cargan;
  - desbordes horizontales de la página;
  - contraste real (WCAG AA) de todo el texto visible, con los colores calculados por el navegador;
  - contraste de los bordes de los campos (componentes de interfaz, mínimo 3:1);
  - foco visible con teclado;
y además, el interruptor de tema (cambia, se recuerda al recargar y «Auto» sigue al sistema).
"""

import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

APP_MODE = "--app" in sys.argv
ARGS = [arg for arg in sys.argv[1:] if arg != "--app"]
DEMO_PORT = 8765
TARGET = ARGS[0] if ARGS and not APP_MODE else str(Path(__file__).with_name("prototipo.html"))
OUT_ARG = ARGS[0] if ARGS and APP_MODE else (ARGS[1] if len(ARGS) > 1 else None)
OUT = Path(OUT_ARG) if OUT_ARG else Path(__file__).with_name("capturas")
URL = f"http://127.0.0.1:{DEMO_PORT}/" if APP_MODE else (
    TARGET if TARGET.startswith("http") else Path(TARGET).resolve().as_uri()
)

THEMES = ("light", "dark")
VIEWPORTS = {"1280": (1280, 900), "768": (768, 1024), "375": (375, 812)}

# ---- Código que se ejecuta dentro de la página --------------------------------------------------

JS_CONTRAST = """
() => {
  const parse = (value) => {
    const m = value.match(/rgba?\\(([^)]+)\\)/);
    if (!m) return null;
    const p = m[1].split(/[ ,\\/]+/).filter(Boolean).map(Number);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const over = (top, base) => ({
    r: top.r * top.a + base.r * (1 - top.a),
    g: top.g * top.a + base.g * (1 - top.a),
    b: top.b * top.a + base.b * (1 - top.a),
    a: 1,
  });
  const backdrop = (el) => {
    const chain = [];
    for (let node = el; node; node = node.parentElement) chain.push(node);
    let base = { r: 255, g: 255, b: 255, a: 1 };           // lienzo por defecto
    for (const node of chain.reverse()) {
      const bg = parse(getComputedStyle(node).backgroundColor);
      if (bg && bg.a > 0) base = over(bg, base);
    }
    return base;
  };
  const channel = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
  const lum = (c) => 0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b);
  const ratio = (a, b) => { const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x); return (hi + 0.05) / (lo + 0.05); };
  const visible = (el) => {
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    if (s.visibility !== "visible" || s.display === "none" || r.width < 2 || r.height < 2) return false;
    for (let n = el; n; n = n.parentElement) if (parseFloat(getComputedStyle(n).opacity) < 1) return false;
    return true;
  };
  const failures = [];
  let checked = 0;
  const test = (el, label, color, minimum) => {
    const bg = backdrop(el);
    const fg = over(color, bg);
    const value = ratio(fg, bg);
    checked++;
    if (value < minimum) failures.push({ el: el.tagName.toLowerCase() + "." + (el.className || "").toString().slice(0, 40),
      text: label.slice(0, 40), ratio: Math.round(value * 100) / 100, needed: minimum });
  };
  for (const el of document.querySelectorAll("body *")) {
    if (["SCRIPT", "STYLE", "SVG", "PATH", "OPTION"].includes(el.tagName.toUpperCase())) continue;
    if (el.closest("svg") || el.disabled || el.getAttribute("aria-disabled") === "true") continue;
    if (!visible(el)) continue;
    const style = getComputedStyle(el);
    const size = parseFloat(style.fontSize), bold = parseInt(style.fontWeight, 10) >= 700;
    const minimum = size >= 24 || (size >= 18.66 && bold) ? 3 : 4.5;
    const color = parse(style.color);
    const ownText = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    const isField = ["INPUT", "SELECT", "TEXTAREA"].includes(el.tagName) && el.type !== "checkbox" && el.type !== "radio";
    if (ownText && color) test(el, el.textContent.trim(), color, minimum);
    if (isField && color && (el.value || el.tagName === "SELECT")) test(el, el.value || el.tagName, color, minimum);
    if (isField && el.placeholder && !el.value) {
      const ph = parse(getComputedStyle(el, "::placeholder").color);
      if (ph) test(el, "placeholder: " + el.placeholder, ph, minimum);
    }
  }
  return { checked, failures };
}
"""

JS_FIELD_BORDERS = """
() => {
  const parse = (v) => { const m = v.match(/rgba?\\(([^)]+)\\)/); const p = m[1].split(/[ ,\\/]+/).filter(Boolean).map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const channel = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
  const lum = (c) => 0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b);
  const ratio = (a, b) => { const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x); return (hi + 0.05) / (lo + 0.05); };
  const out = [];
  for (const el of document.querySelectorAll("input[type=text], select, textarea")) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || getComputedStyle(el).display === "none") continue;
    const border = parse(getComputedStyle(el).borderTopColor);
    let parent = el.parentElement, bg = null;
    while (parent && !bg) { const c = parse(getComputedStyle(parent).backgroundColor); if (c.a === 1) bg = c; parent = parent.parentElement; }
    bg = bg || { r: 255, g: 255, b: 255, a: 1 };
    const value = ratio(border, bg);
    if (value < 3) out.push({ el: el.tagName.toLowerCase() + "." + el.className.toString().slice(0, 30), ratio: Math.round(value * 100) / 100 });
  }
  return out;
}
"""

JS_OVERFLOW = """
() => {
  const doc = document.documentElement;
  const wide = [...document.querySelectorAll("body *")].filter((el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.right > doc.clientWidth + 1 && getComputedStyle(el).position !== "fixed";
  }).slice(0, 5).map((el) => el.tagName.toLowerCase() + "." + el.className.toString().slice(0, 30));
  return { scrollWidth: doc.scrollWidth, clientWidth: doc.clientWidth, wide };
}
"""

JS_FOCUS = """
() => {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const parse = (v) => { const m = v.match(/rgba?\\(([^)]+)\\)/); if (!m) return null; const p = m[1].split(/[ ,\\/]+/).filter(Boolean).map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const over = (t, b) => ({ r: t.r * t.a + b.r * (1 - t.a), g: t.g * t.a + b.g * (1 - t.a), b: t.b * t.a + b.b * (1 - t.a), a: 1 });
  const channel = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
  const lum = (c) => 0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b);
  const ratio = (a, b) => { const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x); return (hi + 0.05) / (lo + 0.05); };
  const chain = []; for (let n = el.parentElement; n; n = n.parentElement) chain.push(n);
  let bg = { r: 255, g: 255, b: 255, a: 1 };
  for (const n of chain.reverse()) { const c = parse(getComputedStyle(n).backgroundColor); if (c && c.a > 0) bg = over(c, bg); }
  const s = getComputedStyle(el);
  const color = parse(s.outlineColor);
  // Un contorno transparente (p. ej. «outline-none» de Tailwind) cuenta como foco invisible.
  const contrast = color && color.a > 0 ? ratio(over(color, bg), bg) : 0;
  return { el: el.tagName.toLowerCase() + "." + el.className.toString().slice(0, 30),
           style: s.outlineStyle, width: parseFloat(s.outlineWidth), contrast: Math.round(contrast * 100) / 100 };
}
"""


def audit_dom(page, label: str, step: str, fail) -> int:
    """Contraste, bordes de campos y desbordes del DOM ACTUAL (tras un cambio de HTMX incluido)."""
    where = f"[{label} · {step}]"
    page.wait_for_timeout(200)  # Tailwind genera las clases del HTML nuevo en el siguiente ciclo
    result = page.evaluate(JS_CONTRAST)
    for item in result["failures"]:
        fail(f"{where} contraste {item['ratio']}:1 < {item['needed']}:1 en {item['el']} «{item['text']}»")
    for item in page.evaluate(JS_FIELD_BORDERS):
        fail(f"{where} borde de campo {item['ratio']}:1 < 3:1 en {item['el']}")
    overflow = page.evaluate(JS_OVERFLOW)
    if overflow["scrollWidth"] > overflow["clientWidth"] + 1:
        fail(f"{where} scroll horizontal: {overflow['scrollWidth']} > {overflow['clientWidth']}")
    return result["checked"]


ROWS = "table.tickets tbody tr"
ASSIGNEES = ROWS + " input[name=set_assignees]"
STATUSES = ROWS + " select[name=set_status]"


def interactions(browser, base_url: str, fail, report: list[str]) -> None:
    for theme in THEMES:
        for name in ("1280", "375"):
            width, height = VIEWPORTS[name]
            label = f"app {theme:5} {name:>4}px"
            context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=theme)
            context.set_default_timeout(10000)  # un selector que ya no existe debe fallar rápido
            page = context.new_page()
            problems: list[str] = []
            page.on("dialog", lambda d, p=problems: (p.append(f"diálogo: {d.message}"), d.dismiss()))
            page.on("pageerror", lambda e, p=problems: p.append(f"error de página: {str(e)[:100]}"))
            page.on("console", lambda m, p=problems: p.append(f"consola: {m.text[:100]}") if m.type == "error" else None)
            page.on("response", lambda r, p=problems: p.append(f"HTTP {r.status} {r.url[-60:]}") if r.status >= 400 else None)
            page.goto(base_url, wait_until="networkidle")
            page.wait_for_function("getComputedStyle(document.body).backgroundColor !== 'rgba(0, 0, 0, 0)'")
            page.evaluate("window.__marker = 'sin-recarga'")
            measured = 0

            def settle(page=page):
                page.wait_for_load_state("networkidle")
                page.wait_for_function("!document.querySelector('.htmx-request, .htmx-swapping, .htmx-settling')")

            def count(page=page):
                return page.locator(ROWS).count()

            def summary(page=page):
                return " ".join(page.locator("#tickets-table p.mb-3").first.inner_text().split())

            def grand_total(page=page):
                match = re.search(r"de (\d+) tickets|Mostrando (\d+) tickets?", summary())
                return int(match.group(1) or match.group(2)) if match else -1

            def values_of(selector, page=page):
                return page.locator(selector).evaluate_all("els => els.map(e => e.value)")

            def check_no_reload(step, page=page, label=label):
                if page.evaluate("window.__marker") != "sin-recarga":
                    fail(f"[{label} · {step}] la página se recargó entera (HTMX debía cambiar solo la tabla)")
                # Tampoco puede perderse el resto de la página: HTMX solo debe repintar la tabla.
                for selector in ("h1", "#create-form", "#filters", "#tickets-table"):
                    if page.locator(selector).count() != 1:
                        fail(f"[{label} · {step}] ha desaparecido «{selector}» (¿se sustituyó más que la tabla?)")

            total = count()
            if total != 20:
                fail(f"[{label}] la primera página debía tener 20 filas y tiene {total}")
            captions = page.locator(ROWS + " td[data-label=Estado] p").all_inner_texts()
            bad = [c for c in captions if not re.fullmatch(r"desde \d\d/\d\d \d\d:\d\d · hace .+", c.strip())]
            if len(captions) != total or bad:
                fail(f"[{label} · inicio] «desde cuándo» ausente o mal formado: {bad[:2]} ({len(captions)}/{total})")
            measured += audit_dom(page, label, "inicio", fail)

            # Paginación: resumen, pager, navegación sin recargar, URL, refresco y volver atrás
            n = grand_total()  # la demo se comparte entre contextos: el total va creciendo
            if not 21 <= n <= 40 or summary() != f"Mostrando 1–20 de {n} tickets":
                fail(f"[{label} · paginación] resumen inicial: «{summary()}» (total {n})")
            if page.locator("nav[aria-label=Paginación] [aria-current=page]").inner_text() != "1":
                fail(f"[{label} · paginación] la página actual no está marcada")
            measured += audit_dom(page, label, "paginador", fail)
            page.click("nav[aria-label=Paginación] a[aria-label='Página siguiente']")
            settle()
            check_no_reload("página siguiente")
            if summary() != f"Mostrando 21–{n} de {n} tickets" or count() != n - 20:
                fail(f"[{label} · página 2] «{summary()}», {count()} filas (total {n})")
            if "page=2" not in page.url:
                fail(f"[{label} · página 2] la URL no refleja la página: {page.url}")
            if "aria-disabled" not in page.locator("nav[aria-label=Paginación]").inner_html():
                fail(f"[{label} · página 2] «Siguiente» debía estar desactivado en la última")
            measured += audit_dom(page, label, "página 2", fail)
            page.reload(wait_until="networkidle")
            if summary() != f"Mostrando 21–{n} de {n} tickets":
                fail(f"[{label} · refrescar] se perdió la página: «{summary()}»")
            page.evaluate("window.__marker = 'sin-recarga'")
            # Editar una fila no saca al usuario de su página
            page.locator(STATUSES).first.select_option("in_progress")
            settle()
            check_no_reload("editar en la página 2")
            if summary() != f"Mostrando 21–{n} de {n} tickets":
                fail(f"[{label} · editar en la página 2] cambió de página: «{summary()}»")
            # Filtrar vuelve a la página 1, queda en la URL y sobrevive al refresco
            page.select_option("#filters select[name=status]", "closed")
            settle()
            check_no_reload("filtrar desde la página 2")
            if "page=" in page.url or "status=closed" not in page.url:
                fail(f"[{label} · filtrar] URL inesperada: {page.url}")
            if not re.fullmatch(r"Mostrando (1–\d+ de \d+|\d+) tickets?", summary()):
                fail(f"[{label} · filtrar] no volvió a la página 1: «{summary()}»")
            page.reload(wait_until="networkidle")
            if page.input_value("#filters select[name=status]") != "closed":
                fail(f"[{label} · refrescar] el filtro de estado no sobrevive al refresco")
            if not re.fullmatch(r"Mostrando (1–\d+ de \d+|\d+) tickets?", summary()):
                fail(f"[{label} · refrescar] «{summary()}»")
            measured += audit_dom(page, label, "filtro recordado", fail)
            # «Limpiar filtros» vuelve al tablero sin filtros
            page.click("text=Limpiar filtros")
            page.wait_for_load_state("networkidle")
            if "?" in page.url or page.input_value("#filters select[name=status]") != "":
                fail(f"[{label} · limpiar] queda estado en la URL o el filtro: {page.url}")
            if summary() != f"Mostrando 1–20 de {n} tickets":
                fail(f"[{label} · limpiar] «{summary()}»")
            page.evaluate("window.__marker = 'sin-recarga'")

            # Filtro por estado
            page.select_option("#filters select[name=status]", "in_progress")
            settle()
            check_no_reload("filtro por estado")
            states = values_of(STATUSES)
            if not states or set(states) != {"in_progress"}:
                fail(f"[{label} · filtro por estado] filas inesperadas: {states}")
            measured += audit_dom(page, label, "filtro por estado", fail)
            page.select_option("#filters select[name=status]", "")
            settle()

            # Solo vencidos
            page.check("#filters input[name=overdue]")
            settle()
            overdue_rows = count()
            badges = page.locator(ROWS + " .badge-danger:text-is('Vencido')").count()
            if overdue_rows == 0 or badges != overdue_rows:
                fail(f"[{label} · solo vencidos] {overdue_rows} filas y {badges} marcas «Vencido»")
            check_no_reload("solo vencidos")
            measured += audit_dom(page, label, "solo vencidos", fail)
            page.uncheck("#filters input[name=overdue]")
            settle()

            # Sin asignar
            page.check("#filters input[name=unassigned]")
            settle()
            filled = [v for v in values_of(ASSIGNEES) if v.strip()]
            if count() == 0 or filled:
                fail(f"[{label} · sin asignar] {count()} filas, con responsables: {filled}")
            page.uncheck("#filters input[name=unassigned]")
            settle()

            # Responsable (texto + autocompletado)
            options = page.locator("#assignee-options option").count()
            if options < 4:
                fail(f"[{label} · autocompletado] solo {options} nombres en la lista")
            page.fill("#filters input[name=assignee]", "ana")
            page.press("#filters input[name=assignee]", "Tab")
            settle()
            names = [v.lower() for v in values_of(ASSIGNEES)]
            if not names or not all("ana" in v for v in names):
                fail(f"[{label} · filtro responsable] filas inesperadas: {names}")
            measured += audit_dom(page, label, "filtro responsable", fail)
            page.fill("#filters input[name=assignee]", "")
            page.press("#filters input[name=assignee]", "Tab")
            settle()

            # Alta invalida: error visible y el formulario conserva lo escrito
            page.fill("#create-form input[name=title]", "!!!")
            page.fill("#create-form textarea[name=description]", "Texto de prueba")
            page.click("#create-form button[type=submit]")
            settle()
            if not page.locator("#tickets-table [role=alert]").is_visible():
                fail(f"[{label} · alta inválida] no se muestra el aviso de error")
            if page.input_value("#create-form input[name=title]") != "!!!":
                fail(f"[{label} · alta inválida] el formulario se vació pese al error")
            check_no_reload("alta inválida")
            measured += audit_dom(page, label, "alta inválida", fail)

            # Alta valida: aparece arriba, el formulario se vacia
            before = grand_total()
            page.fill("#create-form input[name=title]", "Ticket creado en la prueba del navegador")
            page.fill("#create-form textarea[name=description]", "Descripción de la prueba")
            page.fill("#create-form input[name=assignees]", "Ana, Luis")
            page.click("#create-form button[type=submit]")
            settle()
            first_title = page.locator(ROWS + " td[data-label=Ticket] .font-medium").first.inner_text()
            if grand_total() != before + 1 or first_title != "Ticket creado en la prueba del navegador":
                fail(f"[{label} · alta válida] total {before}→{grand_total()}, primera: «{first_title}»")
            if page.input_value("#create-form input[name=title]") != "":
                fail(f"[{label} · alta válida] el formulario no se vació tras crear")
            if page.locator(ASSIGNEES).first.input_value() != "Ana, Luis":
                fail(f"[{label} · alta válida] los responsables no se guardaron")
            check_no_reload("alta válida")
            measured += audit_dom(page, label, "alta válida", fail)

            # Doble clic con un «LLM» lento (0,4 s): el botón se desactiva y solo se crea uno
            before = grand_total()
            page.fill("#create-form input[name=title]", "Ticket lento con doble clic")
            page.fill("#create-form textarea[name=description]", "Descripción de la prueba")
            page.dblclick("#create-form button[type=submit]")
            page.wait_for_timeout(150)
            if not page.locator("#create-form button[type=submit]").is_disabled():
                fail(f"[{label} · doble clic] el botón no se desactiva mientras se envía")
            page.wait_for_timeout(900)
            settle()
            if page.locator("#create-form button[type=submit]").is_disabled():
                fail(f"[{label} · doble clic] el botón se quedó desactivado tras terminar")
            if grand_total() != before + 1:
                fail(f"[{label} · doble clic] el total pasó de {before} a {grand_total()} (debía ser +1)")

            # Editar estado desde la fila
            page.locator(STATUSES).first.select_option("resolved")
            settle()
            check_no_reload("editar estado")
            status = page.locator(STATUSES).first.input_value()
            if status != "resolved":
                fail(f"[{label} · editar estado] el estado quedó en «{status}»")
            # «Desde cuándo»: al cambiar el estado desde la fila, pasa a «hace instantes / N min»
            caption = page.locator(ROWS + " td[data-label=Estado] p").first.inner_text()
            if not re.search(r"desde \d\d/\d\d \d\d:\d\d · hace (instantes|\d+ min)", caption):
                fail(f"[{label} · editar estado] «desde cuándo» no se actualizó: «{caption}»")
            measured += audit_dom(page, label, "editar estado", fail)

            # Editar responsables y comprobar que persiste tras recargar
            first_input = page.locator(ASSIGNEES).first
            first_input.fill("Eva")
            first_input.press("Tab")
            settle()
            page.reload(wait_until="networkidle")
            persisted = page.locator(ASSIGNEES).first.input_value()
            persisted_status = page.locator(STATUSES).first.input_value()
            if persisted != "Eva" or persisted_status != "resolved":
                fail(f"[{label} · persistencia] tras recargar: «{persisted}» / «{persisted_status}»")
            measured += audit_dom(page, label, "tras recargar", fail)

            # Escapado de HTML (ticket de demo con <script> en el titulo)
            escaped = page.locator(ROWS + " td[data-label=Ticket]:has-text('<script>alert(1)</script>')")
            if escaped.count() != 1:
                fail(f"[{label} · escapado] el título con <script> no se ve como texto")

            # Tema forzado + HTMX: el HTML nuevo hereda el tema (clases generadas al insertarlo)
            forced = "dark" if theme == "light" else "light"
            page.click(f"[data-mode={forced}]")
            page.select_option("#filters select[name=category]", "bug")
            settle()
            is_dark = page.evaluate("document.documentElement.classList.contains('dark')")
            if is_dark != (forced == "dark"):
                fail(f"[{label} · tema forzado] el tema {forced} no se aplicó")
            if count() == 0:
                fail(f"[{label} · tema forzado] la tabla quedó vacía")
            measured += audit_dom(page, label, f"tema {forced} forzado + filtro", fail)

            page.screenshot(path=str(OUT / f"app-{theme}-{name}-final.png"), full_page=True)
            for entry in problems:
                fail(f"[{label}] {entry}")
            report.append(f"{label}: interacciones recorridas, {measured} textos medidos tras los cambios")
            context.close()


def run() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    report: list[str] = []

    def fail(message: str) -> None:
        problems.append(message)

    with sync_playwright() as p:
        browser = None
        for channel in ("msedge", "chrome"):
            try:
                browser = p.chromium.launch(channel=channel, headless=True)
                report.append(f"Navegador: {channel} {browser.version}")
                break
            except Exception as error:  # noqa: BLE001 - probamos el siguiente navegador
                report.append(f"({channel} no disponible: {str(error)[:60]})")
        if browser is None:
            print("No hay Edge ni Chrome disponibles.")
            return 2

        # ---- Matriz tema × ancho ----------------------------------------------------------
        for theme in THEMES:
            for name, (width, height) in VIEWPORTS.items():
                label = f"{theme:5} {name:>4}px"
                context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=theme)
                page = context.new_page()
                logs: list[str] = []
                # Registra si el tema oscuro se aplicó antes de que exista <body> (sin parpadeo).
                page.add_init_script(
                    """
                    window.__darkBeforeBody = null;
                    new MutationObserver(() => {
                      const root = document.documentElement;
                      if (window.__darkBeforeBody === null && root && root.classList.contains('dark'))
                        window.__darkBeforeBody = !document.body;
                    }).observe(document, { attributes: true, attributeFilter: ['class'], childList: true, subtree: true });
                    """
                )
                page.on("console", lambda m, logs=logs: logs.append(f"consola {m.type}: {m.text[:100]}") if m.type in ("error", "warning") else None)
                page.on("pageerror", lambda e, logs=logs: logs.append(f"error de página: {str(e)[:100]}"))
                page.on("requestfailed", lambda r, logs=logs: logs.append(f"recurso fallido: {r.url[:80]}"))
                page.goto(URL, wait_until="networkidle")
                page.wait_for_function("getComputedStyle(document.body).backgroundColor !== 'rgba(0, 0, 0, 0)'")

                is_dark = page.evaluate("document.documentElement.classList.contains('dark')")
                if is_dark != (theme == "dark"):
                    fail(f"[{label}] «Auto» no sigue al sistema (clase dark={is_dark})")

                if theme == "dark" and page.evaluate("window.__darkBeforeBody") is not True:
                    fail(f"[{label}] parpadeo: el tema oscuro se aplica después de pintar el <body>")

                overflow = page.evaluate(JS_OVERFLOW)
                if overflow["scrollWidth"] > overflow["clientWidth"] + 1:
                    fail(f"[{label}] scroll horizontal: {overflow['scrollWidth']} > {overflow['clientWidth']} ({', '.join(overflow['wide'])})")

                contrast = page.evaluate(JS_CONTRAST)
                for item in contrast["failures"]:
                    fail(f"[{label}] contraste {item['ratio']}:1 < {item['needed']}:1 en {item['el']} «{item['text']}»")

                for item in page.evaluate(JS_FIELD_BORDERS):
                    fail(f"[{label}] borde de campo {item['ratio']}:1 < 3:1 en {item['el']}")

                # Capturas ANTES de recorrer con Tab (si no, saldría un anillo de foco en medio).
                shot = OUT / f"{theme}-{name}.png"
                page.screenshot(path=str(shot), full_page=True)
                for index in (0, 1):
                    page.locator("table.tickets tbody tr").nth(index).screenshot(path=str(OUT / f"{theme}-{name}-fila{index + 1}.png"))

                # Foco visible con teclado
                page.mouse.click(1, 1)
                focused = 0
                for _ in range(60):
                    page.keyboard.press("Tab")
                    info = page.evaluate(JS_FOCUS)
                    if info is None:
                        continue
                    focused += 1
                    if info["style"] == "none" or info["width"] < 2 or info["contrast"] < 3:
                        fail(f"[{label}] foco no visible en {info['el']} (contraste {info['contrast']}:1)")

                for entry in logs:
                    # Aviso esperado: el stack del proyecto exige Tailwind por CDN.
                    if "cdn.tailwindcss.com should not be used in production" in entry:
                        continue
                    fail(f"[{label}] {entry}")

                report.append(f"{label}: {contrast['checked']} textos medidos, {focused} elementos con foco, captura {shot.name}")
                context.close()

        # ---- Interruptor de tema ----------------------------------------------------------
        context = browser.new_context(viewport={"width": 1280, "height": 900}, color_scheme="light")
        page = context.new_page()
        page.goto(URL, wait_until="networkidle")
        page.wait_for_function("getComputedStyle(document.body).backgroundColor !== 'rgba(0, 0, 0, 0)'")
        light_bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
        page.click("[data-mode=dark]")
        dark_bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
        if not page.evaluate("document.documentElement.classList.contains('dark')") or dark_bg == light_bg:
            fail("[interruptor] «Oscuro» no cambia el tema en un sistema claro")
        if page.get_attribute("[data-mode=dark]", "aria-pressed") != "true":
            fail("[interruptor] «Oscuro» no marca aria-pressed=true")
        page.reload(wait_until="networkidle")
        if not page.evaluate("document.documentElement.classList.contains('dark')"):
            fail("[interruptor] la elección «Oscuro» no se recuerda al recargar")
        page.click("[data-mode=auto]")
        if page.evaluate("document.documentElement.classList.contains('dark')"):
            fail("[interruptor] «Auto» en sistema claro debería ser claro")
        context.close()

        context = browser.new_context(viewport={"width": 1280, "height": 900}, color_scheme="dark")
        page = context.new_page()
        page.goto(URL, wait_until="networkidle")
        page.click("[data-mode=light]")
        if page.evaluate("document.documentElement.classList.contains('dark')"):
            fail("[interruptor] «Claro» no fuerza claro en un sistema oscuro")
        page.emulate_media(color_scheme="light")
        page.click("[data-mode=auto]")
        if page.evaluate("document.documentElement.classList.contains('dark')"):
            fail("[interruptor] «Auto» no sigue al sistema tras cambiarlo a claro")
        context.close()
        if APP_MODE:
            interactions(browser, URL, fail, report)
        browser.close()

    print("\n".join(report))
    if problems:
        print(f"\n{len(problems)} PROBLEMA(S):")
        print("\n".join(" - " + p for p in problems))
        return 1
    print("\nTodo correcto: sin desbordes, contraste AA, foco visible y tema funcionando.")
    return 0


def wait_for(url: str, seconds: int = 30) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except OSError:
            time.sleep(0.5)
    return False


if __name__ == "__main__":
    if not APP_MODE:
        sys.exit(run())
    server = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("demo_server.py")), str(DEMO_PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_for(f"http://127.0.0.1:{DEMO_PORT}/health"):
            print(f"El servidor de demo no arrancó en el puerto {DEMO_PORT} (¿puerto ocupado?).")
            sys.exit(2)
        try:
            code = run()
        except Exception as error:  # noqa: BLE001 - se informa y se sale con error
            detail = str(error).strip().splitlines()
            print("1 PROBLEMA(S):")
            print(f" - el verificador se interrumpió ({type(error).__name__}): {' | '.join(detail[:3])[:300]}")
            code = 1
        sys.exit(code)
    finally:
        server.terminate()  # solo el proceso que lanzó este script
