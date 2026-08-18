from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.publish_ontology_release import publish, require_clean_committed_source


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    asset = root / "campaign_optimizer/ontology/asset.json"
    asset.parent.mkdir(parents=True)
    asset.write_text('{"safe":true}\n', encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Release Test")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "source")
    return root


def test_publish_materializes_and_verifies_immutable_bundle(tmp_path):
    root = _repository(tmp_path)
    manifest = publish(root)
    bundle = root / ".ontology_bundles" / manifest["source_commit"]
    assert (bundle / "campaign_optimizer/ontology/asset.json").read_text() == '{"safe":true}\n'
    assert json.loads((root / "campaign_optimizer/ontology/publication_manifest.json").read_text()) == manifest
    with pytest.raises(RuntimeError, match="not clean"):
        publish(root)


def test_publication_refuses_dirty_source(tmp_path):
    root = _repository(tmp_path)
    (root / "campaign_optimizer/ontology/asset.json").write_text('{"safe":false}\n')
    with pytest.raises(RuntimeError, match="not clean"):
        require_clean_committed_source(root)
