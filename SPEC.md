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
| `status_changed_at` | datetime | UTC, desde cuándo tiene el ticket su estado actual (ver «Estados») |
| `assignees` | list[str] | Responsables del ticket (ver «Responsables»). Puede estar vacía |
| `due_at` | datetime | UTC, calculado en servidor según la prioridad (ver «Fecha límite») |
| `overdue` | bool | Calculado al consultar: `true` si el ticket está vencido (ver «Fecha límite») |
| `created_at` | datetime | UTC, generado en servidor |
| `updated_at` | datetime | UTC, actualizado en cambios relevantes |

Valores por defecto:

- `status`: `open`
- `assignees`: `[]`
- fallback de clasificación: `category="question"`, `priority="P3"`, `tags=[]`

### Almacenamiento (SQLite)

Las tablas las crea y las actualiza `app/db.py` solas en la primera conexión: no hay que crear nada
a mano ni ejecutar migraciones.

| Tabla | Contenido |
|---|---|
| `tickets` | Una fila por ticket: `id` (entero autogenerado, `INTEGER PRIMARY KEY AUTOINCREMENT`), los campos de la tabla anterior y `tags` como texto JSON |
| `ticket_assignees` | Una fila por ticket y responsable: `ticket_id`, `name` (grafía original) y `name_key` (el nombre en minúsculas «plegadas», para comparar sin distinguir mayúsculas ni acentos). Clave primaria `(ticket_id, name_key)`: no puede haber el mismo responsable dos veces en un ticket |

- `overdue` no se guarda (se calcula al consultar). `due_at` y `status_changed_at` sí.
- Las bases de datos creadas con versiones anteriores se migran solas al abrirlas: se añaden las
  columnas `due_at` y `status_changed_at` y se rellenan (`due_at` según la prioridad,
  `status_changed_at` con `updated_at`).

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
- `status_changed_at` es el momento en que el ticket pasó a su estado actual («desde cuándo está
  así»). Al crear el ticket vale `created_at`. Solo cambia cuando el estado **cambia de verdad**:
  un `PATCH` con el mismo estado, o que solo toque `priority` o `assignees`, no lo modifica.
  Reabrir (`resolved`/`closed` → `open`) sí lo actualiza.
- En bases de datos anteriores a este campo se rellena con `updated_at` (aproximado: es lo más
  cercano que se conocía).

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
  timefmt.py       # Formato «hace 3 h» (puro)
  pagination.py    # Tamaño de página y números de página a mostrar (puro)
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
| `GET /tickets` | `200` lista, filtrable por `category`, `priority`, `status`, `assignee`, `unassigned` y `overdue`, y paginable con `limit` y `offset` | `422` si `unassigned` u `overdue` no son booleanos o `limit`/`offset` no son válidos |
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
  "status_changed_at": "2026-06-29T09:30:00Z",
  "due_at": "2026-06-29T21:59:59Z",
  "overdue": false,
  "created_at": "2026-06-29T09:30:00Z",
  "updated_at": "2026-06-29T09:30:00Z"
}
```

(`due_at` del ejemplo: 23:59:59 hora de Madrid en verano = 21:59:59 UTC.)

Errores:

- `422` si `title` está vacío (o solo espacios) o supera 200 caracteres.
- `422` si el `title` no contiene ninguna letra, número ni emoji (por ejemplo `"!!!"` o `"+++"`):
  vale cualquier alfabeto (日本語, العربية, Ελληνικά…) y un título hecho solo de emojis (`"🔥"`).
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
| `limit` | Máximo de tickets a devolver (1–200). Sin `limit` se devuelven **todos** |
| `offset` | Tickets a saltarse desde el principio de la lista (≥ 0, por defecto 0) |

```text
GET /tickets?category=bug&priority=P1&status=open
GET /tickets?assignee=Ana&overdue=true
```

- Un valor de filtro que no coincide con nada (`status=zombie`, `assignee=Nadie`) devuelve `200`
  con lista vacía, nunca un error de servidor.
- `unassigned=true` junto con `assignee=...` no puede coincidir con nada: lista vacía.
- Un valor no booleano en `unassigned` u `overdue` devuelve `422`.
- `limit` fuera de 1–200, `offset` negativo o cualquiera de los dos no entero devuelven `422`.
- La paginación es **opcional** y no cambia la forma de la respuesta (sigue siendo una lista).
  La cabecera `X-Total-Count` trae siempre cuántos tickets cumplen los filtros en total, antes de aplicar `limit` y `offset`.
- El orden es estable (más reciente primero, desempate por `id`), así que páginas consecutivas no repiten ni se saltan tickets.

Devuelve `200` con una lista de tickets con la misma forma que la respuesta de `POST /tickets`.

### `GET /tickets/stats`

Conteos para presentaciones e informes: solo JSON, sin pantalla. No acepta parámetros: cuenta
**todos** los tickets.

```json
{
  "total": 47,
  "by_category": { "bug": 18, "feature_request": 12, "question": 9, "urgent": 8 },
  "by_priority": { "P1": 5, "P2": 30, "P3": 12 },
  "by_status": { "open": 30, "in_progress": 8, "resolved": 5, "closed": 4 }
}
```

- Devuelve `200`. Cada grupo trae **todas** las claves permitidas, con `0` si no hay tickets (con la
  base de datos vacía, todo es `0`): quien lo consuma no tiene que comprobar si una clave existe.
- Las claves salen en el orden de los enums de §2. `by_status` incluye `resolved`.
- La suma de los valores de cada grupo es siempre igual a `total`.
- Los cuatro números salen de **una sola consulta**, así que son coherentes entre sí aunque se
  creen tickets mientras tanto.
- `GET /tickets/stats` no se confunde con un id: `stats` no es un entero.

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

- `404` si no existe el ticket. Cuenta como «no existe» cualquier entero, incluidos `-1`, `0` y los
  que no caben en la base de datos (`99999999999999999999`).
- `422` si el id no es un entero (`abc`, `1.5`).
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
- **Paginación:** el tablero muestra 20 tickets por página. Con más de una página aparece un
  paginador (Anterior, números con «…» y Siguiente) y el texto «Mostrando 21–40 de 140 tickets»;
  con una sola, «Mostrando 9 tickets» y sin paginador.
- Al cambiar un filtro o crear un ticket se vuelve a la página 1. Al modificar un ticket desde
  su fila se mantiene la página (si ya no existe, se pasa a la última). Un número de página no
  válido, o fuera de rango, se corrige a la 1 o a la última, sin errores.
- **Filtros y página en la URL:** `GET /?status=open&page=2` abre el tablero con ese filtro y esa
  página ya aplicados (los desplegables salen marcados). Al filtrar o paginar la dirección del
  navegador se actualiza, así que refrescar la página, usar «Atrás» o compartir el enlace
  mantiene el estado. «Limpiar filtros» vuelve a `/`.
- Los errores de validación se muestran en la propia página (la ruta HTML responde `200` con el
  aviso: HTMX no pinta los `4xx`).
- Reabrir un ticket es elegir «Abierto» en el desplegable de estado de un ticket resuelto o cerrado.
- Bajo el desplegable de estado de cada ticket se muestra desde cuándo está así, en hora de
  Madrid y en relativo: «desde 05/11 14:20 · hace 3 h». El relativo usa «hace instantes»
  (menos de 1 min), «hace N min», «hace N h» (menos de 24 h) y «hace N d».

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
- «Desde cuándo»: `status_changed_at` al crear, solo cambia si cambia el estado, lo actualiza
  reabrir, relleno en bases de datos antiguas y texto relativo en pantalla.
- Fecha límite: cálculo por prioridad en hora de Madrid (incluido el cambio de horario),
  recálculo al cambiar la prioridad, reabrir no la reinicia, filtro `overdue`.
- Frontend: rutas HTML, escapado de HTML en todos los campos de usuario y etiquetas en español.
- Estadísticas: forma exacta, claves completas con `0`, base vacía, suma de cada grupo igual a
  `total`, reflejo de crear y de cambiar estado o prioridad, y que no confunde la ruta con un id.
- Paginación: `limit`/`offset`/`X-Total-Count` en la API, páginas de 20 en el tablero (rangos,
  números fuera de rango, filtros conservados, página 1 al filtrar o crear, página mantenida al
  editar) y filtros y página en la URL.

## 8. No negociable

- No commitear `.env`.
- No hardcodear API keys.
- No propagar excepciones del SDK de la IA al endpoint.
- No modificar los tests de aceptación para hacerlos pasar.
- No introducir React ni frontend complejo.

## 9. Casos límite

Comportamiento acordado ante entradas extremas o malintencionadas. Cada fila tiene pruebas
automáticas (`tests/test_edge_cases.py` y las citadas) y se comprueba en el navegador cuando afecta
a la pantalla.

| Caso | Comportamiento |
|---|---|
| `title` vacío, solo espacios o invisibles (`""`, `"   "`, tabulador, espacio de no separación, espacio de ancho cero) | `422`; no se crea nada. En la pantalla, aviso de error y el formulario conserva lo escrito |
| `title` de 5000 caracteres (o cualquiera > 200) | `422`; el límite se cuenta en caracteres Unicode, no en bytes: 200 `ñ` o 200 `日` valen, 201 no |
| Emojis y alfabetos no latinos | Se guardan y se devuelven exactamente; un título solo de emojis vale; los responsables también admiten Unicode y el filtro no distingue mayúsculas (`ÁLVARO` = `álvaro`) |
| HTML o JavaScript en título, descripción, etiquetas, responsables o filtros de la URL | La API lo devuelve tal cual (es JSON); el tablero lo escapa siempre y ningún dato de usuario llega a un `href`, `src` ni atributo `on…` |
| Inyección SQL (`'); DROP TABLE…`, `' OR '1'='1`) | Es texto: se guarda tal cual. En un filtro no coincide con nada (nunca devuelve «todo»). Todas las consultas usan parámetros |
| LLM caído, con error, lento o con salida inválida | Nunca llega un error al usuario: el ticket se crea con `question` / `P3` / sin etiquetas (§5). Se simula sustituyendo `classify_ticket`, sin `OPENROUTER_API_KEY` o con una clave inválida |
| El mismo ticket enviado dos veces | Son dos tickets independientes: no hay deduplicación (dos tickets iguales pueden ser legítimos). En la pantalla el botón «Crear y clasificar» se desactiva mientras se envía, para evitar el doble clic |
| Id malformado en `PATCH` (`abc`, `1.5`) | `422` |
| Id bien formado pero inexistente (`-1`, `0`, `99999999999999999999`) | `404` (nunca `500`) |
| `PATCH` con valores inválidos (`"inventado"`, tipos erróneos, cuerpo vacío o no JSON) | `422`; el ticket no cambia. Los campos inmutables (`title`, `category`, `id`, `created_at`, `tags`) se ignoran aunque se envíen |
| 20 envíos simultáneos | Se crean 20 tickets distintos, con ids únicos y sin errores. Lecturas y escrituras mezcladas tampoco fallan ni corrompen datos |
