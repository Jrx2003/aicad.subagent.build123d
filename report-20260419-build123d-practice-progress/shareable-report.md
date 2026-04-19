# Build123d 4/19 Practice 进展汇报

## 最新补充：今天三件事、直接证据、下一步

### 今天做了什么

1. 新增共享 turn policy：
   - `src/sub_agent_runtime/agent_loop_v2.py`
   - `semantic_refresh_after_successful_local_finish`
   - 目标是阻止 successful local finish 之后过早退回 whole-part rewrite
2. 修正了 practice 分析层的一个展示错误：
   - `src/sub_agent_runtime/practice_runner.py`
   - `tests/unit/sub_agent_runtime/test_practice_runner.py`
   - 旧逻辑会把 cumulative `action_history` 里的老 `create_sketch` 重复记到后续 round，导致 `practice_analysis.md` 把 round 6/7 错写成 `create_sketch`
3. 用这两层修复重新整理了真实 run：
   - `practice_runs/20260419_203038/hinged_pillbox_magnet_medium_v00_e34c3c99/`
   - 现在可以直接从 artifact 里看出实际链路是：
     - `create_sketch -> add_circle -> cut_extrude`

### 如何证明有效

1. 红绿测试已经覆盖新的 policy bug：
   - `tests/unit/sub_agent_runtime/test_agent_loop_v2_policy.py::test_successful_local_finish_under_short_budget_prefers_semantic_refresh_before_code_escape`
   - focused 结果：`1 passed`
   - 相关 policy 子集：`21 passed`
2. 新增的 practice analysis 回归已经覆盖 cumulative sketch lane：
   - `tests/unit/sub_agent_runtime/test_practice_runner.py::test_summarize_read_model_usage_tracks_latest_action_type_in_cumulative_sketch_lane`
   - 合并 focused 结果：`3 passed`
3. 真实 artifact 已经能直接演示：
   - `practice_runs/20260419_203038/hinged_pillbox_magnet_medium_v00_e34c3c99/practice_analysis.md`
   - 现在其中 `local_targeting_examples` 明确写成：
     - round 5: `create_sketch`
     - round 6: `add_circle`
     - round 7: `cut_extrude`
4. 同一 seed 的新旧 run 对比也已经清楚：
   - 旧：`practice_runs/20260419_201455/hinged_pillbox_magnet_medium_v00_e34c3c99/summary.json`
   - 新：`practice_runs/20260419_203038/hinged_pillbox_magnet_medium_v00_e34c3c99/summary.json`
5. 直接可讲的差异：
   - `executed_action_count: 6 -> 4`
   - `executed_action_types` 从
     - `execute_build123d, apply_cad_action, apply_cad_action, apply_cad_action, execute_build123d, execute_build123d`
     - 变成
     - `execute_build123d, apply_cad_action, apply_cad_action, apply_cad_action`
   - `hallucination.event_count: 2 -> 0`
   - `last_error`: 从 `RectangleRounded radius contract` 清零到 `null`
   - `failure_cluster`: 从 `runtime_gap` 清零到 `null`

### 后续方向

1. 下一条真实验证不再停留在 medium pillbox，而是继续推更高难的 custom practice seed：
   - `clamshell_enclosure_highwater`
   - 或 `organic_clamshell_storage_midhigh`
2. 验证重点不是 benchmark 分数，而是三件事是否继续成立：
   - first write 不要重新掉回 broad whole-part hallucination
   - local topology targeting 还能保持 exact ref consumption
   - validator 还能在更复杂 prompt 下维持 evidence-first，而不是重新退回空转
3. 如果高难 seed 继续稳定，下一层最值得收口的共享瓶颈会是：
   - two-part lid/base separation 的 geometry grounding
   - wall thickness / magnet placement / cavity count 的 evidence coverage
   - repair packet 与 validator registry 的继续分层


## 最新补充：successful local finish 之后先做 semantic refresh

### 今天做了什么

1. 新增了一条共享 turn policy，修的是这类路径失真：
   - `apply_cad_action` 已经成功完成一个实体局部修改
   - geometry 已经变化
   - 但 semantic state 还停留在局部修改前
   - runtime 却已经因为预算紧张，直接退回 `code_first_after_feature_budget_risk`
2. 现在的做法改成：
   - 成功的 local finish 写入之后
   - 如果不在 open-sketch 连续编辑窗口里
   - 且还没有 fresh validation / semantic refresh
   - 下一轮先进入 `semantic_refresh_after_successful_local_finish`
   - 允许工具面优先落在 `query_feature_probes / query_kernel_state / query_topology`
3. 代码位置：
   - `src/sub_agent_runtime/agent_loop_v2.py`

### 如何证明有效

1. 先写了一条贴近真实 practice 路径的红测：
   - `tests/unit/sub_agent_runtime/test_agent_loop_v2_policy.py::test_successful_local_finish_under_short_budget_prefers_semantic_refresh_before_code_escape`
2. 这条测试复现的是：
   - round 1 whole-part host 成功
   - round 2 已经拿到 `query_feature_probes + query_topology`
   - round 3-5 连续走 `create_sketch -> add_circle -> cut_extrude`
   - geometry 已经发生变化
   - 但 feature graph 还没刷新
   - 旧逻辑直接掉进：
     - `policy_id = code_first_after_feature_budget_risk`
3. 修复后回跑：
   - `./.venv/bin/pytest tests/unit/sub_agent_runtime/test_agent_loop_v2_policy.py -q -k 'successful_local_finish_under_short_budget_prefers_semantic_refresh_before_code_escape'`
   - 结果：`1 passed`
4. 再补一圈相关回归：
   - `./.venv/bin/pytest tests/unit/sub_agent_runtime/test_agent_loop_v2_policy.py -q -k 'semantic_refresh_after_successful_local_finish or local_finish or feature_chain_budget_risk or open_sketch_window_under_critical_budget_exits_to_code_first_escape'`
   - 结果：`21 passed`
   - `./.venv/bin/pytest tests/unit/sub_agent_runtime/test_turn_state.py -q`
   - 结果：`1 passed`

### 后续方向

1. 当前正在用真实 Kimi run 验证这条新 policy 是否真的改变 live 路径：
   - `practice_runs/20260419_203038/hinged_pillbox_magnet_medium_v00_e34c3c99/`
2. 这条 run 当前已经落盘到：
   - `plans/round_01_response.json`
   - `queries/round_01_validate_requirement_post_write.json`
   - `plans/round_02_response.json`
   - `queries/round_02_query_feature_probes.json`
   - `queries/round_03_query_kernel_state.json`
   - `queries/round_03_query_topology.json`
3. 这说明新 run 至少已经继续沿 “first write -> validation -> semantic refresh -> topology” 的主线前进，下一步最关键的是继续观察：
   - 在第一个 front-face local cut 成功之后
   - 是否会先做 semantic refresh
   - 而不是再次直接退回 whole-part rewrite


## 晚间新增：`apply_cad_action` no-effect 假成功识别

### 今天又做了什么

1. 在 `134500` 的 preflight 死锁被修掉之后，继续追 `practice_runs/20260419_195039/` 的真实残差。
2. 这条 run 已经证明：
   - round 4 真实走到了 `query_topology + query_kernel_state`
   - round 5-7 真实消费了 exact `face_ref`
   - 但 round 7 的 `cut_extrude` 虽然返回 `success=true`，geometry 实际完全没变
3. 因此本轮新增的通用修复不是继续补 clamshell case rule，而是把“有实体写动作但几何无变化”的情况，从 runtime 的假成功改成结构化失败。
4. 已落地位置：
   - `src/sandbox_mcp_server/service.py`
   - `src/sub_agent_runtime/turn_state.py`
   - `src/sub_agent_runtime/agent_loop_v2.py`

### 如何证明有效

1. 真实 case 证据：
   - `practice_runs/20260419_195039/hinged_pillbox_magnet_medium_v00_e34c3c99/plans/round_05_response.json`
   - `practice_runs/20260419_195039/hinged_pillbox_magnet_medium_v00_e34c3c99/plans/round_06_response.json`
   - `practice_runs/20260419_195039/hinged_pillbox_magnet_medium_v00_e34c3c99/plans/round_07_response.json`
   - `practice_runs/20260419_195039/hinged_pillbox_magnet_medium_v00_e34c3c99/actions/round_07_apply_cad_action.json`
   - `practice_runs/20260419_195039/hinged_pillbox_magnet_medium_v00_e34c3c99/queries/round_07_validate_requirement_post_write.json`
   - `practice_runs/20260419_195039/hinged_pillbox_magnet_medium_v00_e34c3c99/practice_analysis.md`
2. 这些工件共同说明：
   - exact targeting 已经成功，`face_ref=face:1:F_47d924eaa64a`
   - validator 仍报告 `feature_notch_or_profile_cut`
   - round 5 -> 7 的 snapshot geometry 无体积变化、无 bbox 变化、无面边数量变化
   - 所以旧链路里这是标准的 no-effect 假成功
3. 新增回归：
   - `tests/unit/sandbox_mcp_server/test_action_contracts.py::test_apply_cad_action_rejects_no_effect_cut_extrude`
   - `tests/unit/sub_agent_runtime/test_turn_state.py::test_run_state_counts_no_effect_apply_action_as_no_op`
4. 合并验证：
   - `./.venv/bin/pytest tests/unit/sandbox_mcp_server/test_action_contracts.py tests/unit/sub_agent_runtime/test_tool_runtime_execution.py tests/unit/sub_agent_runtime/test_turn_state.py -q`
   - 结果：`22 passed`

### 后续方向

1. 当前 live rerun：
   - `practice_runs/20260419_200420/`
2. 这条 run 现在主要验证：
   - 如果 local finish 再次走到 no-effect 实体写，runtime 是否会更早暴露结构化错误而不是继续误判为成功
3. 如果生效，后续共享瓶颈会更干净地收缩到：
   - whole-part rebuild 的 `transform_context_manager`
   - validator 对 notch/profile cut 的 geometry grounding
   - local finish 的更短 closure policy

## 晚间新增：`134500` 卡死根因、修复、验证

### 今天又做了什么

1. 把 `practice_runs/20260419_134500/` 的 round 1 卡死继续往下压到了具体 preflight 规则，而不是停在“live run 没继续”这种泛结论。
2. 根因已经明确：
   - 不是模型没有返回
   - 不是 Build123d 代码执行卡住
   - 不是 MCP `execute_build123d` 自身卡住
   - 而是 `execute_build123d` 前的 preflight lint 在 `RectangleRounded` radius contract 上进入无限振荡
3. 具体代码路径已经落到：
   - `src/sub_agent_runtime/tool_runtime.py::_preflight_lint_execute_build123d`
   - `src/sub_agent_runtime/tool_runtime.py::_find_rectanglerounded_radius_bounds_hits`
   - `src/sub_agent_runtime/tool_runtime.py::_collect_numeric_assignment_env`
4. 触发机制也已经查明：
   - 同一个临时变量名在不同 loop 中重复赋值
   - 旧的 numeric env 求值采用无界 fixed-point
   - 最终在两个值之间反复切换，导致 preflight lint 永不收敛
5. 已完成修复：
   - numeric env 改成按源码顺序收集赋值
   - 使用有界 pass 收敛
   - 保留 later assignment 覆盖 earlier assignment
   - 不再因为重复局部变量名把 preflight lint 拖死

### 如何证明有效

1. 真实触发源码：
   - `practice_runs/20260419_134500/hinged_pillbox_magnet_medium_v00_e34c3c99/plans/round_01_response.json`
2. 根因栈证据：
   - `/tmp/preflight_lint_stack.txt`
   - 栈顶已经明确指向 `_collect_numeric_assignment_env -> _find_rectanglerounded_radius_bounds_hits`
3. 复现实验已经证明旧实现会振荡：
   - 同一真实 case 里，`magnet_z` 会在 `1.0` 和 `0.8` 之间来回切换
4. 修复后直接重放真实 preflight lint：
   - 对同一份 `134500` round 1 源码直接调用 `_preflight_lint_execute_build123d(...)`
   - 返回时间：`elapsed_sec 2.504`
   - 说明 preflight lint 已经恢复可返回，不再卡死
5. focused 单测已补齐：
   - `tests/unit/sub_agent_runtime/test_tool_runtime_preflight_lint.py::test_numeric_assignment_env_converges_when_same_name_is_reassigned_in_multiple_loops`
   - `tests/unit/sub_agent_runtime/test_tool_runtime_preflight_lint.py::test_preflight_lint_rejects_rectanglerounded_radius_that_exceeds_half_of_height`
   - 结果：`2 passed`
6. 新的真实 rerun 已继续启动：
   - `practice_runs/20260419_195039/`
   - 当前验证目标很直接：确认 live round 1 不再死在 `tool_batch_started`

### 后续方向

1. 继续盯 `practice_runs/20260419_195039/`，确认它是否越过旧的 preflight 死锁点。
2. 如果越过，当前 enclosure seed 的主瓶颈会重新回到更健康的共享问题：
   - `named_face_plane_family_mismatch`
   - clamshell / local-finish closure
3. 这次修复的价值在于：
   - runtime 自己的卡死已经被压掉
   - 后续优化可以继续围绕真实 geometry family，而不是再去救一条内部 lint 死循环

## 4/19 当前主线

### 今天做了什么

1. 继续沿 clamshell / hinge practice lane 收口，但这次重点已经不是“再补一条 Build123d API hard rule”，而是把 shared closure 和 shared geometry contract 再前推一层。
2. 新增并落地了两类共享修复：
   - living hinge 语义收紧：默认是 host-owned back-edge strip / flexure，不再把整个 lid / base 平移到 seam 上，也不再退回 detached hinge barrel / pin
   - open sketch window 收口收紧：在已经 `create_sketch(face_ref=...)` 成功、且 sketch 还是空的时候，下一轮只允许 `apply_cad_action` 继续写第一笔草图几何，不再把 `query_sketch` 暴露成同级选项
3. 连续跑了两条真实 Kimi live run：
   - `practice_runs/20260419_124200/`
   - `practice_runs/20260419_130800/`
4. 针对 `130800` 暴露出的 fresh empty sketch gap，已经继续启动带最新 policy 的 rerun：
   - `practice_runs/20260419_134500/`

### 如何证明有效

最值得直接打开讲的是这组证据：

1. `practice_runs/20260419_124200/hinged_pillbox_magnet_medium_v00_e34c3c99/plans/round_01_response.json`
   - first write 已不再写 detached `hinge_barrel` / `hinge_pin`
   - 也不再把 lid 整体平移到 hinge seam
2. `practice_runs/20260419_124200/hinged_pillbox_magnet_medium_v00_e34c3c99/plans/round_04_response.json`
3. `practice_runs/20260419_124200/hinged_pillbox_magnet_medium_v00_e34c3c99/actions/round_04_apply_cad_action.json`
   - local finish 已经真实消费 exact `face_ref`
   - `get_history` 这一类 session-control 空转已经退出主链
4. `practice_runs/20260419_124200/hinged_pillbox_magnet_medium_v00_e34c3c99/practice_analysis.md`
   - `hallucination: events=0`
   - `topology_targeting_observed: True`
   - `exact_ref_consumption_rate: 1.0`
5. `practice_runs/20260419_130800/hinged_pillbox_magnet_medium_v00_e34c3c99/plans/round_05_response.json`
6. `practice_runs/20260419_130800/hinged_pillbox_magnet_medium_v00_e34c3c99/queries/round_05_query_sketch.json`
   - 这条 run 把新的主矛盾钉死了：
   - `create_sketch(face_ref=...)` 成功之后，系统仍浪费了一轮去看空 sketch
7. `practice_runs/20260419_130800/hinged_pillbox_magnet_medium_v00_e34c3c99/plans/round_06_response.json`
8. `practice_runs/20260419_130800/hinged_pillbox_magnet_medium_v00_e34c3c99/plans/round_08_response.json`
   - 说明它后半段已经不是 Build123d 幻觉问题，而是在多浪费一轮之后仍走出了 `add_rectangle -> cut_extrude`
9. `practice_runs/20260419_130800/hinged_pillbox_magnet_medium_v00_e34c3c99/practice_analysis.md`
   - `hallucination: events=0`
   - `local_targeting_action_count: 3`
   - `exact_ref_consumption_rate: 1.0`
   - 剩余 blocker 只剩整体 two-part + living hinge 几何关系
10. 新 policy 不是只写了文案，而是已经落成机器保护：
   - `tests/unit/sub_agent_runtime/test_agent_loop_v2_policy.py::test_open_sketch_window_after_empty_query_sketch_still_prefers_apply_only`
   - focused 回归：
     - `./.venv/bin/pytest tests/unit/sub_agent_runtime/test_agent_loop_v2_policy.py -q -k 'open_sketch_window_after_empty_query_sketch_still_prefers_apply_only or continue_open_sketch_window_after_apply_action or open_sketch_window_with_fresh_profile_prefers_apply_over_query_sketch or open_sketch_window_under_critical_budget_exits_to_code_first_escape or open_sketch_window_with_empty_sketch_and_two_rounds_left_exits_to_code_first_escape'`
   - 结果：`4 passed`

### 后续方向

1. 先看 `practice_runs/20260419_134500/` 是否能把 `130800` round 5 的空 `query_sketch` 彻底省掉
2. 如果这条 live rerun 生效，当前主瓶颈就会更干净地收缩到：
   - first write 的 two-part clamshell / living hinge 几何组织
   - validator 对 two-part + living hinge 关系的 geometry grounding
3. 后续继续优先做 shared family contract、turn policy、validator grounding，不回到单 case helper 规则

## 直接回答三件事

### 今天做了什么

1. 继续沿 clamshell/hinge practice lane 收口，不再围 benchmark 补 case 规则，重点放在共享 family：
   - clamshell seam-vs-axis contract
   - detached hinge single-axis lane
   - detached positive hinge helper 的 lint 误伤清理
2. 用这组修复重新推进真实 Kimi run，而不是只看单测。
3. 当前最新 live 验证是 `practice_runs/20260419_185457/`，它已经回答了一个更具体的问题：旧的 round 5 plane-family 假阳性被压下去之后，系统确实重新跑到了 `query_topology + exact-ref local finish`，但还残留 2 个共享 write-surface family。

### 如何证明有效

最直接的三份证据就是下面这条 run 链：

1. `practice_runs/20260419_234500/hinged_pillbox_magnet_medium_v00_e34c3c99/summary.json`
   - `build123d_hallucination.event_count = 10`
   - `executed_action_count = 5`
   - `failure_cluster = code_path_family_gap`
2. `practice_runs/20260419_184731/hinged_pillbox_magnet_medium_v00_e34c3c99/summary.json`
   - `build123d_hallucination.event_count = 1`
   - `executed_action_count = 3`
   - `inspection_only_rounds = 5`
   - `last_error = null`
3. `practice_runs/20260419_184731/hinged_pillbox_magnet_medium_v00_e34c3c99/practice_analysis.md`
   - `status = partial_geometry`
   - `query_topology = 3`
   - `query_kernel_state = 3`
   - 剩余 blocker 已收窄到 `feature_notch_or_profile_cut`
4. `practice_runs/20260419_185457/hinged_pillbox_magnet_medium_v00_e34c3c99/summary.json`
   - `build123d_hallucination.event_count = 2`
   - `executed_action_count = 5`
   - `forced_policy_chain` 已出现 `apply_local_finish_after_topology_targeting_from_read_stall`
5. `practice_runs/20260419_185457/hinged_pillbox_magnet_medium_v00_e34c3c99/practice_analysis.md`
   - `local_targeting_action_count = 1`
   - `fresh_targeting_action_count = 1`
   - `exact_ref_consumption_rate = 1.0`
6. `practice_runs/20260419_185457/hinged_pillbox_magnet_medium_v00_e34c3c99/actions/round_08_apply_cad_action.json`
   - 最新 run 已真实消费 exact `face_ref`
   - 剩余 blocker 仍是 `feature_notch_or_profile_cut`

### 后续方向

1. 继续以 `184731 -> 185457` 为主链，保持 `185457` 已经跑出来的 topology-aware local finish，不让系统退回纯 read-only stall。
2. 下一刀优先压 `185457` 暴露出来的两个残余 write-surface family：
   - `named_face_plane_family_mismatch`
   - `detached_subtractive_builder_without_host`
3. 后续优化重点放在“host-owned local cut contract + exact-ref local finish closure”，而不是继续堆单 case helper rule。

## 三句话结论

1. 今天做的不是继续围 benchmark 补 case 规则，而是继续把 practice lane 的共享能力收紧成三层：runtime closure、exact-ref local finish、validator completion closure。
2. 最关键的证据链是三条真实 Kimi run：`20260419_173500 -> 20260419_181500 -> 20260419_184800`。它们把问题从“`validate_requirement` 明明已经 complete，但 runtime 还继续跑”推进到“exact `face_ref` 已经拿到，但模型仍误走 sketch chain”，再推进到“同类 prompt 已直接改成 `hole/countersink + face_ref` 并完成收口”。
3. 当前剩余主瓶颈已经不再是“Build123d 完全不会用”，而是两条更窄的共享问题：first-write 仍有少量 helper/plane contract 幻觉残差，以及 local-targeting 跑起来后还需要继续压 stale-ref repeat。
4. 新的跨 seed 真跑 `20260419_201500` 也已 direct complete，说明今天这组改动不只对同一个 prompt rerun 生效。
5. 今天后半段又把 enclosure seed 的 plane-family 误伤根因继续钉死：`20260419_210500` 证明旧 matcher 会把 front-face local edit 和壳体 host profile 混在一起；当前代码下对同一份 round 1 源码做离线 preflight 复算，命中数已经从 `6 -> 2`，只剩真正该保留的 label / notch 局部 sketch。
6. 最新一条最有说服力的 live 对比是 `20260419_221500 -> 20260419_223900`：前者 8 轮全是 `execute_build123d` 盲修，`hallucination_events = 26`；后者已经收敛成 `execute_build123d -> validate_requirement -> query_feature_probes -> query_kernel_state -> query_topology -> apply_cad_action`，`hallucination_events = 5`，而且 `exact_ref_consumption_rate = 1.0`。

## 最新补充：`224800` 暴露的根因、已经完成的修复、以及 `231500` 的验证目标

### 今天又做了什么

今天最后一轮新增的关键工作，不是再补一条 clamshell case rule，而是把 host-profile alias 识别做成了更稳的 lint 基础能力：

1. `practice_runs/20260419_224800/hinged_pillbox_magnet_medium_v00_e34c3c99/plans/round_01_response.json`
2. `practice_runs/20260419_224800/hinged_pillbox_magnet_medium_v00_e34c3c99/actions/round_01_execute_build123d.json`

这两个 artifact 暴露出同一个通用问题：

1. `inner_w = width - 2 * wall_thick`
2. `inner_d = depth - 2 * wall_thick`
3. 这类典型 base/lid 壳体中空 profile alias 之前没有被 lint 当成 host-profile 派生
4. 所以 `BuildSketch(Plane.XY.offset(...))` 这种本来正确的壳体中空草图，会被误伤成：
   - `invalid_build123d_contract.named_face_plane_family_mismatch`

对应代码修复已经落地到：

1. `src/sub_agent_runtime/tool_runtime.py`
2. `tests/unit/sub_agent_runtime/test_tool_runtime_preflight_lint.py`

这次不是简单加白名单，而是补了一层通用归一逻辑：

1. `_identifier_tokens(...)`
2. `_is_host_profile_modifier_id(...)`
3. `_strip_host_profile_modifier_ids(...)`

也就是把 host-profile modifier 识别从“精确名字命中”升级成“token 归一 + alias 友好识别”，让 `wall_thick`、`wall_thickness` 这一类常见变量名都能被正确处理。

### 如何证明这一步有效

证据链已经齐了，而且很直接：

1. 新红测先失败：
   - `test_named_face_plane_family_mismatch_ignores_host_profile_aliases_derived_via_wall_thick_names`
2. 修复后转绿
3. 宽回归：
   - `./.venv/bin/pytest tests/unit/sub_agent_runtime/test_tool_runtime_preflight_lint.py -q`
   - 结果：`142 passed`

更关键的是，这次修复已经能改变真实产物的离线复算结果：

1. 我把 `224800` 的 round 1 真实代码重新喂给当前 `_find_named_face_plane_family_mismatch_hits(...)`
2. 旧结果是 2 个误伤 hit：
   - `line 45 -> XY`
   - `line 61 -> XY`
3. 当前代码下结果已经变成：
   - `[]`

这说明今天最后补的这层，不是“测试更漂亮了”，而是 enclosure seed 的 first-write 误伤面又少了一层。

### 后续方向是什么

在这轮修复和回归之后，已经立即启动新的真实 Kimi rerun：

1. `practice_runs/20260419_231500/`

当前已经可直接打开的起始工件有：

1. `practice_runs/20260419_231500/practice_manifest.json`
2. `practice_runs/20260419_231500/hinged_pillbox_magnet_medium_v00_e34c3c99/prompt.txt`
3. `practice_runs/20260419_231500/hinged_pillbox_magnet_medium_v00_e34c3c99/prompts/round_01_request.json`
4. `practice_runs/20260419_231500/hinged_pillbox_magnet_medium_v00_e34c3c99/trace/events.jsonl`

这条 rerun 的验证目标非常清楚：

1. 先验证 enclosure seed 能不能越过 `wall_thick / inner_w / inner_d` 这一层 host-profile alias 误伤
2. 如果能越过去，下一层真正该继续压的共享瓶颈大概率就会是：
   - `transform_context_manager`
   - detached hinge positive-solid lane
   - 或者更后面的 read / local-finish closure

## 最新补充：`231500` 暴露出的两层新 contract，已经继续前移到 preflight / requirement parse

`231500` 虽然是旧代码启动的，但它非常有价值，因为它又暴露出两条明确属于共享 contract 的问题：

1. requirement 里同时出现 `mating faces` 和 `front face` 时，旧的 `_named_face_requirement_plane_groups(...)` 只会识别出 `front_back`，不会把 `mating faces` 归到 `top_bottom`
2. `RectangleRounded(width, height, radius)` 如果明显违反 `2 * radius < min(width, height)`，旧系统会直接让它进执行期，然后在 sandbox 里抛：
   - `ValueError: width and height must be > 2*radius`

这两层都已经继续前移到当前代码：

1. `mating face / mating faces / mating surface / mating surfaces`
   - 现在统一归到 `top_bottom`
2. 新增 preflight lint：
   - `invalid_build123d_contract.rectanglerounded_radius_bounds`

直接证据：

1. `test_named_face_requirement_plane_groups_include_mating_faces_as_top_bottom`
2. `test_named_face_plane_family_mismatch_allows_xy_mating_face_edits_when_requirement_mentions_front_face_and_mating_faces`
3. `test_preflight_lint_rejects_rectanglerounded_radius_that_exceeds_half_of_height`
4. `./.venv/bin/pytest tests/unit/sub_agent_runtime/test_tool_runtime_preflight_lint.py -q`
   - 结果：`145 passed`

这两层修复也已经能改变 `231500` 的离线复算结果：

1. `231500` 的 round 3 代码，在当前 `_find_named_face_plane_family_mismatch_hits(...)` 下结果已经是：
   - `[]`
2. `231500` 的 round 1 代码，在当前 `_preflight_lint_execute_build123d(...)` 下已经不再是执行期 `ValueError` 盲炸，而是会提前得到：
   - `invalid_build123d_contract.rectanglerounded_radius_bounds`

在这两层继续补完之后，新的真实 Kimi rerun 也已经启动：

1. `practice_runs/20260419_234500/`

当前已落盘的启动工件：

1. `practice_runs/20260419_234500/practice_manifest.json`
2. `practice_runs/20260419_234500/hinged_pillbox_magnet_medium_v00_e34c3c99/prompt.txt`
3. `practice_runs/20260419_234500/hinged_pillbox_magnet_medium_v00_e34c3c99/prompts/round_01_request.json`
4. `practice_runs/20260419_234500/hinged_pillbox_magnet_medium_v00_e34c3c99/trace/events.jsonl`

这条最新 rerun 的验证目标很直接：

1. 先看 first write 是否还会在旧的：
   - host-profile alias
   - `mating faces` plane-family 误杀
   - `RectangleRounded` radius runtime 爆炸
   这三层继续浪费回合
2. 如果这三层都被压掉，下一层真正需要继续优化的就会更聚焦在：
   - detached hinge positive-solid 表达
   - clamshell whole-part 收口
   - 后半段 local-finish / validation closure

## 最新补充：`221500 -> 223900 -> 224800`

### 今天新做了什么

围绕 enclosure/clamshell 这一侧，我今天后半段又继续做了三件事：

1. 把 `named_face_plane_family_mismatch` 在 clamshell family 下重新绑回 `clamshell_host_local_cut_contract`，不再让它过早退化成抽象的 generic plane-family recipe。
2. 给 `execute_build123d_clamshell_host_local_cut_contract` 增加更具体的 host-plane skeleton：
   - front/back local cut -> `Plane.XZ.offset(±depth/2)` / `Plane(face)`
   - top/bottom mating-face edit -> `Plane.XY.offset(z_face)`
   - 明确禁止用 `BuildSketch(Plane.XY)` / `BuildSketch(Plane.YZ)` 再靠 `Locations((x, y, z))`、`shift_origin(...)` 去“补救” front/back host。
3. 又补了一条 runtime closure：
   - `create_sketch -> query_sketch(empty)` 之后如果只剩 2 轮，直接转 `code_escape_after_open_sketch_window_under_budget`
   - 不再把模型继续锁在 `local_finish` 里逼出 `apply_cad_action(action_type="snapshot")`

相关代码：

1. `src/sub_agent_runtime/tool_runtime.py`
2. `src/sub_agent_runtime/skill_pack.py`
3. `src/sub_agent_runtime/agent_loop_v2.py`

相关单测：

1. `tests/unit/sub_agent_runtime/test_tool_runtime_preflight_lint.py`
2. `tests/unit/sub_agent_runtime/test_skill_pack.py`
3. `tests/unit/sub_agent_runtime/test_agent_loop_v2_policy.py`

### 如何证明这些改动有效

最强的对比不是一句“效果更好了”，而是同一条 seed 的两次 live run：

1. `practice_runs/20260419_221500/brief_report.md`
2. `practice_runs/20260419_223900/brief_report.md`

对比结果非常具体：

1. `hallucination_events: 26 -> 5`
2. `writes: 8 -> 5`
3. `query_topology_cases: 0 -> 1`
4. `query_kernel_state: 0 -> 1`
5. `local_targeting_action_count: 0 -> 1`
6. `fresh_targeting_action_count: 0 -> 1`
7. `exact_ref_consumption_rate: 0.0 -> 1.0`

其中最值得直接打开演示的文件有四个：

1. `practice_runs/20260419_221500/hinged_pillbox_magnet_medium_v00_e34c3c99/actions/round_01_execute_build123d.json`
2. `practice_runs/20260419_223900/hinged_pillbox_magnet_medium_v00_e34c3c99/actions/round_01_execute_build123d.json`
3. `practice_runs/20260419_223900/hinged_pillbox_magnet_medium_v00_e34c3c99/queries/round_04_query_topology.json`
4. `practice_runs/20260419_223900/hinged_pillbox_magnet_medium_v00_e34c3c99/actions/round_05_apply_cad_action.json`

这四个 artifact 能直接说明三件事：

1. round 1 的 plane-family 误伤已经被压掉了：
   - `221500` round 1 同时有 `named_face_plane_family_mismatch + transform_context_manager`
   - `223900` round 1 只剩 `transform_context_manager`
2. runtime 已经不再只是 whole-part 盲修，而是进入了：
   - `validate_requirement -> query_feature_probes -> query_kernel_state -> query_topology -> apply_cad_action`
3. `query_topology` 给出的 exact `face_ref` 已经被真实消费：
   - `223900` 的 `practice_analysis.md` 里
   - `exact_ref_consumption_rate = 1.0`

### 当前最新瓶颈是什么

`223900` 也继续把下一层共享问题暴露了出来：

1. `plans/round_07_response.json`
2. `actions/round_07_apply_cad_action.json`

这两个文件说明：

1. 模型在 round 7 已经明确想走 whole-part fallback
2. 但旧 policy 还把它锁在 local-finish
3. 最后它发出了 `apply_cad_action(action_type="snapshot")`
4. 被 preflight 明确拒绝

这就是为什么今天我又继续补了：

1. `clamshell_transform_lane_contract`
2. `open_sketch_window` 的 2-round code escape closure

### 后续方向是什么

后续方向现在已经非常清晰，不需要再回到笼统描述：

1. 继续观察 `practice_runs/20260419_224800/`：
   - 这条是用最新代码启动的真实 Kimi rerun
   - 重点看它是否还能继续压低 round 1 的 `transform_context_manager`
   - 以及是否能避免 `223900` round 7 那种 `snapshot` 误逃生
2. 如果 `224800` 证明上述两点都成立，下一层真正该继续压的就会是：
   - clamshell first-write 里 detached subtractive builder 的残差
   - 以及 validator 对 `separate lid/base + living hinge` 的 geometry grounding 分辨率

## 今天做了什么

今天的改动可以压缩成三件事。

### 1. 修掉 `validate_requirement complete` 之后仍继续回合的 runtime closure 缺口

代码：

1. `src/sub_agent_runtime/agent_loop_v2.py`
2. `tests/unit/sub_agent_runtime/test_agent_loop_v2_policy.py`

这部分新增了两层控制：

1. 如果当前回合内已经出现 `validate_requirement -> is_complete=true`，runtime 直接用 `validated_complete` 停止，不再继续下一轮。
2. 如果 `latest_validation` 已经 complete，下一轮 policy 只能允许 `finish_run`，不能再回到 local-finish/read-stall ping-pong。

这不是 case rule，而是通用的 completion closure。

### 2. 当 exact host face 已经存在时，给 local finish 增加“direct hole/countersink 优先于 create_sketch”偏置

代码：

1. `src/sub_agent_runtime/context_manager.py`
2. `src/sub_agent_runtime/skill_pack.py`
3. `tests/unit/sub_agent_runtime/test_context_manager.py`
4. `tests/unit/sub_agent_runtime/test_skill_pack.py`

这部分把原来的“只要进入 local finish，就很容易先开 `create_sketch(face_ref=...)`”改成：

1. 如果当前 family 是 `explicit_anchor_hole`
2. 且 exact `face_ref` 已经可用
3. 且目标动作本来就能直接表示成 `hole/countersink/counterbore`

那么 prompt contract 会明确告诉模型：先直接做 host-face action，不要先开 sketch 窗口。

### 3. 把今天最关键的变化真正落进真实 practice 证据链

今天不是只做单测。相关改动已经被真实 Kimi run 消费：

1. `practice_runs/20260419_173500/`
2. `practice_runs/20260419_181500/`
3. `practice_runs/20260419_184800/`

这三条 run 正好构成一条很清楚的“失败形态收缩链”。

另外，今天还补了一条不同 seed 的交叉验证：

4. `practice_runs/20260419_201500/`

## 如何证明这些改动有效

### 证据链 A：`173500` 暴露 closure bug

最值得打开的文件：

1. `practice_runs/20260419_173500/brief_report.md`
2. `practice_runs/20260419_173500/front_face_sketch_recess_focus_v00_194ed1dd/queries/round_07_validate_requirement.json`
3. `practice_runs/20260419_173500/front_face_sketch_recess_focus_v00_194ed1dd/plans/round_08_response.json`

这条 run 的价值不在于失败本身，而在于它把旧问题定位得非常清楚：

1. round 7 的 `validate_requirement` 已经返回 `is_complete=true`
2. `blockers=[]`
3. 但 runtime 仍然进入了 round 8

这就是今天补 `validated_complete` closure 的直接起点。

### 证据链 B：`181500` 暴露 exact ref 已有但仍误走 sketch chain

最值得打开的文件：

1. `practice_runs/20260419_181500/brief_report.md`
2. `practice_runs/20260419_181500/front_face_sketch_recess_focus_v00_194ed1dd/plans/round_04_response.json`
3. `practice_runs/20260419_181500/front_face_sketch_recess_focus_v00_194ed1dd/actions/round_04_apply_cad_action.json`

这条 run 的关键信息是：

1. `hallucination_events = 0`
2. 说明主矛盾已经不再是 write-surface API 幻觉
3. 但 round 4 明明已经拿到了 exact bottom `face_ref`
4. 模型仍然选择：
   - `apply_cad_action(action_type="create_sketch", face_ref=...)`

也就是说，问题已经从“Build123d API 猜错”推进到了“local finish 的动作优先级不对”。

### 证据链 C：`184800` 证明修复已改变真实决策链

最值得打开的文件：

1. `practice_runs/20260419_184800/brief_report.md`
2. `practice_runs/20260419_184800/front_face_sketch_recess_focus_v00_194ed1dd/plans/round_04_response.json`
3. `practice_runs/20260419_184800/front_face_sketch_recess_focus_v00_194ed1dd/actions/round_04_apply_cad_action.json`
4. `practice_runs/20260419_184800/front_face_sketch_recess_focus_v00_194ed1dd/queries/round_07_validate_requirement_post_write.json`
5. `practice_runs/20260419_184800/front_face_sketch_recess_focus_v00_194ed1dd/practice_analysis.md`

这条 run 证明同类 prompt 在修复后已经真正改变了行为：

1. round 4 不再先开 `create_sketch`
2. 而是直接选择：
   - `apply_cad_action(action_type="hole", hole_type="countersink", face_ref=...)`
3. 该 run 最终：
   - `status = complete`
   - `validation = 1`
   - `blockers = []`

这里最有演示价值的两句话是：

1. `181500` 里 exact ref 已有，但 round 4 仍先开 sketch
2. `184800` 里 exact ref 已有，round 4 已直接改成 `hole/countersink + face_ref`

这就是今天改动生效的最直接证据。

## 代码与测试证据

今天最相关的代码文件：

1. `src/sub_agent_runtime/agent_loop_v2.py`
2. `src/sub_agent_runtime/context_manager.py`
3. `src/sub_agent_runtime/skill_pack.py`
4. `tests/unit/sub_agent_runtime/test_agent_loop_v2_policy.py`
5. `tests/unit/sub_agent_runtime/test_context_manager.py`
6. `tests/unit/sub_agent_runtime/test_skill_pack.py`

今天已通过的关键回归：

```bash
./.venv/bin/pytest tests/unit/sub_agent_runtime/test_context_manager.py tests/unit/sub_agent_runtime/test_skill_pack.py tests/unit/sub_agent_runtime/test_agent_loop_v2_policy.py tests/unit/sub_agent_runtime/test_tool_runtime_preflight_lint.py tests/unit/sandbox_mcp_server/test_validate_requirement_contract.py -q
```

结果：

1. `304 passed, 11 warnings`

这组回归覆盖了：

1. `validated_complete` 停止策略
2. exact `face_ref` local-finish contract
3. direct hole/countersink 优先于 `create_sketch(face_ref=...)`

## 跨 seed 补充证据：`20260419_201500`

今天又跑了一条不同 seed 的真实 Kimi case：

1. `practice_runs/20260419_201500/brief_report.md`
2. `practice_runs/20260419_201500/topology_local_finish_focus_v00_95ab3188/practice_analysis.md`
3. `practice_runs/20260419_201500/topology_local_finish_focus_v00_95ab3188/plans/round_04_response.json`
4. `practice_runs/20260419_201500/topology_local_finish_focus_v00_95ab3188/actions/round_04_apply_cad_action.json`

这条 run 不再是 `front_face_sketch_recess_focus` 的同 prompt rerun，而是 `topology_local_finish_focus`。它给出的结果是：

1. `status = complete`
2. `validation = 1`
3. `hallucination_events = 0`
4. `local_targeting_action_count = 1`
5. `fresh_targeting_action_count = 1`
6. `stale_ref_action_count = 0`
7. `exact_ref_consumption_rate = 1.0`

round 4 的决策同样已经是：

1. exact bottom `face_ref` 已可用
2. 直接选择：
   - `apply_cad_action(action_type="hole", hole_type="countersink", face_ref=...)`

这说明今天新增的 local-finish 偏置与 closure 纪律，已经至少在第二条 practice seed 上复用成功。

## 当前已经证明了什么

到今天为止，已经能明确证明三件事：

1. practice lane 不是只会堆日志，已经能稳定产出真实 Build123d 工件与完整 trace。
2. `query_topology -> exact face_ref -> local finish action` 这条 lane 已经能被真实模型消费。
3. 当前剩余问题已经被压缩到更窄的共享 contract，而不是还停留在“Build123d API 普遍不会用”。

## 当前剩余瓶颈

今天收口后，最明确的剩余瓶颈有两条。

### 1. first-write 仍有少量共享 helper / plane contract 幻觉残差

这类问题仍主要出现在 whole-part first write 阶段，表现为：

1. helper 名称猜错
2. plane/face host contract 不稳定
3. centered host 上的 face plane / normal 推断仍会漂

这类问题仍属于 write-surface，但已经比前几天收窄很多。

### 2. local targeting 跑起来后，仍需继续压 stale-ref repeat

直接证据：

1. `practice_runs/20260419_184800/front_face_sketch_recess_focus_v00_194ed1dd/practice_analysis.md`

关键信息：

1. `local_targeting_action_count = 3`
2. `fresh_targeting_action_count = 2`
3. `stale_ref_action_count = 1`

这说明 exact targeting 已经跑起来，但还需要继续让 ref freshness discipline 更稳。

### 3. enclosure/half-shell 这一侧的新入口已经继续收窄到 plane-family false positive

今天新的 enclosure seed 中间工件：

1. `practice_runs/20260419_203500/hinged_pillbox_magnet_medium_v00_e34c3c99/actions/round_01_execute_build123d.json`
2. `practice_runs/20260419_203500/hinged_pillbox_magnet_medium_v00_e34c3c99/plans/round_02_response.json`
3. `practice_runs/20260419_203500/hinged_pillbox_magnet_medium_v00_e34c3c99/actions/round_02_execute_build123d.json`

它们说明当前 enclosure/half-shell 这一侧的新共享瓶颈不是“模型完全不会修”，而是：

1. round 1 的 front-face label recess 仍可能写成错误 plane family
2. 但更关键的是，preflight 的 `named_face_plane_family_mismatch` 还会把 base/lid 的 bare `Plane.XY` host-profile 草图也一起误判进去

这说明下一步需要继续做的不是追加 case rule，而是继续提高 preflight 治理规则的分辨率。

### 4. `210500` 把 matcher 根因进一步钉死，`221500` 正在做 live 验证

今天后半段，我又把同一 seed 用旧代码重跑了一次：

1. `practice_runs/20260419_210500/hinged_pillbox_magnet_medium_v00_e34c3c99/`

它进一步说明旧的 `named_face_plane_family_mismatch` 不是单纯“没看懂 front/back”，而是没有区分两类 sketch：

1. 真正的 front-face local edit
2. base/lid 的主体 profile 与内腔 profile

最直接的证据：

1. `practice_runs/20260419_210500/hinged_pillbox_magnet_medium_v00_e34c3c99/actions/round_01_execute_build123d.json`
2. `practice_runs/20260419_210500/hinged_pillbox_magnet_medium_v00_e34c3c99/plans/round_01_response.json`

旧逻辑下，round 1 一共打出了 6 个 `named_face_plane_family_mismatch`，其中：

1. 真正应该保留的是 front-face label / notch 的 `line 40` 与 `line 67`
2. 不该一起打进来的，是 base/lid host profile 的 `line 20`、`line 26`、`line 48`、`line 54`

因此今天又新增了一条更贴近真实 run 的红测与实现：

1. 红测：
   - `tests/unit/sub_agent_runtime/test_tool_runtime_preflight_lint.py::test_named_face_plane_family_mismatch_only_hits_local_front_face_sketches_not_shell_profiles`
2. 实现：
   - `src/sub_agent_runtime/tool_runtime.py`
   - 新增 host-profile 识别层，让 matcher 只对真正像局部 front-face edit 的 `BuildSketch` 出手

这条修复已经有三层证据：

1. 聚焦回归：
   - `./.venv/bin/pytest tests/unit/sub_agent_runtime/test_tool_runtime_preflight_lint.py -q -k 'only_hits_local_front_face_sketches_not_shell_profiles or named_front_face_plane_family_mismatch or ignores_bare_xy_host_profiles_when_front_face_local_edit_uses_xz or named_front_face_xz_plane_family'`
   - 结果：`4 passed`
2. 宽回归：
   - `./.venv/bin/pytest tests/unit/sub_agent_runtime/test_tool_runtime_preflight_lint.py tests/unit/sub_agent_runtime/test_skill_pack.py -q`
   - 结果：`187 passed`
3. 对 `210500` 的 round 1 源码做当前代码下的离线 preflight 复算：
   - 结果：`named_face_plane_family_mismatch` 命中数已经从 `6 -> 2`
   - 只剩真正该保留的 `line 40` 与 `line 67`

为了验证这次收窄不是只在本地离线成立，我又立刻启动了新的真实 Kimi rerun：

1. `practice_runs/20260419_221500/hinged_pillbox_magnet_medium_v00_e34c3c99/`

这条 run 的判断标准很简单：

1. 如果它不再死在旧的 6-hit plane-family 误伤面，说明这次治理收窄已经真正进入 live lane
2. 一旦越过去，下一层更可能暴露的共享瓶颈将是：
   - `repair_surface` 上的 `clamshell_host_local_cut_contract` 粘性不够
   - `validation_surface` 上的 front/back datum 漂移
   - `write_surface` 上 detached hinge transform 表达不稳定

## 后续方向

下一步不会回到 benchmark 拟合，而是继续沿着 practice lane 压三条共享问题：

1. 继续减少 first-write 的 helper / plane contract 幻觉残差
2. 继续压 local-targeting 后的 stale-ref repeat
3. 继续提高 preflight `named_face_plane_family_mismatch` 与 family repair lane 的分辨率，避免把 enclosure 的局部 front-face edit 和 host profile 混成一类
4. 用真实 Kimi rerun 验证 enclosure seed 是否已经越过旧的 plane-family 误伤面，而不是停留在本地离线结论

这条第 3 项今天已经拿到了第一条正向证据：

1. `practice_runs/20260419_201500/`

同时也拿到了下一条共享修复入口：

2. `practice_runs/20260419_203500/`
3. `practice_runs/20260419_210500/`
4. `practice_runs/20260419_221500/`

## 建议汇报时直接打开的文件

如果需要现场演示，优先打开下面这些文件即可：

1. `practice_runs/20260419_173500/brief_report.md`
2. `practice_runs/20260419_173500/front_face_sketch_recess_focus_v00_194ed1dd/queries/round_07_validate_requirement.json`
3. `practice_runs/20260419_181500/brief_report.md`
4. `practice_runs/20260419_181500/front_face_sketch_recess_focus_v00_194ed1dd/plans/round_04_response.json`
5. `practice_runs/20260419_184800/brief_report.md`
6. `practice_runs/20260419_184800/front_face_sketch_recess_focus_v00_194ed1dd/plans/round_04_response.json`
7. `practice_runs/20260419_184800/front_face_sketch_recess_focus_v00_194ed1dd/actions/round_04_apply_cad_action.json`
8. `practice_runs/20260419_184800/front_face_sketch_recess_focus_v00_194ed1dd/queries/round_07_validate_requirement_post_write.json`
9. `practice_runs/20260419_184800/front_face_sketch_recess_focus_v00_194ed1dd/practice_analysis.md`
10. `practice_runs/20260419_201500/brief_report.md`
11. `practice_runs/20260419_201500/topology_local_finish_focus_v00_95ab3188/practice_analysis.md`
12. `practice_runs/20260419_201500/topology_local_finish_focus_v00_95ab3188/plans/round_04_response.json`
13. `practice_runs/20260419_203500/hinged_pillbox_magnet_medium_v00_e34c3c99/actions/round_01_execute_build123d.json`
14. `practice_runs/20260419_210500/hinged_pillbox_magnet_medium_v00_e34c3c99/actions/round_01_execute_build123d.json`
15. `docs/work_logs/2026-04-19.md`
