"""
Fase 3 — Problema 1: Aplicar temperature 0.45 y maxTokens 170
PATCH completo: lee el estado actual de Vapi, preserva todas las tools,
y aplica solo los cambios de temperature y maxTokens.
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

# Step 1: Get current full assistant config
print("Fetching current assistant config...")
resp = requests.get(f"https://api.vapi.ai/assistant/{ASSISTANT_ID}", headers=HEADERS, timeout=15)
if resp.status_code != 200:
    print(f"❌ Failed to fetch assistant: {resp.status_code} {resp.text[:200]}")
    exit(1)

d = resp.json()
model = d.get("model", {})
current_tools = model.get("tools", [])
current_messages = model.get("messages", [])
current_tool_ids = model.get("toolIds", [])
current_system_prompt = next(
    (m.get("content", "") for m in current_messages if m.get("role") == "system"),
    ""
)

print(f"Current state:")
print(f"  model:       {model.get('model')}")
print(f"  temperature: {model.get('temperature')} → will set to 0.45")
print(f"  maxTokens:   {model.get('maxTokens')} → will set to 170")
print(f"  tools:       {len(current_tools)} → preserving all")
print(f"  tool names:  {[t.get('function',{}).get('name') for t in current_tools]}")
print(f"  system prompt length: {len(current_system_prompt)} chars")

# Step 2: Build PATCH body — preserve everything, only change temperature and maxTokens
patch_body = {
    "serverUrl": SERVER_URL,
    "model": {
        "provider": "openai",
        "model": model.get("model", "gpt-4o-mini"),
        "temperature": 0.45,
        "maxTokens": 170,
        "messages": current_messages,
        "tools": current_tools
    }
}
if current_tool_ids:
    patch_body["model"]["toolIds"] = current_tool_ids

# Step 3: Apply PATCH
print("\nApplying PATCH...")
patch_resp = requests.patch(
    f"https://api.vapi.ai/assistant/{ASSISTANT_ID}",
    headers=HEADERS,
    json=patch_body,
    timeout=20
)

print(f"PATCH status: {patch_resp.status_code}")
if patch_resp.status_code == 200:
    result = patch_resp.json()
    result_model = result.get("model", {})
    result_tools = result_model.get("tools", [])
    result_messages = result_model.get("messages", [])
    result_system = next(
        (m.get("content", "") for m in result_messages if m.get("role") == "system"),
        ""
    )

    print(f"\n✅ SUCCESS")
    print(f"   serverUrl:          {result.get('serverUrl', 'NOT SET')}")
    print(f"   temperature:        {result_model.get('temperature')}")
    print(f"   maxTokens:          {result_model.get('maxTokens')}")
    print(f"   tools count:        {len(result_tools)}")
    print(f"   tool names:         {[t.get('function',{}).get('name') for t in result_tools]}")
    print(f"   system prompt len:  {len(result_system)} chars")

    # Verify all expected tools are present
    expected_tools = [
        "check_availability", "get_contact", "create_contact", "create_booking",
        "get_appointment_by_contact", "reschedule_appointment", "cancel_appointment",
        "schedule_callback", "get_current_time"
    ]
    result_tool_names = [t.get('function',{}).get('name') for t in result_tools]
    print("\n   Tool verification:")
    all_ok = True
    for t in expected_tools:
        status = "✅" if t in result_tool_names else "❌ MISSING"
        if t not in result_tool_names:
            all_ok = False
        print(f"     {status} {t}")

    if all_ok:
        print("\n✅ All tools preserved. PATCH complete.")
    else:
        print("\n❌ Some tools are missing. Check above.")
else:
    print(f"❌ ERROR: {patch_resp.text[:500]}")
