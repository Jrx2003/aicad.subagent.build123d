import json
import subprocess
from dataclasses import dataclass, field
from typing import Any

from common.logging import get_logger

logger = get_logger(__name__)


def _clip_text(value: str, max_chars: int = 2000) -> str:
    if len(value) <= max_chars:
        return value
    return f"{value[:max_chars]}...[truncated {len(value) - max_chars} chars]"


class RuntimeHookManager:
    """Lightweight lifecycle hooks driven by JSON command mapping.

    Config source:
    - `SUB_AGENT_RUNTIME_HOOKS_JSON`
      Example:
      {"round_started": "python scripts/hooks/on_round_started.py"}
    """

    def __init__(self, hooks_json: str, timeout_seconds: float) -> None:
        self._timeout_seconds = max(0.1, float(timeout_seconds))
        self._commands = self._parse_commands(hooks_json)

    @classmethod
    def from_settings(cls, settings: Any) -> "RuntimeHookManager":
        hooks_json = str(getattr(settings, "sub_agent_runtime_hooks_json", "") or "")
        timeout_seconds = float(
            getattr(settings, "sub_agent_runtime_hook_timeout_seconds", 8.0) or 8.0
        )
        return cls(hooks_json=hooks_json, timeout_seconds=timeout_seconds)

    @property
    def enabled(self) -> bool:
        return bool(self._commands)

    def emit(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        command = self._commands.get(event_type)
        if not command:
            return None
        stdin_payload = json.dumps(payload, ensure_ascii=True, sort_keys=True)
        try:
            completed = subprocess.run(
                command,
                shell=True,
                input=stdin_payload,
                text=True,
                capture_output=True,
                timeout=self._timeout_seconds,
                check=False,
            )
            return {
                "event": event_type,
                "command": command,
                "exit_code": completed.returncode,
                "ok": completed.returncode == 0,
                "stdout": _clip_text(completed.stdout or ""),
                "stderr": _clip_text(completed.stderr or ""),
            }
        except Exception as exc:  # noqa: BLE001
            message = f"{exc.__class__.__name__}: {exc}"
            logger.warning(
                "runtime_hook_failed",
                event=event_type,
                command=command,
                error=message,
            )
            return {
                "event": event_type,
                "command": command,
                "ok": False,
                "error": message,
            }

    def _parse_commands(self, raw_json: str) -> dict[str, str]:
        normalized = raw_json.strip()
        if not normalized:
            return {}
        try:
            parsed = json.loads(normalized)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "runtime_hooks_json_invalid",
                error=f"{exc.__class__.__name__}: {exc}",
            )
            return {}
        if not isinstance(parsed, dict):
            logger.warning(
                "runtime_hooks_json_invalid_shape",
                payload_type=str(type(parsed)),
            )
            return {}
        commands: dict[str, str] = {}
        for raw_event, raw_command in parsed.items():
            event_name = str(raw_event).strip()
            if not event_name or not isinstance(raw_command, str):
                continue
            command = raw_command.strip()
            if not command:
                continue
            commands[event_name] = command
        return commands

    def emit_pre_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        round_no: int,
        session_id: str,
    ) -> dict[str, Any] | None:
        return self.emit(
            event_type="pre_tool",
            payload={
                "tool_name": tool_name,
                "arguments": arguments,
                "round": round_no,
                "session_id": session_id,
            },
        )

    def emit_post_tool_success(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        result_summary: dict[str, Any],
        round_no: int,
        session_id: str,
    ) -> dict[str, Any] | None:
        return self.emit(
            event_type="post_tool_success",
            payload={
                "tool_name": tool_name,
                "arguments": arguments,
                "result_summary": result_summary,
                "round": round_no,
                "session_id": session_id,
            },
        )

    def emit_post_tool_failure(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        error: str,
        round_no: int,
        session_id: str,
        recommend_execute_build123d: bool = False,
    ) -> dict[str, Any] | None:
        return self.emit(
            event_type="post_tool_failure",
            payload={
                "tool_name": tool_name,
                "arguments": arguments,
                "error": error,
                "round": round_no,
                "session_id": session_id,
                "recommend_execute_build123d": recommend_execute_build123d,
            },
        )

    def emit_pre_finish(
        self,
        *,
        reason: str,
        round_no: int,
        session_id: str,
    ) -> dict[str, Any] | None:
        return self.emit(
            event_type="pre_finish",
            payload={
                "reason": reason,
                "round": round_no,
                "session_id": session_id,
            },
        )


@dataclass(slots=True)
class ToolHookDecision:
    """Typed hook decision used by V2 tool runtime."""

    allow: bool = True
    mutated_arguments: dict[str, Any] | None = None
    note: str | None = None
    recommend_execute_build123d: bool = False


@dataclass(slots=True)
class ToolHookTrace:
    """Hook side-effects captured alongside a V2 tool call."""

    pre: dict[str, Any] | None = None
    post_success: dict[str, Any] | None = None
    post_failure: dict[str, Any] | None = None
    pre_finish: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)
