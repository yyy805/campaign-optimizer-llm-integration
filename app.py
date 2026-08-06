"""Streamlit demo UI for the campaign optimizer review explainer."""
from __future__ import annotations

import json
import os
from pathlib import Path

import streamlit as st

from campaign_optimizer.llm.agent_workflow_v12 import load_role_configuration, max_provider_calls_v12
from campaign_optimizer.llm.release_pin import load_verified_manifests, release_identity
from campaign_optimizer.llm.request_builder import LLMVersions, RequestBuilder
from campaign_optimizer.llm.retriever import LocalRuleRetriever
from campaign_optimizer.llm.three_role_runner_v13 import RoleCallAdapterV13, ThreeRoleRunnerV13

ROOT = Path(__file__).resolve().parent
CONFIG_V15 = ROOT / "campaign_optimizer" / "llm" / "agent_roles.v15.json"
PLAN = ROOT / "tests" / "fixtures" / "plan_a" / "final_plan.demo.json"
REVIEW = ROOT / "tests" / "fixtures" / "llm_eval" / "reviewer_judgment_v1" / "ontology_review.pending.json"
DEFAULT_QUESTION = "请解释本次推荐方案和本体评价。"


@st.cache_resource
def _config():
    return load_role_configuration(CONFIG_V15)


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _pending_chain(mode: str, question: str):
    versions = LLMVersions(
        workflow_version="not-published",
        prompt_version="draft-1.0",
        knowledge_base_version="not-published",
    )
    artifacts = RequestBuilder(LocalRuleRetriever(), versions=versions).build(
        _load(PLAN), _load(REVIEW), mode=mode, question=question, resolved_intent="EXPLAIN_REVIEW",
    )
    return artifacts


st.set_page_config(page_title="Campaign Optimizer 解释演示", page_icon="🛡️")
st.title("🛡️ Campaign Optimizer 解释演示")
st.caption("Reviewer v9 · canonical R5@2.0-campaign-pending · fail-closed gates")

config = _config()
manifest = next(
    value for value in load_verified_manifests().values()
    if value["ontology_version"] == "2.0-campaign-pending"
)

with st.sidebar:
    st.header("系统状态")
    st.write("本体发布", release_identity(manifest)["rule_version"])
    st.write("Reviewer 提示词", config.roles.prompt_versions["reviewer"])
    key_ok = bool(os.environ.get("DASHSCOPE_API_KEY") and os.environ.get("DASHSCOPE_WORKSPACE_ID"))
    st.write("API 凭据", "✅ 已设置" if key_ok else "❌ 未设置（仅 dry-run 可用）")

mode = st.radio("模式", ["Demo 解释（initial_render，不经 Triage）", "自由提问（chat，经 Triage）"])
chat = mode.startswith("自由")
question = st.text_input("问题", value="本体评价结果UNVERIFIED代表什么？" if chat else DEFAULT_QUESTION, disabled=not chat)
dry = st.checkbox("Dry-run（零调用）", value=True)

if st.button("运行", type="primary"):
    artifacts = _pending_chain("chat" if chat else "initial_render", question or DEFAULT_QUESTION)
    if dry:
        st.info("Dry-run：未构造 provider，零调用。")
        st.json({"provider_call_limit": max_provider_calls_v12(0, chat)})
        st.stop()
    adapter = RoleCallAdapterV13(config)
    adapter.set_total_limit(max_provider_calls_v12(0, chat))
    runner = ThreeRoleRunnerV13(configuration=config, role_calls=adapter)
    try:
        result = runner.run(
            request=artifacts.request, plan=_load(PLAN), review=_load(REVIEW), context=artifacts.context,
            question=question if chat else None, revision_profile="baseline", dry_run=False,
        )
    except Exception:
        st.error("运行失败：环境变量或网络问题。出于安全仅显示此类别，不输出细节。")
        st.stop()
    st.subheader(f"状态：{result.status}")
    if result.output is not None:
        st.markdown(result.output["answer"])
    if result.fallback_reason:
        st.warning(f"安全回退：{result.fallback_reason}")
    st.dataframe(
        [
            {"role": c.role, "outcome": c.outcome, "error_code": c.error_code,
             "latency_ms": c.latency_ms, "total_tokens": c.total_tokens}
            for c in result.calls
        ],
        hide_index=True,
    )
