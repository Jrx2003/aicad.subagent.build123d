# 2026-04-10 Validator / Skill / Token Progress Report

## 1. GitHub 外部仓库测试数据与产物路径

### 1.1 样本索引与 stress set

1. `benchmark/corpus/external_cadquery_index.json`
2. `benchmark/corpus/external_cadquery_stress_set.json`

### 1.2 cadquery-models

初始样本路径：

1. `.external_repos/cadquery-models/arcblock/arc_block.py`
2. `benchmark/corpus/external_cadquery_index.json`
   - `case_id = external_arcblock_sweep_001`

对应 probe / 产物路径：

1. `test_runs/20260410_134742`
2. `test_runs/20260410_135105`
3. `test_runs/20260410_141111`
4. `test_runs/20260410_173900`
5. `test_runs/20260410_174600`

本轮重点证据：

1. `test_runs/20260410_141111/actions/round_01_execute_cadquery.json`
   - 首轮被 `wrong_namespace.cadquery_math` preflight lint 挡下
2. `test_runs/20260410_141111/actions/round_02_execute_cadquery.json`
   - 第二轮被 `wrong_recipe.Workplane.sweep.added_face_profile` 挡下
3. `test_runs/20260410_141111/actions/round_03_execute_cadquery.json`
   - 第三轮真实成功写出 `model.step`
4. `test_runs/20260410_141111/queries/round_03_validate_requirement_post_write.json`
   - 当前剩余问题已经收缩到 `path_sweep_*` 与 `feature_countersink`
5. `test_runs/20260410_141111/summary.json`
   - fresh rerun 最终未收敛，但失败面已经压缩到 `code_path_family_gap`
6. `test_runs/20260410_174600/prompts/round_02_request.json`
   - 已明确包含：
     - `previous_tool_failure_summary.repair_recipe.recipe_id = swept_host_oriented_hole_boolean_recipe`
     - `runtime_skills.swept_host_local_hole_boolean_cutter_recipe`
   - 说明新的 swept-host repair surface 已经真实进入 live prompt，而不是只停在单元测试

### 1.3 cadquery-contrib

初始样本路径：

1. `.external_repos/cadquery-contrib/examples/Remote_Enclosure.py`
2. `benchmark/corpus/external_cadquery_index.json`
   - `case_id = external_remote_enclosure_001`

对应 probe / 产物路径：

1. `test_runs/20260410_134741`
2. `test_runs/20260410_135104`
3. `test_runs/20260410_141112`

本轮重点证据：

1. `test_runs/20260410_141112/actions/round_01_execute_cadquery.json`
2. `test_runs/20260410_141112/queries/round_01_validate_requirement_post_write.json`
   - `shell` clause 已 `verified`
   - `lip_fit` 仍是 `insufficient_evidence`
   - `countersunk holes` 当前是 `contradicted`
3. `test_runs/20260410_135104/summary.json`
   - 暴露了旧 bug：模型给 `validate_requirement` 传入非法 `timeout_seconds`
4. `test_runs/20260410_141112/summary.json`
   - 最新 rerun 不再受 `timeout_seconds` 非法参数阻塞，但仍未收敛

### 1.4 cq-gridfinity

初始样本路径：

1. `.external_repos/cq-gridfinity/cqgridfinity/gf_box.py`
2. `.external_repos/cq-gridfinity/cqgridfinity/gf_ruggedbox.py`
3. `benchmark/corpus/external_cadquery_index.json`
   - `case_id = external_gridfinity_ruggedbox_001`
   - `case_id = external_gf_box_001`

对应 probe / 产物路径：

1. `test_runs/20260410_134743`
2. `test_runs/20260410_141239`

本轮重点证据：

1. `test_runs/20260410_134743`
   - 已用于验证 `Gridfinity shell + holes + dividers + scoops + flanges` 这类 feature-combinatorics case
2. `test_runs/20260410_141239`
   - 最新代码上的 fresh rerun，已跑完但未收敛

## 2. 今天的主要成果

### 2.1 Validator 改进

今天最重要的不是继续加 case rule，而是继续把 validator 往“证据面优先、泛化优先”推进。

已落地：

1. `src/sandbox_mcp_server/validation_evidence.py`
   - 扩大通用 evidence extraction，提升对 bbox / axisymmetric / local feature / geometry facts 的可消费性
2. `src/sandbox_mcp_server/validation_interpretation.py`
   - 新增和强化 clause-level interpretation
   - 让 `coverage_confidence / insufficient_evidence / clause_interpretations / observation_tags / decision_hints` 成为稳定 surface
3. `src/sandbox_mcp_server/service.py`
   - `validate_requirement` 输出和 legacy checks 的兼容投影继续保留，但决策核心已经更多转到 clause interpretation
4. `src/sandbox_mcp_server/validation_llm.py`
   - 验证环节已引入 live adjudication，而不是只靠纯硬规则

今天新增/强化的 validator 侧行为：

1. `path_sweep` clause set 可以读取最新 `execute_cadquery` 代码历史，而不是只看最早 snapshot
2. `path_sweep` geometry fallback 能吃到更完整的 rail/profile/result 证据
3. runtime 不再接受“文字 summary 说 passed，但 clause surface 其实没过”的假完成态
4. `validate_requirement` 的 runtime-managed 参数清洗已经修正：
   - 不再因为非法 `timeout_seconds` 直接浪费整轮
5. `counterbore` 现在已经进入 validator 主合同：
   - `feature_counterbore` 不再被错误挤进 `feature_countersink`
   - execute_cadquery snapshot 下的 stepped-cylinder counterbore geometry 已可被直接识别
6. `counterbored hole` clause interpretation 已独立：
   - 不再继续走旧的 `clause:countersink_feature`
   - 对新 case 会先落到更正确的 `verified / contradicted / insufficient_evidence`

当前对 validator 泛化能力的明确判断：

1. 当前 validator **已经不是纯硬规则**：
   - 现在是 `geometry/topology/process evidence + clause interpretation + validation_llm adjudication`
   - LLM 已进入 live validation surface，而不是只在 benchmark 评测侧旁路存在
2. 当前 validator **有一定新 case 泛化能力，但还不稳定**：
   - 对整体 body 尺寸、部分 axisymmetric 语义、mixed clause、部分 execute_cadquery path-sweep wording，已经明显比旧版强
   - 对完全没见过的新 case，不会再像早期版本那样只能落到 family blocker；现在至少能更诚实地产生 `contradicted / insufficient_evidence / coverage_confidence`
3. 当前 validator **仍然没有达到“对新 case 稳定泛化”的目标**：
   - 复杂局部 host-frame 语义仍弱
   - execute_cadquery 直接生成的 sweep / hole / counterbore 链路仍有 family-heavy 盲区
   - 外部仓库 case 和剩余 L2 说明，泛化面虽然扩大了，但依然不够稳
4. 因此本轮工作的真实目标不是“再补一个 case rule”，而是：
   - 继续扩大通用 evidence surface
   - 继续减少 family-only validator gate
   - 继续让 validator 在新 case 上先给出正确的通用判断，再由 runtime/skill 修复

一句话结论：

1. `validator` 现在 **用了 LLM**
2. `validator` 现在 **有一定泛化能力**
3. 但距离“对新案例稳定泛化”仍有差距，所以今天后半段工作的重点已经转向：
   - execute_cadquery path-sweep generic fallback
   - centered-hole / pattern-hole disentangling
   - combined dimension clause parsing
   - host-face local feature chain integrity

### 2.1.1 Validator 中的 LLM 现在是怎么用的

这里需要明确边界，避免把现在的 validator 误解成“纯 LLM judge”。

当前形态是：

1. 先由程序化层构建 `evidence bundle`
   - 输入来自：
     - `snapshot.geometry`
     - `geometry_objects / topology`
     - `history / latest execute_cadquery code`
     - 通用 relation / feature checks
   - 这一层先把：
     - `bbox`
     - `volume`
     - `solids/faces/edges`
     - `axisymmetric radii`
     - `feature candidate geometry`
     - `path_sweep / hole / countersink / notch` 等补充 checks
     提取出来
2. 再由 clause interpreter 先做一轮 evidence-first 判断
   - 把 requirement 拆成 clause
   - 每个 clause 先根据通用 evidence 和 supplemental checks 产生：
     - `verified`
     - `contradicted`
     - `insufficient_evidence`
     - `not_applicable`
3. 然后 LLM adjudicator 参与收口
   - LLM 不是直接看一句 prompt 就拍脑袋判 pass/fail
   - 它看到的是经过整理的 validation surface
   - 重点是帮助处理：
     - mixed clause
     - wording variation
     - 规则面还没完全覆盖的新表达
     - 多条证据之间的语义归并
4. 最终 `validate_requirement` 输出仍然是结构化结果
   - 不是一段自由文本结论
   - 对 runtime 暴露的仍然是：
     - `clause_interpretations`
     - `coverage_confidence`
     - `insufficient_evidence`
     - `observation_tags`
     - `decision_hints`

LLM 现在**主要负责的事**：

1. 提升 clause 级语义判断的泛化性
2. 减少只因 wording 变化导致的 validator 误判
3. 在 evidence 足够但 rule surface 不够优雅时，帮助把结论收口到更合理的 clause 状态

LLM 现在**不负责的事**：

1. 不直接替代几何证据
2. 不替代 `bbox / topology / feature geometry` 这些底层事实
3. 不应该在没有 evidence 的情况下直接宣布完成
4. 不应成为 runtime repair lane 的唯一依据

为什么现在还不能只靠 LLM：

1. 新 case 的稳定泛化，不是单靠“更会读语义”就能解决
2. 如果底层没有：
   - host-frame
   - local feature anchor
   - path/profile/frame/result
   - counterbore/countersink/hole 的真实几何证据
   那么 LLM 也只能在不完整 surface 上做推断
3. 这会导致：
   - 误信代码意图
   - 误信 wording
   - evidence 不足时假完成

所以目前正确的架构原则是：

1. `LLM` 用来增强 validator 的语义泛化
2. `geometry/topology evidence` 负责提供可验证事实
3. runtime 仍应消费结构化 validation surface，而不是消费一段自由文本 judge

这也是为什么今天的修改方向不是“把 validator 完全改成 LLM judge”，而是：

1. 继续扩大通用 evidence 面
2. 继续修正 LLM 之前/之后的 clause grounding
3. 让 LLM 真正工作在一个更泛化、更稳定的验证中间层之上

代表性代码路径：

1. `src/sandbox_mcp_server/service.py`
2. `src/sandbox_mcp_server/validation_evidence.py`
3. `src/sandbox_mcp_server/validation_interpretation.py`
4. `src/sandbox_mcp_server/validation_llm.py`
5. `src/sub_agent_runtime/diagnostics.py`

### 2.2 Skill 补全

今天补的 skill 不是 case prose，而是把外部仓库和 L2 暴露出的高频失败模式压成更通用的 repair surface。

已增强的 skill / guidance：

1. `src/sub_agent_runtime/skill_pack.py`
   - `path_sweep_wire_profile_frame_repair`
   - `explicit_face_sweep_recipe_after_api_lint`
   - `counterbore_not_countersink_requirement`
   - `host_face_local_feature_chain_integrity`
   - `preserve_sweep_semantics_for_swept_requirement`
2. `src/sub_agent_runtime/tool_runtime.py`
   - preflight lint 不再只是拦错，还会附带 `repair_recipe`
3. `src/sub_agent_runtime/context_manager.py`
   - `previous_tool_failure_summary` 现在强制保留最关键的 `lint_hits` 和 `repair_recipe`

今天新增/强化的高价值 lint / guidance：

1. `wrong_namespace.cadquery_math`
2. `wrong_recipe.Solid.sweep.face_profile_positional`
3. `wrong_recipe.Workplane.sweep.added_face_profile`
4. `ambiguous_selector.faces.workplane`
5. `wrong_context.Workplane.local_feature_without_host`
6. `wrong_selector.faces_gtz_workplane_after_sweep`

今天新增的高价值 repair recipe / runtime skill：

1. `swept_host_oriented_hole_boolean_recipe`
   - 作用：
     - 当 requirement 同时包含 `path_sweep + local hole/counterbore`
     - 且代码在 swept body 上走到 `faces('>Z').workplane()`
     - runtime 不再只给“不要这么做”的 lint，而是给出“如何改”的结构化 recipe
   - 当前 recipe 骨架：
     - `body = profile_wp.sweep(path_wire, transition='round')`
     - `local_plane = cq.Plane(origin=..., xDir=..., normal=...)`
     - `through_cut = cq.Workplane(local_plane).circle(...).extrude(host_depth)`
     - `cbore_cut = cq.Workplane(local_plane).circle(...).extrude(counterbore_depth)`
     - `body = body.cut(through_cut.union(cbore_cut))`
2. `swept_host_local_hole_boolean_cutter_recipe`
   - 作用：
     - 把上面的结构化 recipe 进一步暴露成 runtime skill guidance
   - 目标：
     - 让模型在 curved/swept host 上不再机械重试 face selector
     - 而是转向局部 frame + boolean cutter 的更泛化修法

这些改动的效果是：

1. 错误 CadQuery API 不再白白烧掉 sandbox round
2. 模型更容易看到正确 repair recipe，而不是继续瞎试
3. `counterbore` 与 `countersink` 的语义被明确区分，不再默认滑向 `cskHole`

代表性代码路径：

1. `src/sub_agent_runtime/skill_pack.py`
2. `src/sub_agent_runtime/tool_runtime.py`
3. `src/sub_agent_runtime/context_manager.py`

### 2.3 Token 与无效回合减少

这条线今天有进展，但还没有到“满意”。目前主要做的是减少明显浪费，而不是彻底压低 prompt backbone。

已落地的降耗方向：

1. preflight lint 提前挡掉已知错误 API
   - 避免走完整个 sandbox -> validation -> semantic refresh 的浪费链条
2. `previous_tool_failure_summary` 保留最关键 hint，同时避免无关信息膨胀
3. runtime 对 false completion / false semantic refresh 的处理更严格
   - 减少无意义 read stall
4. `validate_requirement.timeout_seconds` 非法参数已被 runtime 清洗
   - 避免整轮被 `invalid_tool_arguments` 吃掉

但是还没解决的问题也很明确：

1. 某些 L2 case 的 token 仍然很高
2. 典型例子：
   - `benchmark/runs/20260410_031620/L2_90/summary.json`
   - `input_tokens = 91465`
3. 当前 token 问题的主因仍然是：
   - case 本身复杂
   - 后半程反复 repair
   - 还存在 validator/semantic surface 不够稳定导致的额外回合

结论：

1. 今天在 `token 减少` 上做的是“减少无效燃烧”
2. 还没有做到“系统性压低 L2 全体 token”
3. 这仍是后续重点工作

相关代码路径：

1. `src/sub_agent_runtime/context_manager.py`
2. `src/sub_agent_runtime/agent_loop_v2.py`
3. `src/sub_agent_runtime/tool_runtime.py`
4. `src/sub_agent_runtime/diagnostics.py`

## 3. Benchmark / Probe 结果摘要

### 3.1 L2 benchmark

代表性结果路径：

1. `benchmark/runs/20260410_134540/L2_149/benchmark_analysis.json`
   - 已真实 `PASS`
2. `benchmark/runs/20260410_134719/L2_63/benchmark_analysis.json`
   - `PASS`
3. `benchmark/runs/20260410_134719/L2_88/benchmark_analysis.json`
   - `PASS`
4. `benchmark/runs/20260410_134719/L2_96/benchmark_analysis.json`
   - `PASS`
5. `benchmark/runs/20260410_134719/L2_90/benchmark_analysis.json`
   - 当前是 `geometry_mismatch`
   - 不是 validator 假阳性，而是 8 轮都在写代码修复，最后 hole pattern 仍没完全对齐

### 3.2 外部仓库 probe

当前最值得看的三条：

1. `test_runs/20260410_141111`
   - arcblock / path sweep / counterbore
   - 现在已经走到“真实生成几何 + validator 暴露 sweep grounding 问题”
   - 最终 summary: `test_runs/20260410_141111/summary.json`
2. `test_runs/20260410_141112`
   - remote enclosure / shell / lip-fit / countersunk holes
   - 当前 shell 已验证，holes 与 lip-fit 仍未收敛
   - 最终 summary: `test_runs/20260410_141112/summary.json`
3. `test_runs/20260410_141239`
   - Gridfinity shell + holes + dividers + scoops + flanges
   - 最终 summary: `test_runs/20260410_141239/summary.json`

## 4. 目前仍未解决的关键问题

1. `path_sweep` clause grounding 还不够严格
   - 现在存在“clause interpretation 说 sweep verified，但 family/core checks 仍说 rail/profile/frame/result 全失败”的矛盾
2. `validation_llm` provider timeout
   - 在 enclosure run 里仍然能看到 timeout warning
3. `counterbore / countersink / host-face local feature` 仍然是外部 case 高频失败面
4. L2 token 仍偏高
   - 已减少无效回合，但还没有完成体系化压缩

## 5. 今天新增的重要文件与代码路径

本轮特别值得看的代码：

1. `src/sub_agent_runtime/tool_runtime.py`
2. `src/sub_agent_runtime/skill_pack.py`
3. `src/sub_agent_runtime/context_manager.py`
4. `src/sub_agent_runtime/diagnostics.py`
5. `src/sub_agent_runtime/agent_loop_v2.py`
6. `src/sandbox_mcp_server/service.py`
7. `src/sandbox_mcp_server/validation_interpretation.py`
8. `src/sandbox_mcp_server/validation_llm.py`

本轮主要测试文件：

1. `tests/unit/sub_agent_runtime/test_v2_runtime.py`
2. `tests/unit/sandbox_mcp_server/test_validate_requirement_contract.py`
3. `tests/unit/benchmark/test_run_prompt_benchmark.py`

## 6. 当前判断

今天的工作不是“把所有问题都解决了”，但方向是对的，而且证据足够明确：

1. validator 已经不再只是纯硬规则黑箱
2. skill 不再只是在 benchmark 句式上堆 prose
3. token 侧已经开始通过减少无效 round 获得收益
4. 三个 GitHub 仓库已经真实接入，不再只是链接

但距离“L2 full benchmark 满意结果”还有差距，差距主要集中在：

1. path sweep 的验证与修复 surface
2. local hole semantics
3. lip-fit / shell / host-face feature interaction
4. token 规模控制

## 7. 当前进行中的 active runs

1. `benchmark/runs/20260410_134719`

当前三条 fresh GitHub probe 都已完成并生成 summary：

1. `test_runs/20260410_141111/summary.json`
2. `test_runs/20260410_141112/summary.json`
3. `test_runs/20260410_141239/summary.json`

这些产物将继续作为下一轮修复与验证闭环的输入。

## 8. 晚间增量更新

### 8.1 新增 validator / skill / lint 收敛点

1. `src/sub_agent_runtime/tool_runtime.py`
   - 新增 `wrong_method.Wire.makeThreePointArc`
   - 现在会在 preflight 直接拦截 `cq.Wire.makeThreePointArc(...)`
   - repair recipe 会直接落到 `path_start_workplane_sweep`
2. `src/sub_agent_runtime/context_manager.py`
   - `AttributeError: type object 'Wire' has no attribute 'makeThreePointArc'`
     现在会被归一为 `execute_cadquery_curve_api_failure`
3. `src/sub_agent_runtime/skill_pack.py`
   - 新增 `preserve_validated_swept_host_during_counterbore_repair`
   - 当 swept host 已经正确、剩余 blocker 只剩 `feature_counterbore` 时，prompt 会明确要求：
     - 保留已验证的 sweep host
     - 不要把 rail/profile 整体重写
     - 只修局部 counterbore cutter
   - 同时强化了 `swept_host_local_hole_boolean_cutter_recipe`
     - 明确禁止“旋转全局 XY workplane 再沿全局 Z 挤出”这种伪局部 cutter frame
     - 明确要求 local plane normal 必须匹配 host entry normal
     - 明确要求 through-hole 与 counterbore recess 从同一 entry face 朝内切入

### 8.2 新增测试证据

代表性新测试：

1. `tests/unit/sub_agent_runtime/test_v2_runtime.py`
   - `test_execute_cadquery_preflight_lint_blocks_wire_make_three_point_arc_usage`
   - `test_previous_tool_failure_summary_normalizes_execute_cadquery_wire_make_three_point_arc_failure`
   - `test_build_runtime_skill_pack_preserves_validated_swept_host_during_counterbore_repair`
   - `test_build_runtime_skill_pack_strengthens_swept_host_boolean_cutter_orientation_guidance`

本轮相关 focused tests 已通过：

1. `wire_make_three_point_arc_usage / wire_make_three_point_arc_failure`
   - `2 passed`
2. `preserves_validated_swept_host_during_counterbore_repair` 及相邻 counterbore/sweep skill tests
   - `4 passed`
3. `strengthens_swept_host_boolean_cutter_orientation_guidance` 及相邻 swept-host tests
   - `3 passed`

### 8.3 晚间新增真实运行证据

1. targeted L2 rerun：
   - `benchmark/runs/20260410_184700`
   - 当前关键结果：
     - `benchmark/runs/20260410_184700/L2_149/benchmark_analysis.json`
       - `PASS`
     - `benchmark/runs/20260410_184700/L2_172/benchmark_analysis.json`
       - `PASS`
   - 这说明：
     - path-sweep validator mismatch 已明显收敛
     - countersink / local host repair surface 至少在这两个 L2 case 上已经能闭环

2. fresh external probe：
   - `test_runs/20260410_191500`
   - 关键证据：
     - `test_runs/20260410_191500/queries/round_02_validate_requirement_post_write.json`
   - 当前状态：
     - `path_sweep` clause 已 `verified`
     - 唯一 blocker 收敛为 `feature_counterbore`
   - 这说明：
     - 外部 arc-sweep/counterbore case 已不再卡在 rail/profile/frame
     - 真正剩余问题已经缩小到 local counterbore semantics

3. fresh external probe：
   - `test_runs/20260410_192300`
   - 当前关键证据：
     - `test_runs/20260410_192300/trace/events.jsonl`
     - `test_runs/20260410_192300/prompts/round_02_request.json`
   - 当前状态：
     - 第一轮没有再回退到 `Wire.makeThreePointArc(...)`
     - 但出现了 `non_positive_volume`
     - 原因是 counterbore boolean cutter 的局部朝向错误，几乎把 host 切空
   - 这个 run 直接促成了上面新增的 swept-host orientation guidance

### 8.4 当前晚间判断

1. path-sweep 这条线今天晚上继续变强了：
   - 从“经常卡在 rail/profile/frame recipe”
   - 进一步推进到“外部真实 case 里 sweep 基本正确，只剩 local counterbore semantics”
2. validator 的 LLM + clause interpretation 现在已经能稳定把 external case 压缩到更真实的 blocker，而不是泛泛 family mismatch
3. 当前最值得继续追的主瓶颈已经更集中：
   - swept host 上的 counterbore 局部坐标系
   - cutter 入口面 / 方向 / 深度
   - 以及这类局部修复如何不破坏已经验证通过的 sweep host
