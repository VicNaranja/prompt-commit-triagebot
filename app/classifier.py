"""Clasificador de tickets con un LLM (gpt-oss-120b vía OpenRouter).

Es el ÚNICO módulo que llama a la API de IA. No importa nada de FastAPI.
Nunca propaga excepciones: ante cualquier problema devuelve FALLBACK_CLASSIFICATION.
"""

import json
import logging
import os
import re
from typing import Any

from openai import OpenAI

from app.models import ALLOWED_CATEGORIES, ALLOWED_PRIORITIES

logger = logging.getLogger(__name__)

FALLBACK_CLASSIFICATION = {"category": "question", "priority": "P3", "tags": []}

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MODEL = "openai/gpt-oss-120b"
MAX_ATTEMPTS = 2  # la llamada original + como mucho un reintento
TIMEOUT_SECONDS = 30.0

SYSTEM_PROMPT = """\
Eres un asistente de triage de tickets de soporte. Clasifica el ticket que te pase el usuario.

Responde SOLO con un objeto JSON, sin texto adicional ni bloques de código, con esta forma:
{"category": "...", "priority": "...", "tags": ["...", "..."]}

category: exactamente uno de "bug", "feature_request", "question", "urgent" (en minúsculas).
- bug: algo no funciona como debería.
- feature_request: se pide una mejora o una funcionalidad nueva.
- question: una duda o consulta.
- urgent: el ticket expresa urgencia (ver regla de urgencia).

priority: exactamente uno de "P1", "P2", "P3". Aplica los criterios en este orden:
1. Regla de urgencia: si el título o la descripción contienen una palabra de la familia de
   «urge» (urge, urgen, urgente, urgentes, urgencia, urgentemente), sin distinguir mayúsculas
   y minúsculas, y NO está negada («no urge», «no es urgente», «sin urgencia»), entonces
   category = "urgent" y priority = "P1". Esta regla prevalece sobre las demás.
2. P3 si se solicita una mejora o cualquier cosa que no suponga ningún error.
3. P2 si es un error puntual que afecta a una persona.
4. P1 si es un error que afecta a toda la compañía.

tags: lista corta de etiquetas útiles. Puede estar vacía.
"""


def _client() -> OpenAI:
    return OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=os.environ.get("OPENROUTER_API_KEY"),
        timeout=TIMEOUT_SECONDS,
        max_retries=0,  # los reintentos los controlamos aquí (como mucho uno)
    )


def _call_llm(title: str, description: str) -> str:
    response = _client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Título: {title}\n\nDescripción: {description}"},
        ],
        temperature=0,
    )
    return response.choices[0].message.content or ""


def _extract_json(text: str) -> Any:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        raise ValueError("La respuesta del LLM no contiene JSON")
    return json.loads(match.group(0))


def validate_classification(data: Any) -> dict | None:
    """Devuelve la clasificación limpia si cumple el contrato, o None si no."""
    if not isinstance(data, dict):
        return None
    category = data.get("category")
    priority = data.get("priority")
    tags = data.get("tags")
    if category not in ALLOWED_CATEGORIES or priority not in ALLOWED_PRIORITIES:
        return None
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        return None
    clean_tags = [tag.strip() for tag in tags if tag.strip()]
    return {"category": category, "priority": priority, "tags": clean_tags}


def fallback() -> dict:
    return {**FALLBACK_CLASSIFICATION, "tags": []}


def classify_ticket(title: str, description: str) -> dict:
    """Clasifica un ticket. Nunca lanza excepciones."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = _call_llm(title, description)
        except Exception:
            logger.warning("Fallo en la llamada al LLM (intento %d)", attempt, exc_info=True)
            continue
        try:
            result = validate_classification(_extract_json(raw))
        except Exception:
            result = None
        if result is None:
            logger.warning("Respuesta del LLM fuera de contrato; se aplica el fallback")
            return fallback()
        return result
    return fallback()
