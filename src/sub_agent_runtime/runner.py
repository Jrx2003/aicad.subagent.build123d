from __future__ import annotations

from datetime import datetime
from pathlib import Path

from common.config import Settings, settings
from sandbox.mcp_runner import McpSandboxRunner
from sub_agent_runtime.agent_loop_v2 import IterativeAgentLoopV2
from sub_agent_runtime.contracts import IterationRequest, IterationRunResult
from sub_agent_runtime.hooks import RuntimeHookManager


class IterativeSubAgentRunner:
    """Stable API shell for the V2 iterative CAD runtime."""

    def __init__(self, app_settings: Settings | None = None) -> None:
        self._settings = app_settings or settings
        self._sandbox = McpSandboxRunner(
            command=self._settings.sandbox_mcp_server_command,
            args=self._settings.sandbox_mcp_server_args_list,
            cwd=self._settings.sandbox_mcp_server_cwd_effective,
            timeout_buffer_seconds=self._settings.sandbox_mcp_timeout_buffer_seconds,
        )
        self._hook_manager = RuntimeHookManager.from_settings(self._settings)

    @staticmethod
    def create_run_dir(runs_root: Path, run_id: str | None = None) -> Path:
        runs_root.mkdir(parents=True, exist_ok=True)
        resolved_run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = runs_root / resolved_run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    async def run(
        self,
        request: IterationRequest,
        run_dir: Path,
    ) -> IterationRunResult:
        return await self._build_v2_loop().run(request=request, run_dir=run_dir)

    def _build_v2_loop(self) -> IterativeAgentLoopV2:
        return IterativeAgentLoopV2(
            app_settings=self._settings,
            sandbox=self._sandbox,
            hook_manager=self._hook_manager,
        )


async def run_from_env(
    *,
    request: IterationRequest,
    runs_root: Path,
    run_id: str | None = None,
    app_settings: Settings | None = None,
) -> IterationRunResult:
    runner = IterativeSubAgentRunner(app_settings=app_settings)
    run_dir = runner.create_run_dir(runs_root, run_id=run_id)
    return await runner.run(request=request, run_dir=run_dir)
