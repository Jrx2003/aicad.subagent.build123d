# 验证链路说明

`validator` 的核心工作，是在每次写工具执行之后读取当前几何快照和 requirement，把“当前结果是否满足要求”拆成一组结构化检查，再输出完成态、未满足项和 blocker 摘要，供 runtime 决定下一轮怎么继续。

它本身主要负责三件事：

1. 读取当前 session 的最新几何状态，确认有没有 solid、体量是否正常、拓扑和局部几何是否可继续分析
2. 结合 requirement 的语义，生成一组有针对性的检查项，判断哪些 feature 已满足、哪些还没满足
3. 把结果整理成结构化验证证据，写回 runtime state，并影响下一轮 prompt、tool policy 和 benchmark 产物

但如果只讲到这里，其实还不够完整。因为系统在“验证”这件事上，除了几何 validator 本身，还同时维护一条“故障归类证据链”，用来解释代码为什么失败、下一轮应该怎么修。

这份说明文档只做一件事：把系统里“验证”这一块从头到尾讲清楚。

重点放在三件事：

1. 模型写完一轮代码后，系统到底拿什么来判断“对不对”
2. 如果这一轮失败了，系统又是怎么把“哪里出了问题”传给下一轮模型的
3. 这些判断最终会落到哪些产物里，方便回看和复盘

---

## 一、先回答一个最直接的问题：系统怎么知道该用哪些检查

1. 先读取 requirement text，提取一组稳定的语义信号  
   例如是否提到 hole、countersink、pattern、sweep、groove、target face、datum plane、explicit coordinates 等
2. 再根据这些语义信号选择对应的检查构建器  
   也就是激活哪些 family 的检查逻辑，哪些检查不需要启用
3. 检查构建器再结合当前 snapshot 和 action history 生成结构化检查项  
   最终形成 `checks / core_checks / diagnostic_checks`

这套机制走的是下面这条固定链路：

> requirement text  
> -> requirement semantics  
> -> 选择对应的检查构建逻辑  
> -> 结合 snapshot / history 生成结构化 checks

这也是为什么 validator 的行为可复现、可落盘、可复盘。

---

## 二、先说结论：系统里其实有两条“验证证据链”

要理解这套系统，最重要的是先分清它有两条一起工作的验证链：

### 1. 几何验证证据链

这条链关注的是：

- 当前模型有没有成功生成 solid
- 几何是否满足 requirement
- 哪些 feature 已经完成
- 哪些 feature 还缺失

这条链的核心输出是：

- `checks`
- `core_checks`
- `diagnostic_checks`
- `blockers`
- `summary`

也就是大家平时最容易想到的 validator 输出。

### 2. 故障归类证据链

这条链关注的是：

- 这次写代码为什么失败
- 错误是 API 用法问题、几何建模链问题，还是 sweep / selector / loft 这类具体失败模式
- 下一轮更适合继续写代码、先做语义 refresh，还是先做 probe

这条链的核心输出包括：

- `latest_write_health`
- `previous_tool_failure_summary`
- `failure_kind`
- `recovery_bias`
- `recommended_next_tools`

这部分同样会进入下一轮 prompt，只是它不在 `queries/` 里，而更多体现在 `trace/`、runtime state 和下一轮 context bundle 里。

所以真正的完整理解应该是：

> 系统会同时维护  
> “几何证据” 和 “故障归类证据” 两个面，下一轮模型会一起读这两部分信息。

---

## 三、参与这条链路的模块分别干什么

从功能上看，验证相关模块可以分成 6 层。

### 1. 写工具层

主要是：

- `execute_cadquery`
- `apply_cad_action`

这层负责产生新的几何状态，也会直接产生：

- 成功 / 失败
- stdout / stderr
- step 文件
- geometry snapshot

它是两条证据链共同的起点。

### 2. validator 层

核心工具是：

- `validate_requirement`

它负责读取当前 session 的几何快照和 requirement，然后产出结构化 checks 和 blockers。

这层主要服务“几何验证证据链”。

### 3. runtime 归纳层

这层负责把写工具和 validator 的结果转换成 runtime 可直接消费的状态对象。

它做两件关键事：

- 从写工具结果里提取“最新写入健康度”和“失败摘要”
- 从 validator 结果里提取“最新 blockers / 完成态 / lane 信息”

这层同时连接两条证据链。

### 4. 语义状态层

也就是：

- `DomainKernelState`
- `FeatureGraph`

它会把：

- 最新 write 结果
- 最新 validation blockers
- blocker taxonomy
- feature 完成态
- repair packet

统一放进当前语义状态里。

### 5. prompt 组装层

这一层会把 runtime 当前最重要的信息送给下一轮模型，包括：

- requirement
- 最近一次写工具失败摘要
- 最新 validation blockers
- domain kernel digest
- turn tool policy
- runtime skill notes

也就是说，模型在下一轮看到的是系统整理好的决策面，原始日志已经被整理和压缩。

### 6. benchmark / artifact 层

这一层负责把整条链路落成可检查的产物：

- `actions/`
- `queries/`
- `trace/`
- `benchmark_analysis.md`

所以验证结果会完整保留到 run 目录里，便于回看和复盘。

---

## 四、当一轮代码执行完成后，系统具体会做什么

这里分三种情况说。

### 情况 A：代码直接执行失败

例如：

- CadQuery API 参数写错
- 调用了不存在的方法
- sweep / wire / selector 这类操作直接抛异常

这时系统先拿到的是：

- `success = false`
- `stderr`
- `error_message`

然后 runtime 会立刻做一层失败归纳，把原始错误整理成更适合下一轮使用的对象。

这一步会形成：

- 这次失败发生在哪个工具
- 哪一轮失败
- 是否生成了 step
- 是否留下了几何
- 当前 geometry 是否为空、退化或不可信
- 这属于哪类失败 `failure_kind`
- 下一轮推荐怎么修 `recovery_bias`
- 下一轮优先开什么工具 `recommended_next_tools`

这就是故障归类证据链的核心。

换句话说：

> 如果代码连执行都没过，系统不会只把一段原始 stderr 扔给模型，  
> 系统会先把它整理成“这次失败是什么类型、下一步更适合怎么修”的结构化摘要。

### 情况 B：代码执行成功，但几何不满足 requirement

这时系统拿到的是：

- `success = true`
- 有 step 文件
- 有 geometry snapshot

接下来 runtime 可能会自动触发 post-write validation，或者模型在下一轮自己调用 `validate_requirement`。

validator 会对当前几何做结构化检查，产出：

- `checks`
- `core_checks`
- `diagnostic_checks`
- `blockers`
- `summary`

这部分就是几何验证证据链的核心。

换句话说：

> 如果代码跑通了，但结果没做对，系统主要靠 validator 告诉下一轮模型：  
> “哪里还没满足、当前剩下什么 blocker、是否已经接近完成。”

### 情况 C：代码失败信息和几何 blocker 同时存在

这是最常见、也最重要的情况。

例如：

- 上一轮有过明确的 API 失败
- 当前轮虽然写出了几何，但 validator 仍然给出 blocker

这时下一轮模型通常会同时看到：

- 最近一次具体失败摘要
- 最新 validation blockers
- 最新 domain kernel digest
- 当前 turn tool policy

也就是说，下一轮决策会同时看：

1. 代码到底是怎么失败过的
2. 当前几何离 requirement 还差什么

---

## 五、`validate_requirement` 本身到底在做什么

如果只看 validator 这一层，它做的事情可以简单理解成 5 步。

### 第一步：读取当前 session 的最新几何状态

validator 不重新建模，也不自己生成 CAD。  
它读取的是当前 session 已经存在的历史和最新快照。

### 第二步：理解 requirement 的结构化语义

它会先把 requirement 里的关键信息提取出来，例如：

- 目标面
- datum plane
- hole / countersink / pattern / sweep / notch 等语义
- 是否是全长槽、是否是局部面编辑、是否是 hollow profile

这一步的作用是构建后续 checks 所需的语义上下文。

### 第三步：生成检查项

根据前一步得到的语义信号，validator 会选择对应的检查构建逻辑，再结合当前几何状态和 action history 生成检查项。

这一步可以理解成两层：

1. 通用检查  
   例如是否存在 solid、volume 是否为正、是否存在明显 geometry issue
2. family / requirement-aware 检查  
   例如 hole、pattern、sweep、revolved groove、face edit、nested profile 这类题型对应的检查

也就是说，它不会把一套固定大列表全部跑一遍，实际流程是：

- 先做 requirement 语义解析
- 再按语义激活相关检查
- 再用 snapshot / history 去判断这些检查通过还是失败

典型会包括：

- 有没有 solid
- volume 是否为正
- 有没有明显 geometry issue
- 某个 family 对应的关键 feature 是否存在
- feature 的位置、尺寸、局部锚点是否对齐

### 第四步：区分 core checks 和 diagnostic checks

这一步很关键。

- `core_checks`
  - 用于判断 requirement 是否真正完成
- `diagnostic_checks`
  - 更偏向补充信息，不一定直接阻断完成判定

所以 validator 会提前区分哪些失败必须拦住完成，哪些只作为辅助诊断。

### 第五步：生成 blockers 和 summary

最后 validator 会把失败的 core checks 汇总成：

- `blockers`
- `summary`

这样 runtime 不需要自己再从几十条 check 中二次猜测“到底什么最关键”。

---

## 六、几何验证证据链具体怎么进入下一轮

几何验证结果出来之后，不会只停留在 `queries/` 文件里。

它会继续影响下面几层。

### 1. 写进 runtime 的最新验证状态

runtime 会把 validator 的结果标准化后写进：

- `latest_validation`

这个对象是后续所有决策的直接输入之一。

### 2. 写进 `DomainKernelState` / `FeatureGraph`

最新 validation blockers 会同步进语义状态层，影响：

- 当前哪些 feature 被标记为 blocked
- 哪些 feature 已完成
- 当前 blocker 属于哪个 family
- 后续 digest 应该暴露哪些 repair 线索

### 3. 压缩进 `domain_kernel_digest`

接着，语义状态层会被压成一个适合 prompt 使用的 digest。

下一轮模型不会直接读完整 graph，实际读取的是 digest 里的关键摘要，例如：

- 当前 active / blocked / completed features
- 最新 validation blockers
- repair packet 摘要

### 4. 影响 turn tool policy

如果 validator 明确说明：

- 已经完成
- 还剩局部 blocker
- 需要 semantic refresh

那么 runtime 这一轮暴露给模型的可选工具也会跟着变。

所以 validator 的结果不仅影响“认知”，还会影响“这一轮允许怎么做”。

---

## 七、故障归类证据链具体怎么进入下一轮

这部分是上一版文档没有讲清楚的重点。

### 1. 写工具失败后，系统先构建 `latest_write_health`

这里会提炼一些非常客观的状态：

- 有没有 step 文件
- 有没有 solid
- bbox 是否退化
- volume 是否有效
- 有没有明显 invalid signals

这个对象的作用是把“这次写出来的东西客观上健康不健康”整理出来。

### 2. 系统再构建 `previous_tool_failure_summary`

如果上一轮是失败写入，runtime 不会只保留原始报错，而会额外形成一个失败摘要对象。

这个对象通常会包含：

- tool 名称
- 哪一轮失败
- 成功 / 失败
- geometry 是否缺失
- 有没有 step
- invalid signals
- 失败类型 `failure_kind`
- 恢复偏好 `recovery_bias`
- 下一轮建议工具 `recommended_next_tools`

它的价值在于：

> 把一段原始失败日志，变成下一轮模型可以直接消费的“修复任务描述”。

### 3. 失败摘要会直接进下一轮 prompt

下一轮模型实际看到的上下文里，会明确有一项：

- 最近一次写入失败摘要

同时还会看到：

- 当前 latest_write_health
- 当前 turn_tool_policy
- 当前 domain kernel digest

所以模型会在一个已经被系统整理过的失败上下文里继续修复。

### 4. turn tool policy 会根据失败类型主动收窄

例如：

- 某些失败更适合继续 `execute_cadquery` 直接修
- 某些失败更适合先 `query_kernel_state`
- 某些失败更适合先 `execute_cadquery_probe`

这说明故障归类证据链不仅是“告诉模型出了什么错”，还会进一步改变系统的工具暴露方式。

---

## 八、模型下一轮到底看到了什么

如果要用一句最实用的话概括，可以这样说：

> 下一轮模型看到的是系统整理好的“当前问题面”。

这个问题面通常由下面几部分组成：

- requirement
- 最近一次写入健康度
- 最近一次失败摘要
- 最新 validation blockers
- domain kernel digest
- 当前 turn tool policy
- runtime skill notes
- 最近几轮的简化 transcript

因此，模型在下一轮做的事，其实是：

1. 读取系统给出的当前问题面
2. 判断当前更像是：
   - 继续 code repair
   - 先做 semantic refresh
   - 先做 probe
   - 还是已经可以 finish
3. 选择下一步工具并生成下一轮代码或查询

---

## 九、产物应该怎么看

如果想快速理解某条 run 的验证链，不需要先看代码，先看产物就够了。

建议按下面顺序看。

### 1. `actions/`

先看写工具做了什么，以及有没有生成几何。

这里回答的是：

- 本轮写入是否成功
- 有没有 `model.step`
- geometry 大概是什么状态

### 2. `queries/round_XX_validate_requirement_post_write.json`

再看几何验证结果。

这里回答的是：

- 当前 requirement 是否完成
- 当前 blockers 是什么
- 是哪些具体 checks 失败了

### 3. `trace/tool_timeline.jsonl`

这里能看清楚证据是怎么流动的。

比如：

- 某次成功 write 之后，哪些旧验证结果被判 stale
- 某次失败 write 之后，系统保留了哪些旧证据

### 4. `trace/events.jsonl`

这里能看到每一轮的：

- turn tool policy
- turn status
- latest validation summary
- evidence status

这能帮助理解“为什么下一轮只暴露这些工具”。

### 5. `trace/feature_graph_round_XX.json`

这里能看到验证结果怎么进入语义状态层。

重点看：

- `latest_sync_reason`
- 最新 binding
- blocked / completed features

### 6. `benchmark_analysis.md`

最后看管理视角的总结。

这里通常能直接看到：

- Runtime Validation View
- Validation Lanes
- Feature Graph 摘要
- 最终通过 / 失败判断

---

## 十、总结

理解当前系统里的“验证”，不能只盯着 validator 本身。

真正完整的验证链路包括两条：

1. **几何验证证据链**
   - 当前几何满足 requirement 了吗
   - 还剩哪些 blocker

2. **故障归类证据链**
   - 上一轮代码具体错在哪类问题
   - 下一轮更应该怎么修

这两条链会一起进入 runtime state、domain kernel digest、turn tool policy 和下一轮 prompt。

所以更准确地说：

> 当前系统里的验证，不只产出“判通过 / 不通过”，  
> 系统还会把“几何是否正确”和“为什么上一轮失败”整理成下一轮可消费的决策面。
