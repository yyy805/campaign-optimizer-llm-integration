"""
pytest 测试：R-042 匹配器完整测试套件。

覆盖 T11 AC 中所有指定的测试用例及边界条件。
"""
import json
import re

import pytest

from campaign_optimizer.inference.r042_matcher import (
    ClientProfile,
    Evidence,
    Rule,
    R042,
    build_diagnosis,
    check_rebuttal_conditions,
    evaluate_execution_gate,
    evaluate_r042_trigger,
    run_r042_inference,
)


# ---------------------------------------------------------------------------
# 测试夹具
# ---------------------------------------------------------------------------

@pytest.fixture
def base_profile() -> ClientProfile:
    return ClientProfile(
        client_id="client-001",
        acos_baseline=0.18,
        ctr_baseline=0.0015,
        risk_tolerance="neutral",
        confidence_threshold=0.65,
        product_launch_days=30,
    )


@pytest.fixture
def cold_start_profile(base_profile: ClientProfile) -> ClientProfile:
    """新品冷启动期（product_launch_days < 14）。"""
    return ClientProfile(
        client_id=base_profile.client_id,
        acos_baseline=base_profile.acos_baseline,
        ctr_baseline=base_profile.ctr_baseline,
        risk_tolerance=base_profile.risk_tolerance,
        confidence_threshold=base_profile.confidence_threshold,
        product_launch_days=5,
    )


@pytest.fixture
def low_reversibility_rule() -> Rule:
    return Rule.create(
        rule_id="R-042",
        ontology_version="1.0",
        confidence=0.72,
        reversibility="LOW",
        risk_level="HIGH",
        evidence=Evidence(evidence_type="correlational", confidence_cap=0.6),
        rebuttal_conditions=["product_launch_days < 14"],
    )


# ---------------------------------------------------------------------------
# E1-S1  ClientProfile
# ---------------------------------------------------------------------------

class TestClientProfile:
    def test_valid_construction(self):
        p = ClientProfile(
            client_id="c1", acos_baseline=0.2, ctr_baseline=0.001,
            risk_tolerance="conservative", confidence_threshold=0.7,
            product_launch_days=10,
        )
        assert p.client_id == "c1"
        assert p.confidence_threshold == 0.7

    def test_frozen(self, base_profile: ClientProfile):
        with pytest.raises((AttributeError, TypeError)):
            base_profile.client_id = "other"  # type: ignore[misc]

    def test_confidence_threshold_zero_raises(self):
        with pytest.raises(ValueError, match="confidence_threshold"):
            ClientProfile("c", 0.1, 0.001, "neutral", 0.0, 10)

    def test_confidence_threshold_above_one_raises(self):
        with pytest.raises(ValueError, match="confidence_threshold"):
            ClientProfile("c", 0.1, 0.001, "neutral", 1.1, 10)

    def test_confidence_threshold_exactly_one_ok(self):
        p = ClientProfile("c", 0.1, 0.001, "neutral", 1.0, 10)
        assert p.confidence_threshold == 1.0

    def test_negative_launch_days_raises(self):
        with pytest.raises(ValueError, match="product_launch_days"):
            ClientProfile("c", 0.1, 0.001, "neutral", 0.7, -1)

    def test_invalid_risk_tolerance_raises(self):
        with pytest.raises(ValueError, match="risk_tolerance"):
            ClientProfile("c", 0.1, 0.001, "extreme", 0.7, 10)

    def test_from_dict_valid(self):
        d = {
            "client_id": "c1", "acos_baseline": 0.2, "ctr_baseline": 0.001,
            "risk_tolerance": "neutral", "confidence_threshold": 0.7,
            "product_launch_days": 10,
        }
        p = ClientProfile.from_dict(d)
        assert p.client_id == "c1"

    def test_from_dict_missing_key_raises(self):
        with pytest.raises(KeyError):
            ClientProfile.from_dict({"client_id": "c1"})

    def test_from_dict_wrong_type_raises(self):
        d = {
            "client_id": "c1", "acos_baseline": "not_a_float",
            "ctr_baseline": 0.001, "risk_tolerance": "neutral",
            "confidence_threshold": 0.7, "product_launch_days": 10,
        }
        with pytest.raises(TypeError):
            ClientProfile.from_dict(d)


# ---------------------------------------------------------------------------
# E1-S2  Evidence + Rule
# ---------------------------------------------------------------------------

class TestEvidence:
    def test_valid(self):
        e = Evidence(evidence_type="ab_tested", confidence_cap=0.85)
        assert e.evidence_type == "ab_tested"

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match="evidence_type"):
            Evidence(evidence_type="unknown", confidence_cap=0.5)

    def test_cap_out_of_range_raises(self):
        with pytest.raises(ValueError, match="confidence_cap"):
            Evidence(evidence_type="correlational", confidence_cap=1.5)


class TestRule:
    def test_valid(self):
        assert R042.rule_id == "R-042"
        assert R042.reversibility == "HIGH"
        assert R042.risk_level == "MEDIUM"

    def test_invalid_rule_id_format_raises(self):
        with pytest.raises(ValueError, match="rule_id"):
            Rule.create("R42", "1.0", 0.5, "HIGH", "MEDIUM",
                        Evidence("correlational", 0.6))

    def test_invalid_rule_id_letters_raises(self):
        with pytest.raises(ValueError):
            Rule.create("R-04X", "1.0", 0.5, "HIGH", "MEDIUM",
                        Evidence("correlational", 0.6))

    def test_confidence_out_of_range_raises(self):
        with pytest.raises(ValueError, match="confidence"):
            Rule.create("R-042", "1.0", 1.5, "HIGH", "MEDIUM",
                        Evidence("correlational", 0.6))

    def test_invalid_reversibility_raises(self):
        with pytest.raises(ValueError, match="reversibility"):
            Rule.create("R-042", "1.0", 0.5, "MEDIUM", "MEDIUM",  # type: ignore
                        Evidence("correlational", 0.6))

    def test_invalid_risk_level_raises(self):
        with pytest.raises(ValueError, match="risk_level"):
            Rule.create("R-042", "1.0", 0.5, "HIGH", "CRITICAL",  # type: ignore
                        Evidence("correlational", 0.6))

    def test_rebuttal_conditions_default_empty(self):
        r = Rule.create("R-042", "1.0", 0.5, "HIGH", "MEDIUM",
                        Evidence("correlational", 0.6))
        assert r.rebuttal_conditions == ()

    def test_rebuttal_conditions_not_none(self):
        r = Rule.create("R-042", "1.0", 0.5, "HIGH", "MEDIUM",
                        Evidence("correlational", 0.6), rebuttal_conditions=None)
        assert r.rebuttal_conditions == ()


# ---------------------------------------------------------------------------
# E2-S1  evaluate_r042_trigger — T11 AC 6 个指定测试用例
# ---------------------------------------------------------------------------

class TestEvaluateR042Trigger:
    """基准：acos_baseline=0.18, ctr_baseline=0.0015。
    触发阈值：acos > 0.234, ctr < 0.00105, days >= 7。
    """

    def test_normal_trigger(self, base_profile: ClientProfile):
        # acos=0.252 > 0.234, ctr=0.0009 < 0.00105, days=7
        assert evaluate_r042_trigger(0.252, 0.0009, base_profile, 7) is True

    def test_acos_just_above_threshold(self, base_profile: ClientProfile):
        # acos_baseline*1.3 = 0.234；acos=0.2341 > 0.234
        assert evaluate_r042_trigger(0.2341, 0.0009, base_profile, 7) is True

    def test_acos_below_threshold(self, base_profile: ClientProfile):
        # acos=0.216 < 0.234 → 不触发
        assert evaluate_r042_trigger(0.216, 0.0009, base_profile, 7) is False

    def test_ctr_above_threshold(self, base_profile: ClientProfile):
        # ctr=0.00135 > 0.00105 → 不触发
        assert evaluate_r042_trigger(0.252, 0.00135, base_profile, 7) is False

    def test_days_insufficient(self, base_profile: ClientProfile):
        # days=6 < 7 → 不触发
        assert evaluate_r042_trigger(0.252, 0.0009, base_profile, 6) is False

    def test_days_boundary_exactly_seven(self, base_profile: ClientProfile):
        # days=7 恰好满足 >= 7 → 触发
        assert evaluate_r042_trigger(0.252, 0.0009, base_profile, 7) is True

    def test_negative_acos_raises(self, base_profile: ClientProfile):
        with pytest.raises(ValueError, match="acos"):
            evaluate_r042_trigger(-0.1, 0.001, base_profile, 7)

    def test_negative_ctr_raises(self, base_profile: ClientProfile):
        with pytest.raises(ValueError, match="ctr"):
            evaluate_r042_trigger(0.25, -0.001, base_profile, 7)

    def test_negative_days_raises(self, base_profile: ClientProfile):
        with pytest.raises(ValueError, match="consecutive_days"):
            evaluate_r042_trigger(0.25, 0.001, base_profile, -1)


# ---------------------------------------------------------------------------
# E2-S2  check_rebuttal_conditions — T11 AC 4 个测试用例
# ---------------------------------------------------------------------------

class TestCheckRebuttalConditions:
    def test_launch_days_5_rebutted(self, cold_start_profile: ClientProfile):
        rebutted, condition = check_rebuttal_conditions(R042, cold_start_profile)
        assert rebutted is True
        assert condition == "product_launch_days < 14"

    def test_launch_days_13_rebutted(self, base_profile: ClientProfile):
        p = ClientProfile(
            base_profile.client_id, base_profile.acos_baseline,
            base_profile.ctr_baseline, base_profile.risk_tolerance,
            base_profile.confidence_threshold, 13,
        )
        rebutted, condition = check_rebuttal_conditions(R042, p)
        assert rebutted is True
        assert condition == "product_launch_days < 14"

    def test_launch_days_14_not_rebutted(self, base_profile: ClientProfile):
        p = ClientProfile(
            base_profile.client_id, base_profile.acos_baseline,
            base_profile.ctr_baseline, base_profile.risk_tolerance,
            base_profile.confidence_threshold, 14,
        )
        rebutted, condition = check_rebuttal_conditions(R042, p)
        assert rebutted is False
        assert condition is None

    def test_launch_days_30_not_rebutted(self, base_profile: ClientProfile):
        rebutted, condition = check_rebuttal_conditions(R042, base_profile)
        assert rebutted is False
        assert condition is None

    def test_empty_rebuttal_conditions(self, base_profile: ClientProfile):
        rule_no_rebuttal = Rule.create(
            "R-042", "1.0", 0.72, "HIGH", "MEDIUM",
            Evidence("correlational", 0.6),
        )
        rebutted, condition = check_rebuttal_conditions(rule_no_rebuttal, base_profile)
        assert rebutted is False
        assert condition is None


# ---------------------------------------------------------------------------
# E2-S3  evaluate_execution_gate — T11 AC 四象限 + 边界
# ---------------------------------------------------------------------------

class TestEvaluateExecutionGate:
    def test_both_axes_passed(self):
        auto, reason = evaluate_execution_gate(0.8, 0.65, "HIGH")
        assert auto is True
        assert reason == "both_axes_passed"

    def test_high_confidence_low_reversibility(self):
        auto, reason = evaluate_execution_gate(0.9, 0.65, "LOW")
        assert auto is False
        assert reason == "low_reversibility"

    def test_low_confidence_high_reversibility(self):
        auto, reason = evaluate_execution_gate(0.4, 0.65, "HIGH")
        assert auto is False
        assert reason == "low_confidence"

    def test_both_axes_failed(self):
        auto, reason = evaluate_execution_gate(0.4, 0.65, "LOW")
        assert auto is False
        assert reason == "both_axes_failed"

    def test_confidence_exactly_equals_threshold(self):
        # >= 语义：恰好等于阈值应视为达标
        auto, reason = evaluate_execution_gate(0.65, 0.65, "HIGH")
        assert auto is True
        assert reason == "both_axes_passed"

    def test_invalid_reversibility_raises(self):
        with pytest.raises(ValueError, match="reversibility"):
            evaluate_execution_gate(0.8, 0.65, "MEDIUM")

    def test_invalid_threshold_zero_raises(self):
        with pytest.raises(ValueError, match="client_threshold"):
            evaluate_execution_gate(0.8, 0.0, "HIGH")

    def test_invalid_threshold_above_one_raises(self):
        with pytest.raises(ValueError, match="client_threshold"):
            evaluate_execution_gate(0.8, 1.1, "HIGH")


# ---------------------------------------------------------------------------
# E2-S4  build_diagnosis
# ---------------------------------------------------------------------------

class TestBuildDiagnosis:
    def _trigger_ev(self, profile: ClientProfile) -> dict:
        return {
            "acos_actual": 0.252, "ctr_actual": 0.0009,
            "acos_baseline": profile.acos_baseline,
            "ctr_baseline": profile.ctr_baseline,
            "consecutive_days": 10,
        }

    def test_all_required_fields_present(self, base_profile: ClientProfile):
        diag = build_diagnosis(
            R042, base_profile,
            triggered=True,
            rebuttal=(False, None),
            gate=(True, "both_axes_passed"),
            trigger_evidence=self._trigger_ev(base_profile),
        )
        required = {
            "diagnosis_id", "ontology_version", "rule_id", "confidence",
            "risk_level", "reversibility", "auto_executable", "triggered",
            "rebutted", "rebuttal_reason", "gate_reason", "trigger_evidence",
        }
        assert required <= set(diag.keys())

    def test_not_triggered_forces_not_auto_executable(self, base_profile: ClientProfile):
        diag = build_diagnosis(
            R042, base_profile,
            triggered=False,
            rebuttal=(False, None),
            gate=(True, "both_axes_passed"),
            trigger_evidence=self._trigger_ev(base_profile),
        )
        assert diag["auto_executable"] is False

    def test_rebutted_forces_not_auto_executable(self, base_profile: ClientProfile):
        diag = build_diagnosis(
            R042, base_profile,
            triggered=True,
            rebuttal=(True, "product_launch_days < 14"),
            gate=(True, "both_axes_passed"),
            trigger_evidence=self._trigger_ev(base_profile),
        )
        assert diag["auto_executable"] is False

    def test_diagnosis_id_is_unique(self, base_profile: ClientProfile):
        ev = self._trigger_ev(base_profile)
        ids = {
            build_diagnosis(R042, base_profile, True, (False, None),
                            (True, "both_axes_passed"), ev)["diagnosis_id"]
            for _ in range(10)
        }
        assert len(ids) == 10  # 每次调用产生不同的 UUID

    def test_diagnosis_id_is_uuid_v4(self, base_profile: ClientProfile):
        diag = build_diagnosis(
            R042, base_profile, True, (False, None),
            (True, "both_axes_passed"), self._trigger_ev(base_profile),
        )
        # 验证是合法的 UUID 字符串
        import uuid as _uuid
        parsed = _uuid.UUID(diag["diagnosis_id"])
        assert parsed.version == 4

    def test_json_serializable(self, base_profile: ClientProfile):
        diag = build_diagnosis(
            R042, base_profile, True, (False, None),
            (True, "both_axes_passed"), self._trigger_ev(base_profile),
        )
        serialized = json.dumps(diag)
        assert isinstance(serialized, str)

    def test_ontology_version_from_rule(self, base_profile: ClientProfile):
        diag = build_diagnosis(
            R042, base_profile, True, (False, None),
            (True, "both_axes_passed"), self._trigger_ev(base_profile),
        )
        assert diag["ontology_version"] == R042.ontology_version


# ---------------------------------------------------------------------------
# E3-S1  run_r042_inference — T11 端到端 5 个场景
# ---------------------------------------------------------------------------

class TestRunR042Inference:
    def test_normal_trigger_auto_executable(self, base_profile: ClientProfile):
        # 指标越线，launch=30，conf(0.72) >= threshold(0.65)，HIGH 可逆
        diag = run_r042_inference(0.252, 0.0009, 10, base_profile, R042)
        assert diag["triggered"] is True
        assert diag["rebutted"] is False
        assert diag["auto_executable"] is True

    def test_cold_start_rebutted(self, cold_start_profile: ClientProfile):
        # 指标越线，但 launch_days=5 < 14 → 被反驳
        diag = run_r042_inference(0.252, 0.0009, 10, cold_start_profile, R042)
        assert diag["triggered"] is True
        assert diag["rebutted"] is True
        assert diag["auto_executable"] is False

    def test_not_triggered(self, base_profile: ClientProfile):
        # 指标正常，不触发
        diag = run_r042_inference(0.15, 0.002, 10, base_profile, R042)
        assert diag["triggered"] is False
        assert diag["rebutted"] is False
        assert diag["auto_executable"] is False

    def test_trigger_but_low_confidence(self, base_profile: ClientProfile):
        # confidence_threshold=0.9，高于 R042.confidence=0.72
        high_threshold_profile = ClientProfile(
            client_id=base_profile.client_id,
            acos_baseline=base_profile.acos_baseline,
            ctr_baseline=base_profile.ctr_baseline,
            risk_tolerance=base_profile.risk_tolerance,
            confidence_threshold=0.9,
            product_launch_days=base_profile.product_launch_days,
        )
        diag = run_r042_inference(0.252, 0.0009, 10, high_threshold_profile, R042)
        assert diag["triggered"] is True
        assert diag["rebutted"] is False
        assert diag["auto_executable"] is False
        assert diag["gate_reason"] == "low_confidence"

    def test_trigger_but_low_reversibility(
        self, base_profile: ClientProfile, low_reversibility_rule: Rule
    ):
        # reversibility=LOW → 不可自动执行
        diag = run_r042_inference(0.252, 0.0009, 10, base_profile, low_reversibility_rule)
        assert diag["triggered"] is True
        assert diag["rebutted"] is False
        assert diag["auto_executable"] is False
        assert diag["gate_reason"] == "low_reversibility"

    def test_execution_order_skips_gate_when_not_triggered(
        self, base_profile: ClientProfile
    ):
        # 未触发时，gate 应为 not_evaluated
        diag = run_r042_inference(0.10, 0.002, 3, base_profile, R042)
        assert diag["gate_reason"] == "not_evaluated"

    def test_execution_order_skips_gate_when_rebutted(
        self, cold_start_profile: ClientProfile
    ):
        # 触发但被反驳时，gate 也应为 not_evaluated
        diag = run_r042_inference(0.252, 0.0009, 10, cold_start_profile, R042)
        assert diag["gate_reason"] == "not_evaluated"

    def test_trigger_evidence_captured(self, base_profile: ClientProfile):
        diag = run_r042_inference(0.252, 0.0009, 10, base_profile, R042)
        ev = diag["trigger_evidence"]
        assert ev["acos_actual"] == 0.252
        assert ev["ctr_actual"] == 0.0009
        assert ev["consecutive_days"] == 10
        assert ev["acos_baseline"] == base_profile.acos_baseline
        assert ev["ctr_baseline"] == base_profile.ctr_baseline
