"""Host-local Build123d runner for development and practice flows."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

from common.logging import get_logger
from sandbox.docker_runner import _build_runtime_code
from sandbox.interface import SandboxResult

logger = get_logger(__name__)


class LocalProcessSandboxRunner:
    """Execute Build123d code in a local subprocess.

    This runner is intended for development and practice flows where Docker is
    unavailable. It keeps the same `SandboxResult` contract as the Docker
    runner, but executes directly in the current Python environment.
    """

    def __init__(self, python_executable: str | None = None) -> None:
        self._python_executable = python_executable or sys.executable

    async def execute(
        self,
        code: str,
        timeout: int = 120,
        requirement_text: str | None = None,
        session_id: str | None = None,
    ) -> SandboxResult:
        _ = requirement_text
        _ = session_id
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._execute_sync(code=code, timeout=timeout),
        )

    def _execute_sync(
        self,
        *,
        code: str,
        timeout: int,
    ) -> SandboxResult:
        temp_dir = tempfile.mkdtemp(prefix="build123d-local-process-")
        temp_path = Path(temp_dir)
        output_dir = temp_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        code_file = temp_path / "aicad_runtime_main.py"

        try:
            runtime_code = self._rewrite_output_paths(
                _build_runtime_code(code),
                output_dir=output_dir,
            )
            code_file.write_text(runtime_code, encoding="utf-8")

            completed = subprocess.run(
                [self._python_executable, str(code_file)],
                cwd=str(temp_path),
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            stdout = completed.stdout.decode("utf-8", errors="replace")
            stderr = completed.stderr.decode("utf-8", errors="replace")

            output_files, output_file_contents = self._collect_output_files(output_dir)

            if completed.returncode != 0:
                return SandboxResult(
                    success=False,
                    stdout=stdout,
                    stderr=stderr,
                    output_files=output_files,
                    output_file_contents=output_file_contents,
                    error_message=f"Exit code: {completed.returncode}",
                )

            return SandboxResult(
                success=True,
                stdout=stdout,
                stderr=stderr,
                output_files=output_files,
                output_file_contents=output_file_contents,
                error_message=None,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                success=False,
                stdout="",
                stderr=f"Execution timed out after {timeout} seconds",
                output_files=[],
                output_file_contents={},
                error_message="Timeout",
            )
        except Exception as exc:
            logger.exception("local_process_sandbox_execution_failed", error=str(exc))
            return SandboxResult(
                success=False,
                stdout="",
                stderr=str(exc),
                output_files=[],
                output_file_contents={},
                error_message=f"Local process execution failed: {exc}",
            )
        finally:
            shutil.rmtree(temp_path, ignore_errors=True)

    def _rewrite_output_paths(
        self,
        runtime_code: str,
        *,
        output_dir: Path,
    ) -> str:
        resolved_output_dir = str(output_dir.resolve())
        rewritten = runtime_code.replace("'/output", f"'{resolved_output_dir}")
        rewritten = rewritten.replace('"/output', f'"{resolved_output_dir}')
        return rewritten

    def _collect_output_files(
        self,
        output_dir: Path,
    ) -> tuple[list[str], dict[str, bytes]]:
        if not output_dir.exists():
            return [], {}

        files = sorted(path for path in output_dir.rglob("*") if path.is_file())
        output_files: list[str] = []
        output_file_contents: dict[str, bytes] = {}
        for path in files:
            relative_name = path.relative_to(output_dir).as_posix()
            output_files.append(relative_name)
            output_file_contents[relative_name] = path.read_bytes()
        return output_files, output_file_contents
