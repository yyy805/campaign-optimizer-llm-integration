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
]
