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
from .orchestrator import ChatClient, LocalLLMOrchestrator
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
    "ChatClient",
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
