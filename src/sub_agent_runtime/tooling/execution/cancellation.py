from __future__ import annotations

import asyncio


def _clear_current_task_cancellation_state() -> None:
    task = asyncio.current_task()
    if task is None:
        return
    uncancel = getattr(task, "uncancel", None)
    cancelling = getattr(task, "cancelling", None)
    if not callable(uncancel) or not callable(cancelling):
        return
    while cancelling():
        uncancel()
