# TriageBot Template

Repo plantilla para el bootcamp **Prompt & Commit: Desarrollo de aplicaciones con IA Generativa**.

Durante el bootcamp construiréis una aplicación web interna para clasificar tickets de soporte usando IA generativa.

## Qué vais a construir

TriageBot permite:

- Crear tickets con `title` y `description`.
- Clasificarlos automáticamente con un LLM (gpt-oss-120b vía OpenRouter) en:
  - `category`: `bug`, `feature_request`, `question`, `urgent`
  - `priority`: `P1`, `P2`, `P3`
  - `tags`: lista de etiquetas cortas.
- Persistirlos en SQLite.
- Verlos en un tablero web con filtros.
- Ejecutar tests automáticos y CI en GitHub Actions.

## Stack obligatorio

| Capa | Herramienta |
|---|---|
| Lenguaje | Python 3.11+ |
| Backend | FastAPI |
| Datos | SQLite |
| Frontend | HTML + HTMX + Tailwind CDN |
| LLM | gpt-oss-120b vía OpenRouter (SDK de `openai`) |
| Tests | pytest |
| CI/CD | GitHub Actions |
| IDE + IA | VS Code + Claude Code |

## Setup local

```bash
git clone https://github.com/<tu-usuario>/triagebot-template.git
cd triagebot-template
python3 -m venv .venv
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate          # Windows
pip install -r requirements.txt
cp .env.example .env
```

Edita `.env` y añade tu API key:

```bash
OPENROUTER_API_KEY=sk-or-...
```

Comprueba que `.env` está ignorado por Git:

```bash
git status
```

`.env` **no debe aparecer**.

## Ejecutar tests

```bash
pytest -v
```

Al clonar el repo plantilla, los tests de aceptación deben fallar. Eso es lo esperado: todavía no habéis implementado TriageBot.

## Ejecutar la app

```bash
uvicorn app.main:app --reload
```

Abre:

```text
http://127.0.0.1:8000
```

## Contrato mínimo del producto

Los detalles obligatorios están en:

- [`BRIEF.md`](BRIEF.md): briefing del cliente.
- [`SPEC.md`](SPEC.md): contrato funcional recomendado.
- [`CLAUDE.md`](CLAUDE.md): instrucciones del repo para Claude Code.
- [`tests/test_acceptance.py`](tests/test_acceptance.py): los 5 tests obligatorios.

## Reglas del bootcamp

1. Lo que no acaba en GitHub no existe.
2. No se commitean API keys.
3. Commit pequeño cada 20–30 minutos.
4. Leed el diff antes de aceptar cambios de la IA.
5. Si Claude Code propone una dependencia, verificad que existe antes de instalarla.
6. Los tests son la red de seguridad.

## Equipo

Nombres: Alberto Victor

Metodología: `B` / `Spec-Driven`
