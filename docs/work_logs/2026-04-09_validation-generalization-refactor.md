# 2026-04-09 Validation Generalization Refactor

## 背景

这轮工作的起点不是“某几个 benchmark case 还没过”，而是当前验证与 skill 体系已经出现明显的 coverage engine 倾向：

1. 对熟悉 case 收敛较快
2. 对未知 case 容易依赖硬编码归类
3. validator、taxonomy、skill_pack 都存在过早收窄

用户明确要求本轮方向调整为：

1. 目标不是 benchmark pass rate 本身
2. benchmark 只是帮助发现系统性缺口
3. 系统必须朝泛化能力更强的方向重构
4. 会话内复盘是 coding agent 的职责，不应被偷换成 runtime 能力

## 本轮结论

本轮设计确认第一阶段采用：

1. `runtime 先去规则化`
2. 不把 `LLM judge` / `sub-agent` 直接接进 runtime 主回路
3. 先重构验证证据面与 coverage-aware interpretation

核心判断：

1. 现在的主要问题不是缺一个更强的 judge
2. 而是系统把理解工作过早前置到硬规则里
3. 直接在当前 surface 上叠 LLM，很可能只会把旧规则污染放大

## 已读证据

本轮已核对的关键材料：

1. `AGENTS.md`
2. `CODEX.md`
3. `docs/cad_iteration/SYSTEM_RECORD.json`
4. `docs/cad_iteration/INDEX.md`
5. `docs/cad_iteration/DESIGN_INTENT.md`
6. `docs/cad_iteration/ITERATION_PROTOCOL.md`
7. `docs/cad_iteration/TOOL_SURFACE.md`
8. `docs/work_logs/2026-04-09_validation-architecture-review-internal.md`
9. `src/sandbox_mcp_server/service.py`
10. `src/sub_agent_runtime/skill_pack.py`
11. `src/common/blocker_taxonomy.py`

外部 CadQuery 样本也已初步核对：

1. `CadQuery/cadquery-contrib`
2. `tanius/cadquery-models`
3. `michaelgale/cq-gridfinity`

## 设计决定

### 1. 验证链分层

新架构拆成：

1. `Evidence Extraction`
2. `Coverage-Aware Interpretation`
3. `Family Adapters`
4. `Runtime Decision Surface`

### 2. validator 改造方向

`validate_requirement` 不再继续直接扩 `_build_*_checks` 为主的 builder 链，而是：

1. 先生成 `evidence_bundle`
2. 再生成 `interpretation`
3. 兼容输出 `checks / core_checks / diagnostic_checks`

### 3. taxonomy / skill 改造方向

1. `blocker_taxonomy` 降级成 observation tag + decision hint
2. `skill_pack` 从 family skill pack 改成 evidence-gap strategy pack
3. `FamilyRepairPacket` 只在 coverage 充分时触发

### 4. benchmark 角色重定义

benchmark 后续应承担：

1. regression detector
2. generalization sampler
3. failure-cluster monitor

而不继续主要承担“驱动新增 family rules”的角色。

### 5. run 目录整理

第一阶段实现时将同时处理：

1. 三天前 runs 归档
2. 非规范命名 runs 归档
3. 新 runs 强制使用时间戳命名

## 当前边界

本轮仍处于设计阶段，尚未开始代码实现。

因此当前还没有执行：

1. run 目录归档
2. validation / skill / taxonomy 代码重构
3. benchmark 新诊断字段落地

这些内容将进入下一步 implementation plan。

## 输出文件

本轮设计已落地：

1. `docs/superpowers/specs/2026-04-09-validation-generalization-refactor-design.md`
2. `docs/work_logs/2026-04-09_validation-generalization-refactor.md`
3. `docs/work_logs/2026-04-09_validation-generalization-checklist.json`

## 下一步

等待用户 review 设计文档后，再进入 implementation planning：

1. 写 implementation plan
2. 执行 Phase 1 重构
3. 跑 focused tests / real probe / benchmark evidence

## Implementation Plan

设计文档已获批准，implementation plan 已写入：

1. `docs/superpowers/plans/2026-04-09-validation-generalization-phase1.md`

当前状态已从“等待 spec review”推进到“可开始按计划执行 Phase 1”。
