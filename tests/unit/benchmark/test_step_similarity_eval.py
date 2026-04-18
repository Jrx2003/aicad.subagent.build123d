from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


def _load_step_similarity_module():
    script_path = Path(__file__).resolve().parents[3] / "benchmark" / "step_similarity_eval.py"
    benchmark_dir = script_path.parent
    benchmark_dir_str = str(benchmark_dir)
    if benchmark_dir_str not in sys.path:
        sys.path.insert(0, benchmark_dir_str)
    spec = importlib.util.spec_from_file_location("step_similarity_eval", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_evaluate_step_pair_async_closes_runner_on_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_step_similarity_module()
    events: list[str] = []

    class FakeRunner:
        def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
            events.append("init")

        async def execute(self, **kwargs):  # type: ignore[no-untyped-def]
            events.append("execute")
            return SimpleNamespace(
                success=True,
                error_message=None,
                stderr="",
                stdout="",
                output_files=["geometry_info.json", "generated_preview_iso.png"],
                output_file_contents={
                    "geometry_info.json": json.dumps(
                        {
                            "status": "ok",
                            "difference_notes": [],
                            "preview_views": ["iso"],
                            "generated_stats": {"solids": 1},
                        }
                    ).encode("utf-8"),
                    "generated_preview_iso.png": b"png",
                },
            )

        async def aclose(self) -> None:
            events.append("close")

    monkeypatch.setattr(module, "McpSandboxRunner", FakeRunner)

    generated_step = tmp_path / "generated.step"
    ground_truth_step = tmp_path / "ground_truth.step"
    output_dir = tmp_path / "evaluation"
    generated_step.write_bytes(b"generated")
    ground_truth_step.write_bytes(b"ground-truth")

    payload = await module._evaluate_step_pair_async(
        generated_step=generated_step,
        ground_truth_step=ground_truth_step,
        output_dir=output_dir,
        threshold=1.0,
        timeout_seconds=30,
    )

    assert payload["status"] == "ok"
    assert events == ["init", "execute", "close"]
    assert (output_dir / "generated_preview_iso.png").read_bytes() == b"png"


@pytest.mark.asyncio
async def test_evaluate_step_pair_async_closes_runner_when_execute_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_step_similarity_module()
    events: list[str] = []

    class FakeRunner:
        def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
            events.append("init")

        async def execute(self, **kwargs):  # type: ignore[no-untyped-def]
            events.append("execute")
            raise RuntimeError("boom")

        async def aclose(self) -> None:
            events.append("close")

    monkeypatch.setattr(module, "McpSandboxRunner", FakeRunner)

    generated_step = tmp_path / "generated.step"
    ground_truth_step = tmp_path / "ground_truth.step"
    generated_step.write_bytes(b"generated")
    ground_truth_step.write_bytes(b"ground-truth")

    with pytest.raises(RuntimeError, match="boom"):
        await module._evaluate_step_pair_async(
            generated_step=generated_step,
            ground_truth_step=ground_truth_step,
            output_dir=tmp_path / "evaluation",
            threshold=1.0,
            timeout_seconds=30,
        )

    assert events == ["init", "execute", "close"]
