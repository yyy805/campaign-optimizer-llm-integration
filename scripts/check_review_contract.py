"""Validate one final-plan/ontology-review/LLM-context bundle."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from campaign_optimizer.contracts.validation import validate_contract_bundle


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--review", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    validate_contract_bundle(
        _load(args.plan), _load(args.review), _load(args.context),
        _load(args.output) if args.output else None,
    )
    print("Review-only public contract check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
