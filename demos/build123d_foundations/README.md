# Build123d 基础演示集

这个目录包含 3 组以 Build123d 为核心的小型演示案例，它们不是随手写的 CAD 小例子，而是直接对应这个仓库最近真正处理过的运行时问题。

## 为什么是这 3 个演示案例

它们分别对应 3 类对迭代运行时很关键的契约表面：

1. `demo_local_frame_countersink.py`
   - 把 corner-based 草图坐标映射到居中宿主坐标系
   - 用 `Locations` 表达重复孔位布局
   - 对应成功案例 `benchmark/runs/20260413_102600/L2_172`
2. `demo_half_shell_directional_holes.py`
   - 使用同一构建器内的 `Mode.SUBTRACT` 和 `Mode.INTERSECT`
   - 把定向打孔固定在 `Plane.XZ.offset(0)` 上，使局部坐标保持 `(x, z)`
   - 对应 `benchmark/runs/20260413_142700/L2_130` 暴露的 half-shell 修复面
3. `demo_enclosure_body_lid.py`
   - 用 `Mode.PRIVATE` 暂存 cavity 与 lip geometry
   - 把 body 和 lid 语义显式拆开
   - 对应外部 enclosure 实验 `test_runs/20260413_094502`

## 如何理解 Build123d 相对 CadQuery 的优势

如果只是说“Build123d API 更新”“builder 更现代”，其实不太容易让受众真正理解这次迁移的工程价值。
对这个仓库来说，更准确也更容易理解的解释是：

1. Build123d 更容易把局部坐标、工作平面、布尔时机和暂存几何写成显式 contract。
2. 我们的运行时不是单纯追求“手写 CAD 舒不舒服”，而是要做：
   - prompt 约束
   - preflight lint
   - validator
   - repair packet
   - benchmark 复盘
3. 在这种场景下，越显式的建模表面，越容易做自动化诊断和修复。

可以把它和旧 CadQuery 风格粗略对比成这样：

1. CadQuery 更容易把很多状态藏在 workplane 链条里。
2. Build123d 更倾向于把这些状态拆成 `BuildPart`、`BuildSketch`、`Plane`、`Locations`、`Mode.*`。
3. 对“写一次几何模型”来说，两者都能做事；但对“要让 runtime 理解、检查、修复这段建模过程”来说，Build123d 更适合。

## 我们在这次演示里实际利用了哪些 Build123d 特性

不是所有 Build123d 特性都重要，这次真正有帮助的主要是下面几类：

1. `Plane`
   - 用来把“孔沿哪个方向打、局部坐标的两个轴分别是谁”讲清楚。
   - 例如 Y 方向打孔时，真正相关的是 `Plane.XZ` 上的 `(x, z)` 局部坐标。
2. `Locations`
   - 用来表达重复特征 placement。
   - 这对 countersink 阵列、lug holes 这类 requirement 很关键。
3. `Mode.SUBTRACT` / `Mode.INTERSECT`
   - 用来把 half-shell、bore、annular cut 这些逻辑放在同一个 builder 生命周期里处理。
   - 这比“先造一堆临时 solid 再猜布尔顺序”更稳。
4. `Mode.PRIVATE`
   - 用来暂存 cavity、lip、辅助 solid，而不提前污染 host。
   - 这对 enclosure body/lid 解释面很重要。

## 原本基于 CadQuery 的容器是怎么改造的

这里最容易说错的一点是：我们并不是把整个运行时推倒重写了。

更准确的说法是：

1. 上层容器没变：
   - 还是同一个 benchmark 入口
   - 还是同一个工件布局
   - 还是 planner → write tool → validator → repair 的循环
2. 真正变化的是底层建模契约：
   - 从旧的 CadQuery 风格建模表面
   - 切到更显式的 Build123d 风格建模表面
3. 为了配合这个切换，我们一起改了：
   - prompt 约束
   - skill guidance
   - preflight lint
   - validator clause grounding
   - repair packet

所以这次“迁移”不是单纯的语法替换，而是把原本依赖隐含 workplane 状态和脆弱布尔时机的容器，
改造成一个更容易被 runtime 理解和修复的 Build123d 容器。

## 这三组演示案例各自适合强调什么

1. `demo_local_frame_countersink.py`
   - 最适合强调局部坐标 contract。
   - 重点不是“孔怎么打”，而是 requirement 坐标如何从 corner-frame 映射到 centered host frame。
2. `demo_half_shell_directional_holes.py`
   - 最适合强调 builder-native boolean 和定向 workplane。
   - 重点不是“半壳能不能做出来”，而是为什么 `Plane.XZ`、`Mode.INTERSECT`、`Mode.SUBTRACT` 让 runtime 更容易稳定。
3. `demo_enclosure_body_lid.py`
   - 最适合强调 container 改造思路。
   - 重点不是 enclosure family 已经完全做完，而是 body/lid/cavity/lip 的生命周期现在更容易表达和解释。

## 如何启动这 3 个 demo

建议先进入仓库根目录，再决定是单独跑某个 demo，还是一次性跑整套。

```bash
cd ~/code/aicad.subagent.build123d
uv sync
```

上面这两步的含义分别是：

1. `cd` 到仓库根目录，保证三个 demo 都能按统一相对路径找到 `common.py` 和 `artifacts/` 输出目录。
2. `uv sync` 用来准备依赖；第一次跑 demo 或依赖变动后建议先执行一次。

### 单独启动 Demo A

```bash
uv run python demos/build123d_foundations/demo_local_frame_countersink.py
```

启动后会发生 3 件事：

1. 终端会打印 `artifacts/demo_01_local_frame_countersink.step` 的输出路径。
2. 终端还会打印一段 narrative，直接说明这个 demo 为什么能体现“局部坐标从 corner-frame 映射到 centered host frame”。
3. 会生成一个可打开的 STEP 文件，适合配合脚本里的中文注释一起讲 `Locations` 和显式 placement 的价值。

### 单独启动 Demo B

```bash
uv run python demos/build123d_foundations/demo_half_shell_directional_holes.py
```

启动后建议重点看两部分：

1. `artifacts/demo_02_half_shell_directional_holes.step`
   - 这是最终落盘的半壳体几何。
2. 脚本里关于 `Plane.XZ.offset(0)` 和 `(x, z)` 局部坐标的中文注释
   - 这部分最能说明为什么 Build123d 比旧式隐含 workplane 状态更适合 runtime。

### 单独启动 Demo C

```bash
uv run python demos/build123d_foundations/demo_enclosure_body_lid.py
```

这个 demo 会连续写出两个 STEP：

1. `artifacts/demo_03_enclosure_body.step`
2. `artifacts/demo_03_enclosure_lid.step`

它最适合解释“原来的 CadQuery 容器是怎么改造的”，因为 body、lid、cavity、lip 的生命周期在代码里是显式分开的。

### 一次性跑完整套

```bash
uv run python demos/build123d_foundations/run_all.py
```

这条命令会一次性生成 4 个 STEP，并且额外写出：

1. `demos/build123d_foundations/artifacts/summary.json`
2. `entries[].title`
3. `entries[].narrative`
4. `entries[].talking_points`
5. `entries[].bbox`
6. `entries[].volume`

如果你要准备演示或汇报，最省事的方式就是先跑这条命令，再把 `summary.json` 作为讲稿提纲。

## 看哪些落盘产物可以证明这些说法

只说“Build123d 更清晰”是不够的，最好直接对着下面这些产物讲：

1. `artifacts/demo_01_local_frame_countersink.step`
   - 证明 Demo A 已经把四个沉头孔真实落成到板件上，不是停留在坐标说明。
2. `artifacts/demo_02_half_shell_directional_holes.step`
   - 证明 Demo B 不只是写了一个半壳轮廓，而是把 half-shell、pad、侧向打孔一起组织成了最终几何。
3. `artifacts/demo_03_enclosure_body.step`
   - 证明 body 和 cavity 的关系已经明确落成。
4. `artifacts/demo_03_enclosure_lid.step`
   - 证明 lid plate、lip 和沉头孔不是混在一起猜出来的，而是按显式生命周期组织的。
5. `artifacts/summary.json`
   - 看每个 entry 的 `talking_points`，这是最适合直接复述的讲解点。
   - 看每个 entry 的 `bbox`，它能快速证明 demo 的整体尺寸和 requirement 对齐。
   - 看每个 entry 的 `step_path`，它能把“讲解条目”直接对应到具体 STEP 文件。

## 产物目录

```bash
cd ~/code/aicad.subagent.build123d
uv run python demos/build123d_foundations/run_all.py
```

生成的产物会落在 `demos/build123d_foundations/artifacts/`。

## 汇报时可以直接讲的要点

1. Build123d 用 `Plane` 和 `Locations` 把局部坐标系变成了一等公民，这和我们的 hole array、face-local feature 场景非常匹配。
2. 构建器原生的 boolean mode 比起临时拼接的 chained workplane state，更容易被 lint、repair 和解释。
3. `Mode.PRIVATE` 很适合在活动构建器内暂存 solid，避免过早污染 host。
4. 这些模式比旧的 CadQuery 风格契约更容易沉淀成确定性的运行时 guidance、preflight lint 和 validator expectation。
