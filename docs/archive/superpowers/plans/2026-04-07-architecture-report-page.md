# Architecture Report Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone static presentation page that explains the current architecture, key findings, and next plan for `aicad.subagent.iteration` without requiring a presenter.

**Architecture:** Use a self-contained static site under a new report directory. Keep the page framework-free and structure it as full-screen modules with shared styling, lightweight interactions, and embedded evidence excerpts taken from the current codebase and benchmark artifacts.

**Tech Stack:** HTML, CSS, vanilla JavaScript

---

### Task 1: Scaffold the standalone report shell

**Files:**
- Create: `report-20260407-architecture/index.html`
- Create: `report-20260407-architecture/styles.css`
- Create: `report-20260407-architecture/main.js`

- [ ] **Step 1: Create the HTML shell with eight modules and top navigation**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AiCAD 架构现状与后续计划汇报</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400;12..96,600;12..96,700;12..96,800&family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="styles.css">
  <script src="main.js" defer></script>
</head>
<body>
  <nav class="nav">
    <div class="nav-inner">
      <span class="nav-title">AiCAD 架构现状与后续计划</span>
      <div class="nav-dots">
        <button class="nav-dot" data-target="module-0"></button>
        <button class="nav-dot" data-target="module-1"></button>
        <button class="nav-dot" data-target="module-2"></button>
        <button class="nav-dot" data-target="module-3"></button>
        <button class="nav-dot" data-target="module-4"></button>
        <button class="nav-dot" data-target="module-5"></button>
        <button class="nav-dot" data-target="module-6"></button>
        <button class="nav-dot" data-target="module-7"></button>
      </div>
    </div>
  </nav>
  <main class="main">
    <section class="module" id="module-0"></section>
    <section class="module" id="module-1"></section>
    <section class="module" id="module-2"></section>
    <section class="module" id="module-3"></section>
    <section class="module" id="module-4"></section>
    <section class="module" id="module-5"></section>
    <section class="module" id="module-6"></section>
    <section class="module" id="module-7"></section>
  </main>
</body>
</html>
```

- [ ] **Step 2: Create the design-system CSS foundation**

```css
:root {
  --color-bg: #f6f1e8;
  --color-bg-panel: rgba(255, 255, 255, 0.82);
  --color-text: #1f2329;
  --color-muted: #5f696f;
  --color-border: rgba(40, 59, 68, 0.12);
  --color-accent: #1f6f78;
  --color-accent-soft: #dceff0;
  --color-secondary: #b56a3b;
  --font-display: 'Bricolage Grotesque', Georgia, serif;
  --font-body: 'DM Sans', sans-serif;
  --font-mono: 'JetBrains Mono', monospace;
}

body {
  margin: 0;
  font-family: var(--font-body);
  color: var(--color-text);
  background:
    radial-gradient(circle at top left, rgba(31, 111, 120, 0.12), transparent 30%),
    linear-gradient(180deg, #f8f4ec 0%, #f1ece2 100%);
}

.module {
  min-height: 100vh;
  padding: 7rem 1.5rem 4rem;
}
```

- [ ] **Step 3: Add the base interaction script**

```js
function scrollToModule(id) {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });
}

document.querySelectorAll(".nav-dot").forEach((dot) => {
  dot.addEventListener("click", () => scrollToModule(dot.dataset.target));
});
```

- [ ] **Step 4: Verify the directory and file skeleton exist**

Run: `find report-20260407-architecture -maxdepth 1 -type f | sort`
Expected: shows `index.html`, `styles.css`, and `main.js`

### Task 2: Implement the report content and visual modules

**Files:**
- Modify: `report-20260407-architecture/index.html`
- Modify: `report-20260407-architecture/styles.css`

- [ ] **Step 1: Fill `index.html` with the report narrative modules**

```html
<section class="module hero-module" id="module-0">
  <div class="hero-panel">
    <p class="eyebrow">Architecture Report / 2026-04-07</p>
    <h1>系统方向已经正确，下一步是让 V2 收口成唯一主路径。</h1>
    <p class="hero-summary">
      当前系统已经从 planner-centric 迁移到 runtime-centric，但结构化 action 仍在简单任务上制造额外轮次与 token 成本。
    </p>
  </div>
</section>
```

- [ ] **Step 2: Add screen-specific visual components**

```html
<div class="architecture-stack">
  <article class="stack-layer stable">上游稳定接口层</article>
  <article class="stack-layer transition">Runtime 入口层</article>
  <article class="stack-layer core">V2 运行时核心</article>
  <article class="stack-layer execution">MCP / Sandbox 执行层</article>
  <article class="stack-layer observability">Observability / Benchmark</article>
</div>

<div class="case-waterfall">
  <div class="waterfall-step">4 次 apply_cad_action</div>
  <div class="waterfall-step">1 次额外只读检查</div>
  <div class="waterfall-step">1 次 query_geometry + query_topology</div>
  <div class="waterfall-step">2 次 execute_cadquery</div>
</div>
```

- [ ] **Step 3: Embed curated code and artifact evidence blocks**

```html
<article class="evidence-card">
  <div class="evidence-header">
    <span>runner.py</span>
    <span>L92-L109</span>
  </div>
  <pre><code>if runtime_mode == "v2":
    return await IterativeAgentLoopV2(
        app_settings=self._settings,
        sandbox=self._sandbox,
        hook_manager=self._hook_manager,
    ).run(request=request, run_dir=run_dir)</code></pre>
</article>
```

- [ ] **Step 4: Style the modules for presentation readability**

```css
.hero-panel,
.content-panel,
.evidence-card,
.summary-card {
  border: 1px solid var(--color-border);
  border-radius: 24px;
  background: var(--color-bg-panel);
  backdrop-filter: blur(18px);
  box-shadow: 0 24px 60px rgba(31, 35, 41, 0.08);
}

.architecture-stack {
  display: grid;
  gap: 0.9rem;
}

.stack-layer {
  padding: 1rem 1.25rem;
  border-radius: 18px;
}
```

- [ ] **Step 5: Verify content coverage by checking module count and key terms**

Run: `rg -n "module-0|module-7|FeatureGraph|L1_159|execute_cadquery|apply_cad_action" report-20260407-architecture/index.html`
Expected: matches all key sections and report terms

### Task 3: Add interactions, responsive polish, and local verification

**Files:**
- Modify: `report-20260407-architecture/main.js`
- Modify: `report-20260407-architecture/styles.css`

- [ ] **Step 1: Add navigation, active-state, and keyboard controls**

```js
document.addEventListener("keydown", (event) => {
  const modules = [...document.querySelectorAll(".module")];
  const current = modules.findIndex((module) => {
    const rect = module.getBoundingClientRect();
    return rect.top <= 120 && rect.bottom >= 120;
  });
  if (event.key === "ArrowRight" || event.key === "ArrowDown") {
    modules[Math.min(current + 1, modules.length - 1)]?.scrollIntoView({ behavior: "smooth" });
  }
  if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
    modules[Math.max(current - 1, 0)]?.scrollIntoView({ behavior: "smooth" });
  }
});
```

- [ ] **Step 2: Add one step-through interaction and one comparison interaction**

```js
const flowSteps = [...document.querySelectorAll("[data-flow-step]")];
document.getElementById("flow-next")?.addEventListener("click", () => {
  const current = flowSteps.findIndex((step) => step.classList.contains("active"));
  flowSteps[current]?.classList.remove("active");
  flowSteps[(current + 1) % flowSteps.length]?.classList.add("active");
});
```

- [ ] **Step 3: Add responsive breakpoints for mobile and compact screens**

```css
@media (max-width: 900px) {
  .two-column,
  .architecture-overview,
  .case-study-grid,
  .code-evidence-grid {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 4: Run local structural verification**

Run: `node --check report-20260407-architecture/main.js`
Expected: no output, exit code 0

- [ ] **Step 5: Run final file checks**

Run: `wc -l report-20260407-architecture/index.html report-20260407-architecture/styles.css report-20260407-architecture/main.js`
Expected: all three files exist and have non-trivial content
