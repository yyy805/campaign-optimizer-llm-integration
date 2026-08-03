"""Local-first orchestration for explanation requests."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from campaign_optimizer.contracts.exchange import validate_workflow_exchange
from campaign_optimizer.contracts.validation import FIXED_NON_OK_ANSWERS, ContractValidationError

from .intent_policy import HybridIntentPolicy, IntentDecision
from .orchestration_result import AttemptMetadata, OrchestrationResult
from .output_guard import OutputGuard
from .prompt_builder import PromptBuilder
from .qwen_client import QwenClientError, QwenResponse
from .request_builder import REFUSAL_INTENTS, RequestBuilder
from .session_store import InMemorySessionStore, SessionBinding, SessionContext, SessionStore, SessionStoreError

_INITIAL_QUESTION = "Explain the validated ontology review."


class ChatClient(Protocol):
    def chat(self, messages: Sequence[Mapping[str, str]], *, parameters: Mapping[str, Any] | None = None) -> QwenResponse: ...


class LocalLLMOrchestrator:
    """Backend-routed orchestrator that never returns unguarded model text."""

    def __init__(self, request_builder: RequestBuilder, prompt_builder: PromptBuilder,
                 qwen_client: ChatClient | None = None, *,
                 client_factory: Callable[[], ChatClient] | None = None,
                 output_guard: OutputGuard | None = None,
                 intent_policy: HybridIntentPolicy | None = None,
                 session_store: SessionStore | None = None,
                 provider_name: str = "qwen") -> None:
        if (qwen_client is None) == (client_factory is None):
            raise ValueError("provide exactly one qwen_client or client_factory")
        self._request_builder = request_builder
        self._prompt_builder = prompt_builder
        self._qwen_client = qwen_client
        self._client_factory = client_factory
        self._output_guard = output_guard or OutputGuard()
        self._intent_policy = intent_policy or HybridIntentPolicy()
        self._session_store = session_store or InMemorySessionStore()
        self._provider_name = provider_name

    def run(self, plan: Mapping[str, Any], review: Mapping[str, Any], *,
            question: str, session_context: SessionContext) -> OrchestrationResult:
        """Public chat entry point; mode, intent, and history are never caller-owned."""
        decision = self._intent_policy.resolve_chat(question)
        return self._run(plan, review, mode="chat", question=question,
                         decision=decision, session_context=session_context)

    def render_initial(self, plan: Mapping[str, Any], review: Mapping[str, Any]) -> OrchestrationResult:
        """Backend initial-render entry point with a fixed server-owned question."""
        decision = IntentDecision("EXPLAIN_REVIEW", "backend_fixed", 1.0)
        return self._run(plan, review, mode="initial_render", question=_INITIAL_QUESTION,
                         decision=decision, session_context=None)

    def _run(self, plan: Mapping[str, Any], review: Mapping[str, Any], *, mode: str,
             question: str, decision: IntentDecision,
             session_context: SessionContext | None) -> OrchestrationResult:
        context_id = self._request_builder.context_id_for(plan, review)
        binding = self._binding(plan, review, context_id=context_id, mode=mode, session=session_context)

        if decision.intent in REFUSAL_INTENTS:
            history = ()
            persistence_status = "NOT_WRITTEN" if binding else "NOT_APPLICABLE"
        else:
            try:
                history = () if binding is None else self._session_store.read(binding).history
                persistence_status = "READY" if binding else "NOT_APPLICABLE"
            except SessionStoreError:
                history = ()
                persistence_status = "READ_FAILED"

        artifacts = self._request_builder.build(plan, review, mode=mode, question=question,
                                                resolved_intent=decision.intent,
                                                server_chat_history=history)
        request, context = artifacts.request, artifacts.context
        plan_snapshot, review_snapshot = context["plan_context"], context["review_context"]

        if persistence_status == "READ_FAILED":
            return self._fallback(request, plan_snapshot, review_snapshot, context,
                                  mode=mode, decision=decision, binding=binding,
                                  question=question, fallback_reason="SESSION_READ_FAILED",
                                  attempts=(), persistence_status=persistence_status)

        if decision.intent in REFUSAL_INTENTS:
            output = _non_ok_output(request, "REFUSED", decision.intent)
            validate_workflow_exchange(request, plan_snapshot, review_snapshot, context, output)
            return self._result(output, mode=mode, decision=decision, binding=binding,
                                question=question, provider="none", attempts=(),
                                refusal_reason=decision.intent,
                                persistence_status=persistence_status)

        try:
            client = self._qwen_client if self._qwen_client is not None else self._client_factory()
        except QwenClientError as exc:
            return self._fallback(request, plan_snapshot, review_snapshot, context,
                                  mode=mode, decision=decision, binding=binding,
                                  question=question, fallback_reason=exc.code.value,
                                  attempts=(), persistence_status=persistence_status)

        messages = self._prompt_builder.build(request, context)
        attempts: list[AttemptMetadata] = []
        try:
            response = client.chat(messages, parameters=_parameters())
        except QwenClientError as exc:
            attempts.append(_error_attempt(exc, number=1, phase="initial", provider=self._provider_name,
                                           model=_client_model(client)))
            return self._fallback(request, plan_snapshot, review_snapshot, context,
                                  mode=mode, decision=decision, binding=binding,
                                  question=question, fallback_reason=exc.code.value,
                                  attempts=tuple(attempts), persistence_status=persistence_status)

        try:
            output = self._output_guard.validate(response.text, request=request, plan=plan_snapshot,
                                                 review=review_snapshot, context=context, retry_count=0)
            attempts.append(_response_attempt(response, number=1, phase="initial", status="OK",
                                              provider=self._provider_name))
            return self._result(output, mode=mode, decision=decision, binding=binding,
                                question=question, provider=self._provider_name,
                                attempts=tuple(attempts), persistence_status=persistence_status)
        except (ContractValidationError, KeyError, TypeError, ValueError):
            attempts.append(_response_attempt(response, number=1, phase="initial", status="CONTENT_INVALID",
                                              provider=self._provider_name))
            repair_messages = self._prompt_builder.repair(messages, response.text)

        try:
            repaired = client.chat(repair_messages, parameters=_parameters())
        except QwenClientError as exc:
            attempts.append(_error_attempt(exc, number=2, phase="repair", provider=self._provider_name,
                                           model=_client_model(client)))
            return self._fallback(request, plan_snapshot, review_snapshot, context,
                                  mode=mode, decision=decision, binding=binding,
                                  question=question, fallback_reason=exc.code.value,
                                  attempts=tuple(attempts), persistence_status=persistence_status)

        try:
            output = self._output_guard.validate(repaired.text, request=request, plan=plan_snapshot,
                                                 review=review_snapshot, context=context, retry_count=1)
            attempts.append(_response_attempt(repaired, number=2, phase="repair", status="OK",
                                              provider=self._provider_name))
            return self._result(output, mode=mode, decision=decision, binding=binding,
                                question=question, provider=self._provider_name,
                                attempts=tuple(attempts), persistence_status=persistence_status)
        except (ContractValidationError, KeyError, TypeError, ValueError):
            attempts.append(_response_attempt(repaired, number=2, phase="repair", status="CONTENT_INVALID",
                                              provider=self._provider_name))
            return self._fallback(request, plan_snapshot, review_snapshot, context,
                                  mode=mode, decision=decision, binding=binding,
                                  question=question, fallback_reason="CONTENT_VALIDATION_FAILED",
                                  attempts=tuple(attempts), persistence_status=persistence_status)

    def run_legacy(self, plan: Mapping[str, Any], review: Mapping[str, Any], *, mode: str,
                   question: str, untrusted_intent: str | None = None,
                   session_context: SessionContext | None = None) -> OrchestrationResult:
        """Deprecated chat-only shim; caller-owned initial mode and intent are rejected."""
        del untrusted_intent
        if mode != "chat" or session_context is None:
            raise ContractValidationError("legacy surface is chat-only")
        return self.run(plan, review, question=question, session_context=session_context)

    @staticmethod
    def _binding(plan: Mapping[str, Any], review: Mapping[str, Any], *, context_id: str,
                 mode: str, session: SessionContext | None) -> SessionBinding | None:
        if mode != "chat":
            return None
        if session is None:
            raise ContractValidationError("chat requires server SessionContext")
        return SessionBinding(session.tenant_id, session.user_id, session.session_id,
                              plan["plan_id"], review["review_id"], context_id)

    def _fallback(self, request: dict[str, Any], plan: dict[str, Any], review: dict[str, Any],
                  context: dict[str, Any], *, mode: str, decision: IntentDecision,
                  binding: SessionBinding | None, question: str, fallback_reason: str,
                  attempts: tuple[AttemptMetadata, ...], persistence_status: str) -> OrchestrationResult:
        output = _non_ok_output(request, "FALLBACK", "SYSTEM_FALLBACK")
        validate_workflow_exchange(request, plan, review, context, output)
        return self._result(output, mode=mode, decision=decision, binding=binding,
                            question=question, provider=self._provider_name, attempts=attempts,
                            fallback_reason=fallback_reason, persistence_status=persistence_status)

    def _result(self, output: dict[str, Any], *, mode: str, decision: IntentDecision,
                binding: SessionBinding | None, question: str, provider: str,
                attempts: tuple[AttemptMetadata, ...], fallback_reason: str | None = None,
                refusal_reason: str | None = None,
                persistence_status: str = "NOT_APPLICABLE") -> OrchestrationResult:
        if binding is not None and output["status"] == "OK":
            try:
                self._session_store.append_exchange(binding, question=question, answer=output["answer"])
                persistence_status = "WRITTEN"
            except SessionStoreError:
                persistence_status = "WRITE_FAILED"
        return OrchestrationResult(output=output, mode=mode, routed_intent=decision.intent,
                                   router_source=decision.source, router_confidence=decision.confidence,
                                   provider=provider, attempts=attempts,
                                   fallback_reason=fallback_reason, refusal_reason=refusal_reason,
                                   persistence_status=persistence_status)


def _response_attempt(response: Any, *, number: int, phase: str, status: str,
                      provider: str) -> AttemptMetadata:
    return AttemptMetadata(number, phase, status, provider,
                           model=getattr(response, "model", None),
                           request_id=getattr(response, "request_id", None),
                           latency_ms=getattr(response, "latency_ms", None),
                           usage=getattr(response, "usage", None),
                           finish_reason=getattr(response, "finish_reason", None))


def _error_attempt(exc: QwenClientError, *, number: int, phase: str, provider: str,
                   model: str | None) -> AttemptMetadata:
    return AttemptMetadata(number, phase, exc.code.value, provider, model=model,
                           request_id=exc.request_id, status_code=exc.status_code)


def _client_model(client: ChatClient | None) -> str | None:
    model = getattr(getattr(client, "config", None), "model", None)
    return model if isinstance(model, str) else None


def _non_ok_output(request: dict[str, Any], status: str, intent: str) -> dict[str, Any]:
    return {"schema_version": "1.0", **request["expected_versions"], "status": status,
            "intent": intent, "answer": FIXED_NON_OK_ANSWERS[intent], "claims": [],
            "facts_used": [], "rule_ids_used": [], "plan_item_ids_used": [],
            "limitations_included": False, "retry_count": 1 if status == "FALLBACK" else 0,
            "fallback_used": status == "FALLBACK"}


def _parameters() -> dict[str, Any]:
    return {"temperature": 0, "stream": False, "response_format": {"type": "json_object"}}