"""Backend-owned construction of validated LLM requests and contexts."""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from campaign_optimizer.contracts.authority import RULES_DIR, validate_authoritative_review
from campaign_optimizer.contracts.validation import (
    ContractValidationError,
    validate_contract_bundle,
    validate_contract_object,
)

from .retriever import Retriever

EXPLAIN_INTENTS = frozenset({"EXPLAIN_PLAN", "EXPLAIN_REVIEW", "EXPLAIN_RULE"})
REFUSAL_INTENTS = frozenset(
    {"FORBIDDEN_MODEL_INTERNAL", "UNSUPPORTED_WHAT_IF", "OUT_OF_SCOPE"}
)
SUPPORTED_INTENTS = EXPLAIN_INTENTS | REFUSAL_INTENTS


@dataclass(frozen=True)
class LLMVersions:
    workflow_version: str = "local-python-1.0"
    prompt_version: str = "local-prompt-1.0"
    knowledge_base_version: str = "local-rules-1.0"

    def as_dict(self) -> dict[str, str]:
        return {
            "workflow_version": self.workflow_version,
            "prompt_version": self.prompt_version,
            "knowledge_base_version": self.knowledge_base_version,
        }


@dataclass(frozen=True)
class RequestArtifacts:
    request: dict[str, Any]
    context: dict[str, Any]


class RequestBuilder:
    """Derive every whitelist and version on the trusted backend boundary."""

    def __init__(
        self,
        retriever: Retriever,
        *,
        versions: LLMVersions | None = None,
        clock: Callable[[], datetime] | None = None,
        request_id_factory: Callable[[], str] | None = None,
        rules_dir: Path = RULES_DIR,
        max_history_messages: int = 10,
        max_history_chars: int = 8_000,
    ) -> None:
        self._retriever = retriever
        self._versions = versions or LLMVersions()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._request_id_factory = request_id_factory or (
            lambda: f"request_{uuid.uuid4().hex}"
        )
        self._rules_dir = rules_dir
        self._max_history_messages = max_history_messages
        self._max_history_chars = max_history_chars

    def build(
        self,
        plan: Mapping[str, Any],
        review: Mapping[str, Any],
        *,
        mode: str,
        question: str,
        intent: str,
        chat_history: Sequence[Mapping[str, str]] = (),
        history_context_id: str | None = None,
    ) -> RequestArtifacts:
        plan_snapshot = copy.deepcopy(dict(plan))
        review_snapshot = copy.deepcopy(dict(review))
        validate_contract_object("final_plan", plan_snapshot)
        validate_contract_object("ontology_review", review_snapshot)
        _validate_mode_intent(mode, intent)

        context_id = _context_id(plan_snapshot, review_snapshot)
        rule_versions = _rule_versions(review_snapshot)
        rule_ids = list(rule_versions)
        public_rules: list[dict[str, Any]] = []
        if rule_ids:
            results = self._retriever.retrieve(rule_ids, question, rule_versions)
            if [result.rule_id for result in results] != rule_ids:
                raise ContractValidationError(
                    "Retriever result IDs must exactly match requested rule IDs"
                )
            for result in results:
                try:
                    public_rule = json.loads(result.content)
                except (TypeError, json.JSONDecodeError) as exc:
                    raise ContractValidationError(
                        "Retriever returned a malformed public rule projection"
                    ) from exc
                if not isinstance(public_rule, dict):
                    raise ContractValidationError(
                        "Retriever returned a non-object public rule projection"
                    )
                public_rules.append(public_rule)

        created_at = self._clock()
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        context = {
            "schema_version": "1.0",
            "context_id": context_id,
            "context_created_at": created_at.isoformat(),
            "plan_context": plan_snapshot,
            "review_context": review_snapshot,
            "allowed_rule_ids": rule_ids,
            "allowed_plan_item_ids": [
                item["plan_item_id"] for item in plan_snapshot["items"]
            ],
            "allowed_fact_ids": [
                fact["fact_id"]
                for fact in (
                    plan_snapshot["decision_evidence"]
                    + plan_snapshot["review_evidence"]
                )
            ],
            "public_rule_context": public_rules,
        }
        validate_contract_bundle(plan_snapshot, review_snapshot, context)
        validate_authoritative_review(
            plan_snapshot, review_snapshot, context, rules_dir=self._rules_dir
        )

        history = []
        if mode == "chat" and history_context_id == context_id:
            history = trim_chat_history(
                chat_history,
                max_messages=self._max_history_messages,
                max_chars=self._max_history_chars,
            )
        request = {
            "schema_version": "1.0",
            "request_id": self._request_id_factory(),
            "mode": mode,
            "question": question,
            "context_id": context_id,
            "allowed_intents": [intent],
            "expected_versions": self._versions.as_dict(),
            "chat_history": history,
        }
        validate_contract_object("llm_request", request)
        return RequestArtifacts(request=request, context=context)


def trim_chat_history(
    history: Sequence[Mapping[str, str]],
    *,
    max_messages: int = 10,
    max_chars: int = 8_000,
) -> list[dict[str, str]]:
    """Keep newest valid messages within deterministic count and char budgets."""
    if isinstance(history, (str, bytes)) or not isinstance(history, Sequence):
        raise ContractValidationError("chat_history must be a sequence")
    if max_messages < 0 or max_chars < 0:
        raise ValueError("history limits cannot be negative")
    kept: list[dict[str, str]] = []
    remaining = max_chars
    for item in reversed(history):
        if len(kept) >= max_messages or remaining == 0:
            break
        if not isinstance(item, Mapping):
            raise ContractValidationError("chat_history item must be an object")
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            raise ContractValidationError("chat_history item is invalid")
        content = content[: min(2_000, remaining)]
        if not content:
            continue
        kept.append({"role": role, "content": content})
        remaining -= len(content)
    kept.reverse()
    return kept


def _validate_mode_intent(mode: str, intent: str) -> None:
    if mode not in {"initial_render", "chat"}:
        raise ContractValidationError("unsupported LLM request mode")
    if intent not in SUPPORTED_INTENTS:
        raise ContractValidationError("unsupported LLM intent")
    if mode == "initial_render" and intent != "EXPLAIN_REVIEW":
        raise ContractValidationError("initial_render only allows EXPLAIN_REVIEW")


def _rule_versions(review: dict[str, Any]) -> dict[str, str]:
    versions: dict[str, str] = {}
    for item in review["items"]:
        rule_id = item["rule_id"]
        if rule_id is None:
            continue
        version = item["rule_version"]
        previous = versions.setdefault(rule_id, version)
        if previous != version:
            raise ContractValidationError("one rule ID cannot use multiple versions")
    return versions


def _context_id(plan: dict[str, Any], review: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"plan": plan, "review": review},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"context_{hashlib.sha256(canonical).hexdigest()[:24]}"
