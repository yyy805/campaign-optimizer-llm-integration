"""Deterministic release identity for cross-repository ontology consumers."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

ONTOLOGY_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ONTOLOGY_ROOT.parents[1]
MANIFEST_PATH = ONTOLOGY_ROOT / 'publication_manifest.json'
INCLUDED_PREFIXES = (
    Path('campaign_optimizer/ontology'),
    Path('campaign_optimizer/contracts'),
    Path('campaign_optimizer/schemas'),
    Path('tests/fixtures'),
)
EXCLUDED_PARTS = {'__pycache__', '.pytest_cache'}
EXCLUDED_NAMES = {"publication_manifest.json"}


class PackageDriftError(ValueError):
    """The installed ontology files do not match their pinned manifest."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _asset_paths(root: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in root.rglob("*.json")
            if path.name not in EXCLUDED_NAMES and "__pycache__" not in path.parts
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def _bundle_asset_paths(root: Path) -> list[Path]:
    candidates: Iterable[Path] = (
        path for prefix in INCLUDED_PREFIXES for path in (root / prefix).rglob('*')
    )
    return sorted(
        (
            path for path in candidates
            if path.is_file()
            and path.name not in EXCLUDED_NAMES
            and not EXCLUDED_PARTS.intersection(path.parts)
            and path.suffix in {'.json', '.py', '.md', '.csv'}
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def build_publication_manifest(
    *,
    source_commit: str,
    ontology_version: str,
    rule_version: str,
    engine_version: str,
    schema_version: str,
    root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    if len(source_commit) != 40 or any(ch not in "0123456789abcdef" for ch in source_commit):
        raise ValueError("source_commit must be a lowercase 40-character Git SHA")
    entries = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256(path.read_bytes()),
        }
        for path in _bundle_asset_paths(root)
    ]
    for entry, path in zip(entries, _bundle_asset_paths(root), strict=True):
        entry['size'] = len(path.read_bytes())
    identity = {
        "manifest_version": "1.0",
        "source_commit": source_commit,
        "ontology_version": ontology_version,
        "rule_version": rule_version,
        "engine_version": engine_version,
        "schema_version": schema_version,
        "entries": entries,
    }
    canonical = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {**identity, "package_checksum": _sha256(canonical)}


def load_publication_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackageDriftError(
            f'cannot load ontology publication manifest: {exc}'
        ) from exc
    if not isinstance(manifest, dict):
        raise PackageDriftError('ontology publication manifest must be an object')
    return manifest


def verify_publication_manifest(
    manifest: dict[str, Any], *, root: Path = PROJECT_ROOT
) -> None:
    required = {
        'manifest_version', 'source_commit', 'ontology_version', 'rule_version',
        'engine_version', 'schema_version', 'entries', 'package_checksum',
    }
    if set(manifest) != required:
        raise PackageDriftError('ontology publication manifest has an invalid shape')
    if manifest.get('source_commit') == 'TO_BE_PINNED_AFTER_ASSET_COMMIT':
        raise PackageDriftError('ontology publication source_commit is not pinned')
    expected = build_publication_manifest(
        source_commit=manifest["source_commit"],
        ontology_version=manifest["ontology_version"],
        rule_version=manifest["rule_version"],
        engine_version=manifest["engine_version"],
        schema_version=manifest["schema_version"],
        root=root,
    )
    if manifest != expected:
        raise PackageDriftError("ontology publication manifest does not match package files")
