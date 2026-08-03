"""Local-first orchestration for explanation requests."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from campaign_optimizer.contracts.exchange import validate_workflow_exchange
from campaign_optimizer.contracts.validation import FIXED_NON_OK_ANSWERS, ContractValidationError

from .output_guard import OutputGuard
from .prompt_builder import PromptBuilder
from .qwen_client import QwenClientError, QwenResponse
from .request_builder import REFUSAL_INTENTS, RequestBuilder


class ChatClient(Protocol):
    def chat(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        parameters: Mapping[str, Any] | None = None,
    ) -> QwenResponse: ...


class LocalLLMOrchestrator:
    """Single local orchestration source; never returns unguarded model text."""

    def __init__(
        self,
        request_builder: RequestBuilder,
        prompt_builder: PromptBuilder,
        qwen_client: ChatClient | None = None,
        *,
        client_factory: Callable[[], ChatClient] | None = None,
        output_guard: OutputGuard | None = None,
    ) -> None:
        if (qwen_client is None) == (client_factory is None):
            raise ValueError("provide exactly one qwen_client or client_factory")
        self._request_builder = request_builder
        self._prompt_builder = prompt_builder
        self._qwen_client = qwen_client
        self._client_factory = client_factory
        self._output_guard = output_guard or OutputGuard()

    def run(
        self,
        plan: Mapping[str, Any],
        review: Mapping[str, Any],
        *,
        mode: str,
        question: str,
        intent: str,
        chat_history: Sequence[Mapping[str, str]] = (),
        history_context_id: str | None = None,
    ) -> dict[str, Any]:
        artifacts = self._request_builder.build(
            plan,
            review,
            mode=mode,
            question=question,
            intent=intent,
            chat_history=chat_history,
            history_context_id=history_context_id,
        )
        request = artifacts.request
        context = artifacts.context
        plan_snapshot = context["plan_context"]
        review_snapshot = context["review_context"]

        if intent in REFUSAL_INTENTS:
            output = _non_ok_output(request, "REFUSED", intent)
            validate_workflow_exchange(request, plan_snapshot, review_snapshot, context, output)
            return output

        try:
            client = (
                self._qwen_client
                if self._qwen_client is not None
                else self._client_factory()
            )
            messages = self._prompt_builder.build(request, context)
            response = client.chat(messages, parameters=_parameters())
        except QwenClientError:
            return self._fallback(request, plan_snapshot, review_snapshot, context)

        try:
            return self._output_guard.validate(
                response.text,
                request=request,
                plan=plan_snapshot,
                review=review_snapshot,
                context=context,
                retry_count=0,
            )
        except (ContractValidationError, KeyError, TypeError, ValueError):
            repair_messages = self._prompt_builder.repair(messages, response.text)

        try:
            repaired = client.chat(repair_messages, parameters=_parameters())
            return self._output_guard.validate(
                repaired.text,
                request=request,
                plan=plan_snapshot,
                review=review_snapshot,
                context=context,
                retry_count=1,
            )
        except (QwenClientError, ContractValidationError, KeyError, TypeError, ValueError):
            return self._fallback(request, plan_snapshot, review_snapshot, context)

    @staticmethod
    def _fallback(
        request: dict[str, Any],
        plan: dict[str, Any],
        review: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        output = _non_ok_output(request, "FALLBACK", "SYSTEM_FALLBACK")
        validate_workflow_exchange(request, plan, review, context, output)
        return output


def _non_ok_output(request: dict[str, Any], status: str, intent: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        **request["expected_versions"],
        "status": status,
        "intent": intent,
        "answer": FIXED_NON_OK_ANSWERS[intent],
        "claims": [],
        "facts_used": [],
        "rule_ids_used": [],
        "plan_item_ids_used": [],
        "limitations_included": False,
        "retry_count": 1 if status == "FALLBACK" else 0,
        "fallback_used": status == "FALLBACK",
    }


def _parameters() -> dict[str, Any]:
    return {
        "temperature": 0,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
