from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import sub_agent_runtime.tooling.execution as tool_runtime_module
from sub_agent_runtime.semantic_kernel import DomainKernelState, FamilyRepairPacket, FeatureInstance
from sub_agent_runtime.tooling import ToolRuntime
from sub_agent_runtime.turn_state import (
    RunState,
    ToolCategory,
    ToolResultRecord,
    TurnToolPolicy,
)

_EXPLICIT_COUNTERSINK_REQUIREMENT = (
    "Select the top reference plane, draw a 100.0x60.0 millimeter rectangle and extrude it "
    "by 8.0 millimeters. Select the plate surface, and use the sketch to draw four points "
    "with coordinates (25,15), (25,45), (75,15), and (75,45). Exit the sketch, and activate "
    'the Hole Wizard or the revolved cut tool. If using the Hole Wizard: select "Countersink," '
    "set the standard, head diameter 12.0 millimeters, cone angle 90 degrees, through-hole "
    "diameter 6.0 millimeters, and in the position tab, select the four points drawn earlier."
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
