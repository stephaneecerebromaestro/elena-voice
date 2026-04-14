"""
Syntax and import validation for all Elena Voice modules.
Run: python3 tests/test_syntax.py
"""

import sys
import os
import importlib

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Minimal env so imports don't fail on missing secrets
os.environ.setdefault("GHL_PIT", "test")
os.environ.setdefault("GHL_LOCATION_ID", "test")
os.environ.setdefault("VAPI_API_KEY", "test")
os.environ.setdefault("VAPI_ASSISTANT_ID", "1631c7cf-2914-45f9-bf82-6635cdf00aba")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
os.environ.setdefault("TELEGRAM_CHAT_ID", "0")

MODULES = [
    "config",
    "aria_audit",
    "app",
]


def test_imports():
    """All core modules import without errors."""
    failures = []
    for mod_name in MODULES:
        try:
            importlib.import_module(mod_name)
            print(f"  OK  {mod_name}")
        except Exception as e:
            print(f"  FAIL {mod_name}: {e}")
            failures.append(mod_name)
    assert not failures, f"Modules failed to import: {failures}"
    print("test_imports PASS")


def test_active_config_helpers():
    """app.py exposes the multi-treatment config helpers."""
    import app as flask_app
    for fn_name in ("set_active_config", "get_active_config",
                    "get_active_calendar_id", "get_active_booking_title"):
        assert hasattr(flask_app, fn_name), f"app.py missing helper: {fn_name}"
    # set_active_config should resolve Botox correctly
    cfg = flask_app.set_active_config("1631c7cf-2914-45f9-bf82-6635cdf00aba")
    assert cfg["treatment"] == "botox", f"Expected botox, got {cfg.get('treatment')}"
    assert flask_app.get_active_calendar_id() == "hYHvVwjKPykvcPkrsQWT"
    # Unknown assistantId falls back to default (Botox)
    cfg_fallback = flask_app.set_active_config("unknown-id")
    assert cfg_fallback["treatment"] == "botox", "Unknown id should fall back to default (Botox)"
    print("test_active_config_helpers PASS")


def test_flask_app_routes():
    """Flask app creates and registers critical routes."""
    import app as flask_app
    assert flask_app.app is not None, "Flask app is None"
    rules = [r.rule for r in flask_app.app.url_map.iter_rules()]
    expected = [
        "/api/vapi/server-url",
        "/health",
        "/update-date",
        "/aria/vapi/end-of-call",
        "/aria/telegram/webhook",
    ]
    missing = [r for r in expected if r not in rules]
    assert not missing, f"Routes missing: {missing}"
    print(f"test_flask_app_routes PASS ({len(rules)} routes registered)")


def test_tool_handlers_exist():
    """All 9 Vapi tool handlers declared in CLAUDE.md are defined in app.py."""
    import app as flask_app
    expected_handlers = [
        "handle_check_availability",
        "handle_get_contact",
        "handle_create_contact",
        "handle_create_booking",
        "handle_reschedule_appointment",
        "handle_cancel_appointment",
        "handle_get_appointment_by_contact",
        "handle_get_current_time",
        "handle_schedule_callback",
    ]
    missing = [h for h in expected_handlers if not hasattr(flask_app, h)]
    assert not missing, f"Tool handlers missing: {missing}"
    print(f"test_tool_handlers_exist PASS ({len(expected_handlers)} handlers)")


if __name__ == "__main__":
    test_imports()
    test_active_config_helpers()
    test_flask_app_routes()
    test_tool_handlers_exist()
    print("\nALL TESTS PASSED")
