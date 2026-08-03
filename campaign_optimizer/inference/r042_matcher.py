"""
R-042 matcher: 关键词相关性不足诊断规则的推理切片。

覆盖范围：
  E1-S1  ClientProfile 内存对象
  E1-S2  Rule + Evidence 内存对象（含 risk_level，T12 澄清项）
  E2-S1  R-042 触发条件评估
  E2-S2  CAP-7 反驳条件检查
  E2-S3  双轴执行门控（AD-5）
  E2-S4  结构化诊断输出（AD-4）
  E3-S1  端到端推理入口
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Literal


# ---------------------------------------------------------------------------
# E1-S1  ClientProfile
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ClientProfile:
    """每个客户的基准配置，推理时只读传入，不写入规则表（AD-3）。"""

    client_id: str
    acos_baseline: float
    ctr_baseline: float
    risk_tolerance: Literal["conservative", "neutral", "aggressive"]
    confidence_threshold: float
    product_launch_days: int

    def __post_init__(self) -> None:
        if not (0 < self.confidence_threshold <= 1):
            raise ValueError(
                f"confidence_threshold must be in (0, 1], got {self.confidence_threshold}"
            )
        if self.product_launch_days < 0:
            raise ValueError(
                f"product_launch_days must be >= 0, got {self.product_launch_days}"
            )
        if self.risk_tolerance not in ("conservative", "neutral", "aggressive"):
            raise ValueError(
                f"risk_tolerance must be one of conservative/neutral/aggressive, "
                f"got {self.risk_tolerance!r}"
            )

    @classmethod
    def from_dict(cls, d: dict) -> "ClientProfile":
        """从字典构造；字段缺失抛 KeyError，类型错误抛 TypeError。"""
        required = (
            "client_id", "acos_baseline", "ctr_baseline",
            "risk_tolerance", "confidence_threshold", "product_launch_days",
        )
        for key in required:
            if key not in d:
                raise KeyError(key)
        try:
            return cls(
                client_id=str(d["client_id"]),
                acos_baseline=float(d["acos_baseline"]),
                ctr_baseline=float(d["ctr_baseline"]),
                risk_tolerance=d["risk_tolerance"],
                confidence_threshold=float(d["confidence_threshold"]),
                product_launch_days=int(d["product_launch_days"]),
            )
        except (ValueError, TypeError) as exc:
            raise TypeError(f"Type error constructing ClientProfile: {exc}") from exc


# ---------------------------------------------------------------------------
# E1-S2  Evidence + Rule
# ---------------------------------------------------------------------------

_VALID_EVIDENCE_TYPES = frozenset(
    {"human_expert", "correlational", "user_validated", "ab_tested"}
)
_RULE_ID_RE = re.compile(r"^R-\d{3}$")


@dataclass(frozen=True)
class Evidence:
    """规则的证据来源及其置信度上限。"""

    evidence_type: str
    confidence_cap: float

    def __post_init__(self) -> None:
        if self.evidence_type not in _VALID_EVIDENCE_TYPES:
            raise ValueError(
                f"evidence_type must be one of {sorted(_VALID_EVIDENCE_TYPES)}, "
                f"got {self.evidence_type!r}"
            )
        if not (0.0 <= self.confidence_cap <= 1.0):
            raise ValueError(
                f"confidence_cap must be in [0, 1], got {self.confidence_cap}"
            )


@dataclass(frozen=True)
class Rule:
    """单条本体规则：触发条件、置信度、可逆性、反驳条件。"""

    rule_id: str
    ontology_version: str
    confidence: float
    reversibility: Literal["HIGH", "LOW"]
    risk_level: Literal["HIGH", "MEDIUM", "LOW"]
    evidence: Evidence
    rebuttal_conditions: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not _RULE_ID_RE.match(self.rule_id):
            raise ValueError(
                f"rule_id must match R-NNN format, got {self.rule_id!r}"
            )
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be in [0, 1], got {self.confidence}"
            )
        if self.reversibility not in ("HIGH", "LOW"):
            raise ValueError(
                f"reversibility must be HIGH or LOW, got {self.reversibility!r}"
            )
        if self.risk_level not in ("HIGH", "MEDIUM", "LOW"):
            raise ValueError(
                f"risk_level must be HIGH/MEDIUM/LOW, got {self.risk_level!r}"
            )

    @classmethod
    def create(
        cls,
        rule_id: str,
        ontology_version: str,
        confidence: float,
        reversibility: Literal["HIGH", "LOW"],
        risk_level: Literal["HIGH", "MEDIUM", "LOW"],
        evidence: Evidence,
        rebuttal_conditions: list[str] | None = None,
    ) -> "Rule":
        """工厂方法，将 list 转为 tuple 以满足 frozen 约束。"""
        return cls(
            rule_id=rule_id,
            ontology_version=ontology_version,
            confidence=confidence,
            reversibility=reversibility,
            risk_level=risk_level,
            evidence=evidence,
            rebuttal_conditions=tuple(rebuttal_conditions or []),
        )


# ---------------------------------------------------------------------------
# E2-S1  R-042 触发条件评估
# ---------------------------------------------------------------------------

def evaluate_r042_trigger(
    acos: float,
    ctr: float,
    profile: ClientProfile,
    consecutive_days: int,
) -> bool:
    """判断 R-042（高ACoS低CTR→关键词不相关）的触发条件是否成立。

    触发条件（三者同时成立）：
      acos > profile.acos_baseline * 1.3
      ctr  < profile.ctr_baseline  * 0.7
      consecutive_days >= 7
    """
    if acos < 0:
        raise ValueError(f"acos must be >= 0, got {acos}")
    if ctr < 0:
        raise ValueError(f"ctr must be >= 0, got {ctr}")
    if consecutive_days < 0:
        raise ValueError(f"consecutive_days must be >= 0, got {consecutive_days}")

    return (
        acos > profile.acos_baseline * 1.3
        and ctr < profile.ctr_baseline * 0.7
        and consecutive_days >= 7
    )


# ---------------------------------------------------------------------------
# E2-S2  CAP-7 反驳条件检查
# ---------------------------------------------------------------------------

def check_rebuttal_conditions(
    rule: Rule,
    profile: ClientProfile,
) -> tuple[bool, str | None]:
    """检查规则的反驳条件是否命中（CAP-7）。

    Returns:
        (rebutted, matched_condition)
        rebutted=True 表示命中了反驳条件，该规则在此场景下不适用。
    """
    for condition in rule.rebuttal_conditions:
        if _eval_rebuttal_condition(condition, profile):
            return True, condition
    return False, None


def _eval_rebuttal_condition(condition: str, profile: ClientProfile) -> bool:
    """对单条反驳条件字符串求值（当前仅支持 product_launch_days < N）。"""
    match = re.fullmatch(
        r"product_launch_days\s*<\s*(\d+)", condition.strip()
    )
    if match:
        threshold = int(match.group(1))
        return profile.product_launch_days < threshold
    # 未识别的反驳条件视为不命中（保守策略：不因未知条件阻止规则触发）
    return False


# ---------------------------------------------------------------------------
# E2-S3  双轴执行门控（AD-5）
# ---------------------------------------------------------------------------

def evaluate_execution_gate(
    confidence: float,
    client_threshold: float,
    reversibility: str,
) -> tuple[bool, str]:
    """双轴执行门控：置信度轴 × 可逆性轴，独立评估。

    Returns:
        (auto_executable, gate_reason)

    gate_reason 枚举值：
        "both_axes_passed"   — 可自动执行
        "low_reversibility"  — 置信度达标但可逆性不足
        "low_confidence"     — 可逆性达标但置信度不足
        "both_axes_failed"   — 两轴均未达标
    """
    if reversibility not in ("HIGH", "LOW"):
        raise ValueError(
            f"reversibility must be HIGH or LOW, got {reversibility!r}"
        )
    if not (0 < client_threshold <= 1):
        raise ValueError(
            f"client_threshold must be in (0, 1], got {client_threshold}"
        )

    confidence_ok = confidence >= client_threshold
    reversibility_ok = reversibility == "HIGH"

    if confidence_ok and reversibility_ok:
        return True, "both_axes_passed"
    if confidence_ok and not reversibility_ok:
        return False, "low_reversibility"
    if not confidence_ok and reversibility_ok:
        return False, "low_confidence"
    return False, "both_axes_failed"


# ---------------------------------------------------------------------------
# E2-S4  结构化诊断输出（AD-4）
# ---------------------------------------------------------------------------

def build_diagnosis(
    rule: Rule,
    profile: ClientProfile,
    triggered: bool,
    rebuttal: tuple[bool, str | None],
    gate: tuple[bool, str],
    trigger_evidence: dict,
) -> dict:
    """组装 AD-4 规定格式的结构化诊断输出。

    Args:
        rebuttal: check_rebuttal_conditions 的返回值 (rebutted, matched_condition)
        gate:     evaluate_execution_gate 的返回值 (auto_executable, gate_reason)
        trigger_evidence: 含实际观测值的字典（acos_actual, ctr_actual, 等）

    Returns:
        含12个必须字段的 dict，可被 json.dumps 无错序列化。
    """
    rebutted, rebuttal_reason = rebuttal
    gate_auto_executable, gate_reason = gate

    # 未触发或被反驳时，auto_executable 必须为 False
    auto_executable = triggered and not rebutted and gate_auto_executable

    diagnosis = {
        "diagnosis_id": str(uuid.uuid4()),
        "ontology_version": rule.ontology_version,
        "rule_id": rule.rule_id,
        "confidence": rule.confidence,
        "risk_level": rule.risk_level,
        "reversibility": rule.reversibility,
        "auto_executable": auto_executable,
        "triggered": triggered,
        "rebutted": rebutted,
        "rebuttal_reason": rebuttal_reason,
        "gate_reason": gate_reason,
        "trigger_evidence": trigger_evidence,
    }

    # 验证可序列化性（快速 self-check）
    json.dumps(diagnosis)
    return diagnosis


# ---------------------------------------------------------------------------
# E3-S1  端到端推理入口
# ---------------------------------------------------------------------------

def run_r042_inference(
    acos: float,
    ctr: float,
    consecutive_days: int,
    profile: ClientProfile,
    rule: Rule,
) -> dict:
    """R-042 端到端推理入口。

    执行顺序（强制）：
      1. 触发判断
      2. 反驳检查（仅在触发时执行）
      3. 门控判断（仅在触发且未被反驳时执行）
      4. 组装诊断输出

    此函数不抛业务异常——所有入参校验由各子函数处理。
    """
    trigger_evidence = {
        "acos_actual": acos,
        "ctr_actual": ctr,
        "acos_baseline": profile.acos_baseline,
        "ctr_baseline": profile.ctr_baseline,
        "consecutive_days": consecutive_days,
    }

    # 步骤 1：触发判断
    triggered = evaluate_r042_trigger(acos, ctr, profile, consecutive_days)

    # 步骤 2：反驳检查（仅在触发时执行）
    if triggered:
        rebuttal = check_rebuttal_conditions(rule, profile)
    else:
        rebuttal = (False, None)

    rebutted, _ = rebuttal

    # 步骤 3：门控判断（仅在触发且未被反驳时执行）
    if triggered and not rebutted:
        gate = evaluate_execution_gate(
            rule.confidence, profile.confidence_threshold, rule.reversibility
        )
    else:
        gate = (False, "not_evaluated")

    # 步骤 4：组装诊断
    return build_diagnosis(rule, profile, triggered, rebuttal, gate, trigger_evidence)


# ---------------------------------------------------------------------------
# 样例规则实例（供测试和文档引用）
# ---------------------------------------------------------------------------

R042 = Rule.create(
    rule_id="R-042",
    ontology_version="1.0",
    confidence=0.72,
    reversibility="HIGH",
    risk_level="MEDIUM",
    evidence=Evidence(evidence_type="correlational", confidence_cap=0.6),
    rebuttal_conditions=["product_launch_days < 14"],
)
