# ruff: noqa: E501  (JavaScript embebido en cadenas: las líneas largas son intencionadas)
"""Comprueba el diseño en un navegador real (Edge/Chrome vía Playwright), en claro y oscuro.

Herramienta de DESARROLLO: no forma parte de la app ni de pytest, y Playwright no está en
requirements.txt. Uso (desde la raíz del repo, con el venv activado):

    pip install playwright
    python design/check_design.py [ruta.html | URL] [carpeta_de_capturas]

Comprueba, para cada tema (claro/oscuro) y cada ancho (1280, 768 y 375 px):
  - errores de consola, de página y recursos que no cargan;
  - desbordes horizontales de la página;
  - contraste real (WCAG AA) de todo el texto visible, con los colores calculados por el navegador;
  - contraste de los bordes de los campos (componentes de interfaz, mínimo 3:1);
  - foco visible con teclado;
y además, el interruptor de tema (cambia, se recuerda al recargar y «Auto» sigue al sistema).
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

TARGET = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).with_name("prototipo.html"))
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).with_name("capturas")
URL = TARGET if TARGET.startswith("http") else Path(TARGET).resolve().as_uri()

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
        browser.close()

    print("\n".join(report))
    if problems:
        print(f"\n{len(problems)} PROBLEMA(S):")
        print("\n".join(" - " + p for p in problems))
        return 1
    print("\nTodo correcto: sin desbordes, contraste AA, foco visible y tema funcionando.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
