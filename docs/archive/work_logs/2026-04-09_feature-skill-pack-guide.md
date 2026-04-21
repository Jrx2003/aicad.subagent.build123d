# aicad.subagent.iteration Feature Guide: skill_pack

Generated at: 2026-04-07 11:48:13Z

## 1. Scope
- Root: `/Users/jerryx/code/aicad.subagent.iteration`
- Feature keyword: `skill_pack`
- Total indexed files: 149
- Total text hits: 20

## 2. Ranked Related Files
- `tests/unit/sub_agent_runtime/test_v2_runtime.py` (12 hits)
- `docs/work_logs/2026-04-05.md` (3 hits)
- `src/sub_agent_runtime/context_manager.py` (2 hits)
- `docs/work_logs/2026-04-07.md` (1 hits)
- `src/sub_agent_runtime/agent_loop_v2.py` (1 hits)
- `src/sub_agent_runtime/skill_pack.py` (1 hits)

## 3. Key Match Evidence
### `tests/unit/sub_agent_runtime/test_v2_runtime.py`

```text
L34: from sub_agent_runtime.skill_pack import (
L35: build_runtime_skill_pack,
L4252: def test_build_runtime_skill_pack_uses_code_first_annular_band_strategy() -> None:
L4253: skills = build_runtime_skill_pack(
L4280: def test_build_runtime_skill_pack_keeps_positive_extrude_guidance_for_local_centered_hole() -> None:
L4281: skills = build_runtime_skill_pack(
L4297: def test_build_runtime_skill_pack_axisymmetric_guidance_rejects_centered_false_cylinders() -> None:
L4298: skills = build_runtime_skill_pack(
```
- Candidate symbols: `_FakeSandbox, _FailingExecuteSandbox, _TrackingValidationSandbox, __init__, test_tool_runtime_model_schema_hides_runtime_managed_fields, test_tool_runtime_can_filter_exposed_tools, test_initialize_feature_graph_detects_requirement_families, test_initialize_feature_graph_does_not_treat_stud_arrays_as_axisymmetric_family`

### `docs/work_logs/2026-04-05.md`

```text
L699: 它们现在由 `src/sub_agent_runtime/skill_pack.py` 生成，并通过 `context_manager` 作为 user-context attachment 进入 V2 消息栈。
L722: 2. `test_runs/20260405_skill_pack_probe`
L2195: 3. `src/sub_agent_runtime/skill_pack.py`
```

### `src/sub_agent_runtime/context_manager.py`

```text
L14: from sub_agent_runtime.skill_pack import build_runtime_skill_pack
L184: runtime_skills = build_runtime_skill_pack(
```
- Candidate symbols: `PromptBuildResult, V2ContextManager, __init__, build_prompt_payload, build_messages, build_prompt_bundle, _build_prompt_payload, _build_message_stack`

### `docs/work_logs/2026-04-07.md`

```text
L573: 1. `L2_63` 暴露的是一个更底层的问题：V2 的 `feature_graph`、`skill_pack`、`query_feature_probes` 还没有完全共享同一个 requirement-semantics 来源。
```

### `src/sub_agent_runtime/agent_loop_v2.py`

```text
L25: from sub_agent_runtime.skill_pack import (
```
- Candidate symbols: `IterativeAgentLoopV2, __init__, _persist_tool_result, _sync_feature_graph_from_runtime_payload, _write_json, _append_trace, _append_conversation, _append_tool_timeline`

### `src/sub_agent_runtime/skill_pack.py`

```text
L100: def build_runtime_skill_pack(
```
- Candidate symbols: `requirement_prefers_code_first_family, recommended_feature_probe_families, build_runtime_skill_pack, _skill_priority, _requirements_text, _detect_positive_extrude_plane, _requirement_mentions_regular_polygon_side_length, _requirement_prefers_named_face_local_feature_sequence`

## 4. Feature Architecture (Fill Manually)
- Explain the role of this feature in user/business terms.
- Explain entry points, control flow, and branching.
- Explain dependencies and boundaries.

## 5. Contracts and Edge Cases (Fill Manually)
- Inputs/outputs and validation rules.
- Failure modes and how errors propagate.
- Invariants that must remain true after edits.

## 6. Change Guide (Fill Manually)
- Safe extension points.
- High-risk files and why.
- Regression tests/probes to run.

## 7. Example Walkthrough (Fill Manually)
- Walk one request/event through the feature from input to output.

