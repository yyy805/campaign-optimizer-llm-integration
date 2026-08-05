"""Generate an ontology review JSON file from a final-plan JSON file."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from campaign_optimizer.ontology.review_engine import generate_ontology_review
from campaign_optimizer.ontology.publication import (
    PROJECT_ROOT as BUNDLE_ROOT,
    load_publication_manifest,
    verify_publication_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--ontology-version")
    parser.add_argument("--confidence-state-version", default="card-default-v1")
    parser.add_argument(
        "--confidence-state", type=Path, action="append", default=[],
        help="runtime confidence-state JSON; repeat for multiple enabled rules",
    )
    args = parser.parse_args()

    manifest = load_publication_manifest()
    verify_publication_manifest(manifest, root=BUNDLE_ROOT)
    if args.ontology_version not in {None, manifest['ontology_version']}:
        parser.error('ontology version must match the checked-in release manifest')

    if args.plan.resolve() == args.output.resolve():
        parser.error("plan input and review output must be different files")

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    states = {}
    for state_path in args.confidence_state:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        rule_id = state.get("rule_id")
        if rule_id in states:
            parser.error(f"duplicate confidence state for {rule_id}")
        states[rule_id] = state
    review = generate_ontology_review(
        plan,
        ontology_version=manifest['ontology_version'],
        confidence_state_version=args.confidence_state_version,
        release_identity={
            name: manifest[name] for name in (
                'ontology_version', 'rule_version', 'engine_version',
                'schema_version', 'source_commit', 'package_checksum',
            )
        },
        confidence_states=states,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(review, ensure_ascii=False, indent=2) + "\n"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=args.output.parent,
            prefix=f".{args.output.name}.", suffix=".tmp", delete=False,
        ) as temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, args.output)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
