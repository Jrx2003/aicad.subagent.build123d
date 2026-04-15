# Build123d Runtime 两天工作进展说明

时间范围：2026-04-14 至 2026-04-15  
报告目录：`report-20260415-build123d-two-day-progress/`

## 执行摘要

这两天最重要的成果，不是简单地“又多过了几个 benchmark case”，而是 Build123d 迁移后的 runtime 已经开始形成一条稳定的收敛主线。

这条主线可以概括为四件事：

1. 先把迁移后的主要问题缩小到少数几个高价值表面：`validator mismatch`、`read-stall`、`path-sweep contract`、`countersink family`。
2. 再把这些问题对应到明确的 runtime 改动位置，而不是继续靠单题 prompt patch。
3. 用 sampled L1/L2 和固定 canary 两套真实运行结果证明这些改动开始生效。
4. 把证据沉淀到统一的 `plans → actions → queries → trace → evaluation → report` 链路中，使后续优化能够稳定复盘、稳定比较。

当前阶段最适合对外表达的结论是：

- Build123d 主路径已经能够在真实 sampled L1/L2 上完整运行。
- 提升已经开始来自通用 runtime 治理，而不是单题运气。
- L1 的 validator/read-stall 家族明显后退，L2 的问题开始集中到 path-sweep 与 countersink 两个更值得继续投入的 family。

## 两天工作的主线

### 2026-04-14：先把问题真正讲清楚

- 完成 Build123d demo、README、HTML 报告材料的中文化，把迁移工作的展示面和演示面先整理清楚。
- 结合 benchmark 结果重新梳理当前主矛盾，明确 half-shell、path-sweep、countersink 是最值得继续推进的 Build123d family。
- 把后续工作重点从“继续堆零散 prompt 文案”收口到“修主路径控制流、validator mismatch、read-stall、runner exception、Build123d contract”。

对应记录：

- `docs/work_logs/2026-04-14.md`
- `report-20260414-build123d-vs-cadquery-benchmark/`

### 2026-04-15：再把修复入口、回归入口和证据入口接起来

- 新增 `benchmark/canary_case_sets.json`，把 canary 套件固定成正式回归入口。
- 为 `benchmark/run_prompt_benchmark.py` 接入 baseline 指标，形成统一的摘要口径。
- 收紧成功写后的 validation / read-stall policy，减少 validation ping-pong。
- 修复 `validate_requirement` 的取消异常传播，把 runner 层噪声压回结构化 failure。
- 补齐 path-sweep、annular profile、top-face channel 等 clause interpretation 与 family evidence 复用。

对应记录：

- `docs/work_logs/2026-04-15.md`
- `docs/cad_iteration/CANONICAL_BASELINE.md`
- `docs/work_logs/2026-04-15_generalization-checklist.json`

## 这里说的 canary，到底是什么

canary 不是临时挑几题 smoke，而是一组固定的小切片 benchmark，用来代表当前最关键的 failure family 和两三个相对稳定的 guardrail case。

当前 canary 明确定义在：

- `benchmark/canary_case_sets.json`

当前固定 case 为：

- `L1_122`
- `L1_148`
- `L1_157`
- `L1_159`
- `L2_88`
- `L2_130`
- `L2_149`
- `L2_172`

这 8 个 case 的设计目的很明确：

- `L1_122 / L1_157 / L1_159` 负责暴露 `validator mismatch / read-stall`
- `L1_148` 负责暴露 `runner exception`
- `L2_130 / L2_172` 负责暴露 half-shell、directional hole、countersink 这类 Build123d family
- `L2_88 / L2_149` 负责覆盖已经开始变稳定的 revolve / path-sweep guardrail

因此，canary 的意义不是替代 full L1/L2，而是承担每次主路径改动后的第一道固定回归入口。

对应正式定义文件：

- `docs/cad_iteration/CANONICAL_BASELINE.md`

## 页面里的“问题归类”具体怎么做

这份报告里的归类不是人工写备注，而是 `benchmark/run_prompt_benchmark.py` 在聚合 case 产物时直接算出来的。可以把它理解成三层：

### 第一层：`status / failure_category`

这层先回答“哪一层先坏了”。

典型来源包括：

- runner 非零退出：`RUN_ERROR / runner_exception`
- 没有生成 STEP：`NO_STEP / missing_step_artifact`
- evaluator 通过但 validator 没闭环：`VALIDATOR_MISMATCH / validator_evaluator_disagreement`
- 有 STEP 但没收敛：`INCOMPLETE / excessive_reinspection` 或 `incomplete_run`

### 第二层：`failure_cluster`

这层再回答“它到底属于哪一类行为模式”。

当前常见 cluster 包括：

- `read_stall_gap`
- `code_path_family_gap`
- `runtime_gap`
- `tool_gap`

它们不是人工标签，而是从 runtime summary 的聚合判断继承过来的，所以能直接汇总到 `summary.json`、`brief_report.md`、`run_diagnostics.md`。

### 第三层：`recommended_fix_layer`

这层最后回答“应该先改哪一层”。

例如：

- `validator`
- `tool_or_context`
- `kernel_binding_gap`
- `prompt_or_codegen`

这一层会结合 validation lanes、domain-kernel mismatch、stop reason 和失败类别一起给出。

换句话说，这套归类不是为了把问题说得更花，而是为了把“先改哪里”也一起固定下来。

## 改动是怎样在流程中起作用的

这轮工作的重点不是“改了哪些文件”，而是“这些改动怎样改变每一轮 runtime 的运行方式”。

### 1. 首轮写模仍然坚持 Build123d-first，但首轮方向更明确

涉及代码：

- `src/sub_agent_runtime/skill_pack.py`

作用：

- 继续保持 `execute_build123d` 是 canonical first write。
- 让 half-shell、path-sweep、explicit-anchor hole 这类 family 更容易在首轮就进入正确 skeleton，而不是先写出一个方向错误的实体。

### 2. 写前校验先把 Build123d 常见错法拦住

涉及代码：

- `src/sub_agent_runtime/tool_runtime.py`

作用：

- builder method reference、annular same-sketch profile、path-sweep 参数错法、局部 API 误用，会优先在 preflight lint 阶段被发现。
- 这样减少了“白白浪费一轮 sandbox 执行”的情况。

### 3. 写成功以后，validator 不再把系统轻易拖进 read-stall

涉及代码：

- `src/sub_agent_runtime/agent_loop_v2.py`

作用：

- fresh `insufficient_evidence` 写后验证不再被当成普通读操作。
- 如果中间没有新写入、没有新证据，连续 validation 会被阻止继续扩散。
- closure policy 变得更明确，case 更容易在成功写入后直接进入完成态。

### 4. validator clause interpretation 开始真正复用已有几何证据

涉及代码：

- `src/sandbox_mcp_server/validation_interpretation.py`
- `src/sandbox_mcp_server/validation_llm.py`
- `src/sandbox_mcp_server/registry.py`

作用：

- top-face full-span channel、bbox 单维度条款、top/bottom on Z、slot/notch/channel alignment、path-sweep rail/frame/profile 等 requirement clause，开始能够绑定到已有 family evidence 上。
- 这样实体正确时，validator 不会继续因为“证据链不完整”而空转。

### 5. benchmark 不再只是“跑一下看看”，而是形成固定 baseline

涉及代码：

- `benchmark/run_prompt_benchmark.py`
- `benchmark/canary_case_sets.json`

作用：

- sampled L1/L2、canary、brief report、summary、run diagnostics 开始共享同一套指标。
- 后续优化终于可以稳定比较，不再靠人工翻 run artifact 才能判断有没有进步。

## 这些改动是怎么具体实现的

上面那一节讲的是“作用”，这一节补的是“机制”。

### 1. `agent_loop_v2.py`：直接收紧写后控制流

这一层最关键的不是 prompt，而是两个具体判断：

- `_latest_validation_is_fresh_for_write(...)`
- `_has_repeated_validation_without_new_evidence_after_write(...)`

它们共同决定两件事：

- 如果刚写完后的 validation 已经是 fresh 且 complete，就直接走 `post_write_validated_complete`
- 如果刚写完后的 validation 只是 `insufficient_evidence`，但中间没有新写入、没有新 kernel/probe 证据，就禁止继续 validation ping-pong，强制回到 semantic refresh lane

这就是为什么 `L1_122` 能从 `read_stall_gap` 拉回单轮 `PASS`。

### 2. `tool_runtime.py`：把常见 Build123d 错法提前升格成 preflight lint

这轮不是简单“报错更多了”，而是把以前会浪费完整一轮 sandbox 的错法提前拦住。

本轮典型新增/强化规则包括：

- `invalid_build123d_contract.active_builder_temporary_primitive_arithmetic`
- `invalid_build123d_contract.explicit_anchor_manual_cutter_requires_subtract_mode`
- `invalid_build123d_keyword.countersink_*_alias`

其中：

- 第一类拦截 active `BuildPart` 里直接创建临时 primitive 再拿去做显式布尔算术
- 第二类拦截 explicit-anchor hole family 里忘记 `mode=Mode.SUBTRACT` 的手写 cutter
- 第三类拦截 `CounterSinkHole(...)` 的错误关键字，比如 `angle=`、`head_radius=`

它们会在 action 落盘前直接给出 repair recipe，避免 session 先被错误几何污染。

### 3. `blocker_taxonomy.py` + `feature_graph.py`：把新的 blocker 放回正确 family，并清掉旧 blocker

仅有 lint 还不够。如果旧 blocker 继续停留在 `general_geometry`，或者已修复的旧 feature instance 继续挂在 graph 上，下一轮 prompt 还是会被过期信息带偏。

因此这轮又做了两件事：

- 把 `head_diameter_*`、`cone_angle_*`、`through_hole_diameter_*`、`countersink`、`conical_recess`、`activate_the_hole_wizard_or_the_revolved_cut_tool` 这类 blocker 重映射回 `explicit_anchor_hole`
- 把已被新 validation 覆盖的旧 blocker instance 标成 `resolved`

这正是 `L2_172` 能从“孔位已经修对但 prompt 仍被旧 blocker 污染”推进到 3 次写入后 `PASS` 的关键。

## 系统级效果

### sampled L1：已经证明主路径可运行

证据文件：

- `benchmark/runs/20260415_113200/summary.json`

关键结果：

- `status_counts = {'INCOMPLETE': 1, 'NO_STEP': 1, 'PASS': 8}`
- `failure_cluster_counts = {'code_path_family_gap': 1, 'read_stall_gap': 1}`
- `first_solid_success_rate = 0.9`
- `requirement_complete_rate = 0.8`
- `runtime_rewrite_rate = 0.375`

说明：

- L1 主路径已经具备完整运行能力。
- 失败面不再随机扩散，而是集中到有限几个 cluster。

### sampled L2：困难仍在，但问题已经被压缩到少数 family

证据文件：

- `benchmark/runs/20260415_113201/summary.json`

关键结果：

- `status_counts = {'INCOMPLETE': 2, 'NO_STEP': 3, 'PASS': 4, 'RUNTIME_ERROR': 1}`
- `failure_cluster_counts = {'code_path_family_gap': 2, 'runtime_gap': 2, 'tool_gap': 2}`

说明：

- L2 仍显著难于 L1。
- 但问题已经不再是“黑盒失败”，而是可以按照 family 与 fix layer 来组织治理。

### canary：已经成为正式回归入口

证据文件：

- `benchmark/runs/20260416_002500/summary.json`
- `benchmark/runs/20260416_002500/brief_report.md`

关键结果：

- `status_counts = {'INCOMPLETE': 1, 'NO_STEP': 1, 'PASS': 6}`
- `first_solid_success_rate = 0.875`
- `requirement_complete_rate = 0.75`
- `runtime_rewrite_rate = 0.38461538461538464`
- `tokens_per_successful_case = 10493.666666666666`

说明：

- canary 已经足够稳定地指出当前剩余主矛盾。
- 当前剩余热点已经高度集中在 `L2_149` 和 `L2_172`。

## 四个最值得直接展示的案例

### 案例一：`L1_122` 证明收口策略已经开始发挥作用

推荐打开顺序：

1. `benchmark/runs/20260415_203500/L1_122/summary.json`
2. `benchmark/runs/20260415_205500/L1_122/trace/stop_reason.json`
3. `benchmark/runs/20260415_205500/L1_122/queries/round_01_validate_requirement_post_write.json`

前后变化：

| run | status | rounds | tokens | 关键信号 |
| --- | --- | ---: | ---: | --- |
| `20260415_203500` | `INCOMPLETE` | 4 | 23472 | `failure_cluster=read_stall_gap`，存在 3 个 inspection-only round |
| `20260415_205500` | `PASS` | 1 | 4888 | `stop_reason=post_write_validated_complete` |

这一组证据说明：

- 问题并不在于实体做不出来。
- 问题在于实体已经正确时，系统能不能停止继续做无意义读取。
- 4/15 这组修改已经把这类 case 从 write 后空转拉回了首轮闭环。

### 案例二：`L1_148` 证明 runner 噪声已经开始被压平

推荐打开顺序：

1. `benchmark/runs/20260415_183000/L1_148/summary.json`
2. `benchmark/runs/20260415_195500/L1_148/summary.json`
3. `benchmark/runs/20260416_001500/L1_148/trace/stop_reason.json`

前后变化：

| run | status | rounds | tokens | 关键信号 |
| --- | --- | ---: | ---: | --- |
| `20260415_183000` | `INCOMPLETE` | 4 | 22992 | `validation_call_count=4`，且伴随 `read_stall_gap` |
| `20260415_195500` | `INCOMPLETE` | 4 | 25232 | 校验通道已更稳定，但收口仍未完全收干净 |
| `20260416_001500` | `PASS` | 1 | 4630 | `validation_complete=true`，`planner_rounds=1` |

这一组证据说明：

- 之前拖累效果的并不只是几何问题。
- `validate_requirement` 的 transport cancellation 与 close-path error 会把问题抬升成 runner 层噪声。
- 当这些异常被压回结构化返回后，系统才真正有机会收敛几何本身。

### 案例三：`L2_149` 证明 path-sweep 家族已经进入完整治理闭环

推荐打开顺序：

1. `benchmark/runs/20260415_120527/L2_149/queries/round_06_validate_requirement.json`
2. `benchmark/runs/20260415_122001/L2_149/evaluation/benchmark_eval.json`
3. `benchmark/runs/20260415_122633/L2_149/evaluation/benchmark_eval.json`

前后变化：

| run | status | rounds | tokens | 关键信号 |
| --- | --- | ---: | ---: | --- |
| `20260415_120527` | `INCOMPLETE` | 8 | 66088 | 已出实体，但多条 clause 仍是 `insufficient_evidence` |
| `20260415_122001` | `PASS` | 5 | 41796 | `validation_complete=true`，evaluator `final_score=1.0` |
| `20260415_122633` | `EVAL_FAIL` | 3 | 29155 | runtime validator 通过，但 evaluator 识别出 face family 错误 |

这一组证据说明三件事：

1. path-sweep 家族已经能写出实体。
2. 当 clause interpretation 与 family evidence 补齐后，runtime validator 与 evaluator 可以同时认可结果。
3. fallback 一旦过宽，系统也能很快暴露出误判，因此下一步可以继续收紧，而不是停留在“能跑通一次”。

### 案例四：`L2_172` 给出了一条最完整的“实现链”证据

推荐查看：

1. `benchmark/runs/20260415_163643/L2_172/actions/round_08_execute_build123d.json`
2. `benchmark/runs/20260415_164609/L2_172/actions/round_01_execute_build123d.json`
3. `benchmark/runs/20260415_164609/L2_172/actions/round_02_execute_build123d.json`
4. `benchmark/runs/20260415_164609/L2_172/queries/round_03_validate_requirement_post_write.json`
5. `benchmark/runs/20260415_164609/summary.json`

这条链路分别说明：

1. 旧错法是什么
   - 旧 run 里 `CounterSinkHole(...)` 还在写错误关键字，例如 `angle=...`
2. preflight lint 是怎样介入的
   - 新 run 的 round 1 会先拦截手写 `Cone/Cylinder` cutter 却没做 `Mode.SUBTRACT` 的错法
3. family 与 blocker 是怎样被重新拉回正确轨道的
   - 新的 countersink / head diameter blocker 不再掉回 `general_geometry`
4. 最终怎样收敛
   - round 3 validation 已经明确给出 `head_diameter=12.0`、孔位、外形尺寸都闭环，整题 `PASS`

这一组证据最适合说明：

- 改动不是单点生效，而是 lint、taxonomy、feature graph、validator 一起生效
- 当前 Build123d runtime 已经具备把同一 family 的问题沉淀成共享治理逻辑的能力

## 页面中的配图和演示材料

### 页面配图

HTML 页面已经把三组最重要的图直接接入：

- `L2_149`
  - `benchmark/runs/20260415_122001/L2_149/evaluation/generated_preview_iso.png`
  - `benchmark/runs/20260415_122001/L2_149/evaluation/ground_truth_preview_iso.png`
- `L2_130`
  - `report-20260414-build123d-vs-cadquery-benchmark/assets/l2_130_build123d_iso.png`
  - `report-20260414-build123d-vs-cadquery-benchmark/assets/l2_130_cadquery_iso.png`
  - `report-20260414-build123d-vs-cadquery-benchmark/assets/l2_130_ground_truth_iso.png`
- `L2_172`
  - `report-20260414-build123d-vs-cadquery-benchmark/assets/l2_172_build123d_iso.png`
  - `report-20260414-build123d-vs-cadquery-benchmark/assets/l2_172_cadquery_iso.png`
  - `report-20260414-build123d-vs-cadquery-benchmark/assets/l2_172_ground_truth_iso.png`

这三组图分别回答三件事：

- path-sweep 已经出现成功闭环。
- half-shell 已明显恢复健康。
- countersink 仍然是当前主战场。

### 演示材料

为了让汇报不只停留在 benchmark case，本轮还整理了 Build123d demo 材料：

- 中文说明：`demos/build123d_foundations/README.md`
- Demo 脚本：
  - `demos/build123d_foundations/demo_local_frame_countersink.py`
  - `demos/build123d_foundations/demo_half_shell_directional_holes.py`
  - `demos/build123d_foundations/demo_enclosure_body_lid.py`
- 统一落盘摘要：
  - `demos/build123d_foundations/artifacts/summary.json`

这批材料适合在汇报中配合使用，因为它们专门解释了：

- Build123d 相对 CadQuery 的优势是什么；
- 迁移时利用了哪些 Build123d 特性；
- 原有容器是怎样被改造成 Build123d-first runtime 的；
- demo 运行后会落下哪些具体产物，以及这些产物如何证明对应结论。

## 如何快速取证

如果需要把这份报告中的任意一条结论展开，可以按下面顺序取证：

1. `plans/round_*_response.json`
   - 看模型为什么选这个工具。
2. `actions/round_*_execute_build123d.json`
   - 看实际写入是否成功、有没有 preflight lint、有没有结构化失败。
3. `queries/round_*_validate_requirement*.json`
   - 看 validator 怎样解释 clause、哪里 complete、哪里 blocker、哪里 `insufficient_evidence`。
4. `trace/stop_reason.json`
   - 看这个 case 最终为何停下。
5. `evaluation/benchmark_eval.json`
   - 看 runtime validator 和 evaluator 是否一致。
6. `brief_report.md` / `run_diagnostics.md` / `summary.json`
   - 看整个 run root 的状态分布、failure cluster、baseline 指标和重点问题。

另外，如果需要核对 canary 与问题归类本身，优先打开：

1. `benchmark/canary_case_sets.json`
2. `docs/cad_iteration/CANONICAL_BASELINE.md`
3. `benchmark/run_prompt_benchmark.py`

这条证据链的重要性在于：

- 后续优化不需要再靠人工记忆“上一次到底发生了什么”；
- 每次修改都可以回到同一套链路里验证效果；
- benchmark、代码和文档系统终于开始共享同一套口径。

## 当前判断

当前阶段已经拿到的成果：

- Build123d 主路径已经证明自己可以在 sampled L1/L2 上完整运行。
- canary、baseline、brief report、summary 的口径已经统一。
- `L1_122` 和 `L1_148` 给出了很清楚的收口 / runner 稳定性改善证据。
- `L2_149` 给出了 path-sweep 家族“成功闭环 + 暴露误判 + 继续收紧”的完整治理链条。

当前阶段仍需继续推进的重点：

- `L2_172` countersink family 仍未收敛完成，需要继续围绕 countersink geometry recognition、host face、explicit-anchor hole contract 推进。
- `L2_149` 虽然已经出现成功 run，但 canary 仍显示 first-turn bias 和 code path 稳定性不够，需要继续提高 path-sweep recipe 命中率。
- `L2_130` 今天形成了一条完整的“发现 family 路由问题 -> 修 lint 边界与 half-shell recipe -> targeted rerun 收敛”的闭环：
  - 旧 run `benchmark/runs/20260415_164854` 为 `EVAL_FAIL / code_path_family_gap`
  - 新 run `benchmark/runs/20260415_170236` 已为 `PASS`
  - 且 `family_repair_packet_hit=true`，说明 repair packet 已开始在 half-shell family 上真实帮助收敛
- 后续优化必须继续坚持 family-first 泛化，不应回退到针对单题补硬规则的旧路线。

## 一句话收束

这两天最大的进展，不是多过了几题，而是 Build123d 迁移后的 runtime 已经开始具备“发现问题、归类问题、收紧契约、复跑验证”的稳定闭环能力。
