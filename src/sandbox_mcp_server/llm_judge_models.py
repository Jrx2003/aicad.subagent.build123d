from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DimensionScores(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_fidelity: float = Field(..., ge=0.0, le=1.0)
    geometric_reasonableness: float = Field(..., ge=0.0, le=1.0)
    manufacturability_proxy: float = Field(..., ge=0.0, le=1.0)
    code_quality_proxy: float = Field(..., ge=0.0, le=1.0)
    execution_stability: float = Field(..., ge=0.0, le=1.0)


class RubricWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_fidelity: float = Field(..., ge=0.0, le=1.0)
    geometric_reasonableness: float = Field(..., ge=0.0, le=1.0)
    manufacturability_proxy: float = Field(..., ge=0.0, le=1.0)
    code_quality_proxy: float = Field(..., ge=0.0, le=1.0)
    execution_stability: float = Field(..., ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_sum_is_one(self) -> "RubricWeights":
        total = (
            self.requirement_fidelity
            + self.geometric_reasonableness
            + self.manufacturability_proxy
            + self.code_quality_proxy
            + self.execution_stability
        )
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"rubric weights must sum to 1.0, got {total:.6f}")
        return self


class AnchorLevels(BaseModel):
    model_config = ConfigDict(extra="forbid")

    high: str
    mid: str
    low: str


class HardGates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    missing_step_max_score: float = Field(..., ge=0.0, le=1.0)
    empty_artifact_max_score: float = Field(..., ge=0.0, le=1.0)


class ConfidenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    low_threshold: float = Field(..., ge=0.0, le=1.0)
    enable_rejudge: bool = False


class LLMJudgeRubric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rubric_id: str
    rubric_version: str
    prompt_version: str
    judge_model: str
    weights: RubricWeights
    anchors: dict[str, AnchorLevels] = Field(default_factory=dict)
    hard_gates: HardGates
    confidence: ConfidenceConfig


class EvidenceImage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    mime_type: str
    size_bytes: int = Field(..., ge=0)
    sha256: str
    content_base64: str


class EvidenceBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_text: str
    requirement_text_sha256: str | None = None
    request_prompt: str
    execution_success: bool
    execution_error_code: str
    stderr_excerpt: str
    has_step: bool
    step_filename: str | None = None
    step_size_bytes: int | None = Field(default=None, ge=0)
    code_excerpt: str
    step_sha256: str | None = None
    rubric_version: str
    prompt_version: str
    judge_model: str
    evaluator_version: str
    evaluation_trace_id: str
    replay_key: str
    preview_images: list[EvidenceImage] = Field(default_factory=list)


class RuleEvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_score_cap: float | None = Field(default=None, ge=0.0, le=1.0)
    major_issues: list[str] = Field(default_factory=list)


class LLMJudgeParsedOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall_semantic_score: float = Field(..., ge=0.0, le=1.0)
    confidence: float = Field(..., ge=0.0, le=1.0)
    dimension_scores: DimensionScores
    major_issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    reasoning_brief: str


class LLMJudgeEvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["success", "error"]
    reason: str | None = None
    semantic_score: float | None = Field(default=None, ge=0.0, le=1.0)
    judge_output: LLMJudgeParsedOutput | None = None
    judge_attempt_count: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_success_payload(self) -> "LLMJudgeEvaluationResult":
        if self.status == "success" and self.judge_output is None:
            raise ValueError("judge_output is required when status=success")
        if self.status == "success" and self.semantic_score is None:
            raise ValueError("semantic_score is required when status=success")
        return self
