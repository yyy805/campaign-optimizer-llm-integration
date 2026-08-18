"""CLI wrapper for the reviewer judgment v1 dataset validator."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _validator_module():
    spec = importlib.util.spec_from_file_location("reviewer_judgment_v1_validator", ROOT / "validator.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load reviewer judgment v1 validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    validator = _validator_module()
    try:
        total = validator.validate_all(ROOT)
    except validator.DatasetValidationError as exc:
        print(f"reviewer_judgment_v1 validation failed: {exc}", file=sys.stderr)
        return 1
    print(f"reviewer_judgment_v1 validation accepted {total} frozen cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
