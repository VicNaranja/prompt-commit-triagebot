# CLAUDE.md

> **Equipo B (Spec-Driven):** este archivo es vuestro. Completadlo con vuestras
> convenciones como parte del trabajo de hoy — es tan importante como el `SPEC.md`
> que vais a escribir. Claude Code lo lee automáticamente al abrir el repo.
>
> **Equipo A (Vibe):** podéis ignorarlo. No estáis obligados a tocarlo.

Este repo es una plantilla docente para construir **TriageBot**, una aplicación
FastAPI que clasifica tickets de soporte con un LLM (gpt-oss-120b vía OpenRouter).

## Stack (innegociable)

- Python 3.11+
- FastAPI
- SQLite
- HTMX + Jinja2
- Tailwind (por CDN)
- SDK de OpenAI (apuntado a OpenRouter)
- pytest
- ruff

## Reglas del taller (para todos los equipos)

Estas reglas no son metodología: son condiciones del bootcamp. Se cumplen seas
del equipo que seas.

1. No modifiques `tests/test_acceptance.py` salvo que el profesor lo indique
   expresamente.
2. Nunca hardcodees una API key en el código.
3. Lee la API key desde la variable de entorno `OPENROUTER_API_KEY`.
4. `.env` nunca se commitea. Comprueba que está en `.gitignore` antes de tu
   primer commit.

## Pull requests: siempre contra nuestro fork

Este repo es un fork de la plantilla del profesor (`unai-zulaika/prompt-commit-triagebot`).
Todas las PRs van contra el `main` de `VicNaranja/prompt-commit-triagebot`. **Nunca** se abre
una PR, se hace push ni se lanza nada contra el repo del profesor: le notifica y lanza su CI.

- Crea las PRs siempre con el repo de destino explícito:
  `gh pr create -R VicNaranja/prompt-commit-triagebot --base main`.
- Antes de hacer push a una rama, comprueba que no tiene una PR abierta contra el repo del
  profesor (`gh pr list -R unai-zulaika/prompt-commit-triagebot --head <rama>`): cada push la
  actualiza y vuelve a lanzar su CI. Si la tiene, para y avisa.

## Comandos útiles

```bash
pytest -v
pytest --cov=app
ruff check .
uvicorn app.main:app --reload
```
