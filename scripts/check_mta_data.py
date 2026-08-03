"""Validate the explicit MTA sample/output contract without scanning disks."""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path


def _root(cli_value: str | None, env_name: str, fallback: Path | None = None) -> Path:
    raw = cli_value or os.getenv(env_name)
    if raw:
        return Path(raw).expanduser().resolve()
    if fallback is not None:
        return fallback.resolve()
    raise ValueError(f"provide the path with the CLI option or {env_name}")


def validate_mta_data(project_root: Path, mta_root: Path) -> list[str]:
    errors: list[str] = []
    manifest_path = (
        project_root / "campaign_optimizer" / "ontology" / "mta" / "field_manifest.json"
    )
    if not manifest_path.is_file():
        return [f"missing manifest: {manifest_path}"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"invalid manifest {manifest_path}: {exc}"]

    if set(manifest) != {"manifest_version", "files"}:
        return ["MTA manifest must contain exactly manifest_version and files"]
    if manifest["manifest_version"] != "1.0" or not isinstance(manifest["files"], list):
        return ["MTA manifest_version must be 1.0 and files must be an array"]
    if len(manifest["files"]) != 8:
        errors.append("MTA manifest must declare exactly 8 contract files")
    declared_paths: set[str] = set()
    for index, spec in enumerate(manifest["files"]):
        if not isinstance(spec, dict) or set(spec) != {"path", "required_columns"}:
            errors.append(f"manifest files[{index}] has an invalid shape")
            continue
        relative_text = spec["path"]
        columns = spec["required_columns"]
        if not isinstance(relative_text, str) or not relative_text:
            errors.append(f"manifest files[{index}] has an invalid path")
            continue
        if relative_text in declared_paths:
            errors.append(f"duplicate MTA manifest path: {relative_text}")
        declared_paths.add(relative_text)
        if (
            not isinstance(columns, list)
            or not columns
            or not all(isinstance(column, str) and column for column in columns)
            or len(columns) != len(set(columns))
        ):
            errors.append(f"{relative_text} has invalid or duplicate required_columns")

    for spec in manifest["files"]:
        if not isinstance(spec, dict) or "path" not in spec or "required_columns" not in spec:
            continue
        relative = Path(spec["path"])
        path = (mta_root / relative).resolve()
        try:
            path.relative_to(mta_root.resolve())
        except ValueError:
            errors.append(f"MTA manifest path escapes root: {relative.as_posix()}")
            continue
        if not path.is_file():
            errors.append(f"missing MTA file: {relative.as_posix()}")
            continue
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle)
                header = next(reader, None)
                rows = list(reader)
        except (OSError, UnicodeError, csv.Error) as exc:
            errors.append(f"cannot read {relative.as_posix()}: {exc}")
            continue
        if not header:
            errors.append(f"empty header: {relative.as_posix()}")
            continue
        duplicate_headers = sorted({column for column in header if header.count(column) > 1})
        if duplicate_headers:
            errors.append(
                f"{relative.as_posix()} has duplicate columns: {', '.join(duplicate_headers)}"
            )
        missing = [column for column in spec["required_columns"] if column not in header]
        if missing:
            errors.append(
                f"{relative.as_posix()} missing columns: {', '.join(missing)}"
            )
        if not rows:
            errors.append(f"no data rows: {relative.as_posix()}")
        for row_number, row in enumerate(rows, start=2):
            if len(row) != len(header):
                errors.append(
                    f"{relative.as_posix()} row {row_number} has {len(row)} fields; "
                    f"expected {len(header)}"
                )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root")
    parser.add_argument("--mta-root")
    args = parser.parse_args()
    try:
        project_root = _root(
            args.project_root,
            "CAMPAIGN_OPTIMIZER_PROJECT_ROOT",
            Path(__file__).resolve().parent.parent,
        )
        mta_root = _root(args.mta_root, "CAMPAIGN_OPTIMIZER_MTA_ROOT")
    except ValueError as exc:
        parser.error(str(exc))

    errors = validate_mta_data(project_root, mta_root)
    if errors:
        print("MTA contract check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    manifest = json.loads(
        (project_root / "campaign_optimizer" / "ontology" / "mta" / "field_manifest.json")
        .read_text(encoding="utf-8")
    )
    print(
        f"MTA contract check passed: {len(manifest['files'])} files satisfy "
        "the required field contract."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
