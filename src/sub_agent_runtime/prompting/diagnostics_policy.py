from __future__ import annotations

from typing import Any

from sub_agent_runtime.turn_state import RunState


def should_include_diagnostics(
    run_state: RunState,
    diagnostics: dict[str, Any] | None,
) -> bool:
    if not diagnostics:
        return False
    if run_state.previous_error:
        return True
    latest_validation = run_state.latest_validation or {}
    if latest_validation and not bool(latest_validation.get("is_complete")):
        return True
    return False
