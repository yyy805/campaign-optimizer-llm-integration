from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.check_mta_data import validate_mta_data
from scripts.check_ontology_package import validate_ontology_package

ROOT = Path(__file__).parent.parent


def _build_mta_fixture(tmp_path: Path) -> Path:
    manifest = json.loads(
        (ROOT / "campaign_optimizer/ontology/mta/field_manifest.json")
        .read_text(encoding="utf-8")
    )
    for spec in manifest["files"]:
        path = tmp_path / spec["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(spec["required_columns"])
            writer.writerow(["demo"] * len(spec["required_columns"]))
    return tmp_path


def test_real_ontology_package_batch_check_passes():
    assert validate_ontology_package(ROOT) == []


def test_mta_contract_accepts_all_manifest_files(tmp_path):
    assert validate_mta_data(ROOT, _build_mta_fixture(tmp_path)) == []


def test_mta_contract_reports_missing_file(tmp_path):
    mta_root = _build_mta_fixture(tmp_path)
    missing = mta_root / "modules/amc_mta/data/simulated/amazon_ads_report_sample.csv"
    missing.unlink()
    errors = validate_mta_data(ROOT, mta_root)
    assert any("missing MTA file" in error for error in errors)


def test_mta_contract_reports_missing_column(tmp_path):
    mta_root = _build_mta_fixture(tmp_path)
    path = mta_root / "modules/amc_mta/data/simulated/amazon_ads_report_sample.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["reportDate"])
        writer.writerow(["2026-08-03"])
    errors = validate_mta_data(ROOT, mta_root)
    assert any("missing columns" in error for error in errors)
