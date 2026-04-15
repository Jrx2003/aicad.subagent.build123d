from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import sub_agent_runtime.tool_runtime as tool_runtime_module
from sub_agent_runtime.tool_runtime import ToolRuntime


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
