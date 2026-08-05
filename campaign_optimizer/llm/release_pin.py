"""Mandatory ontology release pinning for an ontology consumer repository."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from campaign_optimizer.ontology.publication import PackageDriftError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CURRENT_MANIFEST = PROJECT_ROOT / 'campaign_optimizer/ontology/publication_manifest.json'
HISTORY_MANIFESTS = PROJECT_ROOT / 'campaign_optimizer/ontology/history/manifests'
BUNDLE_ROOT = (
    PROJECT_ROOT / 'campaign_optimizer/ontology/bundles'
    / 'b90391ed77bbe3ce3f10bb929688db32f7627984'
)
IDENTITY_FIELDS = (
    'ontology_version', 'rule_version', 'engine_version',
    'schema_version', 'source_commit', 'package_checksum',
)


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackageDriftError('cannot load a pinned ontology manifest') from exc
    if not isinstance(value, dict):
        raise PackageDriftError('pinned ontology manifest must be an object')
    return value


def verify_consumer_manifest(manifest: Mapping[str, Any], *, root: Path) -> None:
    required = {'manifest_version', *IDENTITY_FIELDS, 'entries'}
    if set(manifest) != required or manifest.get('manifest_version') != '1.0':
        raise PackageDriftError('pinned ontology manifest has an invalid shape')
    identity = {name: manifest[name] for name in required if name != 'package_checksum'}
    canonical = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')
    if hashlib.sha256(canonical).hexdigest() != manifest['package_checksum']:
        raise PackageDriftError('pinned ontology manifest checksum is invalid')
    seen: set[str] = set()
    for entry in manifest['entries']:
        if not isinstance(entry, dict) or set(entry) != {'path', 'sha256', 'size'}:
            raise PackageDriftError('pinned ontology manifest entry is invalid')
        relative = Path(entry['path'])
        if relative.is_absolute() or '..' in relative.parts or entry['path'] in seen:
            raise PackageDriftError('pinned ontology manifest path is unsafe')
        seen.add(entry['path'])
        path = root / relative
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise PackageDriftError('pinned ontology asset is unavailable') from exc
        if len(raw) != entry['size'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise PackageDriftError('pinned ontology asset does not match its manifest')


def release_identity(manifest: Mapping[str, Any]) -> dict[str, str]:
    return {name: str(manifest[name]) for name in IDENTITY_FIELDS}


def load_verified_manifests(*, root: Path = BUNDLE_ROOT) -> dict[str, dict[str, Any]]:
    paths = [CURRENT_MANIFEST]
    if HISTORY_MANIFESTS.is_dir():
        paths.extend(sorted(HISTORY_MANIFESTS.glob('*.json')))
    manifests: dict[str, dict[str, Any]] = {}
    for path in paths:
        manifest = load_manifest(path)
        verify_consumer_manifest(manifest, root=root)
        checksum = manifest['package_checksum']
        if checksum in manifests:
            raise PackageDriftError('duplicate ontology package checksum')
        manifests[checksum] = manifest
    return manifests
