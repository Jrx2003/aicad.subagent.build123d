# Benchmark 失败案例深度分析报告

> 分析目标：2026-04-01 full run (20260401_150500_l2_full_after_corner_cut) 中的 4 个失败案例
> 分析维度：ReAct 模式、反馈机制、内容压缩、工具调用

---

## 1. 执行摘要

| 案例 | 状态 | 核心问题 | 根本原因分类 |
|------|------|----------|-------------|
| L2_130 | FAIL (0.18) | 生成 3 个 solids（期望 1 个） | 草图路径闭合问题 + 无几何约束检查 |
| L2_192 | FAIL (0.19) | 执行错误 `NCollection_Sequence::ChangeValue` | 执行失败未被 relation_eval 捕获 |
| L2_63 | FAIL (0.26) | 方向/朝向不匹配 | 无 pose 约束反馈机制 |
| L2_172 | FAIL (0.61) | 特征锚点偏差 1.0 | 坐标精度/定位问题 |

---

## 2. ReAct 模式实现分析

### 2.1 当前 ReAct 循环结构

**文件位置**: `src/sub_agent_runtime/runner.py:157-1500+`

```python
# 当前实现（简化）
for round_no in range(1, max_rounds + 1):
    # 1. 收集证据
    evidence_status = self._build_evidence_status(...)

    # 2. 构建关系反馈
    relation_focus, relation_eval = self._build_runtime_relation_feedback(...)

    # 3. 收集阻塞器
    latest_unresolved_blockers = self._collect_latest_unresolved_blockers(...)

    # 4. 生成行动计划
    plan = await self._generator.generate_actions(...)

    # 5. 执行动作
    for action in planned_actions:
        sequence_results = await self._sandbox.apply_action_sequence(...)
        last_action_result = sequence_results[-1]

        # 6. 错误处理（问题所在！）
        if not last_action_result.success:
            previous_error = last_action_result.stderr or last_action_result.error_message
            break  # 直接跳出，错误未进入 relation_eval
```

### 2.2 与标准 ReAct 的差异

| 标准 ReAct | 当前实现 | 问题 |
|-----------|---------|------|
| Observation 显式包含执行状态 | Observation 需从多来源组装 | 执行失败易被遗漏 |
| Thought 显式输出 | Thought 隐式包含在 prompt | 难以调试模型推理 |
| Action 原子性 | 每轮可执行 1-3 个动作 | 错误归因困难 |

### 2.3 关键发现：错误传递断裂

**文件位置**: `src/sub_agent_runtime/runner.py:1473-1477`

```python
if not last_action_result.success:
    previous_error = (
        last_action_result.stderr or last_action_result.error_message
    )
    break
```

**问题**: `previous_error` 只通过 prompt 传递给下一轮 LLM，**未被 `relation_eval` 捕获**。

对比 `_collect_latest_unresolved_blockers` (runner.py:10536-10586)：
```python
def _collect_latest_unresolved_blockers(...):
    unresolved.extend(snapshot.get("blockers", []))  # 来自 snapshot
    unresolved.extend(sketch_state.get("issues", []))  # 来自 sketch
    unresolved.extend(relation_eval.get("blocking_eval_ids", []))  # 来自 relation_eval
    # ❌ 缺少: last_action_result.success == False 时的错误信息
```

---

## 3. 内容压缩 (Context Compaction) 分析

### 3.1 当前预算限制（已实现）

**文件位置**: `src/sub_agent/codegen.py:32-50`

```python
_MAX_HISTORY_ITEMS = 8                    # 历史截断
_MAX_PROMPT_GEOMETRY_ITEMS_PER_TYPE = 6   # 几何项限制
_MAX_PROMPT_TOPOLOGY_ITEMS_PER_TYPE = 8   # 拓扑项限制
_MAX_PROMPT_RELATION_ITEMS = 12           # 关系项限制
```

### 3.2 关键漏洞：latest_action_result 绕过限制

**文件位置**: `src/sub_agent_runtime/runner.py:283-285`

```python
latest_action_result_payload = self._build_latest_action_result_payload(
    last_action_result  # ← 完整传递，未截断
)
```

**实测数据** (L2_164 round_04):
```
latest_action_result.snapshot: 218,622 chars
  - geometry_objects.edges: 120 items (超过 _MAX_PROMPT_GEOMETRY_ITEMS_PER_TYPE=6)
  - geometry_objects.faces: 99 items (超过限制)
  - action_history[0].result_snapshot: 1,630 chars (每轮累积)
```

**结果**: 实际 prompt 大小 2MB，远超预算。

### 3.3 压缩缺失点

| 数据路径 | 当前处理 | 应有处理 |
|---------|---------|---------|
| `action_history[].result_snapshot` | 原样保留 | 摘要化（保留关键指标） |
| `latest_action_result.snapshot` | 完整传递 | 截断至预算限制 |
| `query_topology.faces/edges` | 部分截断 | 按 relevance 排序后截断 |

---

## 4. 反馈机制详细分析

### 4.1 relation_feedback 架构

**文件位置**: `src/sub_agent_runtime/relation_feedback.py:18-141`

```python
def build_relation_feedback(...):
    # 只关注两类特征
    if _should_focus_sweep(...):
        focus_items.extend(_build_sweep_focus_items(...))
    elif _requirement_suggests_annular_focus(requirement_text):
        focus_items.extend(_build_annular_focus_items(...))
```

### 4.2 反馈覆盖缺口（按案例）

#### L2_130: 草图路径闭合问题

**需求**: 创建 C 形截面（两个同心半圆 + 底部法兰）
**问题**: `add_path` 创建的路径未正确闭合，导致挤压时生成 3 个独立 solid

**缺失反馈** (relation_feedback.py):
```python
# 当前检查
- annular_profile_section: pass ✓ (检测到同心圆)
- annular_topology_core: missing (因缺少 query_topology)

# 缺失检查
- path_closure_integrity: 路径是否形成闭合区域
- profile_solid_count_prediction: 挤压后 solid 数量预测
```

**文件位置**:
- 问题代码: `src/sub_agent_runtime/relation_feedback.py:49-57`
- 应补充: 路径闭合检查函数（不存在）

#### L2_192: 执行错误未被捕获

**需求**: 6 个均布孔（PCD 55mm，孔径 6mm）
**问题**: 最终 cut_extrude 执行时抛出 `NCollection_Sequence::ChangeValue`

**实际动作序列** (plans/round_04_response.json):
```json
{
  "action_type": "add_circle",
  "action_params": {
    "centers": [[27.5, 0], [13.75, 23.815], ...],  // 6 个中心点 ✓
    "radius": 27.5  // ← 这是 PCD 半径，不是孔半径！
  }
}
```

**模型错误**: 将 PCD 半径 27.5mm 当作圆半径，导致生成直径 55mm 的孔（远超法兰尺寸），cut 操作时拓扑崩溃。

**缺失反馈**:
```python
# relation_eval 显示 (round_04_relation_eval.json)
{
  "items": [{
    "eval_id": "eval:annular_topology_core",
    "status": "pass",  // ← 错误！执行已失败
    "score": 1.0
  }],
  "blocking_eval_ids": []  // ← 空！执行错误未被捕获
}
```

**文件位置**:
- 错误传播: `src/sub_agent_runtime/runner.py:1473-1477`（错误只进 previous_error）
- relation_eval 生成: `src/sub_agent_runtime/relation_feedback.py:120-139`（未检查 action 执行状态）

#### L2_63: 方向/朝向不匹配

**需求**: 3x3 阵列的半球形凹槽
**问题**: 生成的模型 bbox (50x50x15) 与 GT (50x50x25?) 不匹配，可能是方向问题

**缺失反馈**:
```python
# 当前无 pose/orientation 相关检查
# 应补充: _eval_pose_alignment(requirement_text, query_geometry)
```

#### L2_172: 沉头孔坐标精度

**需求**: 4 个沉头孔在坐标 (25,15), (25,45), (75,15), (75,45)
**问题**: centroid offset 0.499，feature-anchor deviation 1.0

**可能原因**:
- 坐标系统理解错误（相对 vs 绝对）
- 或者 hole 创建时未正确放置于指定坐标

---

## 5. 工具调用分析

### 5.1 当前工具集

**文件位置**: `src/sandbox_mcp_server/registry.py` (select_exposure_bundle_ids 附近)

可用工具:
- `query_snapshot` - 会话状态
- `query_sketch` - 草图几何
- `query_geometry` - 几何统计
- `query_topology` - 拓扑关系
- `render_view` - 渲染图像
- `validate_requirement` - 需求验证

### 5.2 工具调用决策

**文件位置**: `src/sub_agent_runtime/runner.py:504-577`

```python
inspection_policy = plan.inspection  # LLM 决定查询哪些工具
should_query_topology = self._inspection_section_requested(inspection_policy, "query_topology")
```

**问题**: 工具选择完全依赖 LLM 自觉，无强制策略。

### 5.3 缺失的关键工具

| 工具 | 用途 | 相关案例 |
|------|------|---------|
| `validate_path_closure` | 验证草图路径闭合性 | L2_130 |
| `check_hole_count` | 验证孔数量 | L2_192 |
| `predict_solid_count` | 预测挤压后 solid 数量 | L2_130 |
| `check_execution_health` | 捕获执行错误 | L2_192 |

---

## 6. 根因总结

### 6.1 架构层面问题

1. **反馈维度不足** (relation_feedback.py)
   - 只有 sweep/annular 两类检查
   - 缺少 hole/pattern 检查（影响 L2_192）
   - 缺少 path closure 检查（影响 L2_130）

2. **错误传递断裂** (runner.py:1473-1477)
   - 执行错误只进 `previous_error`，不进 `relation_eval`
   - `blocking_eval_ids` 无法反映真实阻塞状态

3. **Context 膨胀** (runner.py:283-285)
   - `latest_action_result` 完整传递，未截断
   - 导致 prompt 达 2MB，超出上下文限制

### 6.2 实现层面问题

| 位置 | 代码 | 问题 |
|------|------|------|
| `relation_feedback.py:49` | `elif _requirement_suggests_annular_focus` | 只检查 annular，无 hole pattern |
| `runner.py:283` | `_build_latest_action_result_payload` | 未应用 budget 限制 |
| `runner.py:1475` | `previous_error = last_action_result.stderr` | 错误未进入 relation_eval |
| `codegen.py:261` | `action_history[-_MAX_HISTORY_ITEMS:]` | 截断了历史但未摘要化 |

---

## 7. 改进建议优先级

### P0（关键阻塞）

1. **修复错误传递** (runner.py)
   ```python
   # 在 _collect_latest_unresolved_blockers 中添加
   if last_action_result and not last_action_result.success:
       unresolved.append(f"execution_error:{last_action_result.error_message}")
   ```

2. **截断 latest_action_result** (codegen.py)
   ```python
   # 在 _build_latest_action_result_payload 中
   snapshot = _truncate_snapshot(snapshot, max_items=24)
   ```

### P1（高优先级）

3. **添加 hole count 检查** (relation_feedback.py)
   - 新函数 `_eval_hole_pattern()`
   - 检查 `hole_count`, `pcd`, `diameter`

4. **添加 path closure 检查** (relation_feedback.py)
   - 新函数 `_eval_path_closure()`
   - 验证草图路径是否形成闭合区域

### P2（中优先级）

5. **实现 Context Compaction** (新文件)
   - `src/sub_agent_runtime/context_compact.py`
   - 按轮次分组，早期轮次摘要化

6. **强制工具调用策略** (runner.py)
   - 拓扑变化后强制 query_topology
   - 失败后强制 validate_requirement

---

## 8. 文件索引

| 文件路径 | 相关行号 | 内容描述 |
|---------|---------|---------|
| `src/sub_agent_runtime/runner.py` | 157-1500+ | ReAct 主循环 |
| `src/sub_agent_runtime/runner.py` | 283-285 | latest_action_result 构建 |
| `src/sub_agent_runtime/runner.py` | 10536-10586 | blockers 收集 |
| `src/sub_agent_runtime/runner.py` | 1473-1477 | 错误处理 |
| `src/sub_agent_runtime/relation_feedback.py` | 18-141 | 反馈构建主函数 |
| `src/sub_agent_runtime/relation_feedback.py` | 980-1012 | sweep 检查判断 |
| `src/sub_agent_runtime/relation_feedback.py` | 1013-1030 | annular 检查判断 |
| `src/sub_agent/codegen.py` | 32-50 | 预算限制常量 |
| `src/sub_agent/codegen.py` | 261 | action_history 截断 |
| `src/sandbox_mcp_server/registry.py` | 2212-2518 | 需求语义分析 |

---

## 9. 附录：失败案例详细时间线

### L2_130 执行时间线

```
Round 1: create_sketch → add_circle(25) → add_circle(17.5)
         relation_eval: annular_profile_section pass

Round 2: add_path (试图闭合 C 形)
         ⚠️ path 可能未正确闭合
         relation_eval: annular_profile_section pass (仅检查圆，不检查路径)

Round 3: add_rectangle (左法兰) → add_rectangle (右法兰)
         relation_eval: annular_profile_section pass

Round 4: extrude(40)
         ⚠️ 因路径未闭合，生成 3 个独立 solid

最终评估: solids=3 (期望 1) → FAIL
```

### L2_192 执行时间线

```
Round 1-3: 创建法兰和凸台
           relation_eval: annular_topology_core pass

Round 4: create_sketch → add_circle(centers=6个, radius=27.5) → cut_extrude
         ⚠️ radius=27.5 是 PCD 半径，不是孔半径！
         ⚠️ cut_extrude 抛出 NCollection_Sequence::ChangeValue

         relation_eval: 仍显示 pass (未检查执行错误)
         blocking_eval_ids: [] (空)

最终评估: 模型不完整 → FAIL
```

---

*报告生成时间: 2026-04-01*
*分析范围: benchmark/runs/20260401_150500_l2_full_after_corner_cut*
