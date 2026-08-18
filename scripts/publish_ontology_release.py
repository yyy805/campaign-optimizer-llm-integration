"""Materialize an immutable ontology release from a clean Git commit.

This command deliberately has no staging or commit behavior.  It prepares the
current manifest and its immutable bundle; the operator remains responsible for
reviewing and committing the paired publication change with exact-path staging.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from campaign_optimizer.llm.release_pin import verify_consumer_manifest
from campaign_optimizer.ontology.publication import build_publication_manifest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "campaign_optimizer/ontology/publication_manifest.json"
HISTORY = ROOT / "campaign_optimizer/ontology/history/manifests"
BUNDLES = ROOT / ".ontology_bundles"


def git_output(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def require_clean_committed_source(root: Path) -> str:
    status = git_output(root, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise RuntimeError("refusing publication: Git worktree is not clean")
    source_commit = git_output(root, "rev-parse", "HEAD")
    if len(source_commit) != 40:
        raise RuntimeError("cannot resolve the source commit")
    return source_commit


def verify_all_releases(root: Path, current: dict[str, object]) -> None:
    verify_consumer_manifest(current, root=root / ".ontology_bundles" / str(current["source_commit"]))
    history = root / "campaign_optimizer/ontology/history/manifests"
    for path in sorted(history.glob("*.json")) if history.is_dir() else ():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        verify_consumer_manifest(
            manifest, root=root / ".ontology_bundles" / str(manifest["source_commit"])
        )


def publish(root: Path = ROOT) -> dict[str, object]:
    source_commit = require_clean_committed_source(root)
    destination = root / ".ontology_bundles" / source_commit
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite existing bundle: {destination}")
    manifest = build_publication_manifest(
        source_commit=source_commit,
        ontology_version="2.1-campaign-pending",
        rule_version="R5@2.0-campaign-pending",
        engine_version="2.1",
        schema_version="1.1",
        root=root,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{source_commit}.", dir=destination.parent))
    manifest_path = root / "campaign_optimizer/ontology/publication_manifest.json"
    previous_manifest = manifest_path.read_bytes() if manifest_path.exists() else None
    installed_bundle = False
    try:
        for entry in manifest["entries"]:
            relative = Path(entry["path"])
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / relative, target)
        verify_consumer_manifest(manifest, root=temporary)
        temporary.replace(destination)
        installed_bundle = True
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        verify_all_releases(root, manifest)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        if installed_bundle and destination.exists():
            shutil.rmtree(destination)
        if previous_manifest is None:
            manifest_path.unlink(missing_ok=True)
        else:
            manifest_path.write_bytes(previous_manifest)
        raise
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    manifest = publish()
    print(f"published ontology bundle for {manifest['source_commit']}")
    print(f"package checksum: {manifest['package_checksum']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
