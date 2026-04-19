from pathlib import Path

import pytest

from sandbox.local_process_runner import LocalProcessSandboxRunner


@pytest.mark.asyncio
async def test_local_process_runner_exports_step_for_simple_build123d_code():
    runner = LocalProcessSandboxRunner()

    result = await runner.execute("result = Box(10, 10, 4)")

    assert result.success is True
    assert "model.step" in result.output_files
    assert isinstance(result.output_file_contents.get("model.step"), bytes)
    assert result.output_file_contents["model.step"]


@pytest.mark.asyncio
async def test_local_process_runner_rewrites_output_paths_for_host_execution():
    runner = LocalProcessSandboxRunner()

    result = await runner.execute(
        "Path('/output/custom.txt').write_text('hello', encoding='utf-8')"
    )

    assert result.success is True
    assert "custom.txt" in result.output_files
    assert result.output_file_contents["custom.txt"] == b"hello"
