# Domain Kernel Generalization Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move runtime routing onto a single generic validation-assessment / repair-intent surface and remove the first family/requirement-derived routing helpers immediately after replacement.

**Architecture:** Phase 1 keeps the external tool and artifact contracts stable while changing the internal control surface. `DomainKernelState` becomes the runtime truth source for the latest validation assessment, and `agent_loop_v2` plus `skill_pack` read that surface directly instead of recomputing route choices from requirement text and family heuristics.

**Tech Stack:** Python, pytest, existing V2 runtime, DomainKernelState, validate_requirement payloads.

---

### Task 1: Formalize Validation Assessment In The Kernel

**Files:**
- Modify: `src/sub_agent_runtime/feature_graph.py`
- Test: `tests/unit/sub_agent_runtime/test_v2_runtime.py`

- [ ] **Step 1: Write the failing test**

Add a test that syncs a validation payload into the kernel and expects `domain_kernel_digest` to expose a generic `latest_validation_assessment` summary with coverage, insufficient-evidence, observation tags, decision hints, and contradicted/insufficient clause ids.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src uv run --extra dev pytest -q tests/unit/sub_agent_runtime/test_v2_runtime.py -k validation_assessment`
Expected: FAIL because the digest does not yet expose the assessment surface.

- [ ] **Step 3: Write minimal implementation**

Add a `ValidationAssessment` dataclass, store it on `DomainKernelState`, populate it from validation payloads during sync, and surface it through `digest()` and `to_query_payload()`.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src uv run --extra dev pytest -q tests/unit/sub_agent_runtime/test_v2_runtime.py -k validation_assessment`
Expected: PASS

### Task 2: Replace Requirement/Family Probe Routing In Runtime Policy

**Files:**
- Modify: `src/sub_agent_runtime/agent_loop_v2.py`
- Test: `tests/unit/sub_agent_runtime/test_v2_runtime.py`

- [ ] **Step 1: Write the failing test**

Add a test proving turn policy derives preferred probe families from the kernel state or latest validation assessment rather than requirement-text helper functions.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src uv run --extra dev pytest -q tests/unit/sub_agent_runtime/test_v2_runtime.py -k preferred_probe`
Expected: FAIL because runtime still calls the removed helper path.

- [ ] **Step 3: Write minimal implementation**

Introduce a single helper in `agent_loop_v2.py` that reads the active patch / repair packet / latest binding / latest validation assessment from `DomainKernelState` and uses that surface everywhere `preferred_probe_families` is needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src uv run --extra dev pytest -q tests/unit/sub_agent_runtime/test_v2_runtime.py -k preferred_probe`
Expected: PASS

### Task 3: Remove The Replaced Skill-Pack Routing Helpers

**Files:**
- Modify: `src/sub_agent_runtime/skill_pack.py`
- Modify: `src/sub_agent_runtime/context_manager.py`
- Test: `tests/unit/sub_agent_runtime/test_skill_pack_l2_guidance.py`

- [ ] **Step 1: Write the failing test**

Add a test proving `build_runtime_skill_pack()` can prioritize evidence-gap guidance using `domain_kernel_digest.latest_validation_assessment` even when taxonomy is sparse.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src uv run --extra dev pytest -q tests/unit/sub_agent_runtime/test_skill_pack_l2_guidance.py -k kernel_assessment`
Expected: FAIL because the skill pack cannot yet read the kernel assessment surface.

- [ ] **Step 3: Write minimal implementation**

Extend `build_runtime_skill_pack()` to accept `domain_kernel_digest`, remove `requirement_prefers_code_first_family` and `recommended_feature_probe_families`, and rebase insufficient-evidence guidance on the kernel assessment surface.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src uv run --extra dev pytest -q tests/unit/sub_agent_runtime/test_skill_pack_l2_guidance.py -k kernel_assessment`
Expected: PASS

### Task 4: Update Canonical Docs And Work Tracking

**Files:**
- Modify: `docs/cad_iteration/SYSTEM_RECORD.json`
- Modify: `docs/cad_iteration/DESIGN_INTENT.md`
- Modify: `docs/cad_iteration/FEATURE_GRAPH_RUNTIME.md`
- Modify: `docs/cad_iteration/ITERATION_PROTOCOL.md`
- Modify: `docs/cad_iteration/TOOL_SURFACE.md`
- Modify: `docs/cad_iteration/UPGRADE_ROADMAP.md`
- Modify: `CODEX.md`
- Modify: `docs/work_logs/2026-04-09_validation-generalization-refactor.md`
- Modify: `docs/work_logs/2026-04-09_validation-generalization-checklist.json`

- [ ] **Step 1: Update canonical docs before code-complete claims**

Document that `ValidationAssessment` is now canonical prompt-facing semantic state alongside patches and repair packets, and record Phase 1 deletions.

- [ ] **Step 2: Update work log and checklist**

Record the spec, matrix, phase boundary, and deleted helpers.

### Task 5: Verification And Real Probe

**Files:**
- Test: `tests/unit/sub_agent_runtime/test_v2_runtime.py`
- Test: `tests/unit/sub_agent_runtime/test_skill_pack_l2_guidance.py`

- [ ] **Step 1: Run focused tests**

Run: `PYTHONPATH=src uv run --extra dev pytest -q tests/unit/sub_agent_runtime/test_v2_runtime.py tests/unit/sub_agent_runtime/test_skill_pack_l2_guidance.py`
Expected: PASS

- [ ] **Step 2: Run one real probe**

Run: `PYTHONPATH=src uv run python scripts/run_aci_live_probe.py ...`
Expected: a new `test_runs/YYYYMMDD_HHMMSS` directory with updated validation assessment fields in query and trace artifacts.

- [ ] **Step 3: Record evidence**

Update the work log and checklist with the exact run directory, exact test command, and whether the deleted helpers are absent from runtime call sites.
