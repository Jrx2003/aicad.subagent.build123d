from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sub_agent_runtime.tooling.results import trace_to_dict as _trace_to_dict
from sub_agent_runtime.turn_state import (
    ToolCallRecord,
    ToolCategory,
    ToolExecutionEvent,
)


@dataclass(slots=True)
class _NormalizedWriteBatch:
    tool_calls: list[ToolCallRecord]
    execution_events: list[ToolExecutionEvent] = field(default_factory=list)


def _strip_runtime_managed_fields(
    schema: dict[str, Any],
    managed_fields: set[str],
) -> dict[str, Any]:
    if not managed_fields:
        return schema
    normalized = dict(schema)
    properties = normalized.get("properties")
    if isinstance(properties, dict):
        normalized["properties"] = {
            key: value for key, value in properties.items() if key not in managed_fields
        }
    required = normalized.get("required")
    if isinstance(required, list):
        normalized["required"] = [item for item in required if item not in managed_fields]
    return normalized


def _normalize_multi_write_batch(
    *,
    normalized_calls: list[ToolCallRecord],
    write_calls: list[ToolCallRecord],
    round_no: int,
) -> _NormalizedWriteBatch | None:
    if len(normalized_calls) != len(write_calls):
        return None
    if not write_calls:
        return None
    if any(tool_call.name != "apply_cad_action" for tool_call in write_calls):
        return None

    kept_call = write_calls[0]
    dropped_calls = write_calls[1:]
    if not dropped_calls:
        return None

    return _NormalizedWriteBatch(
        tool_calls=[kept_call],
        execution_events=[
            ToolExecutionEvent(
                round_no=round_no,
                tool_name=kept_call.name,
                phase="normalized",
                category=ToolCategory.WRITE,
                detail={
                    "reason": "truncated_multi_apply_cad_action_batch",
                    "kept_call_id": kept_call.call_id,
                    "kept_action_type": kept_call.arguments.get("action_type"),
                    "dropped_call_ids": [
                        dropped_call.call_id
                        for dropped_call in dropped_calls
                        if dropped_call.call_id is not None
                    ],
                    "dropped_action_types": [
                        str(dropped_call.arguments.get("action_type") or "").strip()
                        for dropped_call in dropped_calls
                    ],
                    "original_write_count": len(write_calls),
                },
            )
        ],
    )


__all__ = [
    "_NormalizedWriteBatch",
    "_normalize_multi_write_batch",
    "_strip_runtime_managed_fields",
    "_trace_to_dict",
]
