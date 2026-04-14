"""
Multi-treatment (multi-assistant) configuration integrity tests.

Ensures every entry in config.ASSISTANTS has the full set of fields required
by app.py's handlers (calendar_id, pipeline_id, booking_title, treatment,
name). A broken or partial entry would silently break live phone calls, so
this guardrails the config before any push to main.

Run: python3 tests/test_bots_config.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("GHL_PIT", "test")
os.environ.setdefault("VAPI_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")

REQUIRED_FIELDS = ("name", "treatment", "calendar_id", "pipeline_id", "booking_title")

# Assistants that MUST be present (production baseline — if these disappear,
# live calls break). Add to this list as new treatments go live.
REQUIRED_ASSISTANTS = {
    "1631c7cf-2914-45f9-bf82-6635cdf00aba": "botox",
    "3d5b77b5-f36c-4b95-88bc-4d6484277380": "lhr",
}


def test_assistants_defined():
    """config.ASSISTANTS has both Botox and LHR (production baseline)."""
    from config import ASSISTANTS
    assert isinstance(ASSISTANTS, dict), "config.ASSISTANTS must be a dict"
    for aid, treatment in REQUIRED_ASSISTANTS.items():
        assert aid in ASSISTANTS, f"Required assistant missing: {treatment} ({aid})"
        assert ASSISTANTS[aid].get("treatment") == treatment, \
            f"Assistant {aid} should be treatment={treatment}, got {ASSISTANTS[aid].get('treatment')}"
    print(f"test_assistants_defined PASS ({len(ASSISTANTS)} assistants)")


def test_each_assistant_complete():
    """Every assistant has all required fields non-empty."""
    from config import ASSISTANTS
    failures = []
    for aid, cfg in ASSISTANTS.items():
        for key in REQUIRED_FIELDS:
            val = cfg.get(key)
            if not val:
                failures.append(f"{aid} ({cfg.get('treatment', '?')}): missing/empty '{key}'")
    assert not failures, "Assistant config issues:\n  " + "\n  ".join(failures)
    print(f"test_each_assistant_complete PASS ({len(ASSISTANTS)} assistants validated)")


def test_default_assistant_resolvable():
    """DEFAULT_ASSISTANT_ID points at a real entry in ASSISTANTS."""
    from config import ASSISTANTS, DEFAULT_ASSISTANT_ID, get_assistant_config
    assert DEFAULT_ASSISTANT_ID in ASSISTANTS, \
        f"DEFAULT_ASSISTANT_ID={DEFAULT_ASSISTANT_ID} not in ASSISTANTS"
    # None / empty / unknown must fall back to default, not crash
    for bad in (None, "", "definitely-not-a-real-id"):
        cfg = get_assistant_config(bad)
        assert cfg and cfg.get("calendar_id"), f"Fallback failed for input {bad!r}"
    print("test_default_assistant_resolvable PASS")


def test_calendar_ids_unique():
    """No two assistants share the same calendar_id (would cross-book patients)."""
    from config import ASSISTANTS
    seen = {}
    for aid, cfg in ASSISTANTS.items():
        cal = cfg["calendar_id"]
        if cal in seen:
            raise AssertionError(
                f"calendar_id {cal} shared by {seen[cal]} and {aid} — "
                "would cause cross-treatment booking collisions"
            )
        seen[cal] = aid
    print(f"test_calendar_ids_unique PASS ({len(seen)} unique calendars)")


if __name__ == "__main__":
    test_assistants_defined()
    test_each_assistant_complete()
    test_default_assistant_resolvable()
    test_calendar_ids_unique()
    print("\nALL BOT CONFIG TESTS PASSED")
