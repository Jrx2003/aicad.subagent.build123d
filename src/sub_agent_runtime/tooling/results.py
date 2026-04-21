from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from sub_agent_runtime.hooks import ToolHookTrace
from sub_agent_runtime.turn_state import (
    ToolCallRecord,
    ToolCategory,
    ToolExecutionEvent,
    ToolResultRecord,
)


@dataclass(slots=True)
class ToolBatchResult:
    tool_calls: list[ToolCallRecord]
    tool_results: list[ToolResultRecord]
    execution_events: list[ToolExecutionEvent] = field(default_factory=list)
    error: str | None = None
    requested_finish: bool = False
    finish_reason: str | None = None

def _record_from_result(
    *,
    name: str,
    category: ToolCategory,
    result: Any,
) -> ToolResultRecord:
    payload = _result_to_dict(result)
    success = bool(payload.get("success", False))
    artifact_files = [
        item for item in payload.get("output_files", []) if isinstance(item, str)
    ]
    artifact_contents = payload.get("output_file_contents")
    if not isinstance(artifact_contents, dict):
        artifact_contents = {}
    normalized_contents = {
        filename: content
        for filename, content in artifact_contents.items()
        if isinstance(filename, str) and isinstance(content, (bytes, bytearray))
    }
    error = payload.get("error_message")
    if not isinstance(error, str):
        error = None
    stderr_value = payload.get("stderr")
    if (
        isinstance(error, str)
        and error.strip().lower().startswith("exit code:")
        and isinstance(stderr_value, str)
        and stderr_value.strip()
    ):
        stderr_head = stderr_value.strip().splitlines()[0].strip()
        if stderr_head:
            error = f"{error.strip()} | {stderr_head[:180]}"
    step_file = payload.get("step_file")
    if not isinstance(step_file, str):
        step_file = None
    return ToolResultRecord(
        name=name,
        category=category,
        success=success,
        payload=payload,
        error=error,
        artifact_files=artifact_files,
        artifact_contents=normalized_contents,
        step_file=step_file,
    )


def _result_to_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return dict(result)
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="python", exclude_none=False)
    if hasattr(result, "__dataclass_fields__"):
        return asdict(result)
    if hasattr(result, "__dict__"):
        return {
            key: value
            for key, value in vars(result).items()
            if not key.startswith("_")
        }
    return {"success": False, "error_message": f"unserializable_result:{type(result)}"}


def _summarize_result_payload(result: ToolResultRecord) -> dict[str, Any]:
    payload = result.payload
    summary: dict[str, Any] = {
        "tool_name": result.name,
        "success": result.success,
    }
    for key in (
        "error_code",
        "error_message",
        "step",
        "session_id",
        "summary",
        "is_complete",
        "blockers",
        "features",
        "issues",
        "view_file",
        "step_file",
        "session_state_persisted",
    ):
        if key in payload:
            summary[key] = payload.get(key)
    if "snapshot" in payload and isinstance(payload["snapshot"], dict):
        summary["snapshot"] = {
            key: payload["snapshot"].get(key)
            for key in ("step", "issues", "geometry")
            if key in payload["snapshot"]
        }
    return summary



def record_from_result(
    *,
    name: str,
    category: ToolCategory,
    result: Any,
) -> ToolResultRecord:
    return _record_from_result(name=name, category=category, result=result)



def summarize_result_payload(result: ToolResultRecord) -> dict[str, Any]:
    return _summarize_result_payload(result)



def trace_to_dict(trace: ToolHookTrace) -> dict[str, Any]:
    return {
        "pre": trace.pre,
        "post_success": trace.post_success,
        "post_failure": trace.post_failure,
        "pre_finish": trace.pre_finish,
        "notes": list(trace.notes),
    }


__all__ = [
    "ToolBatchResult",
    "record_from_result",
    "summarize_result_payload",
    "trace_to_dict",
]
