"""Provider adapters for the local-first LLM integration."""

from .qwen_client import (
    FALLBACK_MESSAGE,
    QwenClient,
    QwenClientError,
    QwenConfig,
    QwenErrorCode,
    QwenResponse,
    QwenUsage,
    SyncTransport,
    TransportRequest,
    TransportResponse,
    UrllibTransport,
)
from .retriever import (
    BailianKnowledgeRetriever,
    LocalRuleRetriever,
    RetrievalError,
    RetrievalErrorCode,
    RetrievalResult,
    Retriever,
)
from .intent_policy import (
    EXPLANATION_INTENTS,
    HybridIntentPolicy,
    IntentDecision,
    RouterClassification,
    RouterClassifier,
)
from .orchestration_result import AttemptMetadata, OrchestrationResult
from .orchestrator import ChatClient, LocalLLMOrchestrator
from .session_store import (
    InMemorySessionStore,
    SessionBinding,
    SessionContext,
    SessionSnapshot,
    SessionStore,
)
from .output_guard import OutputGuard
from .prompt_builder import PromptBuilder
from .request_builder import (
    EXPLAIN_INTENTS,
    REFUSAL_INTENTS,
    SUPPORTED_INTENTS,
    LLMVersions,
    RequestArtifacts,
    RequestBuilder,
    trim_chat_history,
)

__all__ = [
    "FALLBACK_MESSAGE",
    "QwenClient",
    "QwenClientError",
    "QwenConfig",
    "QwenErrorCode",
    "QwenResponse",
    "QwenUsage",
    "SyncTransport",
    "TransportRequest",
    "TransportResponse",
    "UrllibTransport",
    "BailianKnowledgeRetriever",
    "LocalRuleRetriever",
    "RetrievalError",
    "RetrievalErrorCode",
    "RetrievalResult",
    "Retriever",
    "EXPLANATION_INTENTS",
    "HybridIntentPolicy",
    "IntentDecision",
    "RouterClassification",
    "RouterClassifier",
    "AttemptMetadata",
    "OrchestrationResult",
    "ChatClient",
    "InMemorySessionStore",
    "SessionBinding",
    "SessionContext",
    "SessionSnapshot",
    "SessionStore",
    "SessionStoreError",
    "LocalLLMOrchestrator",
    "OutputGuard",
    "PromptBuilder",
    "EXPLAIN_INTENTS",
    "REFUSAL_INTENTS",
    "SUPPORTED_INTENTS",
    "LLMVersions",
    "RequestArtifacts",
    "RequestBuilder",
    "trim_chat_history",
]
