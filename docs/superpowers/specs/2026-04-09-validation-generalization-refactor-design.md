# 2026-04-09 Validation Generalization Refactor Design

## Goal

把当前验证与修复链从“已知 family 覆盖引擎”重构成“证据驱动、覆盖感知、对未知 case 能诚实降级”的系统。

本轮设计的目标不是单纯提升 benchmark 通过率，而是借助 benchmark 识别系统性盲点，提升在未见任务上的稳健性。

## Problem Statement

当前系统已经具备较强的可观测性与 artifact inspectability，但在验证与失败理解上仍有明显的过拟合倾向：

1. `validate_requirement` 将通用几何健康检查与 family-specific requirement checks 混在同一条 builder 链中。
2. `blocker_taxonomy` 将 blocker、family、recommended tools、repair lane 强绑定，导致系统过早收窄。
3. `skill_pack` 大量依赖 requirement phrase、family taxonomy 和已知失败族，容易把 benchmark 优化推向 coverage engine。
4. 面对未知 case，系统缺少显式的 `insufficient_evidence` / `unknown_case` 状态，导致“其实没看懂，但看起来像某个 family”时仍继续高置信输出。

当前问题不是“完全没有验证能力”，而是：

1. 系统已经很会处理熟悉问题。
2. 系统已经很会整理既有证据。
3. 系统对未知问题的诊断与修复桥接仍然偏弱。

## Design Principles

1. 泛化优先于 benchmark case 收口。
2. 事实提取与语义解释必须分层。
3. 没有足够证据时，系统必须能明确表达“不足以判断”。
4. family logic 只能作为解释器插件，不能继续做 runtime 主导控制面。
5. benchmark 的职责是暴露泛化缺口，而不是驱动新一轮 case-specific rule accumulation。
6. 维持 inspectable artifact contract 与稳定外部接口：
   - `sub_agent_runtime.contracts.IterationRequest`
   - `sub_agent_runtime.contracts.IterationRunResult`
   - `aicad-iter-run`

## Target Architecture

### 1. Evidence Extraction Layer

这一层只负责提取事实，不做 requirement-family 判定。

输入来源：

1. `execute_cadquery` / `apply_cad_action` 产物
2. `history`
3. `query_snapshot`
4. `query_geometry`
5. `query_topology`
6. `render_view`
7. STEP 与其他 artifact metadata
8. 代码文本本身

输出统一为 `evidence_bundle`，包含但不限于：

1. 通用几何事实：
   - solids
   - volume
   - bbox
   - issue count
   - merge/connectivity
2. 通用局部结构事实：
   - center sets
   - span
   - symmetry
   - repetition
   - axis alignment
   - face-local candidates
3. 行为与产物事实：
   - action sequence
   - write success/failure
   - code structure facts
   - image availability
   - STEP metadata

这一层禁止直接输出下列结论：

1. `path_sweep failed`
2. `annular_groove blocker`
3. `repair_lane=probe_first`

### 2. Coverage-Aware Interpretation Layer

这一层负责把 requirement clauses 与 evidence bundle 对齐，并显式表达覆盖情况。

输出状态不再只包含 pass/fail，而是：

1. `verified`
2. `contradicted`
3. `insufficient_evidence`
4. `not_applicable`

这一层必须回答：

1. `what_we_know`
2. `what_is_contradicted`
3. `what_we_cannot_verify`
4. `coverage_confidence`
5. `recommended_next_evidence`

核心变化：

1. 缺少 coverage 时不再自动落入某个 family blocker。
2. 未知 case 上优先进入补证据路径，而不是补熟悉 case 的 repair lane。

### 3. Family Adapters

保留少量高价值 family logic，但将其降级为 adapter。

adapter 的约束：

1. 只能消费 `evidence_bundle`
2. 不能绕开证据层直接私读 history/snapshot 做私有强结论
3. 只能补充解释，不能单独决定 stop/continue 或 repair lane

adapter 的职责：

1. 将通用 evidence 翻译成某类 requirement 的附加解释
2. 暴露更有信息量的 signal
3. 在 coverage 充分时支持更窄的 repair surface

### 4. Runtime Decision Surface

runtime 下一轮应看到更中性的 surface，而不是被系统预解释过度的 family summary。

建议 surface：

1. `what_we_know`
2. `what_is_missing`
3. `what_is_contradicted`
4. `coverage_confidence`
5. `recommended_next_evidence`
6. `repair_surface_available`

目标是让 runtime/模型在未知 case 上优先执行 query/probe/evidence gathering，而不是被硬推到熟悉 family。

## Module-Level Changes

### A. `src/sandbox_mcp_server/service.py`

`validate_requirement` 改为两阶段：

1. 生成 `evidence_bundle`
2. 生成 `interpretation`

兼容策略：

1. 现有 `checks`
2. 现有 `core_checks`
3. 现有 `diagnostic_checks`

在第一阶段仍保留对外 contract，但改为从 interpretation 投影生成，而不是继续直接由 `_build_*_checks` 主导。

新增内部对象建议：

1. `RequirementEvidenceBundle`
2. `RequirementClauseInterpretation`
3. `RequirementInterpretationSummary`

### B. `src/common/blocker_taxonomy.py`

从“修复路线表”降级为“观察标签归一化表”。

拆成两类输出：

1. `observation_tags`
   - 例如 `missing_axis_alignment_evidence`
   - 例如 `repeated_write_without_new_geometry_signal`
2. `decision_hints`
   - 例如“更适合先读 topology than blind rewrite”

禁止继续在 taxonomy 内直接绑定：

1. 强 repair lane
2. 强 family-driven tool choice
3. runtime 收敛策略

### C. `src/sub_agent_runtime/skill_pack.py`

从 family skill pack 改为 evidence-gap strategy pack。

skill 触发条件不再主要基于：

1. requirement phrase
2. family taxonomy
3. 已知 benchmark failure keyword

而应基于：

1. 当前缺什么证据
2. 当前是否已经过早收窄
3. 当前是否在重复低信息 retry
4. 当前 write/read 组合是否没有带来新的信息增量

示例：

旧：

1. `path_sweep -> give path_sweep guidance`

新：

1. `rail evidence missing + profile evidence partial + same write failed twice -> probe rail/profile/frame before another rewrite`

### D. `FamilyRepairPacket`

保留，但触发门槛提高。

仅在以下条件满足时允许生成：

1. coverage 足够高
2. adapter 或证据解释层证明当前 repair surface 已高置信可执行

若只有“像某个 family”，但 coverage 不足，则只能输出：

1. `insufficient_evidence`
2. `recommended_next_evidence`

不能直接发 packet。

## Stop / Continue Policy Changes

`complete` 必须同时满足：

1. 通用几何硬约束通过
2. 关键 requirement clauses 已 `verified`
3. 没有关键 clauses 仍为 `contradicted`

若关键 clauses 仍为 `insufficient_evidence`，默认不能判完成。

这一条用于防止：

1. validator 没覆盖到某个 requirement，但因为没有 blocker 就误判 complete

## Benchmark Role Redefinition

benchmark 不再主要驱动“缺哪个 family 就补哪个 rule”。

新角色：

1. `regression detector`
2. `generalization sampler`
3. `failure-cluster monitor`

新增关注指标：

1. `premature_narrowing_rate`
2. `insufficient_evidence_rate`
3. `validator_evaluator_disagreement`
4. `same_signal_repeated_retry_count`
5. `unknown_case_graceful_handling_rate`

benchmark 应帮助识别：

1. 哪些 case 被过早收窄
2. 哪些 case 的 evidence surface 不够
3. 哪些 case 的 interpretation 仍然依赖 case-specific adapter

## External Corpus Strategy

以下外部仓库不应直接转化为新 validator rules，而应作为分布扩展语料：

1. `CadQuery/cadquery-contrib`
2. `tanius/cadquery-models`
3. `michaelgale/cq-gridfinity`

接入方式分两层：

1. `corpus index`
   - 记录 repo、file、modeling theme、feature types、artifact availability
2. `curated stress set`
   - 选择一小批代表性 case
   - 用于验证 evidence extraction 与 interpretation 在新风格上的稳健性

建议用途：

1. `cadquery-contrib`
   - sweep / loft / selector / advanced examples / multi-style CadQuery usage
2. `cadquery-models`
   - parametric parts / small-to-mid mechanical shapes / shape variants
3. `cq-gridfinity`
   - shells / compartments / holes / multi-option parameter combinations / real product-like CAD patterns

## Run Naming And Archival Policy

规范收紧：

1. 新生成 run 目录必须严格使用 `YYYYMMDD_HHMMSS`
2. 语义说明不得继续写入目录名，只能放到目录内 metadata

历史 run 整理策略：

1. 不直接覆盖旧引用路径
2. 使用归档分层

建议目录：

1. `benchmark/runs/archive/pre_20260406/`
2. `test_runs/archive/pre_20260406/`

归档对象：

1. 三天前 run
2. 非规范命名 run

保留特殊入口：

1. `latest`
2. `by_practice`

但这些不算 canonical run dir。

## Documentation Requirements

这次重构必须同步更新：

1. `CODEX.md`
2. `docs/cad_iteration/SYSTEM_RECORD.json`
3. `docs/cad_iteration/DESIGN_INTENT.md`
4. `docs/cad_iteration/ITERATION_PROTOCOL.md`
5. `docs/cad_iteration/TOOL_SURFACE.md`

并新增过程文档：

1. 当日工作日志
2. 当日 checklist JSON
3. 外部 corpus/stress set 说明

## Phase 1 Scope

### In Scope

1. 在 `validate_requirement` 内部引入 `evidence_bundle` 与 `interpretation` 中间层
2. 保持现有外部 validation contract 兼容
3. 降级 `blocker_taxonomy` 与 `skill_pack`
4. 给 benchmark 增加泛化诊断指标
5. 建立最小版外部 corpus index 与 curated stress set
6. 归档三天前和非规范命名 runs
7. 同步文档、工作日志与 checklist

### Out Of Scope

1. 不把 `LLM judge` 或 `sub-agent` 接入 runtime 主回路
2. 不追求一次性删光所有 family adapters
3. 不为了单个 benchmark case 再补新的 case-specific validator / skill prose
4. 不破坏稳定外部 contracts

## Success Criteria

第一阶段完成后，系统应满足：

1. 能显式输出 `insufficient_evidence`，而不是只有 pass/fail。
2. runtime 在 coverage 不足时不再自动收窄到高置信 family repair lane。
3. `skill_pack` 中 requirement phrase / case phrase 驱动分支明显下降。
4. benchmark 诊断能看到新的泛化指标。
5. 外部样本已进入 corpus/stress set，而不只是作为参考链接。
6. run 目录完成一次结构化归档，新增目录恢复一致命名。
7. 有 focused unit tests、至少一个真实 probe、以及 benchmark / 外部样本验证证据。

## Risks

1. 兼容层过长，导致新架构被旧 `checks` 表面重新绑死。
2. adapter 收缩不彻底，系统仍继续通过 family rules 模拟“看懂了”。
3. benchmark 指标若仍过度强调 pass rate，会重新把团队拉回 coverage engine。
4. 归档若直接改旧路径，会破坏历史工作日志中的证据引用。

## Open Decisions Deferred To Implementation Planning

1. `evidence_bundle` 的具体 schema 边界
2. `interpretation` 与现有 `RequirementCheck` 的映射细节
3. benchmark 诊断字段落在 `summary.json`、`brief_report.md` 还是 `run_diagnostics.md`
4. corpus index 使用 JSON 还是 Markdown + JSON hybrid
5. 历史 runs 采用移动、软链还是 manifest-based archive 索引

## Recommendation

按 Phase 1 先重构证据面与解释面，再决定是否需要在后续阶段把 `LLM/sub-agent` 引入 runtime judge 层。

先做这一层的原因是：

1. 当前瓶颈首先是系统过早硬编码，而不是缺少更强的 judge。
2. 没有 coverage-aware evidence surface 时，直接引入 LLM 也只会读取被旧规则污染的 surface。
3. 若这层打稳，后续不论保留 rule、引入 LLM，还是混合两者，系统边界都会更清楚。
