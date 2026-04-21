# V2 / Domain Kernel Tightening Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone static report plus shareable Markdown companion that summarize the V2 / domain-kernel tightening work from 2026-04-08 through the afternoon of 2026-04-09.

**Architecture:** Reuse the existing report-page design language from `report-20260407-architecture/`, but change the story shape from “full architecture review” to “change consolidation report”. The deliverable should combine high-level management conclusions, representative benchmark cases, artifact-chain explanation, and code evidence in a self-guided page plus a linear Markdown handoff.

**Tech Stack:** Plain HTML, CSS, JavaScript, Markdown, repo-local benchmark artifacts, repo-local docs.

---

### Task 1: Scaffold the report package and define file responsibilities

**Files:**
- Create: `report-20260409-v2-kernel-tightening/index.html`
- Create: `report-20260409-v2-kernel-tightening/styles.css`
- Create: `report-20260409-v2-kernel-tightening/main.js`
- Create: `report-20260409-v2-kernel-tightening/shareable-report.md`

- [ ] **Step 1: Create the report directory and file set**

Create the directory and four deliverable files:

```text
report-20260409-v2-kernel-tightening/
  index.html
  styles.css
  main.js
  shareable-report.md
```

- [ ] **Step 2: Assign one clear responsibility to each file**

Use this boundary:

```text
index.html            -> report structure, section content, code and evidence snippets
styles.css            -> editorial visual system, responsive layouts, emphasis states
main.js               -> section navigation, active-dot sync, lightweight steppers/tabs
shareable-report.md   -> linear forwarding artifact for chat, mail, or docs
```

### Task 2: Gather source evidence for the report modules

**Files:**
- Read: `docs/work_logs/2026-04-08.md`
- Read: `docs/cad_iteration/SYSTEM_RECORD.json`
- Read: `docs/cad_iteration/INDEX.md`
- Read: `benchmark/runs/20260408_180500/L1_122/summary.json`
- Read: `benchmark/runs/20260408_204800/L2_149/summary.json`
- Read: `benchmark/runs/20260409_002000_sampled_l2_after_binding_tighten/L2_172/benchmark_analysis.md`
- Read: `src/sub_agent_runtime/context_manager.py`
- Read: `src/sub_agent_runtime/feature_graph.py`
- Read: `src/sub_agent_runtime/agent_loop_v2.py`
- Read: `src/sandbox_mcp_server/service.py`

- [ ] **Step 1: Extract the four change tracks from the work log**

Collect compact evidence for:

```text
benchmark / v2-only tightening
canonical tool surface tightening
domain-kernel / FeatureGraph binding tightening
validator, probe, and runtime-normalized diagnostics tightening
```

- [ ] **Step 2: Extract one positive family-closure case and one positive repair-lane case**

Use:

```text
L1_122 -> full-span channel family closure
L2_149 -> path-sweep repair lane closure
```

Record for each:

```text
run id
planner rounds
token count
what changed in the path
which artifact files prove it
```

- [ ] **Step 3: Extract one remaining risk case**

Use:

```text
L2_172 -> repeated failures, coordinate-frame drift, and API hallucination
```

Record:

```text
why the loop still drifted
why this is not just “model cannot do it”
what architectural bottleneck it reveals
```

### Task 3: Implement the static page

**Files:**
- Modify: `report-20260409-v2-kernel-tightening/index.html`
- Modify: `report-20260409-v2-kernel-tightening/styles.css`
- Modify: `report-20260409-v2-kernel-tightening/main.js`

- [ ] **Step 1: Build the HTML section skeleton**

Create sections in this order:

```text
Executive Summary
What Changed
Architecture Delta
Evidence Cases
Artifact Chain
Current Judgment
Next Plan
Key Code And Evidence
```

The navigation should expose one dot per section.

- [ ] **Step 2: Implement the visual system**

Use a warm editorial base with:

```text
blue-green accent for tightened paths
amber / rust accent for remaining risk
glass-like panels and restrained motion
mobile-safe responsive stacking
```

- [ ] **Step 3: Add lightweight interaction**

Implement:

```text
active section navigation
progress indicator
one or more steppers or tabs for case comparison / artifact chain
keyboard and click navigation
```

### Task 4: Write the Markdown companion

**Files:**
- Modify: `report-20260409-v2-kernel-tightening/shareable-report.md`

- [ ] **Step 1: Rewrite the report into a linear forwarding format**

Use these headings:

```markdown
## Executive Summary
## What Changed
## Architecture Delta
## Evidence Cases
## Artifact Chain
## Current Judgment
## Next Plan
## Key Evidence
```

- [ ] **Step 2: Preserve the same case logic without UI language**

Convert page interactions into chronological text:

```text
L1_122 -> what it proves
L2_149 -> what it proves
L2_172 -> what risk remains
```

### Task 5: Verify the deliverable package

**Files:**
- Verify: `report-20260409-v2-kernel-tightening/index.html`
- Verify: `report-20260409-v2-kernel-tightening/styles.css`
- Verify: `report-20260409-v2-kernel-tightening/main.js`
- Verify: `report-20260409-v2-kernel-tightening/shareable-report.md`

- [ ] **Step 1: Verify file structure**

Run:

```bash
ls report-20260409-v2-kernel-tightening
```

Expected:

```text
index.html
main.js
shareable-report.md
styles.css
```

- [ ] **Step 2: Verify JavaScript syntax**

Run:

```bash
node --check report-20260409-v2-kernel-tightening/main.js
```

Expected:

```text
no output and exit code 0
```

- [ ] **Step 3: Verify report content markers**

Run:

```bash
rg -n "Executive Summary|What Changed|Architecture Delta|Evidence Cases|Artifact Chain|Current Judgment|Next Plan|Key Code And Evidence" report-20260409-v2-kernel-tightening/index.html
```

Expected:

```text
matches for all major sections
```

- [ ] **Step 4: Verify Markdown companion headings**

Run:

```bash
rg -n "^## (Executive Summary|What Changed|Architecture Delta|Evidence Cases|Artifact Chain|Current Judgment|Next Plan|Key Evidence)$" report-20260409-v2-kernel-tightening/shareable-report.md
```

Expected:

```text
matches for all required Markdown sections
```
