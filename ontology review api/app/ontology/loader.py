from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from jsonschema import Draft7Validator


class OntologyLoadError(RuntimeError):
    pass


@dataclass(frozen=True)
class OntologySnapshot:
    version: str
    checksum: str
    root: Path
    concepts: Mapping[str, Mapping[str, Any]]
    rules: Mapping[str, Mapping[str, Any]]
    guardrails: Mapping[str, Mapping[str, Any]]
    clients: Mapping[str, Mapping[str, Any]]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OntologyLoadError(f"cannot read valid JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise OntologyLoadError(f"expected JSON object: {path.name}")
    return value


def _load_cards(folder: Path, id_field: str) -> dict[str, dict[str, Any]]:
    cards: dict[str, dict[str, Any]] = {}
    for path in sorted(folder.glob("*.json"), key=lambda item: item.name):
        card = _read_json(path)
        card_id = card.get(id_field)
        if not isinstance(card_id, str) or not card_id:
            raise OntologyLoadError(f"{path.name} has no {id_field}")
        if card_id in cards:
            raise OntologyLoadError(f"duplicate {id_field}: {card_id}")
        if path.stem != card_id:
            raise OntologyLoadError(f"filename does not match {id_field}: {path.name}")
        cards[card_id] = card
    if not cards:
        raise OntologyLoadError(f"no cards found in {folder.name}")
    return cards


def _validate_schema(cards: dict[str, dict[str, Any]], schema: dict[str, Any], kind: str) -> None:
    validator = Draft7Validator(schema)
    errors: list[str] = []
    for card_id, card in cards.items():
        for error in validator.iter_errors(card):
            where = ".".join(str(part) for part in error.absolute_path)
            errors.append(f"{kind} {card_id}{'.' + where if where else ''}: {error.message}")
    if errors:
        raise OntologyLoadError("; ".join(errors[:10]))


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _freeze_cards(cards: dict[str, dict[str, Any]]) -> Mapping[str, Mapping[str, Any]]:
    return MappingProxyType({key: _deep_freeze(value) for key, value in cards.items()})


def load_ontology(root: Path) -> OntologySnapshot:
    root = root.resolve()
    required_dirs = ["concepts", "rules", "guardrails", "clients", "schemas"]
    if not root.is_dir() or any(not (root / name).is_dir() for name in required_dirs):
        raise OntologyLoadError(f"ontology package is incomplete: {root}")
    try:
        version = (root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise OntologyLoadError("VERSION is unavailable") from exc
    if not re.fullmatch(r"v[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?", version):
        raise OntologyLoadError("VERSION has an invalid format")

    concepts = _load_cards(root / "concepts", "concept_id")
    rules = _load_cards(root / "rules", "rule_id")
    guardrails = _load_cards(root / "guardrails", "guardrail_id")
    clients = _load_cards(root / "clients", "client_id")
    rule_schema = _read_json(root / "schemas" / "rule.schema.json")
    guardrail_schema = _read_json(root / "schemas" / "guardrail.schema.json")
    _validate_schema(rules, rule_schema, "rule")
    _validate_schema(guardrails, guardrail_schema, "guardrail")

    for concept_id, concept in concepts.items():
        for field in ("name_cn", "name_en", "definition", "unit", "granularity", "dimensions"):
            if field not in concept:
                raise OntologyLoadError(f"concept {concept_id} is missing {field}")
        value_range = concept.get("value_range")
        if not isinstance(value_range, list) or len(value_range) != 2:
            raise OntologyLoadError(f"concept {concept_id} has invalid value_range")
        lower, upper = value_range
        bounds = [bound for bound in value_range if bound is not None]
        if any(not isinstance(bound, (int, float, bool)) for bound in bounds):
            raise OntologyLoadError(f"concept {concept_id} has non-numeric value_range bounds")
        numeric_bounds = [float(bound) for bound in bounds]
        if any(not math.isfinite(bound) for bound in numeric_bounds):
            raise OntologyLoadError(f"concept {concept_id} has non-finite value_range bounds")
        if lower is not None and upper is not None and float(lower) > float(upper):
            raise OntologyLoadError(f"concept {concept_id} has reversed value_range bounds")

    if set(rules) != {f"R{number}" for number in range(1, 8)}:
        raise OntologyLoadError("rule package must contain exactly R1-R7")
    if set(guardrails) != {"G1", "G2"}:
        raise OntologyLoadError("guardrail package must contain exactly G1-G2")
    for rule_id, rule in rules.items():
        declared_inputs = {item.get("concept") for item in rule.get("match_inputs", [])}
        for item in rule.get("match_inputs", []):
            if item.get("concept") not in concepts:
                raise OntologyLoadError(f"{rule_id} references unknown concept {item.get('concept')}")
        for condition in rule.get("trigger_condition", {}).get("all", []):
            if condition.get("concept") not in concepts:
                raise OntologyLoadError(f"{rule_id} condition references unknown concept")
            if condition.get("concept") not in declared_inputs:
                raise OntologyLoadError(f"{rule_id} condition is absent from match_inputs")
    for guardrail_id, guardrail in guardrails.items():
        if guardrail["condition"]["concept"] not in concepts:
            raise OntologyLoadError(f"{guardrail_id} references unknown concept")
    for client_id, client in clients.items():
        if not isinstance(client.get("risk_tolerance"), dict):
            raise OntologyLoadError(f"{client_id} has no risk_tolerance")
        for field in ("max_auto_budget_change_pct", "max_auto_bid_change_pct"):
            value = client["risk_tolerance"].get(field)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 <= value <= 1
            ):
                raise OntologyLoadError(f"{client_id} has invalid {field}")
        if client.get("ontology_version_locked") != version:
            raise OntologyLoadError(f"{client_id} is not locked to {version}")

    digest = hashlib.sha256()
    included = [root / "VERSION"]
    for folder in required_dirs:
        included.extend(sorted((root / folder).glob("*.json"), key=lambda item: item.name))
    included.extend(sorted(
        (path for path in (root / "assertions").rglob("*") if path.is_file() and path.suffix in {".json", ".md", ".py"}),
        key=lambda item: item.relative_to(root).as_posix(),
    ))
    for path in sorted(included, key=lambda item: item.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")

    return OntologySnapshot(
        version=version,
        checksum=digest.hexdigest(),
        root=root,
        concepts=_freeze_cards(concepts),
        rules=_freeze_cards(rules),
        guardrails=_freeze_cards(guardrails),
        clients=_freeze_cards(clients),
    )
