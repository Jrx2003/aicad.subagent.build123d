# Validation / Failure Understanding Problem Memo

这份文档只描述当前架构中的问题，不展开实现方案，也不写实施顺序。

---

## 1. 总体判断

当前系统已经具备较强的可观测性，write / validate / trace / digest 这条链路是完整的，已知 family 上也有明显收敛能力。

当前瓶颈集中在“失败理解”和“验证泛化”两块：

- 系统很擅长处理熟悉的失败模式
- 系统很擅长组织已有证据
- 系统对未知 case 的处理上限偏低
- 诊断结果和修复面之间的连接还不够稳定

---

## 2. 问题一：失败理解依赖大量硬编码归类

当前写工具失败后，runtime 会把错误整理成固定的失败类型，并据此生成恢复偏好、推荐工具和下一轮修复方向。

这带来三个问题：

- 新错误模式需要继续往映射表里补分支
- 错误归类一旦偏掉，后续 repair lane 会一起偏掉
- 模型读到的失败信息很多已经是系统预解释过的结果，自主理解空间不大

这部分机制短期有效，但扩展性一般，维护成本会持续上升。

---

## 3. 问题二：validator 混合了通用几何检查和 family 检查

当前 `validate_requirement` 同时承担了两类完全不同的职责：

- 通用几何健康检查  
  例如 solid、volume、geometry issue、明显退化
- family 语义检查  
  例如 hole、pattern、sweep、groove、nested profile、face edit 等

这会带来两个直接后果：

- 从表面上看像一个统一的 requirement validator，实际能力来源很不均匀
- 当 case 超出 family 覆盖时，validator 会明显变弱，但外层很难快速区分“几何错了”还是“覆盖不够”

---

## 4. 问题三：FeatureGraph / DomainKernelState 更强于组织信息，弱于主动诊断

当前 graph / kernel 已经能承载很多重要对象：

- blocker taxonomy
- feature binding
- validation blockers
- repair packet
- digest

问题在于，这些内容大多还是从既有规则结果压缩出来的。

当前状态更像：

- 它能把问题命名清楚
- 它能把状态同步清楚
- 它能把 prompt surface 压缩清楚

但在很多失败 case 里，它还不能稳定地产出高置信、低方差的修复面。

换句话说，graph 现在已经是一个有效的状态协调层，但还不是一个足够强的诊断层。

---

## 5. 问题四：缺少显式的弱诊断 / 未知 case 状态

当前系统倾向于把问题尽量落进某个已知 family、某个 blocker taxonomy 或某个 repair lane。

这在已知 case 上是有价值的，但在未知 case 上会带来风险：

- 证据不够时，系统仍可能过早收窄
- 问题会被压进一个看起来具体、实际不够可靠的 lane
- 模型会在低质量诊断的基础上继续迭代

当前架构里“诊断不足”这个状态表达得不够明确。

---

## 6. 问题五：验证链和修复链之间还有断层

系统已经能做很多验证工作：

- 判断执行是否失败
- 判断当前几何是否满足 requirement
- 给出 blocker
- 给出 failure summary

问题出在验证结果进入下一轮之后，并不总能自然转成一个稳定的 repair surface。

常见表现包括：

- blocker 已经有了，但下一轮动作仍然偏泛
- graph 知道问题在哪个 family，却给不出足够具体的修复约束
- 同一问题可能在多轮里重复出现，但修复路径收敛得不够快

这说明“识别问题”和“约束修复”之间还有一段桥没有真正打通。

---

## 7. 问题六：benchmark 容易把系统推向 coverage engine

当前优化过程很容易围绕“哪个 family 还没收口”展开。

这会自然鼓励下面这些做法不断增加：

- 新的 family check
- 新的 blocker 命名
- 新的 repair note
- 新的 case-specific fallback

短期看，这对 benchmark 收口有效。长期看，系统可能越来越擅长覆盖熟悉样本，却没有同步提升未知 case 的处理质量。

---

## 8. 问题七：模型的职责边界已经被系统前置逻辑大量收缩

当前很多关键理解工作是在模型调用前完成的：

- requirement 被先解析成语义信号
- 写失败被先归成固定 failure kind
- validator 先生成 family-aware checks
- runtime 先整理成 failure summary / digest / tool policy

这带来的结果是：

- 系统稳定性提高了
- prompt surface 更清楚了
- 模型每轮更容易接上当前状态

同时也意味着：

- 模型真正参与 failure understanding 的空间偏小
- 系统对新 case 的表现很依赖前置规则是否已经覆盖

这个边界本身没有对错，但当前已经开始显露出上限。

---

## 9. 汇总结论

当前架构的问题，不在于“完全没有验证能力”，也不在于“完全没有 agent 性”。

更准确的判断是：

- 系统已经很会处理熟悉问题
- 系统已经很会把证据整理成可消费状态
- 系统对未知问题的诊断和修复桥接能力还不够强

因此，当前真正的压力点主要有三类：

- 失败理解过度依赖硬编码归类
- validator 的通用能力与 family 能力混在一起
- graph / kernel 到 repair surface 的这一步还不够强
