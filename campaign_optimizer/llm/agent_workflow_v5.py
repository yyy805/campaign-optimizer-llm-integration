"""Canonical, local contracts for the three heterogeneous agent roles."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from campaign_optimizer.contracts.exchange import validate_workflow_exchange
from campaign_optimizer.contracts.validation import validate_contract_object


ROOT = Path(__file__).parent
PROMPTS = ROOT / "prompts"
SCHEMAS = ROOT.parent / "schemas"
CONFIG = ROOT / "agent_roles.v5.json"
MAX_REVISION_ROUNDS = 5
ROLES = frozenset({"triage", "executor", "reviewer"})
PROFILES = frozenset({"baseline", "production_candidate", "experiment", "stress_only"})


class WorkflowAction(str, Enum):
    FINAL = "FINAL"
    REVISE = "REVISE"
    FALLBACK = "FALLBACK"


@dataclass(frozen=True)
class RoleConfiguration:
    model_aliases: Mapping[str, str]
    prompt_versions: Mapping[str, str]
    prompt_hashes: Mapping[str, str]
    output_contract_prompt_version: str
    revision_profiles: Mapping[str, int]


def _read_json(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_schema(name: str) -> Mapping[str, Any]:
    return _read_json(SCHEMAS / name)


def load_role_configuration(path: Path = CONFIG) -> RoleConfiguration:
    raw = _read_json(path)
    if raw.get("schema_version") != "1.0" or raw.get("configuration_version") != "agent_roles_v5":
        raise ValueError("invalid agent role configuration")
    aliases, artifacts, expected_hashes = raw.get("model_aliases"), raw.get("prompt_artifacts"), raw.get("expected_prompt_hashes")
    if not all(isinstance(value, Mapping) for value in (aliases, artifacts, expected_hashes)):
        raise ValueError("role configuration mappings are required")
    if set(aliases) != ROLES or set(artifacts) != ROLES or set(expected_hashes) != ROLES:
        raise ValueError("all three roles require aliases, prompts, and pinned hashes")
    if any(not isinstance(alias, str) or not alias.strip() for alias in aliases.values()) or len(set(aliases.values())) != len(ROLES):
        raise ValueError("model aliases must be nonempty and heterogeneous")
    versions: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for role, filename in artifacts.items():
        if not isinstance(filename, str):
            raise ValueError("prompt artifact name must be a string")
        prompt_path = (PROMPTS / filename).resolve()
        if prompt_path.parent != PROMPTS.resolve() or not prompt_path.is_file():
            raise ValueError("prompt artifact must remain in the local prompts directory")
        actual_hash = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
        expected_hash = expected_hashes[role]
        if not isinstance(expected_hash, str) or actual_hash != expected_hash.lower():
            raise ValueError("prompt artifact hash does not match the approved configuration")
        versions[role], hashes[role] = prompt_path.stem, actual_hash
    profiles = raw.get("revision_profiles")
    if not isinstance(profiles, Mapping) or set(profiles) != PROFILES:
        raise ValueError("all approved revision profiles are required")
    if any(type(value) is not int or not 0 <= value <= MAX_REVISION_ROUNDS for value in profiles.values()):
        raise ValueError("revision profiles must stay inside the five-round hard cap")
    return RoleConfiguration(dict(aliases), versions, hashes, raw["output_contract_prompt_version"], dict(profiles))


def validate_triage_decision(value: Mapping[str, Any]) -> None:
    Draft202012Validator(_read_schema("triage_decision_v2.schema.json")).validate(dict(value))


@dataclass(frozen=True)
class ReviewerPacket:
    candidate_id: str
    task_manifest: Mapping[str, Any]
    trusted_context: Mapping[str, Any]
    candidate_output: Mapping[str, Any]
    allowed_source_ids: frozenset[str]
    packet_digest: str

    @classmethod
    def from_validated_exchange(
        cls, *, request: Mapping[str, Any], plan: Mapping[str, Any], review: Mapping[str, Any], context: Mapping[str, Any], candidate_output: Mapping[str, Any], resolved_intent: str, candidate_id: str, retry_count: int, config: RoleConfiguration
    ) -> "ReviewerPacket":
        validate_contract_object("llm_request", dict(request))
        validate_contract_object("llm_context", dict(context))
        validate_workflow_exchange(dict(request), dict(plan), dict(review), dict(context), dict(candidate_output))
        if request["expected_versions"]["prompt_version"] != config.output_contract_prompt_version:
            raise ValueError("request output contract is not bound to the approved role configuration")
        if candidate_output.get("status") != "OK" or candidate_output.get("fallback_used") is not False:
            raise ValueError("Reviewer receives only guard-valid OK candidates")
        if candidate_output.get("intent") != resolved_intent or candidate_output.get("retry_count") != retry_count:
            raise ValueError("candidate does not match the backend task state")
        if not candidate_id.startswith("candidate_") or not candidate_id[10:]:
            raise ValueError("candidate_id must be server generated")
        task = {"intent": resolved_intent, "expected_versions": copy.deepcopy(request["expected_versions"]), "retry_count": retry_count}
        trusted = {
            "plan": copy.deepcopy(context["plan_context"]), "review": copy.deepcopy(context["review_context"]), "public_rules": copy.deepcopy(context["public_rule_context"]),
            "allowed_plan_item_ids": list(context["allowed_plan_item_ids"]), "allowed_fact_ids": list(context["allowed_fact_ids"]), "allowed_rule_ids": list(context["allowed_rule_ids"]),
        }
        allowed = frozenset(set(context["allowed_plan_item_ids"]) | set(context["allowed_fact_ids"]) | set(context["allowed_rule_ids"]) | {item["review_item_id"] for item in review["items"]})
        digest_payload = {"candidate_id": candidate_id, "task": task, "trusted": trusted, "candidate_output": candidate_output, "reviewer_prompt_hash": config.prompt_hashes["reviewer"]}
        packet_digest = hashlib.sha256(json.dumps(digest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        return cls(candidate_id, task, trusted, copy.deepcopy(dict(candidate_output)), allowed, packet_digest)

    def as_model_input(self) -> Mapping[str, Any]:
        return copy.deepcopy({"server_task_manifest": self.task_manifest, "trusted_context_snapshot": self.trusted_context, "candidate_id": self.candidate_id, "candidate_output": self.candidate_output, "packet_digest": self.packet_digest})


def validate_reviewer_decision(value: Mapping[str, Any], *, packet: ReviewerPacket) -> None:
    Draft202012Validator(_read_schema("reviewer_decision_v3.schema.json")).validate(dict(value))
    if value["candidate_id"] != packet.candidate_id or value["packet_digest"] != packet.packet_digest:
        raise ValueError("review decision is not bound to this server-issued reviewer packet")
    if not set(value["evidence_source_ids"]).issubset(packet.allowed_source_ids):
        raise ValueError("reviewer evidence is outside the trusted snapshot")
    claim_ids = {claim["claim_id"] for claim in packet.candidate_output["claims"]}
    for action in value["revision_actions"]:
        if action["source_id"] not in packet.allowed_source_ids:
            raise ValueError("revision action source is outside the trusted snapshot")
        if action["operation"] == "ADD_REQUIRED_LIMITATION":
            if action["target_claim_id"] is not None:
                raise ValueError("adding a limitation cannot target an existing claim")
        elif action["target_claim_id"] not in claim_ids:
            raise ValueError("remove/correct action must target a claim in this candidate")


def next_action(value: Mapping[str, Any], *, packet: ReviewerPacket, revision_rounds: int, max_revision_rounds: int) -> WorkflowAction:
    if not 0 <= max_revision_rounds <= MAX_REVISION_ROUNDS or not 0 <= revision_rounds <= max_revision_rounds:
        raise ValueError("revision rounds are outside the hard five-round limit")
    validate_reviewer_decision(value, packet=packet)
    if value["decision"] == "PASS":
        return WorkflowAction.FINAL
    return WorkflowAction.REVISE if value["decision"] == "REVISE" and revision_rounds < max_revision_rounds else WorkflowAction.FALLBACK


def max_role_calls(*, max_revision_rounds: int) -> int:
    if not 0 <= max_revision_rounds <= MAX_REVISION_ROUNDS:
        raise ValueError("max_revision_rounds must be between 0 and 5")
    return 2 * (max_revision_rounds + 1)


def max_provider_calls_with_repairs(*, max_revision_rounds: int) -> int:
    return max_role_calls(max_revision_rounds=max_revision_rounds) + max_revision_rounds + 1
