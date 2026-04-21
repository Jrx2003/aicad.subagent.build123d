from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from sandbox.mcp_runner import McpSandboxRunner, _resolve_mcp_stdio_command
from sub_agent_runtime.contracts import (
    IterationRequest,
    IterationRunResult,
    IterationRunSummary,
)
from sub_agent_runtime.runner import IterativeSubAgentRunner


def test_resolve_mcp_stdio_command_falls_back_to_python_when_uv_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sandbox.mcp_runner.shutil.which", lambda _name: None)

    command, args = _resolve_mcp_stdio_command(
        "uv",
        ["run", "python", "-m", "sandbox_mcp_server"],
    )

    assert command == sys.executable
    assert args == ["-m", "sandbox_mcp_server"]


def test_resolve_mcp_stdio_command_preserves_available_non_uv_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sandbox.mcp_runner.shutil.which", lambda _name: "/usr/bin/fake-mcp")

    command, args = _resolve_mcp_stdio_command(
        "fake-mcp",
        ["--stdio"],
    )

    assert command == "fake-mcp"
    assert args == ["--stdio"]


@pytest.mark.asyncio
async def test_mcp_runner_reuses_single_initialized_session_until_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []

    @asynccontextmanager
    async def fake_stdio_client(_params, errlog=None):
        events.append(("stdio_enter", errlog))
        try:
            yield "read-stream", "write-stream"
        finally:
            events.append(("stdio_exit", None))

    class FakeClientSession:
        def __init__(self, read_stream, write_stream) -> None:
            events.append(("session_init", (read_stream, write_stream)))

        async def __aenter__(self) -> FakeClientSession:
            events.append(("session_enter", None))
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            events.append(("session_exit", exc_type.__name__ if exc_type else None))

        async def initialize(self):
            events.append(("initialize", None))

        async def call_tool(self, tool_name: str, arguments: dict[str, object]):
            events.append(("call_tool", tool_name))
            return {"tool_name": tool_name, "arguments": arguments}

    monkeypatch.setattr("sandbox.mcp_runner.stdio_client", fake_stdio_client)
    monkeypatch.setattr("sandbox.mcp_runner.ClientSession", FakeClientSession)

    runner = McpSandboxRunner(command="fake-mcp")

    first = await runner._call_named_tool("query_snapshot", {"session_id": "s1"})
    second = await runner._call_named_tool("query_geometry", {"session_id": "s1"})
    await runner.aclose()

    assert first["tool_name"] == "query_snapshot"
    assert second["tool_name"] == "query_geometry"
    assert [event for event, _ in events].count("stdio_enter") == 1
    assert [event for event, _ in events].count("initialize") == 1
    assert [event for event, _ in events].count("call_tool") == 2
    assert [event for event, _ in events].count("session_exit") == 1
    assert [event for event, _ in events].count("stdio_exit") == 1


@pytest.mark.asyncio
async def test_mcp_runner_reopens_session_once_after_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdio_entries = 0
    initialized_session_ids: list[int] = []
    created_session_ids: list[int] = []

    @asynccontextmanager
    async def fake_stdio_client(_params, errlog=None):
        nonlocal stdio_entries
        stdio_entries += 1
        try:
            yield "read-stream", "write-stream"
        finally:
            pass

    class FakeClientSession:
        next_session_id = 0

        def __init__(self, read_stream, write_stream) -> None:
            assert read_stream == "read-stream"
            assert write_stream == "write-stream"
            self.session_id = FakeClientSession.next_session_id
            FakeClientSession.next_session_id += 1
            created_session_ids.append(self.session_id)

        async def __aenter__(self) -> FakeClientSession:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            pass

        async def initialize(self):
            initialized_session_ids.append(self.session_id)

        async def call_tool(self, tool_name: str, arguments: dict[str, object]):
            if self.session_id == 0:
                raise RuntimeError("transport_broken")
            return {
                "tool_name": tool_name,
                "arguments": arguments,
                "session_id": self.session_id,
            }

    monkeypatch.setattr("sandbox.mcp_runner.stdio_client", fake_stdio_client)
    monkeypatch.setattr("sandbox.mcp_runner.ClientSession", FakeClientSession)

    runner = McpSandboxRunner(command="fake-mcp")

    result = await runner._call_named_tool("validate_requirement", {"session_id": "s1"})
    await runner.aclose()

    assert result["tool_name"] == "validate_requirement"
    assert result["session_id"] == 1
    assert stdio_entries == 2
    assert created_session_ids == [0, 1]
    assert initialized_session_ids == [0, 1]


@pytest.mark.asyncio
async def test_validate_requirement_returns_structured_failure_on_cancelled_transport() -> None:
    runner = McpSandboxRunner(command="fake-mcp")

    async def fake_call_named_tool(tool_name: str, arguments: dict[str, object]):
        raise asyncio.CancelledError()

    runner._call_named_tool = fake_call_named_tool  # type: ignore[method-assign]

    result = await runner.validate_requirement(
        session_id="session-cancelled",
        requirements={"description": "noop"},
        timeout=5,
    )

    assert result.success is False
    assert result.error_code == "execution_error"
    assert result.blockers == ["execution_error"]
    assert result.summary == "Validation failed"
    assert "cancelled" in (result.error_message or "").lower()


def test_map_requirement_validation_result_preserves_grounding_surface_fields() -> None:
    runner = McpSandboxRunner(command="fake-mcp")
    call_result = SimpleNamespace(
        structuredContent={
            "success": True,
            "error_code": "none",
            "error_message": None,
            "session_id": "session-1",
            "step": 2,
            "is_complete": False,
            "blockers": ["missing_anchor"],
            "checks": [],
            "core_checks": [],
            "diagnostic_checks": [],
            "clause_interpretations": [],
            "coverage_confidence": 0.4,
            "insufficient_evidence": True,
            "observation_tags": ["geometry"],
            "decision_hints": ["query_feature_probes"],
            "grounding_sources": ["geometry", "topology"],
            "grounding_strength": "partial",
            "required_evidence_kinds": ["geometry", "topology"],
            "overclaim_guard": "geometry_grounding_required",
            "repair_hints": ["query_topology", "query_feature_probes"],
            "family_bindings": ["explicit_anchor_hole"],
            "blocker_taxonomy": [],
            "relation_index": None,
            "summary": "Validation incomplete",
        },
        isError=False,
    )

    result = runner._map_requirement_validation_result(call_result, session_id="session-1")

    assert result.grounding_sources == ["geometry", "topology"]
    assert result.grounding_strength == "partial"
    assert result.required_evidence_kinds == ["geometry", "topology"]
    assert result.overclaim_guard == "geometry_grounding_required"
    assert result.repair_hints == ["query_topology", "query_feature_probes"]
    assert result.family_bindings == ["explicit_anchor_hole"]


@pytest.mark.asyncio
async def test_call_named_tool_preserves_cancelled_error_when_close_fails() -> None:
    runner = McpSandboxRunner(command="fake-mcp")

    class FakeClientSession:
        async def call_tool(self, tool_name: str, arguments: dict[str, object]):
            raise asyncio.CancelledError()

    async def fake_ensure_client_session() -> FakeClientSession:
        return FakeClientSession()

    async def fake_aclose() -> None:
        raise RuntimeError("close failed")

    runner._ensure_client_session = fake_ensure_client_session  # type: ignore[method-assign]
    runner.aclose = fake_aclose  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        await runner._call_named_tool("validate_requirement", {"session_id": "s1"})


@pytest.mark.asyncio
async def test_aclose_suppresses_cross_task_cancel_scope_runtime_error() -> None:
    runner = McpSandboxRunner(command="fake-mcp")

    class FakeStack:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True
            raise RuntimeError(
                "Attempted to exit cancel scope in a different task than it was entered in"
            )

    fake_stack = FakeStack()
    runner._session_stack = fake_stack  # type: ignore[assignment]
    runner._client_session = object()  # type: ignore[assignment]

    await runner.aclose()

    assert fake_stack.closed is True
    assert runner._session_stack is None
    assert runner._client_session is None


@pytest.mark.asyncio
async def test_iterative_runner_closes_sandbox_after_run() -> None:
    class FakeSandbox:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    class FakeLoop:
        async def run(self, request: IterationRequest, run_dir):
            return IterationRunResult(
                run_dir=str(run_dir),
                request=request,
                summary=IterationRunSummary(
                    session_id="session-1",
                    provider="kimi",
                    model="kimi-k2.6",
                    planner_rounds=1,
                    executed_action_count=0,
                    executed_action_types=[],
                    converged=True,
                    validation_complete=True,
                    step_file_exists=False,
                    render_file_exists=False,
                    render_image_attached_for_prompt=False,
                ),
            )

    sandbox = FakeSandbox()
    runner = object.__new__(IterativeSubAgentRunner)
    runner._sandbox = sandbox
    runner._build_v2_loop = lambda: FakeLoop()

    result = await IterativeSubAgentRunner.run(
        runner,
        request=IterationRequest(requirements={"description": "noop"}),
        run_dir=Path("/tmp/iter-run"),
    )

    assert result.summary.validation_complete is True
    assert sandbox.closed is True
