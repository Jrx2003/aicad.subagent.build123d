from sandbox_mcp_server.contracts import (
    EvaluationMode,
    EvaluationStatus,
    ExecutionEvaluation,
)
from sandbox_mcp_server.llm_judge_models import (
    EvidenceBundle,
    LLMJudgeEvaluationResult,
    LLMJudgeRubric,
    RuleEvaluationResult,
)

_MAX_DETAIL_ITEMS = 5
_MAX_DETAIL_TEXT_CHARS = 280


class ScoreMerger:
    def __init__(self, rubric: LLMJudgeRubric) -> None:
        self._rubric = rubric

    def merge(
        self,
        evidence: EvidenceBundle,
        rule_result: RuleEvaluationResult,
        llm_result: LLMJudgeEvaluationResult,
    ) -> ExecutionEvaluation:
        requirement_context = self._build_requirement_context_details(evidence)

        if llm_result.status != "success" or llm_result.judge_output is None:
            details: dict[str, str | int | float | bool | None] = {
                "reason": llm_result.reason or "llm_judge_failed",
                "evaluation_trace_id": evidence.evaluation_trace_id,
                "replay_key": evidence.replay_key,
                "evaluator_version": evidence.evaluator_version,
                "judge_attempt_count": llm_result.judge_attempt_count,
                "rubric_version": evidence.rubric_version,
                "prompt_version": evidence.prompt_version,
                "judge_model": evidence.judge_model,
            }
            details.update(requirement_context)
            return ExecutionEvaluation(
                mode=EvaluationMode.LLM_JUDGE,
                status=EvaluationStatus.ERROR,
                metric_name=self._rubric.rubric_id,
                summary="llm_judge_failed",
                details=details,
            )

        parsed = llm_result.judge_output
        base_score = llm_result.semantic_score or 0.0
        if rule_result.max_score_cap is not None:
            final_score = min(base_score, rule_result.max_score_cap)
        else:
            final_score = base_score

        all_major_issues = self._merge_unique(
            rule_result.major_issues,
            parsed.major_issues,
        )

        details = {
            "confidence": round(parsed.confidence, 6),
            "requirement_fidelity": round(
                parsed.dimension_scores.requirement_fidelity, 6
            ),
            "geometric_reasonableness": round(
                parsed.dimension_scores.geometric_reasonableness, 6
            ),
            "manufacturability_proxy": round(
                parsed.dimension_scores.manufacturability_proxy, 6
            ),
            "code_quality_proxy": round(parsed.dimension_scores.code_quality_proxy, 6),
            "execution_stability": round(
                parsed.dimension_scores.execution_stability, 6
            ),
            "llm_overall_semantic_score": round(parsed.overall_semantic_score, 6),
            "weighted_semantic_score": round(base_score, 6),
            "max_score_cap": (
                round(rule_result.max_score_cap, 6)
                if rule_result.max_score_cap is not None
                else None
            ),
            "evaluation_trace_id": evidence.evaluation_trace_id,
            "replay_key": evidence.replay_key,
            "evaluator_version": evidence.evaluator_version,
            "judge_attempt_count": llm_result.judge_attempt_count,
            "rubric_version": evidence.rubric_version,
            "prompt_version": evidence.prompt_version,
            "judge_model": evidence.judge_model,
        }
        details.update(requirement_context)

        self._flatten_list("major_issue", all_major_issues, details)
        self._flatten_list("suggestion", parsed.suggestions, details)

        summary = parsed.reasoning_brief.strip() or "llm_judge_completed"
        summary = summary[:240]

        return ExecutionEvaluation(
            mode=EvaluationMode.LLM_JUDGE,
            status=EvaluationStatus.SUCCESS,
            metric_name=self._rubric.rubric_id,
            score=round(final_score, 6),
            summary=summary,
            details=details,
        )

    def _flatten_list(
        self,
        key_prefix: str,
        values: list[str],
        details: dict[str, str | int | float | bool | None],
    ) -> None:
        for index, value in enumerate(values[:_MAX_DETAIL_ITEMS], start=1):
            normalized = " ".join(value.strip().split())
            details[f"{key_prefix}_{index}"] = normalized[:_MAX_DETAIL_TEXT_CHARS]

    def _merge_unique(self, first: list[str], second: list[str]) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()

        for value in [*first, *second]:
            normalized = value.strip()
            if not normalized or normalized in seen:
                continue
            merged.append(normalized)
            seen.add(normalized)

        return merged

    def _build_requirement_context_details(
        self,
        evidence: EvidenceBundle,
    ) -> dict[str, str | int | float | bool | None]:
        requirement_text = evidence.requirement_text.strip()
        return {
            "requirement_text_present": bool(requirement_text),
            "requirement_text_chars": len(requirement_text),
            "requirement_text_sha256": evidence.requirement_text_sha256,
            "requirement_text_excerpt": requirement_text[:200],
            "requirement_context_source": (
                "explicit_requirement_text"
                if requirement_text
                else "code_inferred_only"
            ),
        }
