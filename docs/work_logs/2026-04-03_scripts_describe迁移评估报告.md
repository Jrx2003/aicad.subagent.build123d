# `scripts/describe` 思路理解与迁移评估报告

日期：2026-04-03

## 1. 背景

这份报告用于说明我对 `scripts/describe/experiment_1` 新增文件的理解，以及这些思路在当前迭代式 CAD runtime 中的可迁移部分、已经落地的迁移内容、实际效果与后续建议。

本轮重点看的文件是：

- `scripts/describe/experiment_1/FEATURE_DETECTION_README.md`
- `scripts/describe/experiment_1/describe.py`
- 相关测试与示例 STEP 文件

## 2. 我对徐老师思路的理解

我认为这套代码真正想解决的，不是“多识别几个几何体类型”这么简单，而是下面这三个更核心的问题。

### 2.1 把底层 B-Rep 信息压缩成 feature-level digest

`describe.py` 并没有停在面、边、曲面类型计数这一层，而是在主动把这些原始信息重组为更高层的可消费对象，例如：

- `FeatureInfo`
- `SolidInfo`
- `RelationInfo`
- `SimilarityInfo`

这意味着它想做的是：

1. 从“几何面/拓扑关系很多、很碎、很难直接指导下一步动作”的底层表示，
2. 压缩成“孔、槽、凸台、层叠圆柱、孔阵列、主体尺寸、所在面、贯穿性”等更接近设计意图的特征表示。

这一点对我们当前项目非常重要，因为我们现在确实存在 relation 信息多、重复、对 planner 不够友好的问题。

### 2.2 把“主体”和“附加/减去特征”分开看

从 README 和代码都能看出来，这套思路不是把整个几何当成一个平面化整体描述，而是倾向于先识别主体，再识别附着其上的特征：

- 主体可能是 cylinder / box / prism / stacked cylinders
- 特征再区分为 hole / boss / slot / pocket 等
- 每个特征再记录：
  - 位置
  - 轴向
  - 所在面
  - 是否贯穿
  - 尺寸参数

这背后的价值是：它天然比“无差别列出所有 relation”更接近建模过程。

也就是说，同事的方向本质上是在回答：

“当前实体已经具备哪些有语义的局部结构，还缺哪些真正重要的局部结构？”

这个问题正是我们 iterative loop 当前最需要回答的。

### 2.3 让描述服务于诊断，而不是只服务于展示

`describe.py` 的输出是可读的，但它的价值不仅是“描述模型长什么样”，更重要的是：

- 可以看出某个孔到底是盲孔还是通孔
- 可以看出某个槽是不是开在边界上
- 可以看出某个凸台是从哪个面长出来的
- 可以看出一个复杂实体能否被近似解释成“主体 + 局部特征”

这使它天然适合作为失败诊断和 feature completeness 分析工具。

所以我对同事方案的总结是：

> 这不是在增加更多几何细节，而是在把几何细节提炼成更适合规划、校验、诊断使用的特征摘要层。

## 3. 为什么这套思路对我们有价值

当前项目的一个典型问题是：

- planner 经常看到很多 relation / topology / snapshot 信息
- 但这些信息并不总能告诉它“下一步最重要的 feature 是什么”
- 相反，关系过多时反而会稀释顺序感和局部目标

这在 `L2_192` 暴露得很明显：

1. 第 1 轮已经做出了主体法兰
2. 第 2 轮真正该做的是底面 boss
3. 但 planner 被更晚的顶面 cut / pattern 信息带偏，直接跳过了更早的 feature phase

这个失败说明我们现在的问题并不只是执行出错，还包括：

- planner 输入中的“顺序信息”不够强
- relation 信息过多但 feature phase 信息不够显式
- 数据结构对“下一步该补哪个 feature”支持不足

而 `scripts/describe` 最有价值的地方，恰好就是在补这层 feature abstraction。

## 4. codex认为不应该直接照抄的部分

虽然方向是对的，但我并不建议把 `describe.py` 原样接进 runtime，原因有四个。

### 4.1 它当前是独立实验脚本，不是正式 query contract

我们的 runtime 当前依赖的是正式 query tool surface，例如：

- `query_snapshot`
- `query_geometry`
- `query_sketch`
- `query_topology`
- `validate_requirement`

而 `describe.py` 目前是一个独立的 STEP 逆向分析实验，不在官方 contract 里。

如果直接把它塞进主循环，会有几个风险：

- 结果口径和现有 query tool 不一致
- prompt 里同时出现两套“事实来源”
- 出现冲突时难以判断谁是准绳

### 4.2 它是启发式 reverse analysis，不适合直接当 hard judge

`describe.py` 识别 boss / hole / slot 的办法，本质上是启发式几何解释，例如：

- 利用包围盒外扩判断 boss
- 利用长宽比、边界 margin 判断 slot
- 利用圆柱端点相对包围盒位置区分 hole 与 boss

这种方法很适合做诊断和摘要，但不适合一上来就做 runtime 中的强制判官。

因为在复杂模型上，它很可能出现：

- 摘要有帮助，但细节并不总是稳定
- 某些 feature 被误分类
- 对 planner 有参考价值，但不应直接覆盖原始 query truth

### 4.3 如果把它整个搬进来，token 可能反而更高

用户这轮特别指出了 token 过多的问题。`describe.py` 若直接输出大量 feature/relation/prose，很可能把问题变得更糟：

- 没有去重前，摘要层和原始层会并存
- planner 看到的信息量增加，不一定变得更清楚
- latency 和 timeout 风险会上升

所以正确做法不是“再加一层详细描述”，而是“用 feature abstraction 替换一部分低效 relation 噪音”。

### 4.4 它目前更适合离线诊断，而不是在线重执行闭环

同事当前这套代码最擅长的是：

- 看现成 STEP
- 解释它像什么
- 提炼特征摘要

但我们 runtime 要做的是：

- 用有限 token 支持逐轮下一步决策
- 在每轮都保持 prompt 紧凑
- 让 planner 知道下一步最该做什么

因此我判断：

`describe.py` 的最佳用途不是直接替代 runtime，而是给 runtime 提供“摘要设计方向”和“失败诊断启发”。

## 5. 我们已经迁移了什么

本轮我没有直接迁移整份 `describe.py`，而是迁移了它最有价值、且对当前系统最安全的核心思想：

> 从 relation-heavy 输入，转向 ordered feature digest。

### 5.1 新增 `feature_agenda`

已新增文件：

- `src/common/feature_agenda.py`

这个模块做的事情是：

1. 从 requirement 文本中提取有顺序的 feature phase
2. 结合 `action_history` 判断哪些 phase 已完成、哪些仍 pending
3. 给 planner 一份轻量、结构化、低 token 的 ordered feature digest

#### 核心代码实现

```python
# src/common/feature_agenda.py

def build_feature_agenda(
    requirements: dict[str, Any] | None,
    action_history: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    “””从需求文本构建有序的 feature agenda”””
    description = _requirement_description(requirements)
    if not description:
        return None

    # 1. 将需求文本切分为有序的 feature clauses
    clauses = _extract_feature_clauses(description)
    if not clauses:
        return None

    # 2. 统计已完成的 feature 类型
    completed_counts = _completed_feature_family_counts(action_history)
    consumed_counts = {key: 0 for key in completed_counts}
    items: list[dict[str, Any]] = []
    next_pending_phase: int | None = None

    # 3. 为每个 clause 判断状态并构建 agenda item
    for phase_index, clause in enumerate(clauses, start=1):
        action_family = _classify_clause_action_family(clause)
        if action_family is None:
            continue
        completed = consumed_counts[action_family] < completed_counts[action_family]
        if completed:
            consumed_counts[action_family] += 1
        status = “completed” if completed else “future”
        if not completed and next_pending_phase is None:
            status = “pending”
            next_pending_phase = phase_index
        items.append(
            {
                “phase”: phase_index,
                “status”: status,
                “action_family”: action_family,
                “face_targets”: _detect_clause_face_targets(clause),
                “summary”: _summarize_clause(clause),
            }
        )

    summary = (
        f”{len(items)} ordered feature phase(s); “
        f”next pending phase={next_pending_phase if next_pending_phase is not None else 'none'}.”
    )
    return {
        “summary”: summary,
        “next_pending_phase”: next_pending_phase,
        “items”: items[:8],
    }
```

#### Feature 分类逻辑

```python
def _classify_clause_action_family(clause: str) -> str | None:
    “””将需求文本分类为 action family”””
    text = clause.lower()
    if not text:
        return None
    if “fillet” in text or “chamfer” in text or “bevel” in text:
        return “edge_finish”
    if any(token in text for token in (“pattern”, “array”, “evenly distributed”)):
        return “pattern_feature”
    subtractive_tokens = (
        “cut extrude”, “cut-extrude”, “hole”, “remove material”, “subtract”
    )
    if any(token in text for token in subtractive_tokens):
        return “subtractive_solid”
    additive_tokens = (“extrude”, “revolve”, “loft”, “sweep”, “boss”, “pad”)
    if any(token in text for token in additive_tokens):
        return “additive_solid”
    return None
```

#### 为什么这样设计

- **轻量级**：不依赖几何查询，只从需求文本解析，零额外 token 开销
- **顺序感知**：利用文本中的 “first/then/after that” 等连接词识别 feature 顺序
- **可验证**：结合 `action_history` 统计已执行的 feature 类型，标记完成状态
- **面目标推断**：从 “on the top face”、”bottom side” 等描述提取 face_targets

它输出的信息包括：

- `phase`: 阶段序号
- `status`: `completed / pending / future`
- `action_family`: `additive_solid / subtractive_solid / pattern_feature / edge_finish`
- `face_targets`: 如 `[“top”, “side”]`
- `summary`: 阶段摘要
- `next_pending_phase`: 下一个待完成的阶段编号

这本质上就是把 `scripts/describe` 那种”特征级理解”迁移到了更轻量、更适配 planner 的形式。

### 5.2 在 planner prompt 中显式注入 feature order

已修改：

- `src/sub_agent/codegen.py`
- `src/sub_agent/prompts/codegen_action.md`

现在 planner 会收到 `feature_agenda`，并且 prompt 中明确写入规则：

- `feature_agenda` 是 ordered requirement-phase contract
- 不要跳过更早的 pending phase 去做更晚的 face/pattern phase

#### 代码生成器中的集成

```python
# src/sub_agent/codegen.py

from common.feature_agenda import build_feature_agenda

class CodeGenerator:
    def build_round_request_evidence(...):
        # ... 构建其他 evidence ...
        
        # 构建并压缩 feature_agenda
        compact_feature_agenda = self._compact_feature_agenda_for_prompt(
            build_feature_agenda(
                requirements=requirements,
                action_history=action_history,
            )
        )
        
        return {
            # ... 其他字段 ...
            “feature_agenda”: compact_feature_agenda,
        }

    def _compact_feature_agenda_for_prompt(
        self,
        feature_agenda: Any,
    ) -> dict[str, Any]:
        “””压缩 feature_agenda 控制 token 使用”””
        if not isinstance(feature_agenda, dict):
            return {}
        items_raw = feature_agenda.get(“items”)
        items = items_raw if isinstance(items_raw, list) else []
        compact_items: list[dict[str, Any]] = []
        # 只保留前6个phase，控制prompt大小
        for item in items[:6]:
            if not isinstance(item, dict):
                continue
            compact_items.append(
                {
                    “phase”: item.get(“phase”),
                    “status”: item.get(“status”),
                    “action_family”: item.get(“action_family”),
                    “face_targets”: self._normalize_string_list(
                        item.get(“face_targets”),
                        limit=3,
                    ),
                    “summary”: item.get(“summary”),
                }
            )
        return {
            “summary”: feature_agenda.get(“summary”),
            “next_pending_phase”: feature_agenda.get(“next_pending_phase”),
            “items”: compact_items,
        }
```

#### Prompt 规则注入

```markdown
<!-- src/sub_agent/prompts/codegen_action.md -->

## 约束条件

- Treat feature_agenda as the ordered requirement-phase contract for this round.
- Do not skip an earlier pending feature_agenda phase in favor of a later one 
  unless current evidence proves the earlier phase is already satisfied or impossible.

## 动作模式规则

- If the requirement also contains a separate additive boss/stud on another face 
  plus later hole/pitch-circle language, do not mix that secondary hole family 
  into the boss extrusion sketch; keep the boss window additive and leave the 
  hole family to its own later subtractive/pattern step.
```

#### 为什么这样设计

- **显式契约**：把 `feature_agenda` 明确定义为 ordered requirement-phase contract，而非可选参考
- **强制顺序**：prompt 中明确禁止跳过更早的 pending phase，直接针对 L2_192 的问题
- **token 控制**：压缩函数只保留最多6个phase，每个phase只保留关键字段
- **分离混合特征**：明确规则禁止把不同阶段的特征（如 boss 和 hole）混在一起处理

这一步非常关键，因为它直接针对 `L2_192` 那种”关系很多，但顺序感丢失”的问题。

### 5.3 用 next pending feature 反推 face targeting

已修改：

- `src/sub_agent_runtime/active_surface.py`
- `src/sub_agent_runtime/runner.py`

具体迁移方式不是”多给 planner 一堆关系”，而是：

1. 先从 `feature_agenda` 推出下一待完成 feature 的 face target
2. 再用这个 face target 去引导：
   - `active_surface` 的 target ref 优先级
   - `query_topology` 的 selection hints

也就是说，我们开始让”下一关键特征”决定当前关注面，而不是让 planner 在大量面关系里自己盲找。

#### Active Surface 中的集成

```python
# src/sub_agent_runtime/active_surface.py

from common.feature_agenda import (
    next_pending_feature_face_targets,
    next_pending_feature_summary,
)

def build_active_surface(...):
    # ... 构建基础 active_surface ...
    
    # 在 post_solid + face_edit_window 状态下，
    # 注入下一个 pending feature 的摘要
    pending_feature_summary = next_pending_feature_summary(
        requirements=requirements,
        action_history=action_history,
    )
    if (
        state_mode == “post_solid”
        and surface_type == “face_edit_window”
        and isinstance(pending_feature_summary, str)
        and pending_feature_summary.strip()
    ):
        rationale = f”{rationale} Next pending feature: {pending_feature_summary}.”

    active_surface = {
        “surface_id”: f”{surface_type}:{_latest_step(evidence_status, action_history)}”,
        “surface_type”: surface_type,
        “rationale”: rationale,
        # ... 其他字段 ...
    }
```

#### Target Ref 优先级重排

```python
def _target_ref_ids_for_surface(
    candidate_sets: list[dict[str, Any]] | None,
    topology_payload: dict[str, Any],
    surface_type: str,
    requirements: dict[str, Any] | None,
    action_history: list[dict[str, Any]] | None,
) -> list[str]:
    “””根据 pending feature 的 face_targets 重排 ref 优先级”””
    target_kind = “edge” if surface_type == “edge_feature_window” else “face”
    
    # 获取下一个 pending feature 偏好的 face targets
    preferred_face_targets = (
        next_pending_feature_face_targets(
            requirements=requirements,
            action_history=action_history,
        )
        if target_kind == “face”
        else []
    )
    
    prioritized_refs: list[str] = []
    fallback_refs: list[str] = []
    
    for item in candidate_sets:
        candidate_id = str(item.get(“candidate_id”, “”)).strip().lower()
        if target_kind not in candidate_id:
            continue
        item_refs = _normalize_string_list(item.get(“ref_ids”), limit=6)
        
        # 如果 candidate_id 匹配 preferred face targets，放入优先队列
        if preferred_face_targets and _candidate_id_matches_face_targets(
            candidate_id=candidate_id,
            face_targets=preferred_face_targets,
        ):
            prioritized_refs.extend(item_refs)
        else:
            fallback_refs.extend(item_refs)
    
    # 优先返回匹配 pending feature 的 refs
    refs.extend(prioritized_refs)
    refs.extend(fallback_refs)
    return list(dict.fromkeys(refs))[:6]


def _candidate_id_matches_face_targets(
    *,
    candidate_id: str,
    face_targets: list[str],
) -> bool:
    “””判断 candidate_id 是否匹配 face_targets”””
    normalized_targets = {
        str(item).strip().lower() for item in face_targets if isinstance(item, str)
    }
    if not normalized_targets:
        return False
    # 特殊处理 “side” 匹配 front/back/left/right
    if “side” in normalized_targets and any(
        token in candidate_id for token in (“front_”, “back_”, “left_”, “right_”)
    ):
        return True
    return any(f”{target}_” in candidate_id for target in normalized_targets)
```

#### Runner 中的调用点

```python
# src/sub_agent_runtime/runner.py

# 在构建 active surface 时传入 requirements 和 action_history
active_surface = build_active_surface(
    candidate_sets=topology_result.get(“candidate_sets”),
    topology_payload=topology_payload,
    evidence_status=evidence_status,
    requirements=request.requirements,  # 新增
    action_history=action_history,       # 新增
)
```

#### 为什么这样设计

- **面目标推断**：从 feature_agenda 自动推断 “next pending feature 应该落在哪个面”
- **优先级重排**：不是丢弃其他面，而是把更可能相关的面排在前面
- **零额外查询**：不增加新的 geometry query，只是重排已有 topology 结果的优先级
- **rationale 注入**：把 pending feature 信息写入 active_surface rationale，让 planner 在上下文中看到

这种方法比直接增加更多 relation/topology 信息更轻量，且更有针对性。

### 5.4 保留 full artifacts，但让 planner 先看摘要

我们没有删除 inspectability。当前策略是：

- 原始 artifacts 仍然全部保留（snapshot、geometry、topology、sketch 等）
- 但 planner prompt 中增加了更短、更有顺序感的 feature digest
- 通过 `_compact_feature_agenda_for_prompt` 控制 token，只保留最关键的 phase 信息

这符合项目的核心约束：

- evidence 不能隐藏（所有原始 query 结果仍可追溯）
- 但 planner 不应被低价值噪音淹没（feature_agenda 提供高信号摘要）

#### Token 控制策略

```python
# 压缩前后对比示例

# 原始 topology（可能数千 tokens）
{
    "faces": [
        {"id": "face:1", "type": "CYLINDER", "area": 1234.5, "normal": [0, 0, 1], ...},
        {"id": "face:2", "type": "PLANE", "area": 567.8, ...},
        # ... 数十个面
    ],
    "edges": [
        {"id": "edge:1", "type": "CIRCLE", "length": 314.15, ...},
        # ... 数百条边
    ]
}

# feature_agenda（约 100-200 tokens）
{
    "summary": "5 ordered feature phase(s); next pending phase=3.",
    "next_pending_phase": 3,
    "items": [
        {"phase": 1, "status": "completed", "action_family": "additive_solid", "face_targets": ["bottom"], "summary": "Create 100x15mm base disk"},
        {"phase": 2, "status": "completed", "action_family": "face_edit", "face_targets": ["top"], "summary": "Attach sketch to top face"},
        {"phase": 3, "status": "pending", "action_family": "subtractive_solid", "face_targets": ["top"], "summary": "Six 15mm holes on 70mm pitch circle"},
        {"phase": 4, "status": "future", "action_family": "edge_finish", "face_targets": [], "summary": "Fillet outer edges"},
    ]
}
```

#### 与现有 query contract 的关系

| Query Tool | 原始用途 | 与 feature_agenda 的关系 |
|------------|----------|--------------------------|
| `query_snapshot` | 当前模型完整状态 | 保留，feature_agenda 只读不写 |
| `query_geometry` | 几何体统计信息 | 保留，用于验证 feature 完成状态 |
| `query_topology` | 面/边拓扑详情 | 保留，但 selection hints 受 feature_agenda 指导 |
| `validate_requirement` | 需求完成度验证 | 保留，但执行频率可降低（phase-aware） |
| `feature_agenda` (新增) | Phase 摘要与顺序 | 轻量、每轮生成、无额外查询开销 |

#### 后续演进空间

当前 `feature_agenda` 是**纯文本派生**的（从 requirement 文本解析），这保证了零额外查询开销。未来可考虑：

1. **geometry-validated agenda**：用 `query_geometry` 验证 phase 是否真的完成
2. **delta agenda**：只返回从上轮到这轮的 phase 状态变化
3. **formal query artifact**：将 `feature_agenda` 升级为正式的 `query_feature_digest` tool

但前提是必须满足：

- 来源统一（不引入与现有 contract 冲突的信息源）
- Token 可控（保持轻量摘要特性）
- 延迟可接受（不增加显著的 geometry 查询时间）

## 6. 这次迁移后的实际效果

### 6.1 效果一：`L2_192` 的根因识别更清楚了

之前对 `L2_192` 的理解还停留在“boss / hole 混在一起执行出错”。这次进一步分析后，问题更明确了：

- 真正更底层的失败是 planner 跳过了应先做的 bottom-face boss phase
- 直接去做 later top-face cut/pattern phase

这说明之前确实缺少 feature order contract。

`feature_agenda` 的引入，正是为了修这个根因，而不是继续打零散补丁。

### 6.2 效果二：planner prompt 里开始出现真正有用的 phase summary

在新 run 中，planner 请求里已经能看到类似：

- `next_pending_phase`
- `summary`
- 每个 phase 的 `completed / pending / future`

例如 `L2_130` 在主体成形后，`feature_agenda` 明确告诉 planner：

- phase 4 additive extrude 已完成
- phase 6 subtractive bolt holes 仍 pending

这比单纯堆 `relation_focus` / `relation_eval` 更直接。

### 6.3 效果三：迁移方向开始真正降低“关系噪音”

这次迁移的关键不是“再多一层解释”，而是让一些决策不再直接依赖大块 relation 文本，而改为依赖：

- 当前阶段缺什么 feature
- 这个 feature 更可能落在哪个 face family 上

这是符合用户这轮需求的：

- 不再执着于过多相互关系
- 更关注真正重要的局部结构
- 减少重复冗余信息

## 7. 当前仍然没有完成的部分

虽然方向已经起效，但还没有走完。

### 7.1 还没有把 feature digest 升级成正式 query artifact

现在的 `feature_agenda` 主要来自：

- requirement text
- action history

它还不是从 geometry/query tool 输出中正式生成的统一 artifact。

下一阶段如果要继续迁移，可以考虑做一个官方、轻量、可控的：

- `query_feature_digest`

但前提是：

- 来源必须统一
- 不能和现有 query contract 打架
- 必须限制 token 膨胀

### 7.2 还没有完成 relation 信息的系统性瘦身

当前虽然已经开始“摘要优先”，但 relation 层仍然偏大，尤其在：

- topology summary
- relation_focus / relation_eval
- validate_requirement 重复回填

这部分还需要继续做：

1. phase-aware evidence gating
2. relation dedupe
3. post-solid 默认不再每轮都拉 `validate_requirement`
4. 对重复 snapshot/history 做 delta compression

### 7.3 还没有迁移到更完整的模型驱动工具编排

这轮迁移主要解决的是“特征顺序与面关注”的问题，还没有完全覆盖用户提到的另外几条方向：

- 更强的工具分区管理
- 更积极的并行只读查询
- 更模型驱动的隐式工具编排
- 更好的思考过程外显

其中只读 evidence 并行这条已经开始做了，但离 `claude-code` 风格还有距离。

## 8. 我的阶段性判断

如果总结成一句话：

> 同事这套 `scripts/describe` 最值得迁移的，不是“几何识别代码本身”，而是“把复杂几何压缩成有顺序的 feature digest，再用它指导下一步决策”的设计方向。

所以我当前的迁移策略是：

1. 不把 `describe.py` 原样塞进 runtime
2. 先迁移它最有价值的抽象层
3. 用更轻、更稳、更低 token 的方式接进 planner / active surface / topology targeting
4. 后续再决定是否把它升级成正式 feature query artifact

## 9. 已落地文件位置

本轮与这条迁移思路直接相关的落地位置如下：

- `src/common/feature_agenda.py`
- `src/sub_agent/codegen.py`
- `src/sub_agent/prompts/codegen_action.md`
- `src/sub_agent_runtime/active_surface.py`
- `src/sub_agent_runtime/runner.py`
- `tests/unit/sub_agent/test_codegen_aci.py`
- `tests/unit/sub_agent_runtime/test_active_surface.py`
- `tests/unit/sub_agent_runtime/test_runner_contracts.py`
- `docs/cad_iteration/ITERATION_PROTOCOL.md`
- `docs/cad_iteration/TOOL_SURFACE.md`
- `docs/cad_iteration/SYSTEM_RECORD.json`

## 10. 后续建议

下一步如果继续沿着这条线深化，我建议按这个顺序推进：

1. 先继续削减 planner 输入里的重复 relation / validation 内容
2. 让 feature-aware evidence gating 更严格，避免每轮过度查询
3. 评估是否增设正式的 `feature_digest` 类 query artifact
4. 再考虑把 `scripts/describe` 中更丰富的 hole/boss/slot 检测经验迁移成诊断工具，而不是直接迁移为 runtime 强规则

## 11. 结论

这次迁移不是“把同事代码抄过来”，而是已经吸收了它最关键的设计价值：

- 从 relation-heavy 转向 feature-centric
- 从静态几何罗列转向 ordered phase contract
- 从“看到很多关系”转向“知道下一步该补哪个关键 feature”

这对当前 iterative CAD loop 的意义，比继续堆更多 relation 输出更大。
