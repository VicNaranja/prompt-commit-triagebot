"""Servidor de demostración para probar el diseño en el navegador (herramienta de desarrollo).

Arranca TriageBot con una base de datos TEMPORAL llena de tickets de ejemplo y un clasificador
falso y determinista: no toca triagebot.db ni llama al LLM real. Lo lanza design/check_design.py
con --app, pero también se puede usar a mano:

    python design/demo_server.py [puerto]      # por defecto 8765
"""

import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp(prefix='triagebot-demo-')}/demo.db"
os.environ.pop("OPENROUTER_API_KEY", None)  # esta demo nunca llama al LLM real

import uvicorn  # noqa: E402

import app.main  # noqa: E402
from app import classifier, db  # noqa: E402


def fake_classify(title: str, description: str) -> dict:
    text = f"{title} {description}".lower()
    if "urge" in text:
        return {"category": "urgent", "priority": "P1", "tags": ["demo"]}
    if "excel" in text or "propuesta" in text or "sería útil" in text:
        return {"category": "feature_request", "priority": "P3", "tags": ["mejora"]}
    if "?" in text:
        return {"category": "question", "priority": "P3", "tags": []}
    return {"category": "bug", "priority": "P2", "tags": ["incidencia"]}


classifier.classify_ticket = fake_classify


def stamp(days_ago: float) -> str:
    moment = datetime.now(UTC) - timedelta(days=days_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def make(title, description, *, assignees=(), status=None, days_ago=0.0, priority=None):
    """Crea un ticket «como si» hubiera nacido hace `days_ago` días (para tener vencidos)."""
    real_now = db._now
    db._now = lambda: stamp(days_ago)
    try:
        classification = fake_classify(title, description)
        if priority:
            classification["priority"] = priority
        ticket = db.create_ticket(title, description, classification, assignees=assignees)
        if status:
            db.update_ticket(ticket["id"], status=status)
    finally:
        db._now = real_now


make("Esto urge porque tenemos demo el viernes", "El informe de ventas no se genera y lo "
     "necesitamos para una demo con un cliente.", assignees=["Ana", "Luis"])
make("La VPN rechaza a todo el equipo de teletrabajo", "Desde primera hora nadie puede "
     "conectarse a la VPN corporativa.", assignees=["Ana"], status="in_progress", days_ago=3,
     priority="P1")
make("Los gráficos del dashboard de ventas aparecen en blanco", "Las tarjetas con números "
     "cargan, pero los gráficos se quedan vacíos.", days_ago=0.2)
make("Sería útil poder exportar los tickets a Excel", "Para hacer informes mensuales tendría "
     "que pasar los datos a mano.", assignees=["Luis"], status="resolved", days_ago=5)
make("¿Dónde puedo descargar mi nómina del mes pasado?", "Necesito la nómina de mayo para un "
     "trámite del banco.", assignees=["Eva"], status="closed", days_ago=6)
make("Propuesta: histórico de versiones en los documentos compartidos", "Sería muy útil poder "
     "ver versiones anteriores de un documento.", assignees=["Ana", "Luis", "Eva", "Marcos"])
make("Error en https://intranet.ejemplo.local/documentos/informes/informe_mensual_resumen_"
     "ejecutivo_final_v3_definitivo_revisado.pdf al abrir", "Al abrir el documento el visor se "
     "queda cargando para siempre.", assignees=["Marcos"], status="in_progress", days_ago=4)
make("El CRM se cierra solo al abrir una oportunidad grande", "Con muchas líneas de producto "
     "la aplicación se cierra sin avisar.", days_ago=2.5, priority="P1")
make("<script>alert(1)</script> en el campo título", "Prueba de escapado: el HTML debe verse "
     "como texto.")

if __name__ == "__main__":
    uvicorn.run(app.main.app, host="127.0.0.1", port=PORT, log_level="warning")
