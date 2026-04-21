# 2026-04-09 Domain Kernel Generalization Migration / Deletion Matrix

## Purpose

定义每个 phase 的：

1. canonical surface
2. temporary compatibility surface
3. implementation target
4. mandatory deletion target

任何 phase 完成后，表中标记为 `delete_now` 的逻辑必须立即删除，不得长期并存。

## Phase 1

### Canonical

1. `DomainKernelState.latest_validation_assessment`
2. `DomainKernelPatch`
3. runtime policy helpers that read assessment / repair intent directly
4. `build_runtime_skill_pack(..., domain_kernel_digest=...)`

### Compatibility

1. `blocker_taxonomy`
2. `FamilyRepairPacket`
3. family-specific skill guidance text

### Implement

1. [`feature_graph.py`](/Users/jerryx/code/aicad.subagent.iteration/.worktrees/validation-generalization-phase1/src/sub_agent_runtime/feature_graph.py)
   - add generic validation assessment object and digest fields
2. [`agent_loop_v2.py`](/Users/jerryx/code/aicad.subagent.iteration/.worktrees/validation-generalization-phase1/src/sub_agent_runtime/agent_loop_v2.py)
   - route through kernel assessment, not requirement/family heuristics
3. [`context_manager.py`](/Users/jerryx/code/aicad.subagent.iteration/.worktrees/validation-generalization-phase1/src/sub_agent_runtime/context_manager.py)
   - pass kernel digest to runtime skill pack
4. [`skill_pack.py`](/Users/jerryx/code/aicad.subagent.iteration/.worktrees/validation-generalization-phase1/src/sub_agent_runtime/skill_pack.py)
   - accept assessment-driven guidance

### delete_now

1. `requirement_prefers_code_first_family`
2. `recommended_feature_probe_families`
3. `agent_loop_v2` imports and call sites that recompute probe families from requirement text

## Phase 2

### Canonical

1. `HostFrame`
2. `FeatureObservation`
3. `ClauseAssessment` backed by host-frame/local-feature evidence

### Compatibility

1. family-specific clause adapters
2. existing repair packets for explicit hole / spherical recess

### Implement

1. [`validation_evidence.py`](/Users/jerryx/code/aicad.subagent.iteration/.worktrees/validation-generalization-phase1/src/sandbox_mcp_server/validation_evidence.py)
2. [`validation_interpretation.py`](/Users/jerryx/code/aicad.subagent.iteration/.worktrees/validation-generalization-phase1/src/sandbox_mcp_server/validation_interpretation.py)
3. [`service.py`](/Users/jerryx/code/aicad.subagent.iteration/.worktrees/validation-generalization-phase1/src/sandbox_mcp_server/service.py)
4. [`feature_graph.py`](/Users/jerryx/code/aicad.subagent.iteration/.worktrees/validation-generalization-phase1/src/sub_agent_runtime/feature_graph.py)

### delete_now

1. local-feature family checks that are fully replaced by host-frame evidence
2. 2D center-set fallback as the main path for local feature validation

## Phase 3

### Canonical

1. V2 runtime loop
2. `DomainKernelState`
3. evidence-first validation

### Compatibility

1. none beyond stable external contracts

### Implement

1. [`active_surface.py`](/Users/jerryx/code/aicad.subagent.iteration/.worktrees/validation-generalization-phase1/src/sub_agent_runtime/active_surface.py)
2. [`relation_feedback.py`](/Users/jerryx/code/aicad.subagent.iteration/.worktrees/validation-generalization-phase1/src/sub_agent_runtime/relation_feedback.py)
3. graph alias helpers in [`feature_graph.py`](/Users/jerryx/code/aicad.subagent.iteration/.worktrees/validation-generalization-phase1/src/sub_agent_runtime/feature_graph.py)
4. old planner-oriented docs and tests

### delete_now

1. `active_surface.py`
2. `relation_feedback.py`
3. `FeatureGraphState` alias and compatibility sync wrappers if no live caller remains
4. planner-era runtime tests that no longer exercise canonical behavior
