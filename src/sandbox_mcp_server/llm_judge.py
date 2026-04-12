import json
import re
from collections import OrderedDict
from typing import Any

from common.logging import get_logger
from llm.interface import (
    LLMClient,
    LLMImageContent,
    LLMMessage,
    LLMTextContent,
)
from sandbox_mcp_server.llm_judge_models import (
    EvidenceBundle,
    EvidenceImage,
    LLMJudgeEvaluationResult,
    LLMJudgeParsedOutput,
    LLMJudgeRubric,
)

logger = get_logger(__name__)

_JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class LLMJudgeEvaluator:
    def __init__(
        self,
        llm_client: LLMClient,
        rubric: LLMJudgeRubric,
        max_prompt_chars: int,
        max_response_tokens: int = 1600,
        cache_size: int = 256,
    ) -> None:
        self._llm_client = llm_client
        self._rubric = rubric
        self._max_prompt_chars = max_prompt_chars
        self._max_response_tokens = max_response_tokens
        self._cache_size = max(0, cache_size)
        self._result_cache: OrderedDict[str, LLMJudgeEvaluationResult] = OrderedDict()

    @property
    def rubric(self) -> LLMJudgeRubric:
        return self._rubric

    async def evaluate(self, evidence: EvidenceBundle) -> LLMJudgeEvaluationResult:
        cached = self._get_cached_result(evidence.replay_key)
        if cached is not None:
            logger.info(
                "llm_judge_cache_hit",
                evaluation_trace_id=evidence.evaluation_trace_id,
                replay_key=evidence.replay_key,
            )
            return cached

        use_multimodal = self._supports_multimodal() and bool(evidence.preview_images)
        attempt_count = 0
        attempt_plan: list[tuple[str | None, bool]] = [
            (None, not use_multimodal),
            ("schema_fix", True),
        ]
        if use_multimodal:
            # Kimi multimodal occasionally returns empty/truncated JSON.
            # Give one extra strict text-only retry before failing.
            attempt_plan.append(("schema_fix", True))

        for attempt_index, (retry_hint, force_text_only) in enumerate(
            attempt_plan, start=1
        ):
            messages = self._build_messages(
                evidence=evidence,
                retry_hint=retry_hint,
                force_text_only=force_text_only,
            )

            try:
                attempt_count += 1
                response = await self._llm_client.complete(
                    messages=messages,
                    temperature=0.0,
                    max_tokens=self._max_response_tokens,
                )
            except Exception as exc:
                if attempt_index == 1 and use_multimodal and not force_text_only:
                    logger.warning(
                        "llm_judge_multimodal_provider_error_fallback_to_text",
                        reason=str(exc),
                        evaluation_trace_id=evidence.evaluation_trace_id,
                    )
                    continue

                logger.warning(
                    "llm_judge_provider_error",
                    reason=str(exc),
                    evaluation_trace_id=evidence.evaluation_trace_id,
                    exc_info=True,
                )
                return LLMJudgeEvaluationResult(
                    status="error",
                    reason="provider_error",
                    judge_attempt_count=attempt_count,
                )

            parsed = self._parse_judge_output(response.content)
            if parsed is not None:
                semantic_score = self._weighted_sum(parsed)
                result = LLMJudgeEvaluationResult(
                    status="success",
                    semantic_score=semantic_score,
                    judge_output=parsed,
                    judge_attempt_count=attempt_count,
                )
                self._set_cached_result(evidence.replay_key, result)
                return result

            if attempt_index < len(attempt_plan):
                logger.warning(
                    "llm_judge_invalid_output_retrying",
                    evaluation_trace_id=evidence.evaluation_trace_id,
                    attempt_index=attempt_index,
                    response_content_length=len(response.content),
                    response_content_preview=response.content[:160],
                    next_force_text_only=attempt_plan[attempt_index][1],
                )

        return LLMJudgeEvaluationResult(
            status="error",
            reason="invalid_judge_output",
            judge_attempt_count=attempt_count,
        )

    def _build_messages(
        self,
        evidence: EvidenceBundle,
        retry_hint: str | None,
        force_text_only: bool = False,
    ) -> list[LLMMessage]:
        schema_description = {
            "overall_semantic_score": "float in [0,1]",
            "confidence": "float in [0,1]",
            "dimension_scores": {
                "requirement_fidelity": "float in [0,1]",
                "geometric_reasonableness": "float in [0,1]",
                "manufacturability_proxy": "float in [0,1]",
                "code_quality_proxy": "float in [0,1]",
                "execution_stability": "float in [0,1]",
            },
            "major_issues": ["string", "..."],
            "suggestions": ["string", "..."],
            "reasoning_brief": "string",
        }

        anchors = {
            name: {
                "high": levels.high,
                "mid": levels.mid,
                "low": levels.low,
            }
            for name, levels in self._rubric.anchors.items()
        }

        payload: dict[str, Any] = {
            "rubric": {
                "rubric_id": self._rubric.rubric_id,
                "rubric_version": self._rubric.rubric_version,
                "prompt_version": self._rubric.prompt_version,
                "judge_model": self._rubric.judge_model,
                "weights": self._rubric.weights.model_dump(mode="json"),
                "anchors": anchors,
            },
            "evidence": self._build_evidence_payload(evidence),
            "output_schema": schema_description,
        }

        user_content_text = self._build_user_content(payload)

        if retry_hint == "schema_fix":
            retry_content = (
                "\n\nThe previous output was invalid. "
                "Return strict JSON only, no extra text, no markdown fences."
            )
            user_content_text = self._truncate_user_content_with_suffix(
                content=user_content_text,
                suffix=retry_content,
            )

        system_content = (
            "You are a strict CAD evaluation judge. "
            "Your output must be valid JSON and must satisfy the requested schema."
        )
        user_content = self._build_user_message_content(
            user_content_text=user_content_text,
            preview_images=evidence.preview_images,
            force_text_only=force_text_only,
        )

        return [
            LLMMessage(role="system", content=system_content),
            LLMMessage(role="user", content=user_content),
        ]

    def _build_user_content(self, payload: dict[str, Any]) -> str:
        prefix = (
            "Evaluate the CAD modeling quality based on the provided rubric and evidence. "
            "Use evidence.requirement_text as the primary intent source for requirement_fidelity. "
            "Use attached preview images when present as the primary geometric evidence. "
            "If requirement_text is empty, infer intent from code only with lower confidence. "
            "Return ONLY one valid JSON object that strictly matches output_schema. "
            "Do not include markdown or explanations outside JSON.\n\n"
        )
        compact_payload = json.loads(json.dumps(payload, ensure_ascii=True))
        user_content = prefix + self._serialize_payload(compact_payload)
        if len(user_content) <= self._max_prompt_chars:
            return user_content

        evidence = compact_payload.get("evidence", {})
        if isinstance(evidence, dict):
            self._shrink_text_field(evidence, "stderr_excerpt", 512)
            self._shrink_text_field(evidence, "code_excerpt", 2048)
            self._shrink_text_field(evidence, "requirement_text", 2048)
            self._shrink_text_field(evidence, "request_prompt", 2048)

        user_content = prefix + self._serialize_payload(compact_payload)
        if len(user_content) <= self._max_prompt_chars:
            return user_content

        if isinstance(evidence, dict):
            self._shrink_text_field(evidence, "code_excerpt", 512)
            self._shrink_text_field(evidence, "requirement_text", 512)
            self._shrink_text_field(evidence, "request_prompt", 512)

        user_content = prefix + self._serialize_payload(compact_payload)
        if len(user_content) <= self._max_prompt_chars:
            return user_content

        rubric = compact_payload.get("rubric", {})
        if isinstance(rubric, dict):
            rubric["anchors"] = {}
        user_content = prefix + self._serialize_payload(compact_payload)
        if len(user_content) <= self._max_prompt_chars:
            return user_content

        if isinstance(evidence, dict):
            minimal_evidence = {
                "execution_success": evidence.get("execution_success"),
                "execution_error_code": evidence.get("execution_error_code"),
                "has_step": evidence.get("has_step"),
                "step_filename": evidence.get("step_filename"),
                "step_size_bytes": evidence.get("step_size_bytes"),
                "preview_image_count": evidence.get("preview_image_count"),
                "preview_images": evidence.get("preview_images"),
                "rubric_version": evidence.get("rubric_version"),
                "prompt_version": evidence.get("prompt_version"),
                "judge_model": evidence.get("judge_model"),
                "evaluator_version": evidence.get("evaluator_version"),
                "evaluation_trace_id": evidence.get("evaluation_trace_id"),
                "replay_key": evidence.get("replay_key"),
            }
            compact_payload["evidence"] = minimal_evidence

        user_content = prefix + self._serialize_payload(compact_payload)
        if len(user_content) <= self._max_prompt_chars:
            return user_content

        return user_content[: self._max_prompt_chars]

    def _build_evidence_payload(self, evidence: EvidenceBundle) -> dict[str, Any]:
        payload = evidence.model_dump(mode="json", exclude={"preview_images"})
        payload["preview_images"] = [
            self._to_preview_metadata(image) for image in evidence.preview_images
        ]
        payload["preview_image_count"] = len(evidence.preview_images)
        return payload

    def _to_preview_metadata(self, image: EvidenceImage) -> dict[str, Any]:
        return {
            "filename": image.filename,
            "mime_type": image.mime_type,
            "size_bytes": image.size_bytes,
            "sha256": image.sha256,
        }

    def _build_user_message_content(
        self,
        user_content_text: str,
        preview_images: list[EvidenceImage],
        force_text_only: bool = False,
    ) -> str | list[LLMTextContent | LLMImageContent]:
        if force_text_only or not self._supports_multimodal() or not preview_images:
            return user_content_text

        content: list[LLMTextContent | LLMImageContent] = [
            LLMTextContent(text=user_content_text)
        ]
        for image in preview_images:
            content.append(
                LLMImageContent(
                    mime_type=image.mime_type,
                    data_base64=image.content_base64,
                )
            )

        return content

    def _supports_multimodal(self) -> bool:
        return bool(getattr(self._llm_client, "supports_multimodal", False))

    def _serialize_payload(self, payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))

    def _truncate_middle(self, text: str, max_chars: int) -> str:
        if max_chars <= 0:
            return ""
        if len(text) <= max_chars:
            return text
        if max_chars <= 16:
            return text[:max_chars]
        head = (max_chars - 3) // 2
        tail = max_chars - 3 - head
        return f"{text[:head]}...{text[-tail:]}"

    def _shrink_text_field(
        self,
        payload: dict[str, Any],
        field_name: str,
        max_chars: int,
    ) -> None:
        value = payload.get(field_name)
        if not isinstance(value, str):
            return
        payload[field_name] = self._truncate_middle(value, max_chars)

    def _truncate_user_content_with_suffix(self, content: str, suffix: str) -> str:
        if len(content) + len(suffix) <= self._max_prompt_chars:
            return content + suffix
        if len(suffix) >= self._max_prompt_chars:
            return suffix[: self._max_prompt_chars]
        allowed_content = self._max_prompt_chars - len(suffix)
        return content[:allowed_content] + suffix

    def _get_cached_result(self, replay_key: str) -> LLMJudgeEvaluationResult | None:
        if self._cache_size <= 0:
            return None
        cached = self._result_cache.get(replay_key)
        if cached is None:
            return None
        self._result_cache.move_to_end(replay_key)
        return cached.model_copy(deep=True)

    def _set_cached_result(
        self,
        replay_key: str,
        result: LLMJudgeEvaluationResult,
    ) -> None:
        if self._cache_size <= 0:
            return
        self._result_cache[replay_key] = result.model_copy(deep=True)
        self._result_cache.move_to_end(replay_key)
        while len(self._result_cache) > self._cache_size:
            self._result_cache.popitem(last=False)

    def _parse_judge_output(self, raw_content: str) -> LLMJudgeParsedOutput | None:
        for candidate in self._json_candidates(raw_content):
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                continue

            if not isinstance(payload, dict):
                continue

            try:
                return LLMJudgeParsedOutput.model_validate(payload)
            except Exception:
                continue

        return None

    def _json_candidates(self, text: str) -> list[str]:
        candidates: list[str] = []

        stripped = text.strip()
        if stripped:
            candidates.append(stripped)

        for match in _JSON_FENCE_PATTERN.findall(text):
            candidate = match.strip()
            if candidate:
                candidates.append(candidate)

        balanced = self._extract_balanced_json_object(text)
        if balanced:
            candidates.append(balanced)

        deduped: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            deduped.append(candidate)
            seen.add(candidate)

        return deduped

    def _extract_balanced_json_object(self, text: str) -> str | None:
        start = text.find("{")
        if start < 0:
            return None

        depth = 0
        in_string = False
        escaped = False

        for index in range(start, len(text)):
            char = text[index]

            if in_string:
                if escaped:
                    escaped = False
                    continue
                if char == "\\":
                    escaped = True
                    continue
                if char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
                continue
            if char == "{":
                depth += 1
                continue
            if char == "}":
                depth -= 1
                if depth == 0:
                    return text[start : index + 1].strip()

        return None

    def _weighted_sum(self, parsed: LLMJudgeParsedOutput) -> float:
        weights = self._rubric.weights
        scores = parsed.dimension_scores

        weighted = (
            scores.requirement_fidelity * weights.requirement_fidelity
            + scores.geometric_reasonableness * weights.geometric_reasonableness
            + scores.manufacturability_proxy * weights.manufacturability_proxy
            + scores.code_quality_proxy * weights.code_quality_proxy
            + scores.execution_stability * weights.execution_stability
        )

        return round(max(0.0, min(1.0, weighted)), 6)
