"""Run the offline synthetic LLM evaluation and print a JSON summary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from campaign_optimizer.llm.eval_runner import DEFAULT_MANIFEST, run_offline_eval


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args(argv)
    try:
        summary = run_offline_eval(args.manifest)
    except Exception:
        summary = {
            "schema_version": "1.0",
            "suite_id": "unavailable",
            "offline": True,
            "total": 0,
            "passed": 0,
            "failed": 1,
            "results": [],
        }
        print("Offline LLM evaluation failed safely.", file=sys.stderr)
        print(json.dumps(summary, sort_keys=True))
        return 2
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
