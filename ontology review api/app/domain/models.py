from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _bounded_json(
    value: Any,
    *,
    max_depth: int = 5,
    max_items: int = 100,
    max_string_length: int = 2_000,
    max_total_nodes: int = 10_000,
) -> Any:
    total_nodes = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal total_nodes
        total_nodes += 1
        if total_nodes > max_total_nodes:
            raise ValueError("nested JSON exceeds maximum total size")
        if depth > max_depth:
            raise ValueError("nested JSON exceeds maximum depth")
        if isinstance(item, float) and (item != item or item in {float("inf"), float("-inf")}):
            raise ValueError("nested JSON contains a non-finite number")
        if isinstance(item, dict):
            if len(item) > max_items:
                raise ValueError("nested JSON has too many keys")
            for key, child in item.items():
                if not isinstance(key, str) or len(key) > 100:
                    raise ValueError("nested JSON has an invalid key")
                visit(child, depth + 1)
        elif isinstance(item, list):
            if len(item) > max_items:
                raise ValueError("nested JSON has too many items")
            for child in item:
                visit(child, depth + 1)
        elif isinstance(item, str) and len(item) > max_string_length:
            raise ValueError("nested JSON string exceeds maximum length")
        elif item is not None and not isinstance(item, (str, int, float, bool)):
            raise ValueError("nested JSON contains an unsupported value")
    visit(value, 0)
    return value


class Outcome(StrEnum):
    MATCH = "MATCH"
    CONFLICT = "CONFLICT"
    NO_COVERAGE = "NO_COVERAGE"


class Disposition(StrEnum):
    AUTO_EXECUTE = "AUTO_EXECUTE"
    REVIEW = "REVIEW"
    MANUAL_CONFIRM = "MANUAL_CONFIRM"
    BLOCKED = "BLOCKED"
    NO_ACTION = "NO_ACTION"


class ReviewStatus(StrEnum):
    PENDING_USER_REVIEW = "PENDING_USER_REVIEW"


class EntityRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grain: Literal["campaign", "touchpoint", "ad_group", "platform"]
    id: str = Field(min_length=1, max_length=255)


class ConceptInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    concept: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    value: float | int | bool | str
    baseline: float | None = None
    source: str | None = Field(default=None, max_length=100)


class ProposedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = Field(min_length=1, max_length=100)
    param: dict[str, Any] = Field(default_factory=dict)

    @field_validator("param")
    @classmethod
    def bounded_param(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_json(value)


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str = Field(min_length=1, max_length=255)
    document_id: str | None = Field(default=None, max_length=255)
    location: str | None = Field(default=None, max_length=500)
    knowledge_version: str | None = Field(default=None, max_length=100)
    summary: str | None = Field(default=None, max_length=1000)


class ReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=100)
    entity: EntityRef
    candidate_rules: list[str] = Field(default_factory=list, max_length=7)
    inputs: list[ConceptInput] = Field(default_factory=list, max_length=100)
    proposed_action: ProposedAction | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=50)
    expected_ontology_version: str | None = Field(default=None, min_length=1, max_length=100)
    as_of: date | None = None
    context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("context")
    @classmethod
    def bounded_context(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_json(value)

    @model_validator(mode="after")
    def no_duplicates(self) -> "ReviewCreate":
        if len(self.candidate_rules) != len(set(self.candidate_rules)):
            raise ValueError("candidate_rules contains duplicate IDs")
        concepts = [item.concept for item in self.inputs]
        if len(concepts) != len(set(concepts)):
            raise ValueError("inputs contains duplicate concepts")
        return self


class RuleEvaluation(BaseModel):
    rule_id: str
    status: str
    matched: bool
    reason: str
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    suppressed_by: str | None = None


class GuardrailEvaluation(BaseModel):
    guardrail_id: str
    applicable: bool
    passed: bool | None
    message: str | None = None


class ActionResult(BaseModel):
    type: str
    param: dict[str, Any]


class ReviewResponse(BaseModel):
    review_id: str
    schema_version: str = "review-v1"
    tenant: str
    client_id: str
    entity: EntityRef
    original_request: dict[str, Any]
    outcome: Outcome
    disposition: Disposition
    reason: str
    matched_rules: list[str]
    winner_rule: str | None
    suppressed_rules: list[dict[str, str]]
    action: ActionResult | None
    rule_evaluations: list[RuleEvaluation]
    guardrail_evaluations: list[GuardrailEvaluation]
    evidence_refs: list[EvidenceRef]
    evidence_status: str
    ontology_version: str
    ontology_checksum: str
    status: ReviewStatus
    principal_id: str
    request_id: str
    record_version: int
    created_at: datetime


class ReviewList(BaseModel):
    items: list[ReviewResponse]
    page: int
    page_size: int
    total: int


class OntologyVersionResponse(BaseModel):
    version: str
    checksum: str
    concepts: list[str]
    rules: dict[str, str]
    guardrails: list[str]
    clients: list[str]
