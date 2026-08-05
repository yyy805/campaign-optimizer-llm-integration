"""Deterministic, fail-closed retrieval of authoritative public rule projections."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft7Validator, FormatChecker

from campaign_optimizer.contracts.authority import latest_rule_version, public_rule_from_card
from campaign_optimizer.contracts.validation import ContractValidationError

RULE_ID = re.compile(r"^R[0-9]+$")
RULES_DIR = Path(__file__).parent.parent / "ontology" / "rules"
RULE_SCHEMA = Path(__file__).parent.parent / "ontology" / "schemas" / "rule.schema.json"
ExpectedVersion = str | Mapping[str, str]
RuleLoader = Callable[[Path], Any]


class RetrievalErrorCode(str, Enum):
    INVALID_REQUEST = "INVALID_REQUEST"
    EMPTY_REQUEST = "EMPTY_REQUEST"
    DUPLICATE_ID = "DUPLICATE_ID"
    UNKNOWN_RULE = "UNKNOWN_RULE"
    RETIRED_RULE = "RETIRED_RULE"
    INACTIVE_RULE = "INACTIVE_RULE"
    VERSION_MISMATCH = "VERSION_MISMATCH"
    INVALID_RULE = "INVALID_RULE"


class RetrievalError(RuntimeError):
    """Stable failure that never exposes rule-root filesystem details."""

    def __init__(self, code: RetrievalErrorCode, *, rule_id: str | None = None) -> None:
        self.code = code
        self.rule_id = rule_id
        super().__init__(f"{code.value}: rule retrieval failed")

    def as_metadata(self) -> dict[str, str | None]:
        return {"error_code": self.code.value, "rule_id": self.rule_id}


@dataclass(frozen=True)
class RetrievalResult:
    rule_id: str
    document_id: str
    version: str
    source: str
    retrieval_method: str
    content: str


class Retriever(Protocol):
    def retrieve(
        self,
        rule_ids: Sequence[str],
        query: str,
        expected_version: ExpectedVersion,
    ) -> tuple[RetrievalResult, ...]: ...


class BailianKnowledgeRetriever(Retriever, Protocol):
    """Type boundary only; no network or Model Studio adapter exists in L3."""


class LocalRuleRetriever:
    """Exact-ID lookup against Git-owned rule cards; query cannot expand scope."""

    def __init__(
        self,
        *,
        rules_dir: Path | None = None,
        loader: RuleLoader | None = None,
    ) -> None:
        self._rules_dir = Path(rules_dir) if rules_dir is not None else RULES_DIR
        self._loader = loader or _load_json
        self._validator = Draft7Validator(
            _load_json(RULE_SCHEMA), format_checker=FormatChecker()
        )

    @staticmethod
    def default_rules_dir() -> Path:
        return RULES_DIR

    def retrieve(
        self,
        rule_ids: Sequence[str],
        query: str,
        expected_version: ExpectedVersion,
    ) -> tuple[RetrievalResult, ...]:
        ids = _validate_request(rule_ids, query)
        versions = _normalize_expected_versions(ids, expected_version)
        results: list[RetrievalResult] = []
        for rule_id in ids:
            card = self._load_card_version(rule_id, versions[rule_id])
            version = latest_rule_version(card)
            status = card["status"]
            if status == "RETIRED":
                raise RetrievalError(RetrievalErrorCode.RETIRED_RULE, rule_id=rule_id)
            if status != "ACTIVE":
                raise RetrievalError(RetrievalErrorCode.INACTIVE_RULE, rule_id=rule_id)
            try:
                public = public_rule_from_card(card)
            except (ContractValidationError, KeyError, TypeError, ValueError) as exc:
                raise RetrievalError(RetrievalErrorCode.INVALID_RULE, rule_id=rule_id) from exc
            results.append(
                RetrievalResult(
                    rule_id=rule_id,
                    document_id=f"rule:{rule_id}@{version}",
                    version=version,
                    source="authoritative_rule_projection",
                    retrieval_method="exact_rule_id",
                    content=json.dumps(
                        public,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
            )
        return tuple(results)

    def _load_card(self, rule_id: str) -> dict[str, Any]:
        try:
            value = self._loader(self._rules_dir / f"{rule_id}.json")
        except FileNotFoundError as exc:
            raise RetrievalError(RetrievalErrorCode.UNKNOWN_RULE, rule_id=rule_id) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise RetrievalError(RetrievalErrorCode.INVALID_RULE, rule_id=rule_id) from exc
        if not isinstance(value, dict) or value.get("rule_id") != rule_id:
            raise RetrievalError(RetrievalErrorCode.INVALID_RULE, rule_id=rule_id)
        if any(self._validator.iter_errors(value)):
            raise RetrievalError(RetrievalErrorCode.INVALID_RULE, rule_id=rule_id)
        return value

    def _load_card_version(self, rule_id: str, expected_version: str) -> dict[str, Any]:
        current = self._load_card(rule_id)
        if latest_rule_version(current) == expected_version:
            return current
        return self._load_historical_card(rule_id, expected_version)

    def _load_historical_card(self, rule_id: str, expected_version: str) -> dict[str, Any]:
        history_dir = self._rules_dir.parent / 'history' / 'rules'
        for path in sorted(history_dir.glob(f'{rule_id}.*.json')):
            try:
                candidate = self._loader(path)
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(candidate, dict) or candidate.get('rule_id') != rule_id:
                continue
            if any(self._validator.iter_errors(candidate)):
                continue
            try:
                version = latest_rule_version(candidate)
            except ContractValidationError:
                continue
            if version == expected_version:
                return candidate
        raise RetrievalError(RetrievalErrorCode.VERSION_MISMATCH, rule_id=rule_id)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_request(rule_ids: Any, query: Any) -> tuple[str, ...]:
    if isinstance(rule_ids, (str, bytes)) or not isinstance(rule_ids, Sequence):
        raise RetrievalError(RetrievalErrorCode.INVALID_REQUEST)
    ids = tuple(rule_ids)
    if not ids:
        raise RetrievalError(RetrievalErrorCode.EMPTY_REQUEST)
    if not isinstance(query, str):
        raise RetrievalError(RetrievalErrorCode.INVALID_REQUEST)
    if any(not isinstance(rule_id, str) or RULE_ID.fullmatch(rule_id) is None for rule_id in ids):
        raise RetrievalError(RetrievalErrorCode.INVALID_REQUEST)
    if len(set(ids)) != len(ids):
        raise RetrievalError(RetrievalErrorCode.DUPLICATE_ID)
    return ids


def _normalize_expected_versions(
    rule_ids: tuple[str, ...], expected_version: ExpectedVersion
) -> dict[str, str]:
    if isinstance(expected_version, str):
        if len(rule_ids) != 1 or not expected_version:
            raise RetrievalError(RetrievalErrorCode.INVALID_REQUEST)
        return {rule_ids[0]: expected_version}
    if not isinstance(expected_version, Mapping):
        raise RetrievalError(RetrievalErrorCode.INVALID_REQUEST)
    if set(expected_version) != set(rule_ids):
        raise RetrievalError(RetrievalErrorCode.INVALID_REQUEST)
    versions = dict(expected_version)
    if any(not isinstance(value, str) or not value for value in versions.values()):
        raise RetrievalError(RetrievalErrorCode.INVALID_REQUEST)
    return versions
