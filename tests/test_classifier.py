"""Tests propios del clasificador. No llaman a la red: sustituyen el cliente del SDK."""

from types import SimpleNamespace

import pytest

from app import classifier

FALLBACK = {"category": "question", "priority": "P3", "tags": []}


class FakeClient:
    """Imita `OpenAI().chat.completions.create`. Cada llamada consume un resultado de la cola."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        message = SimpleNamespace(content=outcome)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.fixture()
def use_client(monkeypatch):
    def _use(*outcomes):
        client = FakeClient(*outcomes)
        monkeypatch.setattr(classifier, "_get_client", lambda: client)
        return client

    return _use


# --- Regla de urgencia ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Esto urge porque tenemos demo el viernes",
        "URGENTE: no podemos facturar",
        "Es una urgencia para el cliente",
        "Necesitamos que se resuelva urgentemente",
        "Los casos urgentes se atienden antes",
        "Estos tickets urgen",
    ],
)
def test_urgency_rule_detects_family_of_urge(text):
    assert classifier.expresses_urgency(text)


@pytest.mark.parametrize(
    "text",
    [
        "No urge, puede esperar",
        "No es urgente",
        "Esto no es nada urgente",
        "Lo pido sin urgencia",
        "Sin ninguna urgencia por ahora",
        "La app no carga",
        "Hay una disputa urgencias médicas aparte",  # «urgencias» no es de la familia
    ],
)
def test_urgency_rule_ignores_negated_or_unrelated_text(text):
    assert not classifier.expresses_urgency(text)


# --- Salida del LLM ------------------------------------------------------------------------


def test_valid_llm_output_is_returned_with_clean_tags(use_client):
    use_client('{"category": "bug", "priority": "P2", "tags": [" Login ", "login", "CRM", ""]}')

    assert classifier.classify_ticket("La app falla", "No deja entrar") == {
        "category": "bug",
        "priority": "P2",
        "tags": ["login", "crm"],
    }


def test_json_inside_code_fence_is_accepted(use_client):
    use_client('```json\n{"category": "question", "priority": "P3", "tags": ["vpn"]}\n```')

    assert classifier.classify_ticket("¿VPN?", "¿Cómo me conecto?")["tags"] == ["vpn"]


@pytest.mark.parametrize(
    "content",
    [
        '{"category": "URGENT", "priority": "P1", "tags": []}',  # mayúsculas
        '{"category": "incident", "priority": "P1", "tags": []}',  # categoría inventada
        '{"category": "bug", "priority": "P9", "tags": []}',  # prioridad inventada
        '{"category": "bug", "priority": "P1", "tags": "login"}',  # tags no es lista
        '{"category": "bug", "priority": "P1", "tags": [1, 2]}',  # tags no son str
        '{"category": "bug", "priority": "P1"}',  # falta tags
        '["bug", "P1"]',  # no es un objeto
        "esto no es JSON",
        "",
        None,
    ],
)
def test_invalid_llm_output_uses_fallback(use_client, content):
    use_client(content)

    assert classifier.classify_ticket("La app falla", "No deja entrar") == FALLBACK


def test_urgent_category_from_llm_forces_p1(use_client):
    use_client('{"category": "urgent", "priority": "P3", "tags": []}')

    result = classifier.classify_ticket("Caída", "El servicio no responde")

    assert (result["category"], result["priority"]) == ("urgent", "P1")


# --- Fallos de la llamada y reintentos -----------------------------------------------------


def test_one_retry_then_success(use_client):
    client = use_client(
        RuntimeError("timeout"), '{"category": "bug", "priority": "P1", "tags": ["vpn"]}'
    )

    result = classifier.classify_ticket("VPN caída", "Nadie conecta")

    assert result == {"category": "bug", "priority": "P1", "tags": ["vpn"]}
    assert client.calls == 2


def test_at_most_one_retry_then_fallback(use_client):
    client = use_client(RuntimeError("boom"), RuntimeError("boom"), RuntimeError("never used"))

    assert classifier.classify_ticket("VPN caída", "Nadie conecta") == FALLBACK
    assert client.calls == 2


def test_missing_api_key_uses_fallback_without_calling_the_sdk(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    assert classifier.classify_ticket("VPN caída", "Nadie conecta") == FALLBACK


def test_fallback_is_not_shared_between_calls(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    classifier.classify_ticket("a", "b")["tags"].append("mutado")

    assert classifier.classify_ticket("a", "b") == FALLBACK
    assert classifier.FALLBACK_CLASSIFICATION["tags"] == []


# --- La regla de urgencia manda sobre el modelo --------------------------------------------


def test_urgency_overrides_llm_category_and_priority_but_keeps_tags(use_client):
    use_client('{"category": "bug", "priority": "P2", "tags": ["demo"]}')

    result = classifier.classify_ticket("Esto urge porque tenemos demo el viernes", "Falla el CRM")

    assert result == {"category": "urgent", "priority": "P1", "tags": ["demo"]}


def test_negated_urgency_does_not_override_llm(use_client):
    use_client('{"category": "feature_request", "priority": "P3", "tags": ["pdf"]}')

    result = classifier.classify_ticket("Exportar a PDF", "No es urgente, pero sería útil")

    assert result == {"category": "feature_request", "priority": "P3", "tags": ["pdf"]}


def test_urgency_still_applies_when_the_llm_is_down(use_client):
    use_client(RuntimeError("boom"), RuntimeError("boom"))

    result = classifier.classify_ticket("Esto urge", "No carga la web")

    assert result == {"category": "urgent", "priority": "P1", "tags": []}
