---
title: 代码审查记录 — 安全关键模块（as-built）
reviewer: 三层对抗（盲区/边界/验收一致性），子代理两次执行失败后由主会话亲审
scope: release_pin / reviewer_binding_v13 / intent_policy / exchange+output_guard（间接）/ app.py / 三脚本
status: final
created: 2026-08-14
---

# 代码审查记录

## Findings

### F-1 [medium] app.py manifest 选取仍为首匹配，与脊 AD-3 修订不符
- 位置：app.py（侧边栏 `next(v for v in ... if ontology_version == "2.0-campaign-pending")`）
- 问题：脊 AD-3 已规定"匹配必须唯一，多匹配即 fail-closed，禁止首匹配"；代码未同步。当前仅一份该 ontology_version 的 manifest，潜伏；R5 转正重发布后同 ontology_version 出现新 rule_version 时，侧边栏身份可能与投影原文分叉。
- 证据：架构审查 round-1 M-2；app.py 现状。
- 建议：改为唯一匹配断言（计数≠1 即安全回退），或按表面显式钉死 rule_version。

### F-2 [low] release_pin 畸形 manifest 抛 KeyError 而非 PackageDriftError
- 位置：release_pin.py `load_verified_manifests`（`root or bundle_root(manifest)` 先于形状校验求值）
- 问题：缺 source_commit 的畸形 manifest 抛 KeyError，错误类型不在安全码分类内。行为仍 fail-closed（异常上抛、调用失败），仅类型一致性瑕疵。
- 建议：先形状校验再求值 bundle_root，或捕获 KeyError 转 PackageDriftError。

### F-3 [low·接受] e2e 脚本泛异常吞栈
- 位置：run_three_role_e2e_v15.py `except Exception` 只显示安全类别
- 判定：AD-2 设计内（对外只暴露安全类别）；排障靠 dry 与离线测试。接受。

### F-4 [low·接受] constrain_tool_schema_v13 空白名单产出空 enum
- 位置：reviewer_binding_v13.py
- 判定：空 enum 下 provider 无法产出合法 source，本地门禁仍拒；fail-closed 由构造保证。接受。

### F-5 [info·接受] intent_policy 归一化方向保守
- 位置：intent_policy.py `_normalize`（标点/符号转空格、去 Cf）
- 判定：归一化只会让禁止模式更易爆发（保守方向），不会把该拒的放进；硬路由先于分类器。接受。

## 通过面

- release_pin：条目路径防穿越（绝对路径/`..`/重复拒）、尺寸+sha256 双验、重复 checksum 拒、形状严格相等。
- reviewer_binding_v13：digest/candidate 相等、allowlist 子集、ADD 动作 target 必空、其余 target 必在 claim 集；全程无值回显。
- intent_policy：硬拒先于锚定、锚定先于分类器；分类器置信度做 bool/有限/区间校验；chat-only 兼容面，initial_render 后端专属。
- 与脊 AD-1..AD-11 对照：除 F-1（AD-3 代码未同步）外无违反；O-6 为已记录开放项，除外。

## Triage

| 必修 | 建议 | 接受风险 |
|---|---|---|
| 0 | 0（F-1、F-2 已修，c3c1336，含新守护测试，全量 571 通过） | 3（F-3、F-4、F-5） |
