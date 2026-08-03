"""Backend-owned fail-closed intent routing for explanation requests."""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Protocol

from campaign_optimizer.contracts.validation import ContractValidationError

EXPLANATION_INTENTS = frozenset({"EXPLAIN_PLAN", "EXPLAIN_REVIEW", "EXPLAIN_RULE"})


@dataclass(frozen=True)
class RouterClassification:
    intent: str
    confidence: float


class RouterClassifier(Protocol):
    def classify(self, question: str, allowed_intents: frozenset[str]) -> RouterClassification: ...


@dataclass(frozen=True)
class IntentDecision:
    intent: str
    source: str
    confidence: float


class HybridIntentPolicy:
    """Hard deny first, anchored explanation templates second, classifier last."""

    def __init__(self, classifier: RouterClassifier | None = None, *, minimum_classifier_confidence: float = 0.80) -> None:
        if not 0 <= minimum_classifier_confidence <= 1:
            raise ValueError("classifier confidence threshold must be within [0, 1]")
        self._classifier = classifier
        self._minimum_classifier_confidence = minimum_classifier_confidence

    def resolve_chat(self, question: str) -> IntentDecision:
        if not isinstance(question, str) or not question.strip():
            return IntentDecision("OUT_OF_SCOPE", "invalid_question", 1.0)
        normalized = _normalize(question)
        if _matches_any(normalized, _FORBIDDEN_PATTERNS):
            return IntentDecision("FORBIDDEN_MODEL_INTERNAL", "hard_policy", 1.0)
        if _matches_any(normalized, _WHAT_IF_PATTERNS):
            return IntentDecision("UNSUPPORTED_WHAT_IF", "hard_policy", 1.0)

        candidates = {
            intent for intent, patterns in _EXPLICIT_PATTERNS.items()
            if any(pattern.fullmatch(normalized) for pattern in patterns)
        }
        if len(candidates) == 1:
            return IntentDecision(candidates.pop(), "deterministic_explicit", 1.0)
        # An explanation verb plus anything outside the tiny anchored templates is
        # a compound/extra-purpose request, not classifier ambiguity.
        if (re.match(r"^(?:please\s+)?(?:explain|clarify|describe)\b|^(?:请)?(?:解释|说明|澄清)|^为什么", normalized)
                and not re.fullmatch(r"(?:please\s+)?(?:explain|clarify|describe)(?:\s+this)?|(?:请)?(?:解释|说明|澄清)(?:一下)?", normalized)):
            return IntentDecision("OUT_OF_SCOPE", "compound_or_extra", 1.0)

        if self._classifier is None:
            return IntentDecision("OUT_OF_SCOPE", "classifier_unavailable", 0.0)
        try:
            classified = self._classifier.classify(question, EXPLANATION_INTENTS)
        except Exception:
            return IntentDecision("OUT_OF_SCOPE", "classifier_failed", 0.0)
        intent = getattr(classified, "intent", None)
        confidence = getattr(classified, "confidence", None)
        if (not isinstance(confidence, (int, float)) or isinstance(confidence, bool)
                or not math.isfinite(confidence) or not 0 <= confidence <= 1):
            return IntentDecision("OUT_OF_SCOPE", "classifier_invalid", 0.0)
        if intent not in EXPLANATION_INTENTS or confidence < self._minimum_classifier_confidence:
            return IntentDecision("OUT_OF_SCOPE", "classifier_low_confidence", confidence)
        return IntentDecision(intent, "classifier", confidence)

    def resolve(self, *, mode: str, question: str) -> IntentDecision:
        """Compatibility surface is chat-only; initial rendering is backend-only."""
        if mode != "chat":
            raise ContractValidationError("initial rendering is backend-only")
        return self.resolve_chat(question)


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    visible = "".join(
        " " if unicodedata.category(character)[0] in {"P", "S"} else character
        for character in normalized
        if unicodedata.category(character) != "Cf"
    )
    return " ".join(visible.casefold().split())


def _matches_any(value: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(value) for pattern in patterns)


def _patterns(*values: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(value, re.IGNORECASE) for value in values)


_FORBIDDEN_PATTERNS = _patterns(
    r"\b(system[\s_-]*prompt|developer[\s_-]*instructions?|chain[\s_-]*of[\s_-]*thought|model[\s_-]*weights?|training[\s_-]*(?:data|dataset)|source[\s_-]*code|internal[\s_-]*(?:formula|instructions?))\b",
    r"\b(?:write|create|generate)\b.{0,30}\b(?:malware|ransomware|virus)\b",
    r"\bignore\b.{0,30}\b(?:safeguards?|instructions?|rules?|policy|policies)\b",
    r"\b(?:send|upload|exfiltrate)\b.{0,40}\b(?:data|secrets?|credentials?)\b.{0,20}\b(?:elsewhere|external|away|third[ -]?party)\b",
    r"(?:系统提示词|系统指令|开发者指令|思维链|模型权重|训练数据|训练资料|内部公式|内部代码|源代码)",
    r"(?:忽略|绕过).{0,20}(?:安全措施|防护|指令|政策)",
    r"(?:发送|上传|外传|窃取).{0,30}(?:数据|秘密|凭据).{0,20}(?:外部|别处|第三方)",
)
_WHAT_IF_PATTERNS = _patterns(
    r"\b(?:what[\s_-]*if|recalculate|regenerate|change|modify)\b.{0,60}\b(?:plan|budget|recommendation)\b",
    r"(?:如果|假如|修改|改变|重算|重新生成).{0,60}(?:方案|预算|建议)",
)

_PREFIX_EN = r"(?:please\s+)?(?:explain|clarify|describe)\s+"
_SUFFIX = r"\s*[?.!。？！]*"
_EXPLICIT_PATTERNS = {
    "EXPLAIN_PLAN": _patterns(
        rf"{_PREFIX_EN}(?:(?:this|the|current|recommended)\s+)?(?:plan|budget recommendation|recommended action){_SUFFIX}",
        r"(?:请)?(?:解释|说明|澄清)(?:一下)?(?:这个|该|当前|本次)?(?:方案|预算建议|推荐动作)\s*[。？！?]*",
        r"为什么(?:这个|该|当前|本次)?方案\s*[。？！?]*",
    ),
    "EXPLAIN_REVIEW": _patterns(
        rf"{_PREFIX_EN}(?:(?:this|the|current)\s+)?(?:review|verdict|ontology assessment|review conflict){_SUFFIX}",
        r"(?:请)?(?:解释|说明|澄清)(?:一下)?(?:这个|该|当前|本次)?(?:审核|评价|裁决|本体评价|审核冲突|本体评价冲突)\s*[。？！?]*",
        r"为什么(?:这个|该|当前|本次)?(?:审核|评价|本体评价)?冲突\s*[。？！?]*",
    ),
    "EXPLAIN_RULE": _patterns(
        rf"{_PREFIX_EN}(?:(?:this|the|current)\s+)?(?:rule|rule\s+r[0-9]+|r[0-9]+){_SUFFIX}",
        r"(?:请)?(?:解释|说明|澄清)(?:一下)?(?:这个|该|这条|当前)?(?:规则|规则\s*r[0-9]+|r[0-9]+)\s*[。？！?]*",
        r"为什么(?:这个|该|这条)?规则\s*[。？！?]*",
    ),
}