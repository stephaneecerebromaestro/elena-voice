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

    # ─── LHR (assistant creado 2026-04-13) ───
    "3d5b77b5-f36c-4b95-88bc-4d6484277380": {
        "name": "Elena Voice - Laser Hair Removal",
        "treatment": "lhr",
        "calendar_id": "gQclGhEhZ2K1NkLal7pt",
        "pipeline_id": "fyieKv1fpjRGJXfRZKT2",
        "booking_title": "Evaluación Laser Hair Removal - Laser Place Miami",
    },

    # ─── ACNÉ (creado 2026-04-15) ───
    "77392648-047e-4a40-9f8a-4f125f2ed6d6": {
        "name": "Elena Voice - Acné",
        "treatment": "acne",
        "calendar_id": "L83X5HSAsWjwZUCblLqm",
        "pipeline_id": "zHEfAknfdfzkqXC9seg3",
        "booking_title": "Evaluación Acné — Elena Voice",
    },

    # ─── CICATRICES (creado 2026-04-15) ───
    "b6b09524-06da-4bf7-b518-a71b6a1c7d8b": {
        "name": "Elena Voice - Cicatrices",
        "treatment": "cicatrices",
        "calendar_id": "innp3I6K8ljcuecoLdPI",
        "pipeline_id": "sxBQVVTrcg4UBA2hIkE7",
        "booking_title": "Evaluación Cicatrices — Elena Voice",
    },

    # ─── FILLERS (creado 2026-04-15) ───
    "a9494200-af37-485c-b0fb-fb85479b17a7": {
        "name": "Elena Voice - Fillers",
        "treatment": "fillers",
        "calendar_id": "WS6XZZAz8ModQXzujkrz",
        "pipeline_id": "wnD72CQDp6WHWWucPhns",
        "booking_title": "Evaluación Fillers — Elena Voice",
    },

    # ─── BIOESTIMULADORES / RADIESSE (creado 2026-04-15) ───
    "39bd6450-055e-4839-9c27-6522e08e8423": {
        "name": "Elena Voice - Bioestimuladores",
        "treatment": "radiesse",
        "calendar_id": "OfFAX4YqVIpTVrslMMx6",
        "pipeline_id": "0nOwProhhs9S3R6Sflk3",
        "booking_title": "Evaluación Bioestimuladores — Elena Voice",
    },

    # ─── REJUVENECIMIENTO / LUXEGLOW (creado 2026-04-15) ───
    "65b3a4b0-2e08-471f-af56-e091e47f26bd": {
        "name": "Elena Voice - Rejuvenecimiento",
        "treatment": "rejuvenecimiento",
        "calendar_id": "jnyBASqRZHiuFYs3vCBR",
        "pipeline_id": "TcG7U1NxywQLteETRzbo",
        "booking_title": "Evaluación Rejuvenecimiento — Elena Voice",
    },
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
