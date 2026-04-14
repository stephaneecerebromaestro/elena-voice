"""
Smoke tests for scripts/audit_continuous.py.

These are pure-function tests: no network, no Telegram, no Supabase. We verify
that the math and formatters behave correctly on synthetic data so CI catches
regressions without needing live credentials.

Run: python3 tests/test_audit_continuous.py
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

os.environ.setdefault("GHL_PIT", "test")
os.environ.setdefault("VAPI_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
os.environ.setdefault("TELEGRAM_CHAT_ID", "0")


def test_import():
    """audit_continuous module imports cleanly."""
    import audit_continuous  # noqa: F401
    print("test_import PASS")


def test_extract_outcome_heuristics():
    """extract_outcome: short transcripts -> no_contesto; audits win if present."""
    import audit_continuous as ac
    # No audit, empty transcript -> no_contesto
    assert ac.extract_outcome({"transcript": ""}, None) == "no_contesto"
    # No audit, meaningful transcript -> unknown
    assert ac.extract_outcome({"transcript": "x" * 200}, None) == "unknown"
    # Audit with aria_outcome wins
    assert ac.extract_outcome({"transcript": ""}, {"aria_outcome": "agendo"}) == "agendo"
    # Audit falls back to original_outcome
    assert ac.extract_outcome({}, {"original_outcome": "no_agendo"}) == "no_agendo"
    print("test_extract_outcome_heuristics PASS")


def test_count_check_availability_loops():
    """count_check_availability_loops counts tool invocations in artifact.messages."""
    import audit_continuous as ac

    def mk_tool(name):
        return {"role": "tool_calls",
                "toolCalls": [{"function": {"name": name}}]}

    call = {"artifact": {"messages": [
        mk_tool("check_availability"),
        mk_tool("get_contact"),
        mk_tool("check_availability"),
        mk_tool("check_availability"),
    ]}}
    assert ac.count_check_availability_loops(call) == 3
    # Empty / missing
    assert ac.count_check_availability_loops({}) == 0
    assert ac.count_check_availability_loops({"artifact": {}}) == 0
    print("test_count_check_availability_loops PASS")


def test_compute_stats_synthetic():
    """compute_stats computes conversion/contact rates correctly on fake data."""
    import audit_continuous as ac

    # 10 calls: 6 no_contesto (empty transcript), 3 conversations, 1 agendó
    calls = []
    for i in range(6):
        calls.append({"id": f"nc{i}", "status": "ended", "transcript": "",
                      "cost": 0.015})
    for i in range(3):
        calls.append({"id": f"nv{i}", "status": "ended",
                      "transcript": "x" * 200, "cost": 0.08})
    calls.append({"id": "win1", "status": "ended",
                  "transcript": "x" * 300, "cost": 0.12})
    # A canceled call should be excluded (status != ended)
    calls.append({"id": "cancel", "status": "queued", "transcript": ""})

    audits = {
        "nv0": {"aria_outcome": "no_agendo",
                "aria_summary": "Paciente preguntó por dolor y colgó"},
        "nv1": {"aria_outcome": "no_agendo",
                "aria_summary": "Paciente preguntó por dolor y colgó"},
        "nv2": {"aria_outcome": "no_interesado"},
        "win1": {"aria_outcome": "agendo",
                 "errors_detected": [{"type": "duplicate_check_availability"}]},
    }

    s = ac.compute_stats(calls, audits)

    assert s["total_calls"] == 10, f"total should be 10, got {s['total_calls']}"
    assert s["no_contesto"] == 6
    assert s["connected"] == 4
    assert s["agendo"] == 1
    assert abs(s["conversion_rate"] - 0.25) < 1e-9  # 1/4
    assert abs(s["contact_rate"] - 0.40) < 1e-9   # 4/10
    assert s["outcomes"]["no_agendo"] == 2
    # Top no_agendo reason should aggregate
    assert s["top_no_agendo_reasons"][0][1] == 2
    assert s["top_errors"][0][0] == "duplicate_check_availability"
    assert s["cost_per_booking"] is not None
    print("test_compute_stats_synthetic PASS")


def test_formatters_dont_crash():
    """format_markdown and format_telegram_summary run without errors on empty results."""
    import audit_continuous as ac
    from datetime import datetime
    import pytz

    empty_stats = {
        "total_calls": 0, "audited": 0, "audit_coverage": 0,
        "outcomes": {}, "agendo": 0, "no_contesto": 0, "connected": 0,
        "conversion_rate": 0, "contact_rate": 0,
        "total_cost": 0, "avg_cost": 0, "cost_per_booking": None,
        "avg_duration_s": 0, "top_errors": [], "top_no_agendo_reasons": [],
        "check_availability_loop_calls": 0,
    }
    results = [{
        "assistant_id": "test-id",
        "config": {"name": "Test Bot", "treatment": "test",
                   "calendar_id": "cal1", "pipeline_id": "pipe1",
                   "booking_title": "Test"},
        "current": empty_stats,
        "previous": dict(empty_stats),
        "delta_conversion_abs": 0,
        "alert_drop": False,
    }]
    utc_cur = ("2026-04-07T04:00:00.000Z", "2026-04-14T04:00:00.000Z")
    utc_prev = ("2026-03-31T04:00:00.000Z", "2026-04-07T04:00:00.000Z")
    now = datetime.now(pytz.timezone("America/New_York"))

    md = ac.format_markdown(results, utc_cur, utc_prev, now)
    assert "# AUDITORÍA SEMANAL" in md
    assert "Test Bot" in md

    tg = ac.format_telegram_summary(results, "2026-04-07", "2026-04-14")
    assert "Elena Voice" in tg
    assert "TEST" in tg  # treatment uppercase
    print("test_formatters_dont_crash PASS")


if __name__ == "__main__":
    test_import()
    test_extract_outcome_heuristics()
    test_count_check_availability_loops()
    test_compute_stats_synthetic()
    test_formatters_dont_crash()
    print("\nALL AUDIT_CONTINUOUS TESTS PASSED")
