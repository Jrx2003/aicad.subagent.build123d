from sandbox_mcp_server.llm_judge_models import (
    EvidenceBundle,
    LLMJudgeRubric,
    RuleEvaluationResult,
)


class RuleEvaluator:
    def __init__(self, rubric: LLMJudgeRubric) -> None:
        self._rubric = rubric

    def evaluate(self, evidence: EvidenceBundle) -> RuleEvaluationResult:
        max_score_cap: float | None = None
        major_issues: list[str] = []

        if not evidence.has_step:
            max_score_cap = self._rubric.hard_gates.missing_step_max_score
            major_issues.append("missing_step_artifact")
        elif evidence.step_size_bytes == 0:
            max_score_cap = self._rubric.hard_gates.empty_artifact_max_score
            major_issues.append("empty_step_artifact")

        if not evidence.requirement_text.strip():
            major_issues.append("missing_requirement_text_context")

        return RuleEvaluationResult(
            max_score_cap=max_score_cap, major_issues=major_issues
        )
