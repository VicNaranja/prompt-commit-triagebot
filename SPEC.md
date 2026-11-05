# SPEC.md — Contrato funcional de TriageBot

> **Equipo A (Vibe):** usad este SPEC tal cual. No lo modifiquéis.
>
> **Equipo B (Spec-Driven):** este SPEC está incompleto a propósito. Vuestra tarea es completarlo
> antes de pedir código: casos límite, errores, funcionalidades de cada pantalla, tests propios y
> todo lo que traslade el cliente en `BRIEF.md`.

## 1. Objetivo

Construir una aplicación web interna para registrar, clasificar y consultar tickets de soporte.

## 2. Modelo de datos

### Ticket

Campos mínimos:

| Campo | Tipo | Reglas |
|---|---|---|
| `id` | int | Autogenerado |
| `title` | str | Obligatorio, 1–200 caracteres tras trim |
| `description` | str | Obligatorio, 1–5000 caracteres tras trim |
| `category` | str | `bug`, `feature_request`, `question`, `urgent` |
| `priority` | str | `P1`, `P2`, `P3` |
| `tags` | list[str] | Lista, puede estar vacía |
| `status` | str | `open`, `in_progress`, `resolved`, `closed` |
| `assignees` | list[str] | Responsables del ticket (ver «Responsables»). Puede estar vacía |
| `due_at` | datetime | UTC, calculado en servidor según la prioridad (ver «Fecha límite») |
| `overdue` | bool | Calculado al consultar: `true` si el ticket está vencido (ver «Fecha límite») |
| `created_at` | datetime | UTC, generado en servidor |
| `updated_at` | datetime | UTC, actualizado en cambios relevantes |

Valores por defecto:

- `status`: `open`
- `assignees`: `[]`
- fallback de clasificación: `category="question"`, `priority="P3"`, `tags=[]`

### Responsables

Un ticket puede tener **varios** responsables (petición del cliente: a veces trabajan dos o tres
técnicos en el mismo ticket). No hay usuarios ni login (fuera de alcance en `BRIEF.md`): un
responsable es un nombre de texto libre.

- Cada nombre: 1–60 caracteres tras trim. Un nombre vacío o solo espacios es inválido (`422`).
- Como mucho 10 responsables por ticket.
- Sin duplicados, **sin distinguir mayúsculas**: `["Ana", "ana"]` se guarda como `["Ana"]`
  (se conserva la primera grafía y el orden de aparición).
- Se escapan al pintarlos en HTML como cualquier otro texto del usuario.

### Estados

`open` → `in_progress` → `resolved` → `closed`.

- `resolved` significa «el equipo lo ha resuelto» y es previo al cierre. Un ticket `resolved` no
  se considera vencido.
- **Reabrir** es poner de nuevo el estado `open` en un ticket `resolved` o `closed`. La API no
  restringe los saltos de estado: cualquier valor del enum es válido desde cualquier estado.
- Reabrir **no** reinicia la fecha límite: el plazo sigue contando desde `created_at`.

### Fecha límite (`due_at`) y vencidos (`overdue`)

El equipo trabaja en España peninsular, así que «hoy» y «mañana» se entienden en hora de
`Europe/Madrid` (con horario de verano), no en UTC. `due_at` se guarda y se devuelve en UTC.

| Prioridad | Fecha límite |
|---|---|
| `P1` | las 23:59:59 (hora de Madrid) del **día de creación** |
| `P2` | las 23:59:59 del día de creación **+ 1 día** |
| `P3` | las 23:59:59 del día de creación **+ 2 días** |

- Se calcula en servidor a partir de `created_at` y la prioridad. El cliente no la envía.
- Si la prioridad cambia con `PATCH`, `due_at` se **recalcula desde `created_at`** con la nueva
  prioridad (no desde el momento del cambio).
- Un ticket está **vencido** (`overdue = true`) si `due_at` ya pasó y su estado es `open` o
  `in_progress`. Los `resolved` y `closed` nunca están vencidos. No se guarda: se calcula en cada
  consulta con la hora actual del servidor.

Los valores de `category`, `priority` y `status` son vinculantes: exactamente esos, en minúsculas
(salvo `P1`–`P3`). Si el código devuelve `"URGENT"`, los tests fallan.

## 3. Arquitectura de módulos

Estructura recomendada:

```text
app/
  __init__.py
  main.py          # FastAPI app y endpoints HTTP
  models.py        # Modelos Pydantic (Ticket, TicketCreate, TicketUpdate)
  db.py            # Conexión y operaciones SQLite (sqlite3)
  deadlines.py     # Cálculo puro de la fecha límite (due_at) según prioridad, hora de Madrid
  classifier.py    # ÚNICO módulo que llama al LLM

templates/
  index.html
  _tickets_table.html

tests/
  test_acceptance.py
```

Regla importante:

> Todo lo que llame a una API de IA (OpenRouter u otra) vive solo en `app/classifier.py`.
> Si hay llamadas al SDK en `main.py`, algo se ha torcido. Y `classifier.py` no importa nada de FastAPI.

El stack cerrado no está aquí: está en `BRIEF.md`, sección «Restricciones técnicas».

## 4. API HTTP

| Endpoint | Devuelve | Errores |
|---|---|---|
| `GET /health` | `200 {"status": "ok"}` | — |
| `POST /tickets` | `201` con el ticket ya clasificado | `422` si título, descripción o responsables no son válidos |
| `GET /tickets` | `200` lista, filtrable por `category`, `priority`, `status`, `assignee`, `unassigned` y `overdue` | `422` si `unassigned` u `overdue` no son booleanos |
| `PATCH /tickets/{ticket_id}` | `200` ticket actualizado | `404` si no existe · `422` si el valor no está en el enum o los responsables no son válidos |
| `GET /` | `200` HTML con el tablero (ver §6) | — |

### `POST /tickets`

Crea un ticket y lo clasifica. La clasificación ocurre de forma síncrona durante la petición: la
respuesta ya trae `category`, `priority` y `tags`.

Request JSON (`assignees` es opcional; por defecto `[]`):

```json
{
  "title": "La app no carga",
  "description": "Desde esta mañana aparece una pantalla en blanco al iniciar sesión",
  "assignees": ["Ana", "Luis"]
}
```

Response `201`:

```json
{
  "id": 1,
  "title": "La app no carga",
  "description": "Desde esta mañana aparece una pantalla en blanco al iniciar sesión",
  "category": "bug",
  "priority": "P1",
  "tags": ["login", "blank-screen"],
  "status": "open",
  "assignees": ["Ana", "Luis"],
  "due_at": "2026-06-29T21:59:59Z",
  "overdue": false,
  "created_at": "2026-06-29T09:30:00Z",
  "updated_at": "2026-06-29T09:30:00Z"
}
```

(`due_at` del ejemplo: 23:59:59 hora de Madrid en verano = 21:59:59 UTC.)

Errores:

- `422` si `title` está vacío (o solo espacios) o supera 200 caracteres.
- `422` si el `title` no contiene ninguna letra ni número (por ejemplo `"!!!"`).
- `422` si `description` está vacía (o solo espacios) o supera 5000 caracteres.
- `422` si algún responsable está vacío o supera 60 caracteres, o hay más de 10.

### `GET /tickets`

Lista tickets en JSON, el más reciente primero. Query params opcionales, combinables entre sí
(todos los filtros indicados se cumplen a la vez):

| Param | Efecto |
|---|---|
| `category`, `priority`, `status` | Coincidencia exacta con el valor |
| `assignee` | Tickets en los que ese nombre es uno de los responsables (sin distinguir mayúsculas) |
| `unassigned=true` | Solo tickets sin ningún responsable |
| `overdue=true` / `overdue=false` | Solo vencidos / solo no vencidos |

```text
GET /tickets?category=bug&priority=P1&status=open
GET /tickets?assignee=Ana&overdue=true
```

- Un valor de filtro que no coincide con nada (`status=zombie`, `assignee=Nadie`) devuelve `200`
  con lista vacía, nunca un error de servidor.
- `unassigned=true` junto con `assignee=...` no puede coincidir con nada: lista vacía.
- Un valor no booleano en `unassigned` u `overdue` devuelve `422`.

Devuelve `200` con una lista de tickets con la misma forma que la respuesta de `POST /tickets`.

### `PATCH /tickets/{ticket_id}`

Solo se pueden cambiar `status`, `priority` y `assignees`. El resto de campos (título,
descripción, categoría, etiquetas…) son inmutables tras la creación.

```json
{
  "status": "resolved",
  "priority": "P2",
  "assignees": ["Ana", "Luis"]
}
```

- `status`: `open`, `in_progress`, `resolved`, `closed`
- `priority`: `P1`, `P2`, `P3` (recalcula `due_at`)
- `assignees`: **sustituye** la lista completa. `[]` quita todos los responsables.
- Hay que indicar al menos uno de los tres campos; los no indicados no cambian.
- Cambiar `status`, `priority` o `assignees` actualiza `updated_at`.

Errores:

- `404` si no existe el ticket.
- `422` si el valor no pertenece al enum permitido, si los responsables no son válidos o si el
  cuerpo no indica ningún campo.

`GET /` devuelve HTML; `GET /tickets` devuelve JSON. No los confundáis: los tests y el agente del
Día 3 usan el JSON.

## 5. Clasificador IA

Función esperada:

```python
def classify_ticket(title: str, description: str) -> dict:
    ...
```

Contrato de salida:

```python
{
    "category": "bug" | "feature_request" | "question" | "urgent",
    "priority": "P1" | "P2" | "P3",
    "tags": list[str],
}
```

Llamada al LLM: SDK de `openai` apuntando a OpenRouter (`base_url="https://openrouter.ai/api/v1"`),
modelo `openai/gpt-oss-120b`, API key desde la variable de entorno `OPENROUTER_API_KEY`.

Requisitos no negociables:

- **Valida la salida.** Si el LLM devuelve `"category": "URGENT"` (mayúsculas) o una categoría
  inventada, aplica el fallback. No propagues basura.
- **Como mucho un reintento** si la llamada falla. Si vuelve a fallar, fallback.
- **Nunca propaga excepciones del SDK.** Ante cualquier problema devuelve el fallback:

```python
{"category": "question", "priority": "P3", "tags": []}
```

El SPEC no fija el prompt: lo decidís vosotros. Sí es obligatorio que la salida cumpla el contrato.

## 6. Frontend mínimo

`GET /` devuelve una página HTML con:

1. Formulario para crear ticket (con campo opcional de responsables, nombres separados por comas).
2. Tablero de tickets, con columnas de responsables (editables en la propia fila) y fecha límite
   (en hora de Madrid), y una marca roja «Vencido» en los tickets vencidos.
3. Filtros por categoría, prioridad, estado y responsable (con autocompletado de los nombres
   existentes), más las casillas «Sin asignar» y «Solo vencidos».

Reglas de pantalla:

- Todo el texto visible está en **español**, incluidos los valores del enum: se muestran como
  «Error», «Petición de mejora», «Pregunta», «Urgente» (categoría) y «Abierto», «En curso»,
  «Resuelto», «Cerrado» (estado). Los valores internos de la API no cambian.
- Los filtros activos se conservan al crear o modificar un ticket.
- Los errores de validación se muestran en la propia página (la ruta HTML responde `200` con el
  aviso: HTMX no pinta los `4xx`).
- Reabrir un ticket es elegir «Abierto» en el desplegable de estado de un ticket resuelto o cerrado.

Recomendación:

- Usar Jinja2 templates.
- Usar HTMX para refrescar la tabla sin recargar toda la página.
- No escribir HTML grande como string dentro de `main.py`.

## 7. Tests obligatorios

El archivo `tests/test_acceptance.py` contiene los 5 tests de aceptación. No se modifica.

Los tests comprueban:

1. Crear un ticket válido devuelve `201` y clasificación.
2. El ticket creado queda persistido y aparece en `GET /tickets`.
3. Input inválido devuelve `422`.
4. Si el clasificador falla, se aplica fallback y la app no cae.
5. Se puede actualizar estado/prioridad y filtrar tickets.

Tests propios del equipo (además de los 5; cubren lo añadido en este SPEC):

- Responsables: crear con varios, deduplicar sin distinguir mayúsculas, validar longitud y máximo,
  sustituir/vaciar con `PATCH`, filtrar por `assignee` y `unassigned`.
- Estados: `resolved` válido, reabrir, y que `resolved`/`closed` no cuentan como vencidos.
- Fecha límite: cálculo por prioridad en hora de Madrid (incluido el cambio de horario),
  recálculo al cambiar la prioridad, reabrir no la reinicia, filtro `overdue`.
- Frontend: rutas HTML, escapado de HTML en todos los campos de usuario y etiquetas en español.

## 8. No negociable

- No commitear `.env`.
- No hardcodear API keys.
- No propagar excepciones del SDK de la IA al endpoint.
- No modificar los tests de aceptación para hacerlos pasar.
- No introducir React ni frontend complejo.
