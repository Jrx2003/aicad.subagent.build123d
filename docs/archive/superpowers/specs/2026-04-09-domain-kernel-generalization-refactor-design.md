# 2026-04-09 Domain Kernel Generalization Refactor Design

## Goal

把当前运行时从“多套控制面并存”重构成“单一 CAD semantic kernel + 单一 evidence-first validation surface + 单一 runtime policy surface”。

第一目标不是 benchmark 通过率，而是面对新 case 时：

1. 不过早收窄到熟悉 family
2. 能稳定表达 `verified / contradicted / insufficient_evidence`
3. 能把局部复杂几何语义转成可修复、可验证、可继续推进的内核状态

## Why The Current Architecture Tops Out

当前主路径存在三套互相竞争的控制面：

1. `validate_requirement`
   - 负责 completion judgment
   - 仍保留大量 family-oriented builder / demotion logic
2. `blocker_taxonomy + skill_pack + FamilyRepairPacket`
   - 负责 family 分类、probe 倾向、repair lane 倾向
3. `active_surface + relation_feedback + old planner artifacts`
   - 虽然文档已降级，但代码仍存在大量兼容逻辑

这导致系统即使已经引入 `DomainKernelState`，仍会在关键时刻退回：

1. requirement phrase 驱动
2. family taxonomy 驱动
3. validator blocker 文本驱动

而不是稳定消费一套统一的 kernel/evidence state。

## Root Bottlenecks

### 1. Local-feature semantics are not kernel-level yet

当前系统仍大量依赖：

1. 2D center matching
2. face label guessing
3. family-specific post-hoc checks

这不足以解释：

1. `at z=20`
2. `through Y`
3. `outside the bore`
4. `shell remains open above the split line`
5. `union this pad with the shell`

### 2. Validation and repair are still family-first

即使 evidence-first layer 已进入 `validate_requirement`，runtime 仍会在这些地方重新 family 化：

1. blocker taxonomy
2. skill pack
3. feature probe family selection
4. kernel patch / repair packet prioritization

### 3. Legacy control surfaces still occupy runtime space

`active_surface`、`relation_feedback`、graph aliases、旧 planner 风格 prompt shaping 继续存在，会迫使新逻辑为了兼容而保留冗余路径。

## Target Architecture

### 1. Geometry Evidence Layer

只提取中性事实，不输出 family 结论。

Canonical outputs:

1. `body_evidence`
2. `topology_evidence`
3. `feature_observations`
4. `host_frames`
5. `boolean_residue`
6. `process_evidence`

### 2. Host-Frame Feature Layer

把局部特征统一建模成：

1. 宿主对象
2. 局部坐标系
3. 几何方向
4. span/depth/opening
5. anchor set
6. residual geometry

Canonical objects:

1. `HostFrame`
2. `FeatureObservation`
3. `AnchorObservation`
4. `DirectionConstraint`
5. `ResidualGeometryObservation`

### 3. Clause Assessment Layer

把 requirement clause 与内核事实对齐，不再以 validation check id 为主索引。

Canonical outputs:

1. `ClauseTarget`
2. `ClauseAssessment`
3. `ValidationAssessment`

`ClauseAssessment.status` 只允许：

1. `verified`
2. `contradicted`
3. `insufficient_evidence`
4. `not_applicable`

### 4. Repair Intent Layer

repair 入口不再主要是 family blocker，而是 generic repair intent。

Canonical outputs:

1. `repair_intent_id`
2. `repair_scope`
3. `repair_mode`
4. `requires_more_evidence`
5. `recommended_next_tools`

Examples:

1. `misaligned_host_frame`
2. `wrong_through_direction`
3. `missing_local_boolean_clearance`
4. `disconnected_merge_result`
5. `unverified_profile_frame`

### 5. Runtime Policy Layer

`agent_loop_v2` 只从这四类 surface 取决策输入：

1. `latest_write_health`
2. `ValidationAssessment`
3. `DomainKernelPatch`
4. `RepairIntent`

不再从：

1. requirement phrase heuristic
2. family taxonomy helper
3. old planner artifacts

重复生成一套第二决策层。

## Canonical Runtime Contract After Refactor

### DomainKernelState remains the canonical semantic state

但其内部主对象改为：

1. `FeatureObservation`
2. `ValidationAssessment`
3. `RepairIntent`
4. `DomainKernelPatch`

`FamilyRepairPacket` 只保留为兼容执行面，不再是主决策面。

### validate_requirement remains the canonical completion judge

但 judge 的输入来自：

1. geometry evidence
2. host-frame feature evidence
3. clause assessments

而不是 family-specific builder expansion。

### query_kernel_state remains the canonical semantic read tool

但输出重点变成：

1. latest validation assessment summary
2. active repair intents
3. active host-frame observations
4. active patch surface

而不是 family-lane summary。

## Phased Delivery

### Phase 1: Single Decision Surface

Objective:

1. 让 runtime、context、skill guidance 全部优先消费 `ValidationAssessment + RepairIntent`
2. 删掉第一批 requirement/family-based routing helpers

Expected removals after phase:

1. `requirement_prefers_code_first_family`
2. `recommended_feature_probe_families`
3. `agent_loop_v2` 中 requirement-based probe family 路由

### Phase 2: Host-Frame Local Feature Kernel

Objective:

1. 在 validation/kernel 中引入 `HostFrame`、`FeatureObservation`
2. 让 local feature clauses 不再主要依赖 2D center-set matching

Expected removals after phase:

1. 局部孔/槽/开口类 requirement 的旧中心点 fallback 主路径
2. 一批 family-specific local-anchor validators

### Phase 3: Legacy Surface Retirement

Objective:

1. 删掉 planner-era runtime control surfaces
2. 收拢 runtime 到单一路径

Expected removals after phase:

1. `active_surface.py`
2. `relation_feedback.py`
3. graph compatibility aliases
4. old prompt/planner compatibility branches no longer referenced by V2

## Required Documentation Changes

Code changes must follow doc updates in:

1. `docs/cad_iteration/SYSTEM_RECORD.json`
2. `docs/cad_iteration/DESIGN_INTENT.md`
3. `docs/cad_iteration/FEATURE_GRAPH_RUNTIME.md`
4. `docs/cad_iteration/ITERATION_PROTOCOL.md`
5. `docs/cad_iteration/TOOL_SURFACE.md`
6. `docs/cad_iteration/UPGRADE_ROADMAP.md`
7. `CODEX.md`

## Success Criteria

1. runtime 只依赖一套主控制面，不再重复做 family routing
2. kernel digest 暴露 generic validation assessment 与 repair intent
3. 新 case 上优先出现 `insufficient_evidence` 或 generic contradiction，而不是最近 family fallback
4. phase 完成后及时删除被替代逻辑，不保留长期双轨
5. benchmark 和外部 corpus 都能验证 unknown-case graceful handling 改善
