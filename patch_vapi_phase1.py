"""
Fase 1 — Patch completo del assistant de Vapi:
1. Agregar get_current_time a las tools
2. Corregir schedule_callback (enum 2/4/12/120 como integers, no strings)
3. Configurar serverUrl en el assistant
4. temperature: 0.4
5. maxTokens: 180
"""
import requests
import json

VAPI_KEY = "VAPI_KEY_REDACTED_ROTATED_2026_04_24"
ASSISTANT_ID = "1631c7cf-2914-45f9-bf82-6635cdf00aba"
SERVER_URL = "https://elena-pdem.onrender.com/api/vapi/server-url"

HEADERS = {
    "Authorization": f"Bearer {VAPI_KEY}",
    "Content-Type": "application/json"
}

# Get current assistant config
resp = requests.get(f"https://api.vapi.ai/assistant/{ASSISTANT_ID}", headers=HEADERS, timeout=15)
d = resp.json()
model = d.get("model", {})
current_messages = model.get("messages", [])
current_tool_ids = model.get("toolIds", [])

# Build the corrected + expanded tools list
# Keep all existing tools except schedule_callback (we replace it)
existing_tools = model.get("tools", [])
tools_to_keep = [t for t in existing_tools if t.get("function", {}).get("name") != "schedule_callback"]

# Corrected schedule_callback — integers 2/4/12/120
schedule_callback_tool = {
    "type": "function",
    "server": {"url": SERVER_URL},
    "function": {
        "name": "schedule_callback",
        "description": (
            "Programa una llamada de seguimiento cuando el cliente no puede hablar ahora. "
            "SIEMPRE llama get_current_time PRIMERO para saber la hora real de Miami. "
            "Luego elige hours según lo que dijo el cliente: "
            "2 = en 2 horas / ahorita / más tarde (vago); "
            "4 = en 4 horas / esta tarde / hora específica ≤5h desde ahora; "
            "12 = mañana / pasado mañana / hora específica >5h; "
            "120 = próxima semana / en unos días / más adelante."
        ),
        "parameters": {
            "type": "object",
            "required": ["hours"],
            "properties": {
                "hours": {
                    "type": "integer",
                    "enum": ["2", "4", "12", "120"],
                    "description": "Horas hasta el callback. Solo acepta: 2, 4, 12, o 120."
                }
            }
        }
    }
}

# New get_current_time tool
get_current_time_tool = {
    "type": "function",
    "server": {"url": SERVER_URL},
    "function": {
        "name": "get_current_time",
        "description": (
            "Obtiene la hora y fecha actual en Miami (Eastern Time). "
            "Llama esta función INMEDIATAMENTE cuando el cliente dice que no puede hablar ahora, "
            "ANTES de preguntar por el callback. Sin parámetros."
        ),
        "parameters": {
            "type": "object",
            "required": [],
            "properties": {}
        }
    }
}

# Final tools list: existing (minus old schedule_callback) + corrected schedule_callback + get_current_time
new_tools = tools_to_keep + [schedule_callback_tool, get_current_time_tool]

print(f"Tools before: {len(existing_tools)}")
print(f"Tools after:  {len(new_tools)}")
print(f"Tool names:   {[t['function']['name'] for t in new_tools]}")

# Build PATCH body
patch_body = {
    "serverUrl": SERVER_URL,
    "model": {
        "provider": "openai",
        "model": model.get("model", "gpt-4o-mini"),
        "temperature": 0.4,
        "maxTokens": 180,
        "messages": current_messages,
        "tools": new_tools
    }
}
if current_tool_ids:
    patch_body["model"]["toolIds"] = current_tool_ids

# Apply PATCH
patch_resp = requests.patch(
    f"https://api.vapi.ai/assistant/{ASSISTANT_ID}",
    headers=HEADERS,
    json=patch_body,
    timeout=20
)

print(f"\nPATCH status: {patch_resp.status_code}")
if patch_resp.status_code == 200:
    result = patch_resp.json()
    result_model = result.get("model", {})
    result_tools = result_model.get("tools", [])
    print(f"✅ SUCCESS")
    print(f"   serverUrl:   {result.get('serverUrl', 'NOT SET')}")
    print(f"   temperature: {result_model.get('temperature', 'NOT SET')}")
    print(f"   maxTokens:   {result_model.get('maxTokens', 'NOT SET')}")
    print(f"   tools count: {len(result_tools)}")
    print(f"   tool names:  {[t.get('function',{}).get('name') for t in result_tools]}")
else:
    print(f"❌ ERROR: {patch_resp.text[:500]}")
