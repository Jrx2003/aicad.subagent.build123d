"""Preflight lint entrypoints."""

from typing import Any


def _preflight_lint_execute_build123d(**kwargs: Any) -> dict[str, Any] | None:
    from sub_agent_runtime.tooling.lint.preflight import (
        _preflight_lint_execute_build123d as _impl,
    )

    return _impl(**kwargs)


__all__ = ["_preflight_lint_execute_build123d"]
