# SPEC.md — Contrato funcional de TriageBot

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
| `technician` | str \| null | Uno de la lista de técnicos (ver abajo), o `null` si está sin asignar |
| `due_date` | date | Fecha límite (`AAAA-MM-DD`), calculada en servidor (ver abajo) |
| `created_at` | datetime | UTC, generado en servidor |
| `updated_at` | datetime | UTC, actualizado en cambios relevantes |

Valores por defecto:

- `status`: `open`
- `technician`: `null` (los tickets nuevos llegan sin asignar)
- fallback de clasificación: `category="question"`, `priority="P3"`, `tags=[]`

### Estados

`open` (abierto) → `in_progress` (en curso) → `resolved` (resuelto) → `closed` (cerrado).
`resolved` es un estado intermedio antes de cerrar. No se obliga a ningún orden: cualquier cambio de
estado está permitido.

Para filtrar se agrupan así:

- **Activos:** `open` e `in_progress`.
- **Resueltos:** `resolved`.
- **Cerrados:** `closed`.

### Técnicos

Cada ticket lo gestiona un técnico, o ninguno si está sin asignar. La lista de técnicos es estática:
una lista fija definida en el código (`app/models.py`). Por ahora, diez: de `Técnico 1` a
`Técnico 10`.

### Fecha límite (`due_date`)

Se calcula a partir de la prioridad y de la fecha de creación, en días naturales y con la hora de
España (`Europe/Madrid`):

- `P1`: el mismo día de la creación.
- `P2`: el día siguiente.
- `P3`: dos días después.

Si cambia la prioridad, la fecha límite se recalcula, contando siempre desde la fecha de creación
(no desde el día del cambio). Un ticket está **vencido** si su fecha límite es anterior a hoy (hora
de España).

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
| `POST /tickets` | `201` con el ticket ya clasificado | `422` si título o descripción vacíos o demasiado largos |
| `GET /tickets` | `200` lista, filtrable por `category`, `priority`, `status`, `technician` y `due` | — |
| `PATCH /tickets/{ticket_id}` | `200` ticket actualizado | `404` si no existe · `422` si el valor no está en el enum o en la lista de técnicos |
| `GET /` | `200` HTML con la web de gestión de tickets (ver §6) | — |
| `GET /gestion/tabla` | `200` HTML con la tabla de tickets filtrada (la usa la web de gestión) | — |
| `GET /gestion/tickets/{ticket_id}` | `200` HTML con la ventana de detalle del ticket | `404` si no existe |
| `POST /gestion/tickets/{ticket_id}` | `200` HTML: guarda los cambios de la ventana de detalle | `404` si no existe |
| `GET /kanban` | `200` HTML con el tablero kanban (ver §6) | — |
| `GET /kanban/tablero` | `200` HTML con las columnas del tablero, filtrable por `technician` | — |
| `POST /kanban/tickets/{ticket_id}/estado` | `200` HTML con el tablero actualizado tras mover un ticket | `404` si no existe · `422` si el estado no es válido |
| `GET /nuevo` | `200` HTML con la web de creación de tickets, paso 1 (ver §6) | — |
| `POST /nuevo/titulo` | `200` HTML del paso 2, o del paso 1 con el error | — |
| `POST /nuevo/descripcion` | `200` HTML con el agradecimiento, o del paso 2 con el error | — |

### `POST /tickets`

Crea un ticket y lo clasifica. La clasificación ocurre de forma síncrona durante la petición: la
respuesta ya trae `category`, `priority` y `tags`.

Request JSON:

```json
{
  "title": "La app no carga",
  "description": "Desde esta mañana aparece una pantalla en blanco al iniciar sesión"
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
  "technician": null,
  "due_date": "2026-06-29",
  "created_at": "2026-06-29T09:30:00Z",
  "updated_at": "2026-06-29T09:30:00Z"
}
```

Errores:

- `422` si `title` está vacío (o solo espacios) o supera 200 caracteres.
- `422` si `description` está vacía (o solo espacios) o supera 5000 caracteres.


### `GET /tickets`

Lista tickets en JSON. Query params opcionales:

- `category`, `priority`: valor exacto.
- `status`: valor exacto. Se puede repetir para pedir varios estados a la vez
  (`?status=open&status=in_progress` son los activos).
- `technician`: nombre exacto del técnico, o `none` para los tickets sin asignar.
- `due`: `overdue` (vencidos), `today` (vencen hoy) o `tomorrow` (vencen mañana).

Un valor desconocido en cualquier filtro no da error: devuelve una lista vacía.

```text
GET /tickets?category=bug&priority=P1&status=open
GET /tickets?status=open&status=in_progress&technician=none&due=overdue
```

Devuelve `200` con una lista de tickets con la misma forma que la respuesta de `POST /tickets`.

Orden: por `created_at`, el más reciente primero. Si dos tickets tienen el mismo `created_at`, se
desempata por `id`, el mayor primero.

### `PATCH /tickets/{ticket_id}`

Solo se pueden cambiar `status`, `priority` y `technician`. El resto de campos son inmutables tras
la creación (`due_date` se recalcula sola si cambia la prioridad).

```json
{
  "status" : "in_progress",
  "priority": "P2",
  "technician": "Técnico 1"
}
```

- `status`: `open`, `in_progress`, `resolved`, `closed`
- `priority`: `P1`, `P2`, `P3`
- `technician`: uno de la lista de técnicos, o `null` para dejarlo sin asignar.

Errores:

- `404` si no existe el ticket.
- `422` si el valor no pertenece al enum permitido o el técnico no está en la lista.
- `422` si el cuerpo está vacío (`{}`).

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

Criterio para el prompt: 
- P1 y categoría `urgent` si el ticket expresa urgencia (ver «Regla de urgencia»). Prevalece sobre los criterios siguientes
- P3 Si se solicita una mejora o cualquier cosa que no suponga ningun error
- P2 Si es un error puntual a una persona
- P1 Si es un error que afecta a toda la compañia 

Regla de urgencia:

- Se aplica si el título o la descripción contienen una palabra de la familia de «urge»: `urge`,
  `urgen`, `urgente`, `urgentes`, `urgencia`, `urgentemente`. Sin distinguir mayúsculas y minúsculas.
- No se aplica si la palabra está negada: «no urge», «no es urgente», «sin urgencia».
- Resultado: `category = "urgent"` y `priority = "P1"`. Los `tags` se generan igual que en cualquier
  otro ticket (por ejemplo, un error urgente puede llevar el tag `bug`).
- Ejemplo del brief: «Esto urge porque tenemos demo el viernes» → `urgent`, `P1`.


## 6. Frontend mínimo

Hay dos webs separadas: una para crear tickets y otra para gestionarlos.

**Modo oscuro.** Las tres páginas (`/nuevo`, `/` y `/kanban`) tienen un botón para cambiar entre modo
claro y oscuro. Siempre empiezan en claro; la elección se recuerda en ese navegador.

### Web de creación de tickets (`/nuevo`)

Página de bienvenida con estilo minimalista. Pide los datos en dos pasos, sin recargar la página:

1. **Título.** Mensaje de bienvenida y un único campo: el título. Botón «Siguiente».
2. **Descripción.** Un único campo: la descripción. Botón «Aceptar».
3. **Agradecimiento.** Al aceptar se crea el ticket (igual que `POST /tickets`: se valida, se
   clasifica y se guarda) y se muestra un mensaje de agradecimiento por su ticket, con un enlace
   para crear otro.

Reglas:

- Mismas validaciones que la API: título de 1 a 200 caracteres y descripción de 1 a 5000, tras
  quitar espacios. Si no se cumplen, se queda en el mismo paso y muestra el error junto al campo.
- La clasificación tarda unos segundos (llamada al LLM): mientras tanto el botón «Aceptar» se
  desactiva y se muestra que se está enviando.
- Si el clasificador falla, el ticket se crea con el fallback y se muestra el agradecimiento igual.
- La página no muestra la clasificación: es para quien abre el ticket, no para Soporte.

### Web de gestión de tickets (`GET /`)

Para el equipo de Soporte. Muestra el listado de tickets y permite gestionarlos sin recargar la
página (HTMX).

**Listado.** Una tabla con una fila por ticket y estas columnas: título, categoría, prioridad,
estado, técnico («Sin asignar» si no tiene), fecha límite (en rojo si está vencida) y fecha de
creación. Orden: el más reciente primero, como `GET /tickets`. Si no hay tickets que cumplan los
filtros, se muestra un mensaje en lugar de la tabla.

**Filtros**, encima de la tabla. Al cambiar uno se refresca la tabla:

| Filtro | Opciones | Por defecto |
|---|---|---|
| Estado | Activos · Resueltos · Cerrados · Todos | Activos |
| Prioridad | Todas · P1 · P2 · P3 | Todas |
| Técnico | Todos · Sin asignar · cada técnico de la lista | Todos |
| Fecha límite | Todas · Vencidos · Vencen hoy · Vencen mañana | Todas |

Por defecto, al entrar, se ven solo los tickets activos.

**Ventana de detalle.** Al pulsar un ticket se abre una ventana emergente (popup) con:

- Todos sus datos: título, descripción completa, categoría, tags, fecha de creación y fecha límite.
- Desplegables para cambiar estado, prioridad y técnico, y un botón «Guardar».
- Un botón «Marcar como resuelto» que pasa el ticket a `resolved` directamente.

Al guardar, se cierra la ventana y se refresca la tabla con los filtros que hubiera. Se cierra
también con un botón «Cerrar», con la tecla Escape o pulsando fuera.

Arriba de la página, un botón «Nuevo ticket» lleva a la web de creación (`/nuevo`) y otro,
«Tablero kanban», lleva a `/kanban`.

### Tablero kanban (`/kanban`)

Complemento de la web de gestión: muestra los tickets por estado y permite cambiarlos de estado
arrastrándolos.

- **Columnas:** una por estado, en este orden: Abierto (`open`), En curso (`in_progress`), Resuelto
  (`resolved`) y Cerrado (`closed`). Cada columna muestra cuántos tickets tiene.
- **Tarjetas:** una por ticket, con título, prioridad, técnico («Sin asignar» si no tiene) y fecha
  límite (en rojo si está vencida). Dentro de cada columna, el más reciente primero.
- **Mover:** arrastrar una tarjeta a otra columna cambia el estado del ticket (igual que el `PATCH`)
  y se guarda al soltarla. Si falla, la tarjeta vuelve a su columna y se muestra un aviso. El
  arrastre usa la librería SortableJS, cargada por CDN con versión fija
  (`https://cdn.jsdelivr.net/npm/sortablejs@1.15.7/Sortable.min.js`); funciona también en móvil.
- **Filtro por técnico**, encima del tablero: Todos · Sin asignar · cada técnico de la lista. Por
  defecto, Todos. Al cambiarlo se refresca el tablero.
- **Detalle:** al pulsar una tarjeta se abre la misma ventana de detalle que en la web de gestión.
- Un botón «Volver al listado» lleva a `/`.

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

### Requisitos que imponen los tests

La forma en que están escritos los tests obliga a lo siguiente. Si no se cumple, fallan:

- **Base de datos desde `DATABASE_URL`.** Los tests fijan `DATABASE_URL=sqlite:///<ruta temporal>`
  para usar una base de datos aislada. La app debe leer esa variable en cada petición (al abrir la
  conexión), no al importar el módulo, porque los tests la fijan después de importar `app.main`.
  Si no se lee, los tests escriben en la base real y el test 5 (`len(tickets) == 1`) falla a partir
  de la segunda ejecución.
- **Llamar al clasificador a través del módulo.** Los tests sustituyen `app.classifier.classify_ticket`.
  En `main.py` hay que importar el módulo (`from app import classifier`) y llamar a
  `classifier.classify_ticket(...)`. Con `from app.classifier import classify_ticket` la sustitución
  no se aplica y los tests llaman al LLM real.
- **El endpoint también captura errores del clasificador.** El test 4 hace que `classify_ticket` lance
  una excepción. `POST /tickets` debe capturar cualquier excepción del clasificador, aplicar el
  fallback (`question`, `P3`, `[]`) y responder `201`.

## 8. No negociable

- No commitear `.env`.
- No hardcodear API keys.
- No propagar excepciones del SDK de la IA al endpoint.
- No modificar los tests de aceptación para hacerlos pasar.
- No introducir React ni frontend complejo.
