from __future__ import annotations

import asyncio
from typing import Any

from sub_agent_runtime.tooling.execution.cancellation import (
    _clear_current_task_cancellation_state,
)
from sub_agent_runtime.turn_state import ToolCallRecord, ToolResultRecord


async def _gather_results(
    tasks: list[Any],
    *,
    fallback_tool_calls: list[ToolCallRecord],
) -> list[ToolResultRecord]:
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)
    results: list[ToolResultRecord] = []
    saw_cancellation = False
    for tool_call, raw_result in zip(fallback_tool_calls, raw_results):
        if isinstance(raw_result, ToolResultRecord):
            results.append(raw_result)
            continue
        if isinstance(raw_result, asyncio.CancelledError):
            saw_cancellation = True
            results.append(
                ToolResultRecord(
                    name=tool_call.name,
                    category=tool_call.category,
                    success=False,
                    payload={},
                    error="CancelledError: tool batch cancelled before completion",
                )
            )
            continue
        if isinstance(raw_result, BaseException):
            results.append(
                ToolResultRecord(
                    name=tool_call.name,
                    category=tool_call.category,
                    success=False,
                    payload={},
                    error=f"{raw_result.__class__.__name__}: {raw_result}",
                )
            )
    if saw_cancellation:
        _clear_current_task_cancellation_state()
    return results


__all__ = ["_gather_results"]
