from common.logging import get_logger
from sandbox.interface import SandboxResult
from sandbox_mcp_server.contracts import (
    EvaluationMode,
    EvaluationStatus,
    ExecuteBuild123dInput,
    ExecutionEvaluation,
    SandboxErrorCode,
)
from sandbox_mcp_server.evidence_builder import EvidenceBuilder
from sandbox_mcp_server.llm_judge import LLMJudgeEvaluator
from sandbox_mcp_server.rule_evaluator import RuleEvaluator
from sandbox_mcp_server.score_merger import ScoreMerger

logger = get_logger(__name__)


class EvaluationOrchestrator:
    def __init__(
        self,
        llm_judge_enabled: bool = False,
        evidence_builder: EvidenceBuilder | None = None,
        rule_evaluator: RuleEvaluator | None = None,
        llm_judge_evaluator: LLMJudgeEvaluator | None = None,
        score_merger: ScoreMerger | None = None,
    ) -> None:
        self._llm_judge_enabled = llm_judge_enabled
        self._evidence_builder = evidence_builder
        self._rule_evaluator = rule_evaluator
        self._llm_judge_evaluator = llm_judge_evaluator
        self._score_merger = score_merger

    async def evaluate(
        self,
        request: ExecuteBuild123dInput,
        sandbox_result: SandboxResult,
        error_code: SandboxErrorCode,
    ) -> ExecutionEvaluation:
        if not sandbox_result.success:
            return self._not_requested_evaluation()

        if not self._llm_judge_enabled:
            return self._not_requested_evaluation()

        if (
            self._evidence_builder is None
            or self._rule_evaluator is None
            or self._llm_judge_evaluator is None
            or self._score_merger is None
        ):
            logger.warning("llm_judge_components_not_configured")
            return self._not_requested_evaluation()

        try:
            rubric = self._llm_judge_evaluator.rubric
            evidence = self._evidence_builder.build(
                request=request,
                sandbox_result=sandbox_result,
                error_code=error_code,
                rubric=rubric,
            )
            rule_result = self._rule_evaluator.evaluate(evidence)
            llm_result = await self._llm_judge_evaluator.evaluate(evidence)
            return self._score_merger.merge(
                evidence=evidence,
                rule_result=rule_result,
                llm_result=llm_result,
            )
        except Exception as exc:
            logger.warning(
                "llm_judge_orchestration_failed", reason=str(exc), exc_info=True
            )
            return ExecutionEvaluation(
                mode=EvaluationMode.LLM_JUDGE,
                status=EvaluationStatus.ERROR,
                summary="llm_judge_failed",
                details={"reason": "orchestrator_error", "error": str(exc)[:200]},
            )

    def _not_requested_evaluation(self) -> ExecutionEvaluation:
        return ExecutionEvaluation(
            mode=EvaluationMode.NONE,
            status=EvaluationStatus.NOT_REQUESTED,
            summary="Evaluation not requested",
        )
