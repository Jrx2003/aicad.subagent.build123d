"""Compatibility facade for runtime orchestration runner entrypoints."""

from sub_agent_runtime.orchestration import runner as _runner

globals().update(
    {
        name: getattr(_runner, name)
        for name in dir(_runner)
        if not name.startswith("__")
    }
)

__all__ = [name for name in globals() if not name.startswith("__")]

