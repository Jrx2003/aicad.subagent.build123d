from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from common.logging import get_logger
from llm.interface import LLMClient, LLMMessage
from sandbox_mcp_server.contracts import (
    ActionHistoryEntry,
    RequirementClauseInterpretation,
    RequirementClauseStatus,
)
from sandbox_mcp_server.validation_evidence import RequirementEvidenceBundle

logger = get_logger(__name__)

_JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class ValidationLLMClauseDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clause_id: str = Field(...)
    status: RequirementClauseStatus = Field(...)
    evidence: str = Field(default="")
    decision_hints: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ValidationLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(default="")
    clauses: list[ValidationLLMClauseDecision] = Field(default_factory=list)


class ValidationLLMAdjudicator:
    def __init__(
        self,
        llm_client: LLMClient,
        *,
        max_prompt_chars: int = 12000,
        max_response_tokens: int = 1200,
        request_timeout_seconds: float = 45.0,
    ) -> None:
        self._llm_client = llm_client
        self._max_prompt_chars = max_prompt_chars
        self._max_response_tokens = max_response_tokens
        self._request_timeout_seconds = max(0.1, float(request_timeout_seconds))

    async def adjudicate(
        self,
        *,
        requirement_text: str,
        bundle: RequirementEvidenceBundle,
        clauses: list[RequirementClauseInterpretation],
        history: list[ActionHistoryEntry],
    ) -> ValidationLLMOutput | None:
        unresolved_clauses = [
            clause
            for clause in clauses
            if clause.status == RequirementClauseStatus.INSUFFICIENT_EVIDENCE
        ]
        if not unresolved_clauses:
            return None
        messages = self._build_messages(
            requirement_text=requirement_text,
            bundle=bundle,
            clauses=clauses,
            unresolved_clauses=unresolved_clauses,
            history=history,
            retry_hint=None,
        )
        for attempt_index in range(2):
            try:
                response = await asyncio.wait_for(
                    self._llm_client.complete(
                        messages=messages,
                        temperature=0.0,
                        max_tokens=self._max_response_tokens,
                    ),
                    timeout=self._request_timeout_seconds,
                )
            except Exception as exc:
                logger.warning(
                    "validation_llm_provider_error",
                    reason=str(exc),
                    exc_info=True,
                )
                return None
            parsed = self._parse_output(response.content)
            if parsed is not None:
                return parsed
            if attempt_index == 0:
                messages = self._build_messages(
                    requirement_text=requirement_text,
                    bundle=bundle,
                    clauses=clauses,
                    unresolved_clauses=unresolved_clauses,
                    history=history,
                    retry_hint="schema_fix",
                )
        logger.warning("validation_llm_invalid_output")
        return None

    def _build_messages(
        self,
        *,
        requirement_text: str,
        bundle: RequirementEvidenceBundle,
        clauses: list[RequirementClauseInterpretation],
        unresolved_clauses: list[RequirementClauseInterpretation],
        history: list[ActionHistoryEntry],
        retry_hint: str | None,
    ) -> list[LLMMessage]:
        payload = {
            "requirement_text": requirement_text,
            "unresolved_clauses": [
                {
                    "clause_id": clause.clause_id,
                    "clause_text": clause.clause_text,
                    "current_status": clause.status.value,
                    "current_evidence": clause.evidence,
                    "observation_tags": clause.observation_tags,
                    "decision_hints": clause.decision_hints,
                }
                for clause in unresolved_clauses
            ],
            "all_clause_summaries": [
                {
                    "clause_id": clause.clause_id,
                    "clause_text": clause.clause_text,
                    "status": clause.status.value,
                    "evidence": clause.evidence,
                }
                for clause in clauses
            ],
            "geometry_facts": self._compact_value(bundle.geometry_facts, max_list_items=12),
            "topology_facts": self._compact_value(bundle.topology_facts, max_list_items=12),
            "process_facts": self._compact_value(bundle.process_facts, max_list_items=12),
            "latest_code_excerpt": self._latest_code_excerpt(history),
            "output_schema": {
                "summary": "string",
                "clauses": [
                    {
                        "clause_id": "string",
                        "status": "verified|contradicted|insufficient_evidence",
                        "evidence": "string",
                        "decision_hints": ["string"],
                        "confidence": "float in [0,1]",
                    }
                ],
            },
        }
        prompt = (
            "You are a strict CAD validation adjudicator. "
            "Resolve ONLY the listed unresolved clauses using the supplied evidence. "
            "Do not infer geometry from likely intent. "
            "Do not mark localized coordinate, direction, clearance, union, or host-relation clauses as verified "
            "unless the geometry/topology facts explicitly contain matching anchors or topology evidence. "
            "If the evidence is still weak, keep the clause as insufficient_evidence. "
            "Prefer contradicted only when the supplied evidence clearly conflicts with the clause. "
            "Return strict JSON only.\n\n"
            + json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        )
        if len(prompt) > self._max_prompt_chars:
            payload["topology_facts"] = self._compact_value(bundle.topology_facts, max_list_items=4)
            payload["latest_code_excerpt"] = payload["latest_code_excerpt"][:1200]
            prompt = (
                "You are a strict CAD validation adjudicator. "
                "Resolve ONLY the listed unresolved clauses using the supplied evidence. "
                "Do not infer geometry from likely intent. "
                "Do not mark localized coordinate, direction, clearance, union, or host-relation clauses as verified "
                "unless the geometry/topology facts explicitly contain matching anchors or topology evidence. "
                "Return strict JSON only.\n\n"
                + json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
            )
        if retry_hint == "schema_fix":
            prompt += "\n\nThe previous output was invalid. Return exactly one JSON object and nothing else."
        return [
            LLMMessage(
                role="system",
                content=(
                    "You are a CAD validation model. "
                    "Your output must be valid JSON and must satisfy the requested schema."
                ),
            ),
            LLMMessage(role="user", content=prompt),
        ]

    def _parse_output(self, raw_content: str) -> ValidationLLMOutput | None:
        candidates = [raw_content]
        fenced = _JSON_FENCE_PATTERN.search(raw_content)
        if fenced is not None:
            candidates.insert(0, fenced.group(1))
        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except Exception:
                continue
            try:
                return ValidationLLMOutput.model_validate(payload)
            except Exception:
                continue
        return None

    def _latest_code_excerpt(self, history: list[ActionHistoryEntry]) -> str:
        for entry in reversed(history):
            action_params = entry.action_params if isinstance(entry.action_params, dict) else {}
            for key in ("build123d_code", "cad_code", "code"):
                value = action_params.get(key)
                if isinstance(value, str) and value.strip():
                    return value[:2400]
        return ""

    def _compact_value(
        self,
        value: Any,
        *,
        max_depth: int = 4,
        max_list_items: int = 8,
    ) -> Any:
        if max_depth <= 0:
            return "<truncated>"
        if isinstance(value, dict):
            return {
                str(key): self._compact_value(
                    item,
                    max_depth=max_depth - 1,
                    max_list_items=max_list_items,
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [
                self._compact_value(
                    item,
                    max_depth=max_depth - 1,
                    max_list_items=max_list_items,
                )
                for item in value[:max_list_items]
            ]
        if isinstance(value, str):
            return value[:1000]
        return value
