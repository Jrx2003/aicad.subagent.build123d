from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from sandbox.mcp_runner import McpSandboxRunner
from sub_agent_runtime.contracts import (
    IterationRequest,
    IterationRunResult,
    IterationRunSummary,
)
from sub_agent_runtime.runner import IterativeSubAgentRunner


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
                    model="kimi-k2.5-thinking",
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
