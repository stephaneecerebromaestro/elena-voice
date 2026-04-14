"""
Smoke tests for scripts/check_prompt_drift.py.

El test real (fetching Vapi live) NO se puede correr en CI porque no hay
creds reales. Acá solo verificamos que el módulo importa, que el mapeo
MIRRORS apunta a archivos existentes, y que strip_header funciona.

El drift check real corre los lunes como parte de run_weekly_audit.sh
(con creds reales de /etc/elena-voice/env).

Run: python3 tests/test_prompt_drift.py
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

os.environ.setdefault("VAPI_API_KEY", "test")


def test_import():
    """Module imports cleanly."""
    import check_prompt_drift  # noqa: F401
    print("test_import PASS")


def test_mirrors_exist():
    """Every assistant in MIRRORS points to a file that actually exists."""
    import check_prompt_drift as cpd
    missing = [info["path"] for info in cpd.MIRRORS.values()
               if not info["path"].exists()]
    assert not missing, f"Mirror files missing: {missing}"
    assert len(cpd.MIRRORS) >= 2, \
        f"Expected at least 2 assistants (Botox+LHR), got {len(cpd.MIRRORS)}"
    print(f"test_mirrors_exist PASS ({len(cpd.MIRRORS)} mirrors)")


def test_strip_header():
    """strip_header removes the mirror's metadata preamble."""
    import check_prompt_drift as cpd
    sample = (
        "# Header line 1\n"
        "# Header line 2\n"
        "# Header line 3\n"
        "\n"
        "[SYSTEM_PROMPT_VERSION: 2.2.0]\n"
        "Actual prompt content here.\n"
    )
    stripped = cpd.strip_header(sample)
    assert stripped.startswith("[SYSTEM_PROMPT_VERSION"), \
        f"Header not stripped; got: {stripped[:60]!r}"
    assert "# Header" not in stripped
    print("test_strip_header PASS")


def test_skip_without_creds():
    """With VAPI_API_KEY=test, script exits 0 (skipped) instead of failing."""
    import subprocess
    env = {**os.environ, "VAPI_API_KEY": "test"}
    r = subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "scripts/check_prompt_drift.py")],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, \
        f"Expected exit 0 when creds are dummy, got {r.returncode}. stderr: {r.stderr}"
    assert "skipping" in r.stdout.lower() or "skipped" in r.stdout.lower(), \
        f"Expected 'skipped' in output, got: {r.stdout}"
    print("test_skip_without_creds PASS")


if __name__ == "__main__":
    test_import()
    test_mirrors_exist()
    test_strip_header()
    test_skip_without_creds()
    print("\nALL PROMPT_DRIFT TESTS PASSED")
