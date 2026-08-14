from __future__ import annotations

from datetime import date
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


EntityType = Literal["campaign"]
PlanAction = Literal["increase_budget", "decrease_budget", "keep_budget"]
Verdict = Literal["SUPPORT", "CONFLICT", "NOT_APPLICABLE", "UNVERIFIED", "INSUFFICIENT_EVIDENCE"]


class PlanPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["next_14_days"]
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def ordered(self) -> "PlanPeriod":
        if (self.end_date - self.start_date).days != 13:
            raise ValueError("next_14_days must cover exactly 14 inclusive calendar days")
        return self


class PlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    plan_item_id: str = Field(pattern=r"^plan_item_[A-Za-z0-9_-]+$")
    entity_type: EntityType
    entity_id: str = Field(min_length=1, max_length=200)
    action: PlanAction
    delta_pct: float = Field(strict=True, ge=-100, le=1000)
    current_budget: float = Field(strict=True, ge=0)
    recommended_budget: float = Field(strict=True, ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")

    @model_validator(mode="after")
    def budget_math(self) -> "PlanItem":
        expected = self.current_budget * (1 + self.delta_pct / 100)
        if not math.isfinite(expected):
            raise ValueError("calculated budget is not finite")
        tolerance = max(0.01, abs(expected) * 1e-6)
        if abs(self.recommended_budget - expected) > tolerance:
            raise ValueError("recommended_budget is inconsistent with current_budget and delta_pct")
        if self.action == "increase_budget" and self.delta_pct <= 0:
            raise ValueError("increase_budget requires positive delta_pct")
        if self.action == "increase_budget" and self.recommended_budget <= self.current_budget:
            raise ValueError("increase_budget must increase the budget")
        if self.action == "decrease_budget" and self.delta_pct >= 0:
            raise ValueError("decrease_budget requires negative delta_pct")
        if self.action == "keep_budget" and self.delta_pct != 0:
            raise ValueError("keep_budget requires zero delta_pct")
        return self


class PlanFact(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    fact_id: str = Field(pattern=r"^(decision|review)_fact_[A-Za-z0-9_-]+$")
    plan_item_id: str = Field(pattern=r"^plan_item_[A-Za-z0-9_-]+$")
    entity_type: EntityType
    entity_id: str = Field(min_length=1, max_length=200)
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    value: float | str | bool | None = Field(default=None)
    baseline_value: float | None = Field(default=None, strict=True)
    baseline_source: str | None = Field(default=None, min_length=1, max_length=100)
    baseline_period: str | None = Field(default=None, min_length=1, max_length=64)
    unit: Literal["ratio", "percentage", "currency", "count", "boolean", "text"]
    period: str = Field(min_length=1, max_length=64)
    source: str = Field(min_length=1, max_length=100)
    scope: Literal["public_output", "ontology_review"]

    @model_validator(mode="after")
    def baseline_provenance(self) -> "PlanFact":
        supplied = (self.baseline_value is not None, self.baseline_source is not None, self.baseline_period is not None)
        if any(supplied) and not all(supplied):
            raise ValueError("baseline_value, baseline_source, and baseline_period must be supplied together")
        if isinstance(self.value, str) and len(self.value) > 500:
            raise ValueError("fact string value exceeds 500 characters")
        return self


class FinalPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    plan_id: str = Field(pattern=r"^plan_[A-Za-z0-9_-]+$")
    source: Literal["DEMO_OPTIMIZER_STUB", "SMALL_MODEL_CHAIN"]
    source_version: str = Field(min_length=1, max_length=64)
    is_optimized: Literal[True]
    period: PlanPeriod
    items: list[PlanItem] = Field(min_length=1, max_length=100)
    decision_evidence: list[PlanFact] = Field(default_factory=list, max_length=500)
    review_evidence: list[PlanFact] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def bindings(self) -> "FinalPlan":
        items = {item.plan_item_id: item for item in self.items}
        if len(items) != len(self.items):
            raise ValueError("plan_item_id values must be unique")
        fact_ids: set[str] = set()
        concept_keys: set[tuple[str, str, str]] = set()
        for expected_scope, prefix, facts in (
            ("public_output", "decision_fact_", self.decision_evidence),
            ("ontology_review", "review_fact_", self.review_evidence),
        ):
            for fact in facts:
                if fact.fact_id in fact_ids:
                    raise ValueError("fact_id values must be unique")
                fact_ids.add(fact.fact_id)
                concept_key = (fact.plan_item_id, fact.name, fact.scope)
                if concept_key in concept_keys:
                    raise ValueError("a concept may appear only once per plan item and scope")
                concept_keys.add(concept_key)
                item = items.get(fact.plan_item_id)
                if item is None:
                    raise ValueError("fact references an unknown plan_item_id")
                if fact.scope != expected_scope or not fact.fact_id.startswith(prefix):
                    raise ValueError("fact scope does not match its collection")
                if fact.entity_type != item.entity_type or fact.entity_id != item.entity_id:
                    raise ValueError("fact entity does not match its plan item")
        return self


class OntologyReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_item_id: str = Field(pattern=r"^review_item_[A-Za-z0-9_-]+$")
    plan_item_id: str = Field(pattern=r"^plan_item_[A-Za-z0-9_-]+$")
    verdict: Verdict
    rule_id: str | None = Field(default=None, pattern=r"^R[0-9]+$")
    rule_version: str | None = Field(default=None, min_length=1, max_length=64)
    base_confidence: float | None = Field(default=None, ge=0, le=1)
    runtime_confidence: float | None = Field(default=None, ge=0, le=1)
    matched_fact_ids: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    missing_rule_parameters: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def verdict_invariants(self) -> "OntologyReviewItem":
        if self.verdict == "UNVERIFIED":
            if any(value is not None for value in (self.rule_id, self.rule_version, self.base_confidence, self.runtime_confidence)):
                raise ValueError("UNVERIFIED cannot reference a rule or confidence")
            if self.matched_fact_ids or self.missing_evidence or self.missing_rule_parameters:
                raise ValueError("UNVERIFIED cannot carry rule evidence")
        else:
            if self.rule_id is None or self.rule_version is None:
                raise ValueError("rule-bearing verdict requires rule identity")
        if self.verdict in {"SUPPORT", "CONFLICT", "NOT_APPLICABLE"}:
            if self.base_confidence is None or self.runtime_confidence is None or not self.matched_fact_ids:
                raise ValueError("evaluated verdict requires confidence and matched facts")
            if self.missing_evidence or self.missing_rule_parameters:
                raise ValueError("evaluated verdict cannot report missing evidence")
        if self.verdict == "INSUFFICIENT_EVIDENCE" and not (self.missing_evidence or self.missing_rule_parameters):
            raise ValueError("INSUFFICIENT_EVIDENCE requires a missing item")
        return self


class OntologyReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    review_id: str = Field(pattern=r"^review_[A-Za-z0-9_-]+$")
    plan_id: str = Field(pattern=r"^plan_[A-Za-z0-9_-]+$")
    source: Literal["ONTOLOGY_ENGINE"] = "ONTOLOGY_ENGINE"
    ontology_version: str = Field(min_length=1, max_length=64)
    release_identity: dict[str, str]
    confidence_state_version: str = Field(min_length=1, max_length=64)
    is_synthetic: bool
    overall_verdict: Verdict
    items: list[OntologyReviewItem] = Field(min_length=1)
