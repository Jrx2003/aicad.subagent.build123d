# 2026-04-10 Validator / Generalization Report Redesign

## Goal

Rewrite the existing validator report page into a much simpler manager-facing flow page that answers five direct questions in plain language:

1. 昨天到底做了什么
2. 原理是什么
3. 数据流怎么走
4. 涉及哪些模块
5. 最后有什么效果和提升

The page must work both as a presentation surface and as a defense surface when a reviewer asks follow-up questions.

## Deliverables

Update the existing report package:

- `report-20260409-validator-generalization/index.html`
- `report-20260409-validator-generalization/styles.css`
- `report-20260409-validator-generalization/main.js`
- `report-20260409-validator-generalization/shareable-report.md`

## Core Direction

The previous version failed because it:

- spread the story across too many modules
- mixed architecture commentary with the actual work summary
- used too much abstract language
- did not help the presenter answer concrete follow-up questions

The new version should behave like an interactive speaking outline, not a broad architecture memo.

## Narrative Spine

Use a 6-screen vertical flow:

### 1. 昨天做了什么

Only show the two actual work items:

- preflight lint for `execute_cadquery`
- validator fallback for spherical recess geometry evidence

This screen should also answer:

- 为什么这两件事值得做

### 2. 原理是什么

Explain two parallel chains in simple language:

- 几何验证链
- 失败归类链

This screen must also include an explicit answer to:

- validator 现在是不是还在用硬规则

### 3. 数据流怎么走

Build this as an interactive stepper:

1. 模型写代码
2. runtime 先做什么
3. sandbox 做什么
4. validator 做什么
5. graph / kernel 同步什么
6. 下一轮 prompt 注入什么

Each step should show:

- 输入
- 输出
- 下游谁消费

### 4. 涉及哪些模块

Only keep 5 modules:

- `tool_runtime.py`
- `context_manager.py`
- `service.py`
- `diagnostics.py`
- `feature_graph.py`

Each module card should answer:

- 它干什么
- 昨天改了什么
- 在整条链里处在哪一步

### 5. 最后有什么效果

Use two interactive case cards:

- `L2_172` for failure compression
- `L2_63` for validator recognition improvement

Optional support mention:

- `L1_122` as a short “this pattern is reusable” note

Each card should show:

- 改前
- 改后
- 为什么会变

### 6. 提升到哪里，没解决什么

This screen is the defense screen.

It must separate:

- 已经提升的地方
- 还没解决的地方
- 主管继续追问时可以怎么回答

## Interaction Rules

The page should feel interactive, but the interaction must serve explanation.

Required interaction elements:

- top navigation
- one main stepper for the data flow screen
- one tab switcher for the effect cases
- one short follow-up Q/A panel on every screen

Do not add:

- deep branching navigation
- large free-floating diagrams with no reading order
- dense dashboards

## Language Rules

The page must use plain Chinese.

Avoid:

- abstract architecture slogans
- long English-heavy labels
- mixed-language headings unless a code identifier is necessary
- rhetorical contrast patterns that make the prose sound artificial

Every screen should end with:

- one short conclusion band
- one “追问备答” block

## Markdown Companion

Rewrite `shareable-report.md` to follow the same 6-part order:

1. 昨天做了什么
2. 原理是什么
3. 数据流怎么走
4. 涉及哪些模块
5. 最后有什么效果
6. 提升到哪里，没解决什么

It should read like a speaking memo, not a web page export.

## Evidence to Preserve

Keep these concrete facts in the new page:

- `L2_172` changed from `8 rounds / 61853 tokens` to `2 rounds / 14383 tokens`
- `L2_63` changed from `VALIDATOR_MISMATCH / 8 rounds / 74017 tokens` to `PASS / 1 round / 5573 tokens`
- preflight lint returns `failure_kind / lint_hits / repair_recipe`
- spherical recess validator fallback uses sphere bbox and host-plane circle-edge centers
- validator core principle is still rule-driven and family-aware

## Verification

Before handoff:

- run `node --check report-20260409-validator-generalization/main.js`
- verify all 4 report files exist
- verify the page contains the 6 target questions
- verify the Markdown companion follows the same 6-part order

If browser rendering is not checked, say so explicitly.
