#!/usr/bin/env python3.11
"""Update Vapi assistant system prompt — preserves model.tools and analysisPlan.

ARCHITECTURE (post-patch v17.46):
  - model.toolIds = [] intentionally EMPTY. The 7 GHL-native tools were removed because
    their OAuth session (GoHighLevelMCPCredential) is broken/expired. When present, the LLM
    called get_free_slots (GHL-native) instead of check_availability (our server), causing
    "GoHighLevelMCPCredential not found" errors on every call.
  - model.tools = 9 custom server-url tools:
      check_availability, get_contact, create_contact, create_booking (CORE — added as inline tools)
      get_appointment_by_contact, reschedule_appointment, cancel_appointment,
      get_current_time, schedule_callback
    ALL tools route through our Render server URL.

CRITICAL: Never add toolIds back without verifying the GHL OAuth session is active.
"""
import requests, json, re, sys

VAPI_KEY = "88453266-9413-4275-babf-8e2481a9a1d6"
ASSISTANT_ID = "1631c7cf-2914-45f9-bf82-6635cdf00aba"

# Read new prompt
with open("/home/ubuntu/elena-vapi-server/system_prompt.txt") as f:
    prompt = f.read()

# Get current assistant config — MUST read first to preserve model.tools and analysisPlan
r = requests.get(
    f"https://api.vapi.ai/assistant/{ASSISTANT_ID}",
    headers={"Authorization": f"Bearer {VAPI_KEY}"}
)
if r.status_code != 200:
    print(f"ERROR getting assistant: {r.status_code} {r.text[:200]}")
    sys.exit(1)

assistant = r.json()
model_cfg = assistant.get("model", {})
provider = model_cfg.get("provider", "openai")
model_name = model_cfg.get("model", "gpt-4o-mini")
current_tools = model_cfg.get("tools", [])       # 5 custom inline tools — ALWAYS preserve
current_analysis_plan = assistant.get("analysisPlan", {})

# INTENTIONALLY NOT reading or restoring toolIds.
# The GHL-native toolIds were removed because their OAuth is broken.
# Do NOT add them back. See module docstring.
current_tool_ids_count = len(model_cfg.get("toolIds", []))
if current_tool_ids_count > 0:
    print(f"WARNING: {current_tool_ids_count} toolIds found in current config — they will NOT be preserved.")
    print(f"  GHL-native toolIds are intentionally empty. If you see this, someone added them back manually.")
    print(f"  Do NOT restore them without verifying GHL OAuth is active.")

print(f"Current state: toolIds=0 (intentional), tools={len(current_tools)}, analysisPlan={'yes' if current_analysis_plan else 'no'}")

# Build patch body — toolIds explicitly set to [] to ensure they stay empty
patch_body = {
    "model": {
        "provider": provider,
        "model": model_name,
        "messages": [{"role": "system", "content": prompt}],
        "toolIds": [],           # intentionally empty — GHL OAuth broken
        "tools": current_tools,  # preserve 5 custom server-url tools
    }
}
if current_analysis_plan:
    patch_body["analysisPlan"] = current_analysis_plan

r2 = requests.patch(
    f"https://api.vapi.ai/assistant/{ASSISTANT_ID}",
    headers={"Authorization": f"Bearer {VAPI_KEY}", "Content-Type": "application/json"},
    json=patch_body
)
if r2.status_code == 200:
    result = r2.json()
    content = result.get("model", {}).get("messages", [{}])[0].get("content", "")
    restored_tool_ids = result.get("model", {}).get("toolIds", [])
    restored_tools = result.get("model", {}).get("tools", [])
    version_match = re.search(r'v17\.\d+', content)
    version = version_match.group(0) if version_match else '??'
    print(f"Vapi updated OK")
    print(f"  Version: {version}")
    print(f"  Prompt length: {len(content)} chars")
    print(f"  toolIds: {len(restored_tool_ids)} (should be 0)")
    print(f"  model.tools: {len(restored_tools)} (should be 5)")
    for t in restored_tools:
        print(f"    - {t.get('function',{}).get('name','?')}")
    print(f"  analysisPlan: {'YES' if result.get('analysisPlan') else 'NO'}")
else:
    print(f"ERROR {r2.status_code}: {r2.text[:300]}")
    sys.exit(1)
