from pathlib import Path

import yaml

from common.logging import get_logger
from sandbox_mcp_server.llm_judge_models import LLMJudgeRubric

logger = get_logger(__name__)


class RubricLoader:
    def __init__(self, rubric_path: str) -> None:
        self._rubric_path = rubric_path
        self._cached_rubric: LLMJudgeRubric | None = None
        self._cached_mtime: float | None = None

    def load(self) -> LLMJudgeRubric:
        resolved_path = self._resolve_path(self._rubric_path)
        mtime = resolved_path.stat().st_mtime

        if self._cached_rubric is not None and self._cached_mtime == mtime:
            return self._cached_rubric

        payload = yaml.safe_load(resolved_path.read_text(encoding="utf-8"))
        rubric = LLMJudgeRubric.model_validate(payload)

        self._cached_rubric = rubric
        self._cached_mtime = mtime

        logger.info(
            "llm_judge_rubric_loaded",
            rubric_path=str(resolved_path),
            rubric_version=rubric.rubric_version,
            prompt_version=rubric.prompt_version,
            judge_model=rubric.judge_model,
        )
        return rubric

    def _resolve_path(self, path_text: str) -> Path:
        configured = Path(path_text)
        candidates: list[Path] = []

        if configured.is_absolute():
            candidates.append(configured)
        else:
            candidates.append(Path.cwd() / configured)
            repo_root = Path(__file__).resolve().parents[5]
            candidates.append(repo_root / configured)

        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return candidate

        searched = ", ".join(str(candidate) for candidate in candidates)
        raise FileNotFoundError(
            f"LLM judge rubric file not found for path={path_text}; searched={searched}"
        )
