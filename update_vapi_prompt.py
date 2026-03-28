#!/usr/bin/env python3.11
"""Update Vapi assistant system prompt — ALWAYS preserves toolIds, analysisPlan, tools.

CRITICAL: Never send a PATCH to Vapi without first reading the current config and
re-including toolIds, tools, and analysisPlan. Omitting them wipes them from the assistant,
causing the LLM to lose access to check_availability, get_contact, create_booking, etc.
"""
import requests, json, re, sys

VAPI_KEY = "VAPI_KEY_REDACTED_ROTATED_2026_04_24"
ASSISTANT_ID = "1631c7cf-2914-45f9-bf82-6635cdf00aba"

# Read new prompt
with open("/home/ubuntu/elena-vapi-server/system_prompt.txt") as f:
    prompt = f.read()

# Get current assistant config — MUST read first to preserve toolIds, tools, analysisPlan
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
current_tools = model_cfg.get("tools", [])
current_tool_ids = model_cfg.get("toolIds", [])
current_analysis_plan = assistant.get("analysisPlan", {})

print(f"Current state: toolIds={len(current_tool_ids)}, tools={len(current_tools)}, analysisPlan={'yes' if current_analysis_plan else 'no'}")

# Build patch body — ALWAYS include toolIds and analysisPlan to avoid wiping them
patch_body = {
    "model": {
        "provider": provider,
        "model": model_name,
        "messages": [{"role": "system", "content": prompt}]
    }
}
if current_tools:
    patch_body["model"]["tools"] = current_tools
if current_tool_ids:
    patch_body["model"]["toolIds"] = current_tool_ids
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
    version_match = re.search(r'v17\.\d+', content)
    version = version_match.group(0) if version_match else '??'
    print(f"Vapi updated OK")
    print(f"  Version: {version}")
    print(f"  Prompt length: {len(content)} chars")
    print(f"  toolIds preserved: {len(restored_tool_ids)}")
    print(f"  analysisPlan preserved: {'YES' if result.get('analysisPlan') else 'NO'}")
else:
    print(f"ERROR {r2.status_code}: {r2.text[:300]}")
    sys.exit(1)
