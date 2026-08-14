#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""换库演练灵魂步:SQLite vs PostgreSQL 确定性双跑。

用法(正式演练,需要 PG_DRILL_URL 环境变量;PG 表须已由 alembic 建好):
    uv run --with psycopg2-binary python scripts/drill_cross_db_double_run.py

离线自检(不连 PG:用两个相互独立的临时 SQLite 各跑一遍,验证引擎确定性与比对逻辑):
    uv run python scripts/drill_cross_db_double_run.py --sqlite-only

做法(基于 2026-08-12 从 .worktrees/hannah-convergence 读到的真实 API):
    1. 从 Hannah 集成分支导入完整引擎(worktree 外的 main 工作区没有
       review_workflow.py / review_engine.py,双跑只能基于 worktree 代码):
         campaign_optimizer.ontology.db            init_db / build_engine /
                                                   ClientRow / PlanSnapshotRow / PlanItemRow /
                                                   OntologyReviewRow / OntologyReviewItemRow /
                                                   canonical_digest
         campaign_optimizer.ontology.publication   build_publication_manifest
         campaign_optimizer.ontology.review_workflow
                                                   ReviewRelease.from_manifest /
                                                   ReviewWorkflow(engine, release) /
                                                   .review_final_plan / .rereview
    2. 同一份种子 plan(默认 worktree 的 tests/fixtures/plan_a/final_plan.demo.json),
       同一个固定 release(source_commit/各版本号全部硬编码,manifest 由文件哈希算出,
       两侧完全一致),分别在 (a) 临时 SQLite、(b) PG_DRILL_URL 上执行:
         review_final_plan(plan)          → 首次提交 + 原样重放(幂等)
         rereview(prior_review_id)        → 生成 revision=1 的链式复审
    3. 读回持久化状态并比对:review_id、overall_verdict、完整 review payload、
       plan_snapshots / plan_items / ontology_reviews / ontology_review_items 的
       持久化字段与行哈希。

为什么可行(确定性依据):
    - generate_ontology_review 的 review_id = sha256(plan + 版本串 + release 身份 +
      confidence 状态) 的前缀,无时间戳、无随机数;
    - 本脚本不启用任何规则(enabled_rule_ids=()),confidence_state_version 固定为
      'no-enabled-rules',两侧逐位一致;
    - rereview 的 review_id 由 (base_review_id, parent_review_id, revision) 摘要得出。

为什么不含 apply_feedback 腿:
    FeedbackEventRow.received_at / RuleConfidenceStateRow.updated_at 是墙钟
    datetime.now(),两次运行的持久化值必然不同,无法做逐位比对;
    review + rereview 两腿已覆盖 plan_snapshots / plan_items / ontology_reviews /
    ontology_review_items 四条持久化路径,足以暴露 schema、约束与 JSONB 往返差异。

created_at 也是墙钟,因此行哈希比对显式排除 created_at(但校验其带时区)。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKTREE = REPO_ROOT / ".worktrees" / "hannah-convergence"
DEFAULT_PLAN_REL = Path("tests") / "fixtures" / "plan_a" / "final_plan.demo.json"

# 双跑 release 常量:全部硬编码,保证两条腿的 release 身份逐位一致。
RELEASE_CONSTS = {
    "source_commit": "a" * 40,
    "ontology_version": "drill-pg-cutover",
    "rule_version": "R5@drill-pg-cutover",
    "engine_version": "drill-1.0",
    "schema_version": "1.0",
}

# PG 侧必须已存在的表(见 runbook 第 3 步:create_all 五张基础表 + alembic upgrade head)。
REQUIRED_PG_TABLES = {
    "clients", "model_artifacts", "plan_snapshots", "plan_items",
    "ontology_reviews", "ontology_review_items", "feedback_events",
    "rule_confidence_states", "plan_decision_events",
}


def fail(message: str):
    print(f"[FAIL] {message}", file=sys.stderr)
    raise SystemExit(1)


def import_worktree(worktree: Path):
    if not (worktree / "campaign_optimizer" / "ontology" / "review_workflow.py").exists():
        fail(
            f"{worktree} 下找不到 campaign_optimizer/ontology/review_workflow.py。"
            "双跑必须基于 Hannah 集成分支的完整引擎;请确认 worktree 路径(--worktree)。"
        )
    sys.path.insert(0, str(worktree))
    # 防御:如果进程里已有其他来源的 campaign_optimizer(如 main 旧版),先清掉再导入。
    for name in [m for m in sys.modules if m.split(".")[0] == "campaign_optimizer"]:
        del sys.modules[name]


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def row_hash(*fields) -> str:
    import hashlib

    return hashlib.sha256(canonical_json(list(fields)).encode("utf-8")).hexdigest()


def diff_dicts(expected: dict, actual: dict, path: str = "") -> list[str]:
    diffs: list[str] = []
    for key in sorted(set(expected) | set(actual)):
        here = f"{path}.{key}" if path else str(key)
        if key not in expected:
            diffs.append(f"{here}: 仅实际侧存在 = {actual[key]!r}")
        elif key not in actual:
            diffs.append(f"{here}: 仅期望侧存在 = {expected[key]!r}")
        elif isinstance(expected[key], dict) and isinstance(actual[key], dict):
            diffs.extend(diff_dicts(expected[key], actual[key], here))
        elif expected[key] != actual[key]:
            diffs.append(f"{here}: 期望 {expected[key]!r} ≠ 实际 {actual[key]!r}")
    return diffs


class Leg:
    """一条腿:一个后端(SQLite 或 PG)上的完整 plan→review→rereview + 读回。"""

    def __init__(self, label: str, engine, modules, release, plan: dict, client_id: str):
        self.label = label
        self.engine = engine
        self.m = modules
        self.release = release
        self.plan = plan
        self.client_id = client_id

    def run(self) -> dict:
        Session = self.m["Session"]
        ClientRow = self.m["ClientRow"]

        with Session(self.engine) as session, session.begin():
            session.add(ClientRow(client_id=self.client_id, card={"client_id": self.client_id}))

        workflow = self.m["ReviewWorkflow"](self.engine, self.release)
        first = workflow.review_final_plan(client_id=self.client_id, plan=self.plan)
        replay = workflow.review_final_plan(client_id=self.client_id, plan=self.plan)
        second = workflow.rereview(
            client_id=self.client_id, prior_review_id=first["review"]["review_id"],
        )
        persisted = self._readback(first["review"]["review_id"], second["review"]["review_id"])
        return {"first": first, "replay": replay, "second": second, "persisted": persisted}

    def _readback(self, review_id: str, rereview_id: str) -> dict:
        Session = self.m["Session"]
        select = self.m["select"]
        PlanSnapshotRow = self.m["PlanSnapshotRow"]
        PlanItemRow = self.m["PlanItemRow"]
        OntologyReviewRow = self.m["OntologyReviewRow"]
        OntologyReviewItemRow = self.m["OntologyReviewItemRow"]

        with Session(self.engine) as session:
            snapshot = session.get(PlanSnapshotRow, (self.client_id, self.plan["plan_id"]))
            items = session.scalars(
                select(PlanItemRow).where(PlanItemRow.client_id == self.client_id)
            ).all()
            reviews = session.scalars(
                select(OntologyReviewRow).where(OntologyReviewRow.client_id == self.client_id)
            ).all()
            review_items = session.scalars(
                select(OntologyReviewItemRow).where(
                    OntologyReviewItemRow.client_id == self.client_id
                )
            ).all()

        def timestamp_ok(value) -> bool:
            return value is not None and value.tzinfo is not None

        persisted = {
            "plan_snapshots": {} if snapshot is None else {
                snapshot.plan_id: {
                    "source_version": snapshot.source_version,
                    "plan_digest": snapshot.plan_digest,
                    "payload": snapshot.payload,
                    "created_at_tz_aware": timestamp_ok(snapshot.created_at),
                },
            },
            "plan_items": sorted(
                (
                    row_hash(r.plan_id, r.plan_item_id, r.entity_id, r.action, r.payload),
                    {"plan_item_id": r.plan_item_id, "entity_id": r.entity_id,
                     "action": r.action, "payload": r.payload},
                )
                for r in items
            ),
            "ontology_reviews": {
                r.review_id: {
                    "plan_id": r.plan_id,
                    "parent_review_id": r.parent_review_id,
                    "revision": r.revision,
                    "ontology_version": r.ontology_version,
                    "rule_version": r.rule_version,
                    "engine_version": r.engine_version,
                    "schema_version": r.schema_version,
                    "source_commit": r.source_commit,
                    "package_checksum": r.package_checksum,
                    "confidence_state_version": r.confidence_state_version,
                    "overall_verdict": r.overall_verdict,
                    "payload": r.payload,
                    "created_at_tz_aware": timestamp_ok(r.created_at),
                }
                for r in reviews
            },
            "ontology_review_items": sorted(
                (
                    row_hash(r.review_id, r.review_item_id, r.plan_item_id,
                             r.rule_id, r.rule_version, r.verdict, r.payload),
                    {"review_item_id": r.review_item_id, "review_id": r.review_id,
                     "plan_item_id": r.plan_item_id, "rule_id": r.rule_id,
                     "rule_version": r.rule_version, "verdict": r.verdict,
                     "confidence_snapshot": r.confidence_snapshot, "payload": r.payload},
                )
                for r in review_items
            ),
            "review_ids": sorted({review_id, rereview_id}),
        }
        return persisted


def check(results: list[tuple[str, dict]], plan: dict) -> bool:
    """跨腿比对;打印每项 PASS/FAIL,返回总体是否通过。"""
    ok = True

    def verdict(name: str, passed: bool, details: list[str] | None = None):
        nonlocal ok
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        if not passed:
            ok = False
            for line in (details or ["(无明细)"])[:20]:
                print(f"         {line}")

    labels = [label for label, _ in results]
    legs = [leg for _, leg in results]
    print(f"\n双跑比对:{' vs '.join(labels)}")

    for label, leg in results:
        first, replay = leg["first"], leg["replay"]
        verdict(f"{label}: review_final_plan 返回 COMMITTED",
                first.get("status") == "COMMITTED",
                [f"status={first.get('status')!r}"])
        verdict(f"{label}: 同一 plan 重放幂等(返回值逐位一致)",
                first == replay,
                diff_dicts(first, replay) if first != replay else None)

    a, b = legs[0], legs[1]
    review_a = a["first"]["review"]
    review_b = b["first"]["review"]

    verdict("review_id 跨库一致",
            review_a["review_id"] == review_b["review_id"],
            [f"{labels[0]}={review_a['review_id']!r}", f"{labels[1]}={review_b['review_id']!r}"])
    verdict("overall_verdict 跨库一致",
            review_a["overall_verdict"] == review_b["overall_verdict"],
            [f"{labels[0]}={review_a['overall_verdict']!r}",
             f"{labels[1]}={review_b['overall_verdict']!r}"])
    verdict("完整 review payload 跨库一致(引擎输出逐位一致)",
            review_a == review_b, diff_dicts(review_a, review_b))
    verdict("review payload 与种子 plan 关联一致(plan_id)",
            review_a["plan_id"] == plan["plan_id"] and review_b["plan_id"] == plan["plan_id"])

    second_a, second_b = a["second"], b["second"]
    verdict("rereview: revision=1 且 parent 链接跨库一致",
            second_a["revision"] == 1 and second_b["revision"] == 1
            and second_a["parent_review_id"] == second_b["parent_review_id"]
            == review_a["review_id"],
            [f"{labels[0]}={second_a['revision']}/{second_a['parent_review_id']!r}",
             f"{labels[1]}={second_b['revision']}/{second_b['parent_review_id']!r}"])
    verdict("rereview: review_id 跨库一致",
            second_a["review"]["review_id"] == second_b["review"]["review_id"],
            [f"{labels[0]}={second_a['review']['review_id']!r}",
             f"{labels[1]}={second_b['review']['review_id']!r}"])
    verdict("rereview: 完整 payload 跨库一致",
            second_a["review"] == second_b["review"],
            diff_dicts(second_a["review"], second_b["review"]))

    pa, pb = a["persisted"], b["persisted"]
    verdict("持久化 plan_snapshots 跨库一致(plan_digest + payload)",
            pa["plan_snapshots"] == pb["plan_snapshots"],
            diff_dicts(pa["plan_snapshots"], pb["plan_snapshots"]))
    verdict("持久化 plan_items 行哈希集合跨库一致",
            [h for h, _ in pa["plan_items"]] == [h for h, _ in pb["plan_items"]],
            [f"{labels[0]}={[h[:12] for h, _ in pa['plan_items']]}",
             f"{labels[1]}={[h[:12] for h, _ in pb['plan_items']]}"])
    verdict("持久化 ontology_reviews 跨库一致(排除墙钟 created_at)",
            pa["ontology_reviews"] == pb["ontology_reviews"],
            diff_dicts(pa["ontology_reviews"], pb["ontology_reviews"]))
    verdict("持久化 ontology_review_items 行哈希集合跨库一致",
            [h for h, _ in pa["ontology_review_items"]] == [h for h, _ in pb["ontology_review_items"]],
            [f"{labels[0]}={len(pa['ontology_review_items'])} 行",
             f"{labels[1]}={len(pb['ontology_review_items'])} 行"])

    timestamps_ok = all(
        row.get("created_at_tz_aware", False)
        for leg in legs
        for row in leg["persisted"]["ontology_reviews"].values()
    )
    verdict("created_at 均为带时区时间戳(不参与逐位比对)", timestamps_ok)
    return ok


def build_release(modules: dict, worktree: Path):
    manifest = modules["build_publication_manifest"](root=worktree, **RELEASE_CONSTS)
    return modules["ReviewRelease"].from_manifest(
        manifest, confidence_state_version="drill-double-run", root=worktree,
    )


def sqlite_engine(modules: dict, path: Path):
    return modules["init_db"](f"sqlite:///{path}")


def pg_engine(modules: dict, url: str):
    if url.startswith("postgresql+psycopg://"):
        fail(
            "PG_DRILL_URL 用了 postgresql+psycopg://(psycopg3)方案;"
            "本演练环境安装的是 psycopg2-binary,请改用 postgresql:// 或 postgresql+psycopg2://。"
        )
    engine = modules["build_engine"](url)
    inspector = modules["inspect"](engine)
    existing = set(inspector.get_table_names())
    missing = REQUIRED_PG_TABLES - existing
    if missing:
        fail(
            f"PG 中缺少表:{sorted(missing)}。请先完成 docs/pg-drill-runbook.md 第 3 步"
            "(create_all 五张基础表 + ONTOLOGY_DATABASE_URL=$PG_DRILL_URL alembic upgrade head)。"
        )
    return engine


def main() -> None:
    import os

    parser = argparse.ArgumentParser(description="SQLite vs PG 确定性双跑(换库演练灵魂步)")
    parser.add_argument("--worktree", default=str(DEFAULT_WORKTREE),
                        help="Hannah 集成分支 worktree 路径(引擎代码来源)")
    parser.add_argument("--plan", default=None,
                        help=f"种子 plan JSON(默认 {DEFAULT_WORKTREE / DEFAULT_PLAN_REL})")
    parser.add_argument("--sqlite-only", action="store_true",
                        help="离线自检:两个独立临时 SQLite 互比,不连接 PG")
    args = parser.parse_args()

    worktree = Path(args.worktree).resolve()
    import_worktree(worktree)

    modules = {}
    from sqlalchemy import inspect, select
    from sqlalchemy.orm import Session
    from campaign_optimizer.ontology import db as db_mod
    from campaign_optimizer.ontology import publication as pub_mod
    from campaign_optimizer.ontology import review_workflow as wf_mod

    modules.update({
        "init_db": db_mod.init_db, "build_engine": db_mod.build_engine,
        "canonical_digest": db_mod.canonical_digest,
        "ClientRow": db_mod.ClientRow, "PlanSnapshotRow": db_mod.PlanSnapshotRow,
        "PlanItemRow": db_mod.PlanItemRow, "OntologyReviewRow": db_mod.OntologyReviewRow,
        "OntologyReviewItemRow": db_mod.OntologyReviewItemRow,
        "build_publication_manifest": pub_mod.build_publication_manifest,
        "ReviewRelease": wf_mod.ReviewRelease, "ReviewWorkflow": wf_mod.ReviewWorkflow,
        "Session": Session, "select": select, "inspect": inspect,
    })

    plan_path = Path(args.plan) if args.plan else worktree / DEFAULT_PLAN_REL
    if not plan_path.exists():
        fail(f"种子 plan 不存在:{plan_path}")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    release = build_release(modules, worktree)
    client_id = f"drill-{uuid.uuid4().hex[:12]}"
    print(f"种子 plan:{plan_path}(plan_id={plan.get('plan_id')})")
    print(f"release:ontology={release.ontology_version} package={release.package_checksum[:16]}…")
    print(f"client_id:{client_id}(每次运行唯一,避免与演练库历史数据冲突)")

    results: list[tuple[str, dict]] = []
    with tempfile.TemporaryDirectory(prefix="drill_double_run_") as tmp:
        sqlite_path = Path(tmp) / "leg_a.sqlite"
        engine_a = sqlite_engine(modules, sqlite_path)
        leg_a = Leg("SQLite", engine_a, modules, release, plan, client_id)
        results.append(("SQLite", leg_a.run()))
        engine_a.dispose()

        if args.sqlite_only:
            sqlite_path_b = Path(tmp) / "leg_b.sqlite"
            engine_b = sqlite_engine(modules, sqlite_path_b)
            leg_b = Leg("SQLite-B(离线对照)", engine_b, modules, release, plan, client_id)
            results.append(("SQLite-B", leg_b.run()))
            engine_b.dispose()
        else:
            url = os.environ.get("PG_DRILL_URL", "")
            if not url:
                fail("缺少 PG_DRILL_URL 环境变量。离线自检请加 --sqlite-only。")
            engine_pg = pg_engine(modules, url)
            leg_pg = Leg("PostgreSQL", engine_pg, modules, release, plan, client_id)
            try:
                results.append(("PostgreSQL", leg_pg.run()))
            finally:
                engine_pg.dispose()

    ok = check(results, plan)
    if ok:
        print("\n结果:PASS —— 双跑一致。PG 侧持久化与 SQLite 逐位对齐,可作为切换依据之一。")
        raise SystemExit(0)
    print("\n结果:FAIL —— 双跑不一致。切换必须暂停,先分析上面的差异明细。")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
