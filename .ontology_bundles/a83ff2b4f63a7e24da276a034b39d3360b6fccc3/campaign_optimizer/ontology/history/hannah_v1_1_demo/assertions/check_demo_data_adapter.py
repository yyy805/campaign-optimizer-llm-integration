#!/usr/bin/env python3
"""Validate Ontology-owned Demo adapters against current MTA fixtures."""

from __future__ import annotations

import csv
import json
import sys
from datetime import date
from pathlib import Path

from jsonschema import Draft7Validator, FormatChecker

HERE = Path(__file__).resolve().parent
ROOT = next(parent for parent in HERE.parents if (parent / "docs").is_dir())
ADS = ROOT / "docs/marketing-roi-analysis-mta_-/modules/amc_mta/data/simulated/amazon_ads_report_sample.csv"
SUMMARY = ROOT / "docs/marketing-roi-analysis-mta_-/modules/amc_mta/outputs/attribution/amc_mta_model_comparison_summary.csv"
ASSERTIONS = HERE / "story_assertions.json"

EXPECTED_CAMPAIGN_BY_PRODUCT = {
    "AMAZON_DSP": "campaign_dsp_001",
    "SPONSORED_BRANDS": "campaign_sb_001",
    "SPONSORED_DISPLAY": "campaign_sd_001",
    "SPONSORED_PRODUCTS": "campaign_sp_001",
}
EXPECTED_MOCK_CONTRACTS: dict[str, tuple[str, str]] = {}


def load_json(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def main() -> int:
    try:
        contract = load_json(HERE / "demo_data_adapter.json")
        schema = load_json(HERE / "demo_data_adapter.schema.json")
        assertions = load_json(ASSERTIONS)
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL: cannot load contract inputs: {error}")
        return 1
    errors = [
        f"schema {list(error.path)}: {error.message}"
        for error in Draft7Validator(schema, format_checker=FormatChecker()).iter_errors(contract)
    ]
    if errors:
        print(f"FAIL: {len(errors)} schema error(s)")
        for error in errors:
            print(f"- {error}")
        return 1

    manifest = contract["batch_manifest"]
    try:
        start = date.fromisoformat(manifest["report_start_date"])
        end = date.fromisoformat(manifest["report_end_date"])
        if start > end:
            errors.append("batch manifest start date is after end date")

        with ADS.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        required_ads_headers = {"reportDate", "adProduct", "normalizedTouchpoint"}
        if not rows or not required_ads_headers.issubset(rows[0]):
            raise ValueError(f"Ads CSV missing headers {sorted(required_ads_headers)}")
        if rows[0]["reportDate"] != "报告日期":
            raise ValueError("Ads CSV bilingual description row missing or moved; refusing to discard first row")
        data_rows = rows[1:]
        if not data_rows:
            raise ValueError("Ads CSV contains no data rows")
        row_dates = [date.fromisoformat(row["reportDate"]) for row in data_rows]
    except (OSError, KeyError, ValueError) as error:
        errors.append(f"cannot validate Ads fixture: {error}")
        data_rows = []
        row_dates = []

    live_touchpoints = {row["normalizedTouchpoint"] for row in data_rows}
    mapping_rows = contract["campaign_adapter"]["mappings"]
    configured_list = [item["touchpoint"] for item in mapping_rows]
    configured = set(configured_list)
    if len(configured_list) != len(configured):
        errors.append("duplicate touchpoint in campaign lookup")
    if configured != live_touchpoints:
        errors.append(
            f"campaign lookup drift: missing={sorted(live_touchpoints-configured)}, "
            f"unexpected={sorted(configured-live_touchpoints)}"
        )

    for item in mapping_rows:
        product = item["touchpoint"].split(":", 1)[0]
        expected_campaign = EXPECTED_CAMPAIGN_BY_PRODUCT.get(product)
        if expected_campaign is None or item["demo_campaign_id"] != expected_campaign:
            errors.append(
                f"wrong Demo campaign assignment for {item['touchpoint']}: "
                f"expected={expected_campaign}, actual={item['demo_campaign_id']}"
            )

    if row_dates:
        if min(row_dates) != start or max(row_dates) != end:
            errors.append(
                f"batch date drift: manifest={start}..{end}, Ads={min(row_dates)}..{max(row_dates)}"
            )
        keys = [(row["reportDate"], row["normalizedTouchpoint"]) for row in data_rows]
        if len(keys) != len(set(keys)):
            errors.append("duplicate reportDate + normalizedTouchpoint row in Ads fixture")
        expected_days = (end - start).days + 1
        if len(keys) != expected_days * len(live_touchpoints):
            errors.append(
                f"incomplete daily touchpoint grid: expected={expected_days * len(live_touchpoints)}, "
                f"actual={len(keys)}"
            )

    try:
        with SUMMARY.open(encoding="utf-8-sig", newline="") as handle:
            summary_rows = list(csv.DictReader(handle))
        required_summary_headers = {
            "outcome", "report_start_date", "report_end_date", "calculation_valid",
            "data_support_sufficient", "models_consistent",
        }
        if not summary_rows or not required_summary_headers.issubset(summary_rows[0]):
            raise ValueError(f"summary CSV missing headers {sorted(required_summary_headers)}")
    except (OSError, ValueError) as error:
        errors.append(f"cannot validate comparison summary: {error}")
        summary_rows = []
    outcomes = {row["outcome"] for row in summary_rows}
    required = set(contract["consistency_contract"]["required_outcomes"])
    if len(summary_rows) != len(outcomes):
        errors.append("duplicate outcome row in comparison summary")
    if outcomes != required:
        errors.append(f"consistency outcome drift: expected={sorted(required)}, actual={sorted(outcomes)}")
    for row in summary_rows:
        if row["report_start_date"] != str(start) or row["report_end_date"] != str(end):
            errors.append(f"summary window drift for outcome {row['outcome']}")
        for field in ("calculation_valid", "data_support_sufficient", "models_consistent"):
            if row[field].lower() != "true":
                errors.append(f"consistency criterion failed: {row['outcome']}.{field}={row[field]}")

    mock_contracts = contract["mock_contracts"]
    mock_ids = [item["concept_id"] for item in mock_contracts]
    if len(mock_ids) != len(set(mock_ids)):
        errors.append("duplicate mock concept contract")
    if set(mock_ids) != set(EXPECTED_MOCK_CONTRACTS):
        errors.append("mock concept set drift")
    for item in mock_contracts:
        expected = EXPECTED_MOCK_CONTRACTS.get(item["concept_id"])
        actual = (item["owner"], item["missing_behavior"])
        if expected is not None and actual != expected:
            errors.append(f"mock owner/rule binding drift for {item['concept_id']}: expected={expected}, actual={actual}")

    seen_mock_inputs: set[str] = set()
    for assertion in assertions["scenarios"]:
        for item in assertion.get("inputs", []):
            if item.get("concept") not in mock_ids:
                continue
            seen_mock_inputs.add(item["concept"])
            provenance = item.get("mock_provenance", {})
            if (
                item.get("source") != "demo_mock"
                or provenance.get("label") != "DEMO_ONLY_MOCK"
                or provenance.get("production_evidence") is not False
            ):
                errors.append(
                    f"assertion {assertion.get('assertion_id')} has unmarked Demo mock {item['concept']}"
                )
    if seen_mock_inputs != set(mock_ids):
        errors.append(f"mock coverage drift: unused={sorted(set(mock_ids) - seen_mock_inputs)}")

    if errors:
        print(f"FAIL: {len(errors)} error(s)")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        f"PASS: {len(configured)} touchpoints -> "
        f"{len({item['demo_campaign_id'] for item in contract['campaign_adapter']['mappings']})} Demo campaigns, "
        f"{len(required)} consistency outcomes, {len(mock_ids)} mock contracts"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
