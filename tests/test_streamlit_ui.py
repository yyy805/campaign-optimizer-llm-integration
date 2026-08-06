"""Offline gates for the Streamlit demo UI; dry-run only, zero provider construction."""
from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


def test_ui_renders_and_dry_run_is_safe():
    at = AppTest.from_file(ROOT / "app.py", default_timeout=60)
    at.run()
    assert not at.exception
    at.button[0].click().run()
    assert not at.exception
    assert any("零调用" in str(info.value) for info in at.info)
