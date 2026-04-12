import asyncio
import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from common.logging import get_logger
from sandbox_mcp_server.contracts import (
    EvaluationMode,
    EvaluationStatus,
    ExecutionEvaluation,
)

logger = get_logger(__name__)

_ENTITY_ID_PATTERN = re.compile(r"^#\d+\s*=\s*")
_ENTITY_REF_PATTERN = re.compile(r"#\d+")
_STEP_EXTENSIONS = (".step", ".stp")
DEFAULT_METRIC_NAME = "step_data_jaccard_v1"
DEFAULT_BENCHMARK_NAME = "Text2CAD-Bench"


class GroundTruthBenchmarkIndex(BaseModel):
    """On-disk index for benchmark ground-truth artifacts."""

    model_config = ConfigDict(extra="forbid")

    benchmark_name: str = DEFAULT_BENCHMARK_NAME
    metric_name: str = DEFAULT_METRIC_NAME
    pass_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    root_dir: str | None = None
    samples: dict[str, str] = Field(
        default_factory=dict,
        description="sample_id -> step file path (relative to root_dir or index file).",
    )


class GroundTruthBenchmarkEvaluator:
    """Evaluate generated STEP artifact against benchmark ground truth."""

    def __init__(
        self,
        index_path: str,
        default_benchmark_name: str = DEFAULT_BENCHMARK_NAME,
        default_pass_threshold: float = 0.85,
    ) -> None:
        self._index_path = Path(index_path)
        self._default_benchmark_name = default_benchmark_name
        self._default_pass_threshold = default_pass_threshold
        self._cached_index: GroundTruthBenchmarkIndex | None = None

    async def evaluate(
        self,
        sample_id: str,
        output_file_contents: dict[str, bytes],
    ) -> ExecutionEvaluation:
        generated = self._pick_generated_step(output_file_contents)
        if generated is None:
            return ExecutionEvaluation(
                mode=EvaluationMode.GROUND_TRUTH,
                status=EvaluationStatus.SKIPPED,
                sample_id=sample_id,
                summary="Ground-truth evaluation skipped: no STEP artifact available",
                details={"reason": "missing_step_artifact"},
            )

        try:
            benchmark_index = await self._load_index()
        except Exception as exc:
            logger.warning("benchmark_index_load_failed", error=str(exc), exc_info=True)
            return ExecutionEvaluation(
                mode=EvaluationMode.GROUND_TRUTH,
                status=EvaluationStatus.ERROR,
                sample_id=sample_id,
                summary="Ground-truth evaluation failed: benchmark index unavailable",
                details={"reason": f"index_load_failed:{exc}"},
            )

        step_rel_path = benchmark_index.samples.get(sample_id)
        benchmark_name = benchmark_index.benchmark_name or self._default_benchmark_name

        if not step_rel_path:
            return ExecutionEvaluation(
                mode=EvaluationMode.GROUND_TRUTH,
                status=EvaluationStatus.ERROR,
                benchmark_name=benchmark_name,
                sample_id=sample_id,
                metric_name=benchmark_index.metric_name,
                summary="Ground-truth evaluation failed: sample_id not found in benchmark index",
                details={"reason": "sample_not_found"},
            )

        reference_path = self._resolve_reference_path(benchmark_index, step_rel_path)
        if not reference_path.exists():
            return ExecutionEvaluation(
                mode=EvaluationMode.GROUND_TRUTH,
                status=EvaluationStatus.ERROR,
                benchmark_name=benchmark_name,
                sample_id=sample_id,
                metric_name=benchmark_index.metric_name,
                summary="Ground-truth evaluation failed: reference STEP file does not exist",
                details={"reason": f"missing_reference:{reference_path}"},
            )

        reference_step = await asyncio.to_thread(reference_path.read_bytes)
        score, details = self._score_step_similarity(
            generated_step=generated[1],
            reference_step=reference_step,
        )

        threshold = (
            benchmark_index.pass_threshold
            if benchmark_index.pass_threshold is not None
            else self._default_pass_threshold
        )
        passed = score >= threshold
        return ExecutionEvaluation(
            mode=EvaluationMode.GROUND_TRUTH,
            status=EvaluationStatus.SUCCESS,
            benchmark_name=benchmark_name,
            sample_id=sample_id,
            metric_name=benchmark_index.metric_name,
            score=score,
            threshold=threshold,
            passed=passed,
            summary=(
                f"Ground-truth score={score:.4f} "
                f"(threshold={threshold:.2f}, passed={str(passed).lower()})"
            ),
            details={
                "generated_filename": generated[0],
                **details,
            },
        )

    async def _load_index(self) -> GroundTruthBenchmarkIndex:
        if self._cached_index is not None:
            return self._cached_index

        if not self._index_path.exists():
            raise FileNotFoundError(f"Benchmark index not found: {self._index_path}")

        raw_text = await asyncio.to_thread(self._index_path.read_text, encoding="utf-8")
        payload = json.loads(raw_text)
        self._cached_index = GroundTruthBenchmarkIndex.model_validate(payload)
        return self._cached_index

    def _resolve_reference_path(
        self,
        benchmark_index: GroundTruthBenchmarkIndex,
        reference_path: str,
    ) -> Path:
        base_dir = self._index_path.parent
        if benchmark_index.root_dir:
            root = Path(benchmark_index.root_dir)
            if not root.is_absolute():
                root = base_dir / root
            base_dir = root

        resolved = Path(reference_path)
        if not resolved.is_absolute():
            resolved = base_dir / resolved
        return resolved

    def _pick_generated_step(
        self,
        output_file_contents: dict[str, bytes],
    ) -> tuple[str, bytes] | None:
        if "model.step" in output_file_contents:
            return "model.step", output_file_contents["model.step"]

        for filename, content in output_file_contents.items():
            if filename.lower().endswith(_STEP_EXTENSIONS):
                return filename, content

        return None

    def _score_step_similarity(
        self,
        generated_step: bytes,
        reference_step: bytes,
    ) -> tuple[float, dict[str, str]]:
        generated_lines = self._extract_data_lines(
            generated_step.decode("utf-8", "ignore")
        )
        reference_lines = self._extract_data_lines(
            reference_step.decode("utf-8", "ignore")
        )

        generated_set = set(generated_lines)
        reference_set = set(reference_lines)

        if not generated_set and not reference_set:
            data_similarity = 1.0
        else:
            union_size = len(generated_set | reference_set)
            intersection_size = len(generated_set & reference_set)
            data_similarity = (
                float(intersection_size) / float(union_size) if union_size > 0 else 0.0
            )

        generated_count = len(generated_lines)
        reference_count = len(reference_lines)
        if generated_count == 0 and reference_count == 0:
            entity_count_similarity = 1.0
        else:
            max_count = max(generated_count, reference_count)
            min_count = min(generated_count, reference_count)
            entity_count_similarity = float(min_count) / float(max_count)

        score = (0.85 * data_similarity) + (0.15 * entity_count_similarity)
        normalized_score = max(0.0, min(1.0, round(score, 6)))

        details = {
            "data_similarity": f"{data_similarity:.6f}",
            "entity_count_similarity": f"{entity_count_similarity:.6f}",
            "generated_entity_count": str(generated_count),
            "reference_entity_count": str(reference_count),
        }
        return normalized_score, details

    def _extract_data_lines(self, step_text: str) -> list[str]:
        lines = step_text.splitlines()
        in_data_section = False
        normalized: list[str] = []

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue

            upper_line = line.upper()
            if not in_data_section:
                if upper_line == "DATA;":
                    in_data_section = True
                continue

            if upper_line == "ENDSEC;":
                break

            normalized_line = _ENTITY_ID_PATTERN.sub("", line)
            normalized_line = _ENTITY_REF_PATTERN.sub("#N", normalized_line)
            normalized_line = " ".join(normalized_line.split()).upper()
            if normalized_line:
                normalized.append(normalized_line)

        return normalized
