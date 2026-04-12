"""
Elena Voice — Configuracion multi-tratamiento
Cada assistant de Vapi tiene su propio calendar, pipeline y booking title.

Para agregar un nuevo tratamiento:
1. Crear el assistant en Vapi (con su prompt)
2. Agregar una entrada aqui con el assistantId como key
3. Configurar el calendar y pipeline en GHL (Juan)
4. Push + deploy

El servidor identifica el assistant via call.assistantId en cada request de Vapi.
"""
import os

# Default assistant (Botox) — se usa si el assistantId no se encuentra en el mapa
DEFAULT_ASSISTANT_ID = os.environ.get("VAPI_ASSISTANT_ID", "1631c7cf-2914-45f9-bf82-6635cdf00aba")

# Mapa de assistants: assistantId → config
ASSISTANTS = {
    # ─── BOTOX (activo en produccion) ───
    "1631c7cf-2914-45f9-bf82-6635cdf00aba": {
        "name": "Elena Voice - Botox",
        "treatment": "botox",
        "calendar_id": "hYHvVwjKPykvcPkrsQWT",
        "pipeline_id": "jiLGCWy0CEsa0iAmmMWT",
        "booking_title": "Evaluación Botox - Laser Place Miami",
    },

    # ─── LHR (pendiente — calendar ya existe) ───
    # "VAPI_ASSISTANT_ID_AQUI": {
    #     "name": "Elena Voice - Laser Hair Removal",
    #     "treatment": "lhr",
    #     "calendar_id": "gQclGhEhZ2K1NkLal7pt",
    #     "pipeline_id": "fyieKv1fpjRGJXfRZKT2",
    #     "booking_title": "Evaluación Laser Hair Removal - Laser Place Miami",
    # },

    # ─── Nuevos tratamientos van aqui ───
    # "VAPI_ID": {
    #     "name": "Elena Voice - Fillers",
    #     "treatment": "fillers",
    #     "calendar_id": "(pedir a Juan)",
    #     "pipeline_id": "(pedir a Juan)",
    #     "booking_title": "Evaluación Fillers - Laser Place Miami",
    # },
}


def get_assistant_config(assistant_id):
    """
    Busca la config del assistant por su ID.
    Si no lo encuentra, usa la config del default (Botox).
    Si el ID es None o vacio, usa el default.

    Retorna dict con: name, treatment, calendar_id, pipeline_id, booking_title
    """
    if assistant_id and assistant_id in ASSISTANTS:
        return ASSISTANTS[assistant_id]

    # Fallback al default
    default_config = ASSISTANTS.get(DEFAULT_ASSISTANT_ID)
    if default_config:
        return default_config

    # Ultra-fallback: env vars (compatibilidad con config anterior)
    return {
        "name": "Elena Voice - Default",
        "treatment": "unknown",
        "calendar_id": os.environ.get("GHL_CALENDAR_ID", "hYHvVwjKPykvcPkrsQWT"),
        "pipeline_id": "",
        "booking_title": os.environ.get("BOOKING_TITLE", "Evaluación - Laser Place Miami"),
    }
