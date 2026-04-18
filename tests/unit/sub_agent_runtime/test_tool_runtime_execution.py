from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import sub_agent_runtime.tool_runtime as tool_runtime_module
from sub_agent_runtime.tool_runtime import ToolRuntime
from sub_agent_runtime.turn_state import (
    RunState,
    ToolCategory,
    ToolResultRecord,
    TurnToolPolicy,
)


@pytest.mark.asyncio
async def test_execute_tool_calls_returns_structured_failure_when_validate_requirement_is_cancelled() -> None:
    class FakeSandbox:
        async def validate_requirement(
            self,
            *,
            session_id: str,
            requirements: dict[str, object],
            requirement_text: str,
            step: int | None = None,
            timeout: int,
        ):
            raise asyncio.CancelledError()

    tool_runtime = ToolRuntime(sandbox=FakeSandbox())

    batch = await tool_runtime.execute_tool_calls(
        tool_calls=[
            SimpleNamespace(
                name="validate_requirement",
                arguments={},
                id="validate_requirement:0",
            )
        ],
        session_id="session-cancelled",
        requirements={"description": "noop"},
        requirement_text="noop",
        sandbox_timeout=30,
        round_no=1,
        run_state=None,
    )

    assert len(batch.tool_results) == 1
    result = batch.tool_results[0]
    assert result.name == "validate_requirement"
    assert result.success is False
    assert "CancelledError" in (result.error or "")


@pytest.mark.asyncio
async def test_execute_tool_calls_clears_task_cancellation_state_after_validate_requirement_cancel() -> None:
    class FakeSandbox:
        async def validate_requirement(
            self,
            *,
            session_id: str,
            requirements: dict[str, object],
            requirement_text: str,
            step: int | None = None,
            timeout: int,
        ):
            task = asyncio.current_task()
            assert task is not None
            task.cancel()
            await asyncio.sleep(0)
            return None

    tool_runtime = ToolRuntime(sandbox=FakeSandbox())

    batch = await tool_runtime.execute_tool_calls(
        tool_calls=[
            SimpleNamespace(
                name="validate_requirement",
                arguments={},
                id="validate_requirement:0",
            )
        ],
        session_id="session-cancelled-state",
        requirements={"description": "noop"},
        requirement_text="noop",
        sandbox_timeout=30,
        round_no=1,
        run_state=None,
    )

    assert len(batch.tool_results) == 1
    result = batch.tool_results[0]
    assert result.name == "validate_requirement"
    assert result.success is False
    assert "CancelledError" in (result.error or "")


@pytest.mark.asyncio
async def test_execute_tool_calls_returns_structured_failure_when_gather_sees_cancelled_tool_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_runtime = ToolRuntime(sandbox=SimpleNamespace())

    async def fake_execute_single_guarded(**_: object):
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        tool_runtime,
        "_execute_single_guarded",
        fake_execute_single_guarded,
    )

    batch = await tool_runtime.execute_tool_calls(
        tool_calls=[
            SimpleNamespace(
                name="validate_requirement",
                category="read",
                arguments={},
                id="validate_requirement:0",
            )
        ],
        session_id="session-gather-cancelled",
        requirements={"description": "noop"},
        requirement_text="noop",
        sandbox_timeout=30,
        round_no=1,
        run_state=None,
    )

    assert len(batch.tool_results) == 1
    result = batch.tool_results[0]
    assert result.name == "validate_requirement"
    assert result.success is False
    assert "CancelledError" in (result.error or "")


@pytest.mark.asyncio
async def test_execute_tool_calls_allows_following_await_after_cancelled_tool_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_runtime = ToolRuntime(sandbox=SimpleNamespace())

    async def fake_execute_single_guarded(**_: object):
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        tool_runtime,
        "_execute_single_guarded",
        fake_execute_single_guarded,
    )

    batch = await tool_runtime.execute_tool_calls(
        tool_calls=[
            SimpleNamespace(
                name="validate_requirement",
                category="judge",
                arguments={},
                id="validate_requirement:0",
            )
        ],
        session_id="session-following-await",
        requirements={"description": "noop"},
        requirement_text="noop",
        sandbox_timeout=30,
        round_no=1,
        run_state=None,
    )

    await asyncio.sleep(0)

    assert len(batch.tool_results) == 1
    assert batch.tool_results[0].success is False


@pytest.mark.asyncio
async def test_single_validate_requirement_tool_call_bypasses_gather_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSandbox:
        async def validate_requirement(
            self,
            *,
            session_id: str,
            requirements: dict[str, object],
            requirement_text: str,
            step: int | None = None,
            timeout: int,
        ):
            return SimpleNamespace(
                success=True,
                error_code="none",
                error_message=None,
                session_id=session_id,
                step=step,
                is_complete=False,
                blockers=[],
                checks=[],
                core_checks=[],
                diagnostic_checks=[],
                clause_interpretations=[],
                coverage_confidence=0.0,
                insufficient_evidence=True,
                observation_tags=[],
                decision_hints=[],
                blocker_taxonomy=[],
                relation_index=None,
                summary="Requirement validation has insufficient evidence",
            )

    async def fail_if_gather_called(*_: object, **__: object):
        raise AssertionError("_gather_results should not run for a single validate_requirement tool")

    monkeypatch.setattr(tool_runtime_module, "_gather_results", fail_if_gather_called)

    tool_runtime = ToolRuntime(sandbox=FakeSandbox())

    batch = await tool_runtime.execute_tool_calls(
        tool_calls=[
            SimpleNamespace(
                name="validate_requirement",
                category="judge",
                arguments={},
                id="validate_requirement:0",
            )
        ],
        session_id="session-direct-judge",
        requirements={"description": "noop"},
        requirement_text="noop",
        sandbox_timeout=30,
        round_no=1,
        run_state=None,
    )

    assert len(batch.tool_results) == 1
    assert batch.tool_results[0].name == "validate_requirement"
    assert batch.tool_results[0].success is True


@pytest.mark.asyncio
async def test_query_feature_probes_injects_preferred_probe_families_from_turn_policy() -> None:
    captured: dict[str, object] = {}

    class FakeSandbox:
        async def query_feature_probes(
            self,
            *,
            session_id: str,
            requirements: dict[str, object],
            requirement_text: str,
            step: int | None = None,
            families: list[str],
            timeout: int,
        ):
            captured["session_id"] = session_id
            captured["families"] = list(families)
            return {
                "success": True,
                "error_code": "none",
                "error_message": None,
                "session_id": session_id,
                "step": step,
                "detected_families": list(families),
                "probes": [],
                "summary": "ok",
            }

    run_state = RunState(
        session_id="session-probe-family-injection",
        requirements={"description": "noop"},
    )
    run_state.add_turn_tool_policy(
        TurnToolPolicy(
            round_no=4,
            policy_id="semantic_refresh_before_under_grounded_kernel_patch_for_local_feature_gap",
            mode="graph_refresh",
            reason="Need all policy-preferred families when probing.",
            allowed_tool_names=["query_feature_probes"],
            blocked_tool_names=[],
            preferred_tool_names=["query_feature_probes"],
            preferred_probe_families=[
                "explicit_anchor_hole",
                "core_geometry",
                "named_face_local_edit",
            ],
        )
    )

    tool_runtime = ToolRuntime(sandbox=FakeSandbox())

    batch = await tool_runtime.execute_tool_calls(
        tool_calls=[
            SimpleNamespace(
                name="query_feature_probes",
                arguments={"families": ["explicit_anchor_hole", "core_geometry"]},
                id="query_feature_probes:0",
            )
        ],
        session_id="session-probe-family-injection",
        requirements={"description": "noop"},
        requirement_text="noop",
        sandbox_timeout=30,
        round_no=4,
        run_state=run_state,
    )

    assert len(batch.tool_results) == 1
    assert batch.tool_results[0].success is True
    assert captured["families"] == [
        "explicit_anchor_hole",
        "core_geometry",
        "named_face_local_edit",
    ]


@pytest.mark.asyncio
async def test_apply_cad_action_fillet_requires_edge_refs_once_topology_candidates_exist() -> None:
    class FakeSandbox:
        async def apply_cad_action(self, **_: object):
            raise AssertionError("apply_cad_action should be blocked by preflight gate")

    run_state = RunState(
        session_id="session-local-fillet",
        requirements={"description": "fillet the opening rim"},
    )
    run_state.evidence.update(
        tool_name="query_topology",
        round_no=3,
        payload={
            "candidate_sets": [
                {
                    "label": "Opening Rim Edges",
                    "entity_type": "edge",
                    "ref_ids": ["edge:1:E_opening_a", "edge:1:E_opening_b"],
                }
            ]
        },
    )
    tool_runtime = ToolRuntime(sandbox=FakeSandbox())

    batch = await tool_runtime.execute_tool_calls(
        tool_calls=[
            SimpleNamespace(
                name="apply_cad_action",
                arguments={
                    "action_type": "fillet",
                    "action_params": {"radius": 1.5},
                },
                id="apply_cad_action:0",
            )
        ],
        session_id="session-local-fillet",
        requirements={"description": "fillet the opening rim"},
        requirement_text="fillet the opening rim",
        sandbox_timeout=30,
        round_no=4,
        run_state=run_state,
    )

    assert len(batch.tool_results) == 1
    result = batch.tool_results[0]
    assert result.success is False
    assert result.payload["failure_kind"] == "apply_cad_action_contract_failure"
    assert "missing edge_refs" in (result.error or "")
    assert result.payload["candidate_edge_set_labels"] == ["Opening Rim Edges"]


@pytest.mark.asyncio
async def test_query_topology_injects_preferred_probe_families() -> None:
    captured: dict[str, object] = {}

    class FakeSandbox:
        async def query_topology(self, **kwargs: object):
            captured.update(kwargs)
            return {
                "success": True,
                "candidate_sets": [],
                "matched_ref_ids": [],
                "matched_entity_ids": [],
                "applied_hints": kwargs.get("selection_hints", []),
            }

    run_state = RunState(
        session_id="session-topology-family-injection",
        requirements={"description": "query topology around a mounting face"},
    )
    run_state.turn_tool_policies.append(
        SimpleNamespace(
            round_no=4,
            policy_id="local_finish_after_feature_probe_refresh",
            mode="graph_refresh",
            reason="noop",
            allowed_tool_names=["query_topology"],
            blocked_tool_names=[],
            preferred_tool_names=["query_topology"],
            preferred_probe_families=[
                "explicit_anchor_hole",
                "named_face_local_edit",
            ],
        )
    )

    tool_runtime = ToolRuntime(sandbox=FakeSandbox())

    batch = await tool_runtime.execute_tool_calls(
        tool_calls=[
            SimpleNamespace(
                name="query_topology",
                arguments={
                    "selection_hints": ["bottom", "planar"],
                    "include_edges": False,
                },
                id="query_topology:0",
            )
        ],
        session_id="session-topology-family-injection",
        requirements={"description": "noop"},
        requirement_text="noop",
        sandbox_timeout=30,
        round_no=4,
        run_state=run_state,
    )

    assert len(batch.tool_results) == 1
    assert batch.tool_results[0].success is True
    assert captured["family_ids"] == [
        "explicit_anchor_hole",
        "named_face_local_edit",
    ]
    assert captured["include_edges"] is True


@pytest.mark.asyncio
async def test_execute_tool_calls_truncates_multi_apply_cad_action_batch_to_first_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_runtime = ToolRuntime(sandbox=SimpleNamespace())
    captured: dict[str, object] = {}

    async def fake_execute_single(**kwargs: object) -> ToolResultRecord:
        tool_call = kwargs["tool_call"]
        captured["tool_name"] = tool_call.name
        captured["action_type"] = tool_call.arguments["action_type"]
        captured["call_id"] = tool_call.call_id
        return ToolResultRecord(
            name="apply_cad_action",
            category=ToolCategory.WRITE,
            success=True,
            payload={"summary": "created sketch"},
        )

    monkeypatch.setattr(tool_runtime, "_execute_single", fake_execute_single)

    batch = await tool_runtime.execute_tool_calls(
        tool_calls=[
            SimpleNamespace(
                name="apply_cad_action",
                arguments={
                    "action_type": "create_sketch",
                    "action_params": {"face_ref": "front_faces", "name": "front_recess_sketch"},
                },
                id="apply_cad_action:0",
            ),
            SimpleNamespace(
                name="apply_cad_action",
                arguments={
                    "action_type": "add_polygon",
                    "action_params": {
                        "sketch_name": "front_recess_sketch",
                        "shape": "rounded_rectangle",
                        "width": 12,
                        "height": 6,
                    },
                },
                id="apply_cad_action:1",
            ),
            SimpleNamespace(
                name="apply_cad_action",
                arguments={
                    "action_type": "cut_extrude",
                    "action_params": {"profile": "recess_profile", "depth": 2},
                },
                id="apply_cad_action:2",
            ),
        ],
        session_id="session-front-recess",
        requirements={"description": "front recess local edit"},
        requirement_text="front recess local edit",
        sandbox_timeout=30,
        round_no=4,
        run_state=None,
    )

    assert batch.error is None
    assert [call.call_id for call in batch.tool_calls] == ["apply_cad_action:0"]
    assert len(batch.tool_results) == 1
    assert captured == {
        "tool_name": "apply_cad_action",
        "action_type": "create_sketch",
        "call_id": "apply_cad_action:0",
    }
    normalized_events = [
        event for event in batch.execution_events if event.phase == "normalized"
    ]
    assert len(normalized_events) == 1
    assert normalized_events[0].detail["reason"] == "truncated_multi_apply_cad_action_batch"
    assert normalized_events[0].detail["dropped_call_ids"] == [
        "apply_cad_action:1",
        "apply_cad_action:2",
    ]


@pytest.mark.asyncio
async def test_execute_tool_calls_still_rejects_mixed_write_tool_batch() -> None:
    tool_runtime = ToolRuntime(sandbox=SimpleNamespace())

    batch = await tool_runtime.execute_tool_calls(
        tool_calls=[
            SimpleNamespace(
                name="execute_build123d",
                arguments={"code": "result = None"},
                id="execute_build123d:0",
            ),
            SimpleNamespace(
                name="apply_cad_action",
                arguments={
                    "action_type": "fillet",
                    "action_params": {"edge_refs": ["edge:1:E0"], "radius": 1.0},
                },
                id="apply_cad_action:1",
            ),
        ],
        session_id="session-mixed-write-batch",
        requirements={"description": "noop"},
        requirement_text="noop",
        sandbox_timeout=30,
        round_no=2,
        run_state=None,
    )

    assert batch.error == "at_most_one_write_tool_per_turn"
    assert batch.tool_results == []
