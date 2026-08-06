"""Offline gates for the three-role live E2E script; zero provider construction."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_script(*args):
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_three_role_e2e_v15.py"), *args],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    return json.loads(completed.stdout)


def test_e2e_dry_run_initial_render_budget_is_two_calls():
    out = run_script()
    assert out["status"] == "DRY_RUN" and out["mode"] == "initial_render"
    assert out["provider_call_limit"] == 4 and out["reviewer_prompt"] == "reviewer_v9"


def test_e2e_dry_run_chat_budget_includes_triage():
    out = run_script("--chat")
    assert out["status"] == "DRY_RUN" and out["mode"] == "chat"
    assert out["provider_call_limit"] == 5
