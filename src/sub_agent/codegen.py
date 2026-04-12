import asyncio
import base64
import binascii
import json
import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from common.feature_agenda import build_feature_agenda
from common.logging import get_logger
from sandbox_mcp_server.registry import (
    get_supported_action_types,
    render_capability_cards,
    render_inspection_cards,
    render_library_card,
    render_sketch_card,
    render_topology_card,
    select_exposure_bundle_ids,
)
from llm.factory import create_provider_client, create_tiered_llm_client
from llm.interface import LLMImageContent, LLMMessage, LLMTextContent, LLMTier
from sub_agent.prompts import load_prompt

if TYPE_CHECKING:
    from common.config import Settings

logger = get_logger(__name__)

LEGACY_SYSTEM_PROMPT = load_prompt("codegen")
ACI_SYSTEM_PROMPT = load_prompt("codegen_action")

_MAX_HISTORY_ITEMS = 8
_MAX_ACTIONS_PER_ROUND = 5
_MAX_RECONSTRUCTED_ACTIONS = 20
_MAX_PROMPT_GEOMETRY_ITEMS_PER_TYPE = 6
_MAX_PROMPT_MATCHED_ENTITY_IDS = 24
_MAX_PROMPT_CHECKS = 12
_MAX_PROMPT_SUGGESTIONS = 6
_MAX_PROMPT_OUTPUT_FILES = 12
_MAX_PROMPT_FEATURES = 10
_MAX_PROMPT_ISSUES = 8
_MAX_PROMPT_TOPOLOGY_ITEMS_PER_TYPE = 8
_MAX_PROMPT_TOPOLOGY_CANDIDATE_SETS = 10
_MAX_PROMPT_TOPOLOGY_CANDIDATE_REFS = 12
_MAX_PROMPT_RELATION_ENTITIES = 10
_MAX_PROMPT_RELATIONS = 12
_MAX_PROMPT_RELATION_GROUPS = 8
_MAX_PROMPT_RELATION_FOCUS_ITEMS = 8
_MAX_PROMPT_RELATION_EVAL_ITEMS = 8
_MAX_LLM_RETRY_ATTEMPTS = 3
_ROUND_REQUEST_PROMPT_CHAR_BUDGET = 36000
_ROUND_REQUEST_PROMPT_HARD_LIMIT = 52000
_ROUND_REQUEST_TRACE_LINES = 24
_ROUND_REQUEST_TRACE_CHARS = 2400
_SUPPORTED_ACTION_TYPES = set(get_supported_action_types())
_STEP_LOCAL_REF_RE = re.compile(r"^(face|edge|path|profile):(\d+):(.+)$")


@dataclass
class CodeGenResult:
    """Build123d code generation result."""

    code: str
    usage: dict[str, int] | None


@dataclass
class ActionGenResult:
    """ACI action generation result."""

    actions: list[dict[str, Any]]
    usage: dict[str, int] | None
    raw_content: str
    inspection: dict[str, Any] | None = None
    planner_note: str | None = None
    expected_outcome: dict[str, Any] | None = None
    prompt_text: str | None = None
    prompt_metadata: dict[str, Any] | None = None


class CodeGenerator:
    """LLM-based generator for both legacy code and ACI action planning."""

    def __init__(self, settings: "Settings"):
        self._settings = settings

    async def generate(
        self,
        requirements: dict[str, Any],
        previous_error: str | None = None,
    ) -> CodeGenResult:
        """Generate full Build123d code (legacy compatibility path)."""
        client = create_tiered_llm_client(LLMTier.REASONING, self._settings)
        user_content = self._build_legacy_user_prompt(requirements, previous_error)
        messages = [
            LLMMessage(role="system", content=LEGACY_SYSTEM_PROMPT),
            LLMMessage(role="user", content=user_content),
        ]

        response = await client.complete(
            messages=messages,
            temperature=0.2,
            max_tokens=2000,
        )
        code = self._extract_code(response.content)

        logger.info(
            "legacy_code_generation_complete",
            code_length=len(code),
            usage=response.usage,
        )
        return CodeGenResult(code=code, usage=response.usage)

    async def generate_actions(
        self,
        requirements: dict[str, Any],
        action_history: list[dict[str, Any]] | None = None,
        suggestions: list[str] | None = None,
        completeness: dict[str, Any] | None = None,
        query_snapshot: dict[str, Any] | None = None,
        query_sketch: dict[str, Any] | None = None,
        query_geometry: dict[str, Any] | None = None,
        query_topology: dict[str, Any] | None = None,
        requirement_validation: dict[str, Any] | None = None,
        render_view: dict[str, Any] | None = None,
        evidence_status: dict[str, Any] | None = None,
        relation_focus: dict[str, Any] | None = None,
        relation_eval: dict[str, Any] | None = None,
        active_surface: dict[str, Any] | None = None,
        surface_policy: dict[str, Any] | None = None,
        expected_outcome: dict[str, Any] | None = None,
        outcome_delta: dict[str, Any] | None = None,
        round_budget: dict[str, Any] | None = None,
        latest_action_result: dict[str, Any] | None = None,
        latest_unresolved_blockers: list[str] | None = None,
        previous_error: str | None = None,
    ) -> ActionGenResult:
        """Generate the next ACI action batch based on current CAD feedback."""
        client = create_tiered_llm_client(LLMTier.REASONING, self._settings)
        user_content = self._build_action_user_prompt(
            requirements=requirements,
            action_history=action_history or [],
            suggestions=suggestions or [],
            completeness=completeness,
            query_snapshot=query_snapshot,
            query_sketch=query_sketch,
            query_geometry=query_geometry,
            query_topology=query_topology,
            requirement_validation=requirement_validation,
            render_view=render_view,
            evidence_status=evidence_status,
            relation_focus=relation_focus,
            relation_eval=relation_eval,
            active_surface=active_surface,
            surface_policy=surface_policy,
            expected_outcome=expected_outcome,
            outcome_delta=outcome_delta,
            round_budget=round_budget,
            latest_action_result=latest_action_result,
            latest_unresolved_blockers=latest_unresolved_blockers,
            previous_error=previous_error,
        )
        messages = self._build_action_messages(
            user_content=user_content,
            render_view=render_view,
            client=client,
        )
        logger.info(
            "aci_action_prompt_composed",
            provider=self._settings.llm_reasoning_provider,
            model=self._settings.llm_reasoning_model,
            render_image_attached=self._message_has_image(messages),
        )
        prompt_metadata = {
            "provider": self._settings.llm_reasoning_provider,
            "model": self._settings.llm_reasoning_model,
            "render_image_attached": self._message_has_image(messages),
            "user_prompt_chars": len(user_content),
            "user_prompt_lines": len(user_content.splitlines()),
        }

        response = await self._complete_with_retries(
            client=client,
            messages=messages,
            temperature=0.2,
            max_tokens=self._action_generation_max_tokens(),
        )
        actions, inspection, planner_note, expected_outcome = self._extract_action_plan(
            response.content
        )
        repaired_response = await self._repair_underfilled_mixed_hole_layout_plan(
            client=client,
            messages=messages,
            temperature=0.2,
            max_tokens=self._action_generation_max_tokens(),
            requirements=requirements,
            query_geometry=query_geometry,
            actions=actions,
        )
        if repaired_response is not None:
            response = repaired_response
            actions, inspection, planner_note, expected_outcome = self._extract_action_plan(
                response.content
            )

        logger.info(
            "aci_action_generation_complete",
            action_count=len(actions),
            inspection_enabled=inspection is not None,
            planner_note_present=isinstance(planner_note, str),
            usage=response.usage,
        )
        return ActionGenResult(
            actions=actions,
            inspection=inspection,
            planner_note=planner_note,
            expected_outcome=expected_outcome,
            usage=response.usage,
            raw_content=response.content,
            prompt_text=user_content,
            prompt_metadata=prompt_metadata,
        )

    async def _repair_underfilled_mixed_hole_layout_plan(
        self,
        *,
        client: Any,
        messages: list[LLMMessage],
        temperature: float,
        max_tokens: int | None,
        requirements: dict[str, Any],
        query_geometry: dict[str, Any] | None,
        actions: list[dict[str, Any]],
    ) -> Any | None:
        if not self._plan_needs_mixed_hole_layout_repair(
            requirements=requirements,
            query_geometry=query_geometry,
            actions=actions,
        ):
            return None
        repair_messages = [
            *messages,
            LLMMessage(
                role="user",
                content=(
                    "The current plan only covers one circle family on a face that still "
                    "requires both a central cut and a secondary bolt-circle/distributed-hole family. "
                    "Return the same-face circle-builder window instead: keep create_sketch(face_ref) "
                    "if needed, then add every required circle family on that face before the later cut. "
                    "Do not split this mixed-diameter layout into sequential direct hole actions."
                ),
            ),
        ]
        return await self._complete_with_retries(
            client=client,
            messages=repair_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def _plan_needs_mixed_hole_layout_repair(
        self,
        *,
        requirements: dict[str, Any],
        query_geometry: dict[str, Any] | None,
        actions: list[dict[str, Any]],
    ) -> bool:
        if not actions:
            return False
        solid_count = 0
        if isinstance(query_geometry, dict):
            geometry = query_geometry.get("geometry")
            if isinstance(geometry, dict):
                raw_solids = geometry.get("solids")
                if isinstance(raw_solids, (int, float)):
                    solid_count = int(raw_solids)
        if solid_count <= 0:
            return False
        if not self._requirements_describe_mixed_face_circle_cut_layout(requirements):
            return False
        circle_builder_count = 0
        has_direct_hole = False
        for action in actions:
            if not isinstance(action, dict):
                continue
            action_type_raw = action.get("action_type")
            action_type = (
                action_type_raw.strip().lower()
                if isinstance(action_type_raw, str)
                else ""
            )
            if action_type == "hole":
                has_direct_hole = True
                circle_builder_count += 1
                continue
            if action_type != "add_circle":
                continue
            params = action.get("action_params")
            action_params = params if isinstance(params, dict) else {}
            if bool(action_params.get("construction")):
                continue
            circle_builder_count += 1
        return has_direct_hole or circle_builder_count < 2

    def _requirements_describe_mixed_face_circle_cut_layout(
        self,
        requirements: dict[str, Any] | None,
    ) -> bool:
        if not isinstance(requirements, dict):
            return False
        text = str(requirements.get("description", "") or "").strip().lower()
        if not text:
            return False
        has_central_family = any(
            token in text
            for token in (
                "central circle",
                "central hole",
                "center circle",
                "center hole",
            )
        )
        has_pattern_family = any(
            token in text
            for token in (
                "bolt circle",
                "pitch circle",
                "distributed circle",
                "circular array",
                "circular pattern",
                "evenly distributed holes",
            )
        )
        return has_central_family and has_pattern_family

    def build_round_request_evidence(
        self,
        requirements: dict[str, Any],
        action_history: list[dict[str, Any]] | None = None,
        suggestions: list[str] | None = None,
        completeness: dict[str, Any] | None = None,
        query_snapshot: dict[str, Any] | None = None,
        query_sketch: dict[str, Any] | None = None,
        query_geometry: dict[str, Any] | None = None,
        query_topology: dict[str, Any] | None = None,
        requirement_validation: dict[str, Any] | None = None,
        render_view: dict[str, Any] | None = None,
        evidence_status: dict[str, Any] | None = None,
        relation_focus: dict[str, Any] | None = None,
        relation_eval: dict[str, Any] | None = None,
        active_surface: dict[str, Any] | None = None,
        surface_policy: dict[str, Any] | None = None,
        expected_outcome: dict[str, Any] | None = None,
        outcome_delta: dict[str, Any] | None = None,
        round_budget: dict[str, Any] | None = None,
        latest_action_result: dict[str, Any] | None = None,
        latest_unresolved_blockers: list[str] | None = None,
        previous_error: str | None = None,
    ) -> dict[str, Any]:
        planner_query_snapshot = self._planner_current_evidence_payload(
            tool_name="query_snapshot",
            payload=query_snapshot,
            evidence_status=evidence_status,
        )
        planner_query_sketch = self._planner_current_evidence_payload(
            tool_name="query_sketch",
            payload=query_sketch,
            evidence_status=evidence_status,
        )
        planner_query_geometry = self._planner_current_evidence_payload(
            tool_name="query_geometry",
            payload=query_geometry,
            evidence_status=evidence_status,
        )
        planner_query_topology = self._planner_current_evidence_payload(
            tool_name="query_topology",
            payload=query_topology,
            evidence_status=evidence_status,
        )
        planner_requirement_validation = self._planner_current_evidence_payload(
            tool_name="validate_requirement",
            payload=requirement_validation,
            evidence_status=evidence_status,
        )
        planner_render_view = self._planner_current_evidence_payload(
            tool_name="render_view",
            payload=render_view,
            evidence_status=evidence_status,
        )
        history_slice = self._compact_action_history_for_prompt(
            action_history=(action_history or [])[-_MAX_HISTORY_ITEMS:]
        )
        compact_query_snapshot = self._compact_query_snapshot_for_prompt(planner_query_snapshot)
        compact_query_sketch = self._compact_query_sketch_for_prompt(planner_query_sketch)
        compact_query_geometry = self._compact_query_geometry_for_prompt(planner_query_geometry)
        compact_query_topology = self._compact_query_topology_for_prompt(planner_query_topology)
        compact_requirement_validation = (
            self._compact_requirement_validation_for_prompt(planner_requirement_validation)
        )
        sanitized_render_view = self._sanitize_render_view_for_prompt(planner_render_view)
        compact_evidence_status = self._compact_evidence_status_for_prompt(
            evidence_status
        )
        compact_relation_focus = self._compact_relation_focus_for_prompt(relation_focus)
        compact_relation_eval = self._compact_relation_eval_for_prompt(relation_eval)
        compact_active_surface = self._compact_active_surface_for_prompt(active_surface)
        compact_surface_policy = self._compact_surface_policy_for_prompt(surface_policy)
        compact_expected_outcome = self._compact_expected_outcome_for_prompt(
            expected_outcome
        )
        compact_outcome_delta = self._compact_outcome_delta_for_prompt(outcome_delta)
        compact_feature_agenda = self._compact_feature_agenda_for_prompt(
            build_feature_agenda(
                requirements=requirements,
                action_history=action_history,
            )
        )
        compact_latest_action_result = self._compact_latest_action_result_for_prompt(
            latest_action_result
        )
        stale_evidence = self._build_stale_evidence_summary(
            evidence_status=evidence_status,
            query_snapshot=query_snapshot,
            query_sketch=query_sketch,
            query_geometry=query_geometry,
            query_topology=query_topology,
            requirement_validation=requirement_validation,
            render_view=render_view,
        )
        latest_evidence_step = self._latest_evidence_step(evidence_status)
        reconstructed_trace = self._build_reconstructed_action_trace(
            action_history=(action_history or [])[-_MAX_RECONSTRUCTED_ACTIONS:],
            latest_step=latest_evidence_step,
        )
        reconstructed_code = self._build_reconstructed_build123d_code(
            action_history=(action_history or [])[-_MAX_RECONSTRUCTED_ACTIONS:]
        )
        exposure_bundle_ids = select_exposure_bundle_ids(
            requirements=requirements,
            action_history=history_slice,
            completeness=completeness,
            query_geometry=compact_query_geometry,
            query_topology=compact_query_topology,
            requirement_validation=compact_requirement_validation,
            latest_unresolved_blockers=latest_unresolved_blockers,
            previous_error=previous_error,
        )
        exposure_bundle_ids = self._refine_exposure_bundle_ids_for_round(
            bundle_ids=exposure_bundle_ids,
            active_surface=compact_active_surface,
            surface_policy=compact_surface_policy,
            query_geometry=compact_query_geometry,
            action_history=history_slice,
        )
        return {
            "requirements": requirements,
            "history_entries": len(history_slice),
            "action_history": history_slice,
            "query_snapshot": compact_query_snapshot,
            "query_sketch": compact_query_sketch,
            "query_geometry": compact_query_geometry,
            "query_topology": compact_query_topology,
            "requirement_validation": compact_requirement_validation,
            "render_view": sanitized_render_view,
            "evidence_status": compact_evidence_status,
            "relation_focus": compact_relation_focus,
            "relation_eval": compact_relation_eval,
            "active_surface": compact_active_surface,
            "surface_policy": compact_surface_policy,
            "expected_outcome": compact_expected_outcome,
            "outcome_delta": compact_outcome_delta,
            "feature_agenda": compact_feature_agenda,
            "round_budget": (
                round_budget if isinstance(round_budget, dict) else {}
            ),
            "stale_evidence": stale_evidence,
            "latest_action_result": compact_latest_action_result,
            "latest_unresolved_blockers": self._normalize_string_list(
                latest_unresolved_blockers,
                limit=_MAX_PROMPT_CHECKS,
            ),
            "previous_error": previous_error,
            "reconstructed_action_trace": reconstructed_trace,
            "reconstructed_build123d_code": reconstructed_code,
            "exposure_bundle_ids": exposure_bundle_ids,
        }

    def _round_state_mode(
        self,
        active_surface: dict[str, Any] | None,
        surface_policy: dict[str, Any] | None,
        query_geometry: dict[str, Any] | None,
        action_history: list[dict[str, Any]] | None = None,
    ) -> str | None:
        for payload in (active_surface, surface_policy):
            if not isinstance(payload, dict):
                continue
            state_mode = payload.get("state_mode")
            if isinstance(state_mode, str) and state_mode.strip():
                return state_mode.strip().lower()
        geometry_payload = (
            query_geometry.get("geometry") if isinstance(query_geometry, dict) else None
        )
        if isinstance(geometry_payload, dict):
            solids = geometry_payload.get("solids")
            if isinstance(solids, (int, float)) and int(solids) > 0:
                return "post_solid"
        if self._history_implies_solid(action_history):
            return "post_solid"
        return None

    def _history_implies_solid(
        self,
        action_history: list[dict[str, Any]] | None,
    ) -> bool:
        solid_transition_actions = {
            "extrude",
            "revolve",
            "loft",
            "sweep",
            "cut_extrude",
            "trim_solid",
            "hole",
            "sphere_recess",
            "fillet",
            "chamfer",
            "pattern_linear",
            "pattern_circular",
        }
        for item in reversed(action_history or []):
            if not isinstance(item, dict):
                continue
            action_type = str(item.get("action_type") or "").strip().lower()
            if action_type in solid_transition_actions:
                return True
        return False

    def _round_has_solid(
        self,
        active_surface: dict[str, Any] | None,
        surface_policy: dict[str, Any] | None,
        query_geometry: dict[str, Any] | None,
        action_history: list[dict[str, Any]] | None = None,
    ) -> bool:
        state_mode = self._round_state_mode(
            active_surface=active_surface,
            surface_policy=surface_policy,
            query_geometry=query_geometry,
            action_history=action_history,
        )
        if state_mode == "post_solid":
            return True
        geometry_payload = (
            query_geometry.get("geometry") if isinstance(query_geometry, dict) else None
        )
        if isinstance(geometry_payload, dict):
            solids = geometry_payload.get("solids")
            if isinstance(solids, (int, float)):
                return int(solids) > 0
        return False

    def _refine_exposure_bundle_ids_for_round(
        self,
        *,
        bundle_ids: list[str] | None,
        active_surface: dict[str, Any] | None,
        surface_policy: dict[str, Any] | None,
        query_geometry: dict[str, Any] | None,
        action_history: list[dict[str, Any]] | None = None,
    ) -> list[str]:
        deduped = [
            bundle_id
            for bundle_id in (bundle_ids or [])
            if isinstance(bundle_id, str) and bundle_id.strip()
        ]
        if not deduped:
            return []

        state_mode = self._round_state_mode(
            active_surface=active_surface,
            surface_policy=surface_policy,
            query_geometry=query_geometry,
            action_history=action_history,
        )
        has_solid = self._round_has_solid(
            active_surface=active_surface,
            surface_policy=surface_policy,
            query_geometry=query_geometry,
            action_history=action_history,
        )
        allowed_actions = {
            str(action).strip().lower()
            for action in (
                surface_policy.get("allowed_actions")
                if isinstance(surface_policy, dict)
                and isinstance(surface_policy.get("allowed_actions"), list)
                else []
            )
            if isinstance(action, str) and action.strip()
        }

        def _keep(bundle_id: str) -> bool:
            if bundle_id in {"inspection_tools", "repair_state"}:
                return True
            if bundle_id == "bootstrap_sketch":
                return state_mode == "pre_solid" or not has_solid
            if bundle_id == "face_attached_sketch":
                if not has_solid:
                    return False
                if not allowed_actions:
                    return True
                return bool(
                    allowed_actions
                    & {
                        "create_sketch",
                        "add_rectangle",
                        "add_circle",
                        "add_polygon",
                        "extrude",
                        "cut_extrude",
                    }
                )
            if bundle_id == "subtractive_edit":
                if not has_solid:
                    return False
                if not allowed_actions:
                    return True
                return bool(
                    allowed_actions
                    & {
                        "cut_extrude",
                        "trim_solid",
                        "hole",
                        "sphere_recess",
                        "revolve",
                    }
                )
            if bundle_id == "spherical_face_edit":
                return has_solid and (
                    not allowed_actions or "sphere_recess" in allowed_actions
                )
            if bundle_id == "feature_patterns":
                if not has_solid:
                    return False
                if not allowed_actions:
                    return True
                return any(action.startswith("pattern_") for action in allowed_actions)
            if bundle_id == "edge_ref_features":
                return has_solid and (
                    not allowed_actions
                    or bool(allowed_actions & {"fillet", "chamfer"})
                )
            if bundle_id == "path_sweep":
                return not allowed_actions or bool(
                    allowed_actions & {"add_path", "sweep", "create_sketch"}
                )
            if bundle_id == "loft_profile_stack":
                return not allowed_actions or "loft" in allowed_actions
            if bundle_id == "inner_void_cutout":
                return state_mode == "pre_solid" or not has_solid
            if bundle_id == "orthogonal_additive_union":
                return state_mode == "pre_solid" or not has_solid
            if bundle_id == "revolved_groove_cut":
                return has_solid and (
                    not allowed_actions
                    or bool(allowed_actions & {"revolve", "cut_extrude"})
                )
            return True

        return [bundle_id for bundle_id in deduped if _keep(bundle_id)]

    def _should_render_library_card(
        self,
        bundle_ids: list[str],
    ) -> bool:
        complex_bundles = {
            "path_sweep",
            "loft_profile_stack",
            "inner_void_cutout",
            "orthogonal_additive_union",
            "revolved_groove_cut",
            "repair_state",
        }
        return any(bundle_id in complex_bundles for bundle_id in bundle_ids)

    def _planner_current_evidence_payload(
        self,
        tool_name: str,
        payload: dict[str, Any] | None,
        evidence_status: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        if not self._evidence_tool_is_current(
            evidence_status=evidence_status,
            tool_name=tool_name,
        ):
            return None
        return payload

    def _evidence_tool_is_current(
        self,
        evidence_status: dict[str, Any] | None,
        tool_name: str,
    ) -> bool:
        if not isinstance(evidence_status, dict):
            return True
        current = evidence_status.get("current")
        if not isinstance(current, dict):
            return True
        if tool_name in current:
            return True
        stale = evidence_status.get("stale")
        if isinstance(stale, dict) and tool_name in stale:
            return False
        return True

    def _build_stale_evidence_summary(
        self,
        evidence_status: dict[str, Any] | None,
        query_snapshot: dict[str, Any] | None,
        query_sketch: dict[str, Any] | None,
        query_geometry: dict[str, Any] | None,
        query_topology: dict[str, Any] | None,
        requirement_validation: dict[str, Any] | None,
        render_view: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(evidence_status, dict):
            return {}
        stale = evidence_status.get("stale")
        if not isinstance(stale, dict) or not stale:
            return {}
        payloads = {
            "query_snapshot": query_snapshot,
            "query_sketch": query_sketch,
            "query_geometry": query_geometry,
            "query_topology": query_topology,
            "validate_requirement": requirement_validation,
            "render_view": render_view,
        }
        summary: dict[str, Any] = {}
        for tool_name, stale_meta in stale.items():
            if not isinstance(stale_meta, dict):
                continue
            item: dict[str, Any] = {
                "step": stale_meta.get("step"),
                "latest_step": stale_meta.get("latest_step"),
                "reason": stale_meta.get("reason"),
            }
            payload = payloads.get(tool_name)
            if isinstance(payload, dict):
                item["success"] = payload.get("success")
                if tool_name == "query_sketch":
                    sketch_state = payload.get("sketch_state")
                    if isinstance(sketch_state, dict):
                        item["issues"] = self._normalize_string_list(
                            sketch_state.get("issues"),
                            limit=8,
                        )
                if tool_name == "validate_requirement":
                    item["blockers"] = self._normalize_string_list(
                        payload.get("blockers"),
                        limit=8,
                    )
            summary[tool_name] = item
        return summary

    async def _complete_with_retries(
        self,
        client: Any,
        messages: list[LLMMessage],
        temperature: float,
        max_tokens: int | None,
    ) -> Any:
        max_attempts = max(
            1, min(int(self._settings.sub_agent_max_retries), _MAX_LLM_RETRY_ATTEMPTS)
        )
        last_exc: Exception | None = None
        attempt_max_tokens = max_tokens
        attempt_messages = list(messages)
        for attempt in range(1, max_attempts + 1):
            try:
                timeout_seconds = self._llm_timeout_seconds_for_request()
                response = await asyncio.wait_for(
                    client.complete(
                        messages=attempt_messages,
                        temperature=temperature,
                        max_tokens=attempt_max_tokens,
                    ),
                    timeout=timeout_seconds,
                )
                self._raise_for_empty_kimi_thinking_response(response)
                return response
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                retryable = self._is_retryable_llm_error(exc)
                if not retryable or attempt >= max_attempts:
                    break
                if self._is_empty_kimi_thinking_response_error(exc) and isinstance(
                    attempt_max_tokens, int
                ):
                    attempt_max_tokens = max(256, int(attempt_max_tokens * 0.75))
                    attempt_messages = [
                        *messages,
                        LLMMessage(
                            role="user",
                            content=(
                                "Return the final JSON action payload now. "
                                "Do not continue hidden reasoning and do not add commentary."
                            ),
                        ),
                    ]
                delay_seconds = min(8.0, float(2 ** (attempt - 1)))
                logger.warning(
                    "aci_action_generation_retry",
                    attempt=attempt,
                    max_attempts=max_attempts,
                    max_tokens=attempt_max_tokens,
                    delay_seconds=delay_seconds,
                    error=f"{exc.__class__.__name__}: {exc}",
                )
                await asyncio.sleep(delay_seconds)
        if last_exc is not None:
            fallback_response = await self._try_kimi_non_thinking_recovery(
                messages=messages,
                temperature=temperature,
                last_exc=last_exc,
            )
            if fallback_response is not None:
                return fallback_response
            raise last_exc
        raise RuntimeError("llm completion failed without explicit exception")

    def _is_retryable_llm_error(self, exc: Exception) -> bool:
        message = f"{exc.__class__.__name__}: {exc}".lower()
        retry_markers = (
            "timeout",
            "timed out",
            "broken pipe",
            "brokenpipe",
            "rate limit",
            "too many requests",
            "429",
            "503",
            "connection",
            "temporary",
            "temporarily unavailable",
            "server error",
            "gateway",
            "kimi_empty_final_content",
        )
        return any(marker in message for marker in retry_markers)

    def _raise_for_empty_kimi_thinking_response(self, response: Any) -> None:
        provider = self._settings.llm_reasoning_provider.strip().lower()
        model = self._settings.llm_reasoning_model.strip().lower()
        if provider != "kimi" or "thinking" not in model:
            return
        content = str(getattr(response, "content", "") or "").strip()
        usage = getattr(response, "usage", None)
        output_tokens = usage.get("output_tokens") if isinstance(usage, dict) else None
        if content or not isinstance(output_tokens, (int, float)) or output_tokens <= 0:
            return
        raise RuntimeError("kimi_empty_final_content")

    def _is_empty_kimi_thinking_response_error(self, exc: Exception) -> bool:
        return "kimi_empty_final_content" in f"{exc.__class__.__name__}: {exc}".lower()

    async def _try_kimi_non_thinking_recovery(
        self,
        *,
        messages: list[LLMMessage],
        temperature: float,
        last_exc: Exception,
    ) -> Any | None:
        provider = self._settings.llm_reasoning_provider.strip().lower()
        model = self._settings.llm_reasoning_model.strip().lower()
        if provider != "kimi" or not model.startswith("kimi-k2.5-thinking"):
            return None
        if not self._is_retryable_llm_error(last_exc):
            return None

        fallback_model = "kimi-k2.5"
        logger.warning(
            "aci_action_generation_fallback_model",
            from_model=self._settings.llm_reasoning_model,
            to_model=fallback_model,
            reason=f"{last_exc.__class__.__name__}: {last_exc}",
        )
        try:
            fallback_client = create_provider_client("kimi", fallback_model, self._settings)
            fallback_messages = [
                *messages,
                LLMMessage(
                    role="user",
                    content=(
                        "Return the final JSON action payload only. "
                        "Do not include hidden reasoning or commentary."
                    ),
                ),
            ]
            timeout_seconds = max(
                120.0,
                float(getattr(self._settings, "llm_timeout_seconds", 60.0)),
            )
            response = await asyncio.wait_for(
                fallback_client.complete(
                    messages=fallback_messages,
                    temperature=temperature,
                    max_tokens=2048,
                ),
                timeout=timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "aci_action_generation_fallback_failed",
                fallback_model=fallback_model,
                error=f"{exc.__class__.__name__}: {exc}",
            )
            return None

        content = str(getattr(response, "content", "") or "").strip()
        if not content:
            logger.warning(
                "aci_action_generation_fallback_empty",
                fallback_model=fallback_model,
            )
            return None
        return response

    def _action_generation_max_tokens(self) -> int | None:
        provider = self._settings.llm_reasoning_provider.strip().lower()
        model = self._settings.llm_reasoning_model.strip().lower()
        if provider == "kimi" and model.startswith("kimi-k2.5-thinking"):
            return None
        if provider == "kimi" and "thinking" in model:
            return None
        if provider == "kimi" and model.startswith("kimi-k2.5"):
            return 2048
        return 1800

    def _build_legacy_user_prompt(
        self,
        requirements: dict[str, Any],
        previous_error: str | None,
    ) -> str:
        parts: list[str] = []
        parts.append("## Requirements\n")
        parts.append(
            f"Description: {requirements.get('description', 'Not specified')}\n"
        )

        if requirements.get("dimensions"):
            parts.append(f"Dimensions: {requirements['dimensions']}\n")

        if requirements.get("features"):
            parts.append(f"Features: {requirements['features']}\n")

        if requirements.get("constraints"):
            parts.append(f"Constraints: {requirements['constraints']}\n")

        if previous_error:
            parts.append("\n## Previous Attempt Failed\n")
            parts.append("The previous code failed with the following error:\n")
            parts.append(f"```\n{previous_error}\n```\n")
            parts.append("\nPlease fix the code to address this error.\n")

        parts.append("\nGenerate the Build123d code:")
        return "".join(parts)

    def _build_action_user_prompt(
        self,
        requirements: dict[str, Any],
        action_history: list[dict[str, Any]],
        suggestions: list[str],
        completeness: dict[str, Any] | None,
        query_snapshot: dict[str, Any] | None = None,
        query_sketch: dict[str, Any] | None = None,
        query_geometry: dict[str, Any] | None = None,
        query_topology: dict[str, Any] | None = None,
        requirement_validation: dict[str, Any] | None = None,
        render_view: dict[str, Any] | None = None,
        evidence_status: dict[str, Any] | None = None,
        relation_focus: dict[str, Any] | None = None,
        relation_eval: dict[str, Any] | None = None,
        active_surface: dict[str, Any] | None = None,
        surface_policy: dict[str, Any] | None = None,
        expected_outcome: dict[str, Any] | None = None,
        outcome_delta: dict[str, Any] | None = None,
        round_budget: dict[str, Any] | None = None,
        latest_action_result: dict[str, Any] | None = None,
        latest_unresolved_blockers: list[str] | None = None,
        previous_error: str | None = None,
    ) -> str:
        round_request = self.build_round_request_evidence(
            requirements=requirements,
            action_history=action_history,
            suggestions=suggestions,
            completeness=completeness,
            query_snapshot=query_snapshot,
            query_sketch=query_sketch,
            query_geometry=query_geometry,
            query_topology=query_topology,
            requirement_validation=requirement_validation,
            render_view=render_view,
            evidence_status=evidence_status,
            relation_focus=relation_focus,
            relation_eval=relation_eval,
            active_surface=active_surface,
            surface_policy=surface_policy,
            expected_outcome=expected_outcome,
            outcome_delta=outcome_delta,
            round_budget=round_budget,
            latest_action_result=latest_action_result,
            latest_unresolved_blockers=latest_unresolved_blockers,
            previous_error=previous_error,
        )
        bundle_ids = select_exposure_bundle_ids(
            requirements=requirements,
            action_history=round_request.get("action_history") or [],
            completeness=round_request.get("completeness"),
            query_geometry=round_request.get("query_geometry"),
            query_topology=round_request.get("query_topology"),
            requirement_validation=round_request.get("requirement_validation"),
            latest_unresolved_blockers=round_request.get("latest_unresolved_blockers"),
            previous_error=previous_error,
        )
        bundle_ids = self._refine_exposure_bundle_ids_for_round(
            bundle_ids=bundle_ids,
            active_surface=round_request.get("active_surface"),
            surface_policy=round_request.get("surface_policy"),
            query_geometry=round_request.get("query_geometry"),
            action_history=round_request.get("action_history"),
        )
        round_request["exposure_bundle_ids"] = bundle_ids
        compact_round_request = self._prune_round_request_for_prompt(round_request)
        capability_cards = render_capability_cards(bundle_ids)
        inspection_cards = render_inspection_cards(bundle_ids)
        sketch_card = render_sketch_card(bundle_ids)
        topology_card = render_topology_card(bundle_ids)
        library_card = (
            render_library_card(bundle_ids)
            if self._should_render_library_card(bundle_ids)
            else []
        )
        prompt: list[str] = [
            "## Task",
            "Plan the next CAD actions for iterative modeling.",
            "",
            "## Round Request",
            "Use this compact state payload as the current truth for planning.",
            "Treat reconstructed_action_trace as the authoritative execution history.",
            "If reconstructed_build123d_code is present, treat it only as a convenience sketch, not ground truth.",
            "```json",
            json.dumps(
                compact_round_request,
                ensure_ascii=True,
                sort_keys=True,
                indent=2,
            ),
            "```",
        ]

        if capability_cards:
            prompt.extend(["", "## Capability Cards", *capability_cards])
        if inspection_cards:
            prompt.extend(inspection_cards)
        if sketch_card:
            prompt.extend(sketch_card)
        if topology_card:
            prompt.extend(topology_card)
        if library_card:
            prompt.extend(library_card)

        if previous_error:
            prompt.extend(
                [
                    "",
                    "## Previous Error",
                    previous_error,
                ]
            )

        prompt.extend(
            [
                "",
                "## Output Rules",
                "Return ONLY JSON, no markdown.",
                "Preferred: {'actions': [...], 'inspection': {...}, 'planner_note': '...'}",
                "Preferred with ReAct contract: {'actions': [...], 'inspection': {...}, 'planner_note': '...', 'expected_outcome': {...}}",
                "Backward-compatible: JSON array of actions.",
                "Each action object MUST use keys: action_type, action_params.",
                "Return a coherent local action window, usually 1 action and at most 5 actions.",
                "Under one_action_per_round the runtime may execute only the leading actionable prefix, so keep returned actions sequential and within one local work window.",
                "Use explicit parameters (e.g., width/height/distance/radius/edge_refs/face_ref).",
                "Use active_surface as the default local work boundary for this round.",
                "Use surface_policy.allowed_actions and surface_policy.required_evidence as the local contract unless current evidence proves a narrower repair path.",
                "Use surface_policy.inspection_partitions to choose the smallest tool lane for the next decision; do not request all inspection tools by default.",
                "Treat validate_requirement as the semantic_completion lane, not as an every-round companion query.",
                "If surface_policy.joint_request_groups contains the exact pair you need for one local decision, you may request that pair together; otherwise prefer one inspection lane at a time.",
                "Read outcome_delta before repeating the same local strategy; if the last expected change was missed, repair or roll back instead of rephrasing the same action.",
                "Treat feature_agenda as the ordered requirement-phase contract for this round.",
                "Do not skip an earlier pending feature_agenda phase in favor of a later one unless current evidence proves the earlier phase is already satisfied or impossible.",
                "If the model is complete, return an empty string.",
                "If requirement_validation.is_complete=true with no blockers, return an empty string instead of more inspection.",
                "If requirement_validation.is_complete is false or requirement_validation.blockers is non-empty, do not return an empty string and do not return actions: [] unless the inspection is gathering concrete missing evidence.",
                "If latest_unresolved_blockers contains any eval:* blocker, do not return an empty string and do not return actions: [] unless the inspection is gathering targeted repair evidence.",
                "If more evidence is needed before editing, return {'actions': [], 'inspection': {...}}.",
                "If validate_requirement has solid_exists blocker, do not return actions: []; propose concrete recovery actions.",
                "If validate_requirement reports target-face/blocker semantics, prefer repairing the local sketch-on-face window instead of adding unrelated actions.",
                "If validate_requirement reports edge-target blockers, query_topology and use explicit edge_refs rather than broad selectors.",
                "If query_sketch reports path_disconnected, path_segment_sequence_mismatch, missing_profile, or profile_not_closed, do not continue to profile/sweep until that blocker is repaired.",
                "Use relation_focus to see which relative structures matter in the current round; use relation_eval to compare expected vs observed drift before choosing the next repair.",
                "When relation_eval provides a specific sweep/path/profile diagnosis, prefer it over broad generic blockers such as feature_profile_shape_alignment.",
                "If a blocking relation_eval appears immediately after the latest topology-changing step, prefer a direct repair of that feature; if the whole step is invalid, use rollback with steps_back=1 or an explicit target_step.",
                "If the blocking relation_eval is topology-side (for example sweep_result_annular_topology), do not ask only for query_geometry; use the current query_topology, request a narrower query_topology window, or repair/rollback the feature.",
                "If sweep_profile_section shows a valid path attachment and one observed loop radius already matches the requirement, extend the current profile by adding the missing concentric circle instead of rolling back the whole sketch window.",
                "If the rail is already verified and one returned action batch can finish the attached profile, include the terminal sweep in that same batch rather than deferring sweep to a later round.",
                "When an add_path requirement gives explicit straight-segment lengths, encode those segments with length + direction instead of guessing absolute to=[x,y] endpoints after an arc.",
                "If evidence_status.required_missing is non-empty, request inspection for the missing evidence instead of guessing.",
                "If round_budget.remaining_rounds <= 1 and one local sketch edit window can finish the blocker, emit the complete local edit window now instead of setup-only actions.",
                "Treat latest_action_result and latest_unresolved_blockers as hard feedback from the last executed step.",
                "Treat stale_evidence as archival context only; stale sketch/path warnings must not override current post-solid evidence.",
                "Historical face_ref/edge_ref/path_ref/profile_ref values from older steps are not reusable targets; only use current-step refs from the current query_topology/query_sketch evidence.",
                "Use query_sketch primitive_types / regular_sides / point_count as authoritative sketch-shape evidence before loft/extrude/cut_extrude.",
                "If no solid exists and current query_sketch is absent, start the local window with an explicit create_sketch action before any add_rectangle/add_circle/add_polygon/add_path action.",
                "If the requirement names a rectangle, square, triangle, hexagon, or polygon before the first solid is created and current query_sketch does not yet expose that primitive, stay in the current sketch window and add the missing primitive before extrude/revolve/loft/sweep.",
                "When the requirement explicitly names a local post-solid shape such as hexagon, triangle, or rectangle, preserve that primitive instead of substituting a circle.",
                "add_circle is a full-circle primitive only; do not attach arc_degrees/start_angle/end_angle semantics to add_circle.",
                "For semicircles or open circular arcs, use add_path with arc / three_point_arc segments instead of add_circle.",
                "When the requirement asks to split, trim, or remove material above/below a datum plane to form a frustum or truncated body, prefer trim_solid over inventing an unrelated sketch cut.",
                "When a regular polygon is used as a local subtractive/profile window and corner alignment matters, use add_polygon.rotation_degrees explicitly instead of assuming the default polygon phase is correct.",
                "When regular-polygon sizing is described by center-to-side / line-offset wording, use add_polygon.size_mode='apothem' (or distance_to_side) instead of treating the same number as a circumradius.",
                "If a regular polygon requirement mentions both an inscribed-circle radius and explicit line/flat offsets from the center, prefer the line/flat offset wording as the controlling size constraint and express it with add_polygon.size_mode='apothem'.",
                "For repeated circumferential additive teeth/ribs/bosses on an existing host face, prefer one additive seed feature plus pattern_circular over path_sweep unless the requirement explicitly defines a real sweep rail.",
                "For annular radial teeth or serrations on a washer/ring, the seed profile should materially span the annular band; a tiny top-face bump is not equivalent.",
                "If validate_requirement reports groove/notch/profile blockers, repair the core cut/profile window before decorative edits or extra inspection.",
                "Always include planner_note for non-empty JSON output.",
                "planner_note: one short sentence (<120 chars) explaining immediate intent.",
                "Always include expected_outcome for non-empty JSON output.",
                "expected_outcome: include surface_type, summary, expected_changes, and target_blockers.",
                "Do not invent decorative features (fillet/chamfer/hole/pattern) unless requirement explicitly asks.",
                "For notch/groove/cutout/U-shape requirements, complete the core cut/extrude shape before decorative edits.",
                "If editing an existing solid via sketch, output a complete local edit window (create_sketch -> profile -> cut/extrude) when feasible.",
                "For repeated circles/holes/studs on one face, prefer add_circle with centers=[...] so the sketch window can finish in one round.",
                "If one face needs a mixed subtractive circle layout with different diameters, especially a central cut plus a bolt-circle / construction-circle hole pattern, keep the same face-local sketch window until all circle families are drawn; do not split that mixed layout into sequential direct hole actions.",
                "If the face edit is one uniform drilled blind/through hole family, prefer hole with position=/centers=[...] over create_sketch + add_circle + cut_extrude; mixed-diameter same-face layouts should stay in a sketch-building flow.",
                "If the requirement also contains a separate additive boss/stud on another face plus later hole/pitch-circle language, do not mix that secondary hole family into the boss extrusion sketch; keep the boss window additive and leave the hole family to its own later subtractive/pattern step.",
                "For revolve-based stepped shafts/studs, prefer add_polygon with length_list/radius_list or another closed half-profile that returns to the axis; avoid open or shell-like profiles.",
                "In no-solid state, avoid using cut_extrude as first material operation; build base solid first, then cut.",
                "For explicit symmetric/symmetrical extrusion requirements, use extrude with both_sides=true and half-distance per side.",
                "Prefer query_topology candidate_sets/ref_ids directly when they match the requirement.",
                "If query_topology for the current step is already present in the prompt and includes the needed candidate set/ref_ids, do not ask for query_topology again; use the provided evidence.",
                "For Boolean-difference cylinder/slot/groove requirements, prefer subtractive flow (cut_extrude) over additive cylinder extrude.",
                "Avoid redundant create_sketch actions in the same round unless plane/context actually changes.",
                "",
                "Inspection policy:",
                "- Query only the evidence needed for the next decision.",
                "- Prefer one inspection partition at a time: state_readback, sketch_state, topology_targeting, semantic_completion, or visual_confirmation.",
                "- Use query_sketch after rail construction and before sweep/profile transitions.",
                "- Use query_topology before topology-sensitive edits on existing faces/edges.",
                "- Do not request validate_requirement as the only inspection when a blocking relation_eval already pinpoints the failed feature.",
                "- Do not request validate_requirement after every post-solid sketch action; use it when semantic completion or blocker confirmation is the real bottleneck.",
                "- If object windows are truncated or uncertainty is high, request inspection-only round.",
                "- If requirement fit is uncertain after an action, request validate_requirement or focused inspection before further topology-changing edits.",
            ]
        )
        return "\n".join(prompt)

    def _prune_round_request_for_prompt(
        self, round_request: dict[str, Any]
    ) -> dict[str, Any]:
        keep_always = {
            "requirements",
            "reconstructed_action_trace",
            "reconstructed_build123d_code",
            "exposure_bundle_ids",
        }
        compact: dict[str, Any] = {}
        for key, value in round_request.items():
            if key in keep_always:
                compact[key] = value
                continue
            if value in (None, "", [], {}):
                continue
            compact[key] = value
        return self._apply_prompt_budget_to_round_request(compact)

    def _apply_prompt_budget_to_round_request(
        self,
        round_request: dict[str, Any],
    ) -> dict[str, Any]:
        chars_before = self._json_char_count(round_request, pretty=True)
        if chars_before <= _ROUND_REQUEST_PROMPT_CHAR_BUDGET:
            return round_request

        compacted = json.loads(json.dumps(round_request, ensure_ascii=True))
        stages: list[str] = []

        self._compact_round_request_relation_payloads(compacted)
        stages.append("relation_digest_only")
        chars_after = self._json_char_count(compacted, pretty=True)

        if chars_after > _ROUND_REQUEST_PROMPT_CHAR_BUDGET:
            self._compact_round_request_topology_payloads(compacted)
            stages.append("topology_window_trimmed")
            chars_after = self._json_char_count(compacted, pretty=True)

        if chars_after > _ROUND_REQUEST_PROMPT_CHAR_BUDGET:
            self._compact_round_request_history_trace(compacted)
            stages.append("history_trace_trimmed")
            chars_after = self._json_char_count(compacted, pretty=True)

        if chars_after > _ROUND_REQUEST_PROMPT_HARD_LIMIT:
            for section in ("query_geometry", "query_topology", "query_sketch"):
                payload = compacted.get(section)
                if not isinstance(payload, dict):
                    continue
                compacted[section] = {
                    "success": bool(payload.get("success")),
                    "error_code": payload.get("error_code"),
                    "error_message": payload.get("error_message"),
                    "step": payload.get("step"),
                }
            stages.append("hard_drop_heavy_evidence_windows")
            chars_after = self._json_char_count(compacted, pretty=True)

        compacted["prompt_budget"] = {
            "chars_before": chars_before,
            "chars_after": chars_after,
            "target_chars": _ROUND_REQUEST_PROMPT_CHAR_BUDGET,
            "stages": stages,
        }
        return compacted

    def _json_char_count(self, payload: Any, *, pretty: bool = False) -> int:
        try:
            dump_kwargs: dict[str, Any] = {
                "ensure_ascii": True,
                "sort_keys": True,
            }
            if pretty:
                dump_kwargs["indent"] = 2
            return len(json.dumps(payload, **dump_kwargs))
        except Exception:
            return 0

    def _compact_round_request_relation_payloads(
        self,
        round_request: dict[str, Any],
    ) -> None:
        for section in ("query_sketch", "query_topology", "requirement_validation"):
            payload = round_request.get(section)
            if not isinstance(payload, dict):
                continue
            relation_index = payload.get("relation_index")
            if not isinstance(relation_index, dict):
                continue
            payload["relation_index"] = {
                "version": relation_index.get("version"),
                "source_tool": relation_index.get("source_tool"),
                "step": relation_index.get("step"),
                "summary": relation_index.get("summary"),
                "focus_entity_ids": self._normalize_string_list(
                    relation_index.get("focus_entity_ids"),
                    limit=8,
                ),
                "entity_count": relation_index.get("entity_count"),
                "relation_count": relation_index.get("relation_count"),
                "relation_group_count": relation_index.get("relation_group_count"),
                "relation_type_counts": (
                    relation_index.get("relation_type_counts")
                    if isinstance(relation_index.get("relation_type_counts"), dict)
                    else {}
                ),
                "group_type_counts": (
                    relation_index.get("group_type_counts")
                    if isinstance(relation_index.get("group_type_counts"), dict)
                    else {}
                ),
                "planner_digest": (
                    relation_index.get("planner_digest")
                    if isinstance(relation_index.get("planner_digest"), dict)
                    else {}
                ),
            }

        relation_focus = round_request.get("relation_focus")
        if isinstance(relation_focus, dict):
            round_request["relation_focus"] = {
                "version": relation_focus.get("version"),
                "step": relation_focus.get("step"),
                "state_mode": relation_focus.get("state_mode"),
                "selection_basis": relation_focus.get("selection_basis"),
                "summary": relation_focus.get("summary"),
                "item_count": relation_focus.get("item_count"),
                "planner_digest": (
                    relation_focus.get("planner_digest")
                    if isinstance(relation_focus.get("planner_digest"), dict)
                    else {}
                ),
            }

        relation_eval = round_request.get("relation_eval")
        if isinstance(relation_eval, dict):
            round_request["relation_eval"] = {
                "version": relation_eval.get("version"),
                "step": relation_eval.get("step"),
                "state_mode": relation_eval.get("state_mode"),
                "selection_basis": relation_eval.get("selection_basis"),
                "summary": relation_eval.get("summary"),
                "blocking_eval_ids": self._normalize_string_list(
                    relation_eval.get("blocking_eval_ids"),
                    limit=8,
                ),
                "item_count": relation_eval.get("item_count"),
                "planner_digest": (
                    relation_eval.get("planner_digest")
                    if isinstance(relation_eval.get("planner_digest"), dict)
                    else {}
                ),
            }

    def _compact_round_request_topology_payloads(
        self,
        round_request: dict[str, Any],
    ) -> None:
        query_topology = round_request.get("query_topology")
        if isinstance(query_topology, dict):
            query_topology["matched_entity_ids"] = self._normalize_string_list(
                query_topology.get("matched_entity_ids"),
                limit=8,
            )
            query_topology["matched_ref_ids"] = self._normalize_string_list(
                query_topology.get("matched_ref_ids"),
                limit=8,
            )
            candidate_sets_raw = query_topology.get("candidate_sets")
            candidate_sets = candidate_sets_raw if isinstance(candidate_sets_raw, list) else []
            compact_candidate_sets: list[dict[str, Any]] = []
            for item in candidate_sets[:6]:
                if not isinstance(item, dict):
                    continue
                metadata = item.get("metadata")
                metadata_dict = metadata if isinstance(metadata, dict) else {}
                compact_candidate_sets.append(
                    {
                        "candidate_id": item.get("candidate_id"),
                        "label": item.get("label"),
                        "entity_type": item.get("entity_type"),
                        "ref_ids": self._normalize_string_list(
                            item.get("ref_ids"),
                            limit=8,
                        ),
                        "metadata": {
                            key: metadata_dict.get(key)
                            for key in (
                                "primary_axis",
                                "sketch_plane",
                                "target_axis_side",
                                "target_coordinate",
                            )
                            if metadata_dict.get(key) not in (None, "", [], {})
                        },
                    }
                )
            query_topology["candidate_sets"] = compact_candidate_sets

            topology_window = query_topology.get("topology_window")
            if isinstance(topology_window, dict):
                query_topology["topology_window"] = {
                    "face_count": len(topology_window.get("faces") or []),
                    "edge_count": len(topology_window.get("edges") or []),
                    "faces_truncated": bool(topology_window.get("faces_truncated")),
                    "edges_truncated": bool(topology_window.get("edges_truncated")),
                }

        query_geometry = round_request.get("query_geometry")
        if isinstance(query_geometry, dict):
            object_index_window = query_geometry.get("object_index_window")
            if isinstance(object_index_window, dict):
                query_geometry["object_index_window"] = {
                    "solid_count": len(object_index_window.get("solids") or []),
                    "face_count": len(object_index_window.get("faces") or []),
                    "edge_count": len(object_index_window.get("edges") or []),
                    "solids_truncated": bool(object_index_window.get("solids_truncated")),
                    "faces_truncated": bool(object_index_window.get("faces_truncated")),
                    "edges_truncated": bool(object_index_window.get("edges_truncated")),
                }

    def _compact_round_request_history_trace(
        self,
        round_request: dict[str, Any],
    ) -> None:
        action_history = round_request.get("action_history")
        if isinstance(action_history, list) and len(action_history) > 6:
            round_request["action_history"] = action_history[-6:]

        trace_raw = round_request.get("reconstructed_action_trace")
        if isinstance(trace_raw, str) and trace_raw.strip():
            trace_lines = trace_raw.splitlines()
            trimmed_lines = trace_lines[-_ROUND_REQUEST_TRACE_LINES:]
            trimmed_trace = "\n".join(trimmed_lines)
            if len(trimmed_trace) > _ROUND_REQUEST_TRACE_CHARS:
                trimmed_trace = trimmed_trace[-_ROUND_REQUEST_TRACE_CHARS:]
            round_request["reconstructed_action_trace"] = trimmed_trace

    def _llm_timeout_seconds_for_request(self) -> float:
        timeout_seconds = max(
            0.1,
            float(getattr(self._settings, "llm_timeout_seconds", 60.0)),
        )
        provider = self._settings.llm_reasoning_provider.strip().lower()
        model = self._settings.llm_reasoning_model.strip().lower()
        if provider == "kimi" and model.startswith("kimi-k2.5-thinking"):
            return max(timeout_seconds, 180.0)
        return timeout_seconds

    def _build_action_messages(
        self,
        user_content: str,
        render_view: dict[str, Any] | None,
        client: Any,
    ) -> list[LLMMessage]:
        messages = [LLMMessage(role="system", content=ACI_SYSTEM_PROMPT)]
        image_content = self._build_render_view_image_content(
            render_view=render_view,
            client=client,
        )
        if image_content is None:
            messages.append(LLMMessage(role="user", content=user_content))
            return messages

        messages.append(
            LLMMessage(
                role="user",
                content=[
                    LLMTextContent(text=user_content),
                    image_content,
                ],
            )
        )
        return messages

    def _build_render_view_image_content(
        self,
        render_view: dict[str, Any] | None,
        client: Any,
    ) -> LLMImageContent | None:
        if not isinstance(render_view, dict):
            return None
        if not bool(getattr(client, "supports_multimodal", False)):
            return None
        provider = self._settings.llm_reasoning_provider.strip().lower()
        model = self._settings.llm_reasoning_model.strip().lower()
        if provider == "glm":
            return None
        if not self._model_allows_image_input(provider=provider, model=model):
            return None

        image_base64 = render_view.get("image_base64")
        image_mime_type = render_view.get("image_mime_type", "image/png")
        if not isinstance(image_base64, str) or not image_base64.strip():
            return None
        if not isinstance(image_mime_type, str) or not image_mime_type.strip():
            return None
        if not self._looks_like_valid_base64(image_base64):
            return None

        return LLMImageContent(
            mime_type=image_mime_type.strip(),
            data_base64=image_base64.strip(),
        )

    def _model_allows_image_input(self, provider: str, model: str) -> bool:
        if provider == "kimi":
            return any(token in model for token in ("vision", "vl", "multimodal"))
        return True

    def _sanitize_render_view_for_prompt(
        self,
        render_view: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(render_view, dict):
            return {}
        payload = {
            "success": bool(render_view.get("success")),
            "error_code": render_view.get("error_code"),
            "error_message": render_view.get("error_message"),
            "session_id": render_view.get("session_id"),
            "step": render_view.get("step"),
            "view_file": render_view.get("view_file"),
            "output_files": self._normalize_string_list(
                render_view.get("output_files"),
                limit=_MAX_PROMPT_OUTPUT_FILES,
            ),
            "camera": (
                render_view.get("camera")
                if isinstance(render_view.get("camera"), dict)
                else {}
            ),
            "image_size_bytes": render_view.get("image_size_bytes"),
            "image_mime_type": render_view.get("image_mime_type"),
            "focus_bbox": (
                render_view.get("focus_bbox")
                if isinstance(render_view.get("focus_bbox"), dict)
                else None
            ),
            "focused_entity_ids": self._normalize_string_list(
                render_view.get("focused_entity_ids"),
                limit=_MAX_PROMPT_MATCHED_ENTITY_IDS,
            ),
        }
        image_base64 = payload.pop("image_base64", None)
        if isinstance(render_view.get("image_base64"), str):
            image_base64 = render_view.get("image_base64")
        payload["image_attached"] = isinstance(image_base64, str) and bool(
            image_base64.strip()
        )
        return payload

    def _looks_like_valid_base64(self, value: str) -> bool:
        try:
            base64.b64decode(value, validate=True)
            return True
        except (binascii.Error, ValueError):
            return False

    def _message_has_image(self, messages: list[LLMMessage]) -> bool:
        if len(messages) < 2:
            return False
        user_content = messages[1].content
        if not isinstance(user_content, list):
            return False
        return any(isinstance(part, LLMImageContent) for part in user_content)

    def _build_reconstructed_build123d_code(
        self,
        action_history: list[dict[str, Any]],
    ) -> str:
        lines: list[str] = ["from build123d import *"]
        has_result = False
        encountered_unsupported = False

        for item in action_history:
            if not isinstance(item, dict):
                continue
            action_type = item.get("action_type")
            action_params = item.get("action_params", {})
            if not isinstance(action_type, str):
                continue
            if not isinstance(action_params, dict):
                action_params = {}

            line = self._render_action_as_build123d_line(
                action_type=action_type,
                action_params=action_params,
            )
            if line is None:
                encountered_unsupported = True
                break
            lines.append(line)
            has_result = True

        if encountered_unsupported:
            return ""
        if not has_result:
            lines.extend(
                [
                    "with BuildSketch(Plane.XY) as sketch:",
                    "    pass",
                    "result = sketch.sketch",
                ]
            )

        return "\n".join(lines)

    def _build_reconstructed_action_trace(
        self,
        action_history: list[dict[str, Any]],
        latest_step: int | None = None,
    ) -> str:
        trace_lines: list[str] = []
        for idx, item in enumerate(action_history, start=1):
            if not isinstance(item, dict):
                continue
            action_type_raw = item.get("action_type")
            if not isinstance(action_type_raw, str) or not action_type_raw.strip():
                continue
            action_params = item.get("action_params")
            params = (
                self._sanitize_prompt_action_params(
                    action_params if isinstance(action_params, dict) else {},
                    latest_step=latest_step,
                )
                if isinstance(action_params, dict)
                else {}
            )
            step = item.get("step")
            step_label = (
                f"{int(step):02d}" if isinstance(step, int) and step > 0 else f"{idx:02d}"
            )
            rendered_params = self._render_action_trace_params(params)
            if rendered_params:
                trace_lines.append(f"{step_label}. {action_type_raw}({rendered_params})")
            else:
                trace_lines.append(f"{step_label}. {action_type_raw}()")
        return "\n".join(trace_lines)

    def _render_action_trace_params(self, action_params: dict[str, Any]) -> str:
        rendered: list[str] = []
        for key in sorted(action_params.keys()):
            formatted = self._format_trace_value(action_params.get(key))
            rendered.append(f"{key}={formatted}")
        return ", ".join(rendered)

    def _format_trace_value(self, value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return repr(value)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=True, sort_keys=True)
        if isinstance(value, list):
            return json.dumps(value[:6], ensure_ascii=True)
        return json.dumps(value, ensure_ascii=True, sort_keys=True)

    def _render_action_as_build123d_line(
        self,
        action_type: str,
        action_params: dict[str, Any],
    ) -> str | None:
        if action_type == "create_sketch":
            if action_params.get("attach_to_solid") or action_params.get("face_ref"):
                return None
            if (
                action_params.get("position") is not None
                or action_params.get("center") is not None
                or action_params.get("origin") is not None
            ):
                return None
            plane = action_params.get("plane", "XY")
            if not isinstance(plane, str) or not plane.strip():
                plane = "XY"
            plane_expr = {
                "XY": "Plane.XY",
                "XZ": "Plane.XZ",
                "YZ": "Plane.YZ",
            }.get(plane.strip().upper(), "Plane.XY")
            return f"with BuildSketch({plane_expr}) as sketch:"

        if action_type == "add_rectangle":
            width = self._to_positive_number(action_params.get("width"), default=50.0)
            height = self._to_positive_number(action_params.get("height"), default=50.0)
            return f"    Rectangle({width}, {height})"

        if action_type == "add_circle":
            radius = self._to_positive_number(action_params.get("radius"), default=10.0)
            return f"    Circle({radius})"

        if action_type == "extrude":
            distance = self._to_positive_number(
                action_params.get("distance"), default=10.0
            )
            direction = action_params.get("direction", "up")
            signed_distance = (
                -distance
                if isinstance(direction, str) and direction.lower() == "down"
                else distance
            )
            return "\n".join(
                [
                    "with BuildPart() as part:",
                    "    add(sketch.sketch)",
                    f"    extrude(amount={signed_distance})",
                    "result = part.part",
                ]
            )

        if action_type in {
            "cut_extrude",
            "revolve",
            "loft",
            "add_polygon",
            "fillet",
            "chamfer",
            "hole",
        }:
            return None
            if (
                not isinstance(position, list)
                or len(position) < 2
                or not isinstance(position[0], (int, float))
                or not isinstance(position[1], (int, float))
            ):
                position = [0.0, 0.0]
            x, y = position[0], position[1]
            if isinstance(depth, (int, float)) and depth > 0:
                return (
                    "result = result.faces('>Z').workplane()"
                    f".center({x}, {y}).hole({diameter}, {depth})"
                )
            return (
                "result = result.faces('>Z').workplane()"
                f".center({x}, {y}).hole({diameter})"
            )

        return None

    def _edges_selector_call(self, action_params: dict[str, Any]) -> str:
        edge_scope = action_params.get("edge_scope")
        if isinstance(edge_scope, str):
            normalized_scope = edge_scope.strip().lower()
            if normalized_scope in {"all", "all_outer", "outer", "all_edges"}:
                return "result.edges()"
            if normalized_scope in {"top", "top_edges"}:
                return "result.edges('>Z')"
            if normalized_scope in {"bottom", "bottom_edges"}:
                return "result.edges('<Z')"
            if normalized_scope in {"vertical", "vertical_edges"}:
                return "result.edges('|Z')"

        selector = action_params.get("edges_selector")
        if isinstance(selector, str):
            normalized_selector = selector.strip()
            if normalized_selector.lower() in {"all", "all_outer", "outer"}:
                return "result.edges()"
            if re.fullmatch(r"[<>|XYZxyz+\-#%&*()]+", normalized_selector):
                return f"result.edges({normalized_selector!r})"

        return "result.edges()"

    def _to_positive_number(self, value: Any, default: float) -> float:
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        return default

    def _extract_actions(self, content: str) -> list[dict[str, Any]]:
        actions, _, _, _ = self._extract_action_plan(content)
        return actions

    def _extract_action_plan(
        self,
        content: str,
    ) -> tuple[
        list[dict[str, Any]],
        dict[str, Any] | None,
        str | None,
        dict[str, Any] | None,
    ]:
        stripped = content.strip()
        if not stripped:
            return [], None, None, None

        parsed = self._parse_json_payload(stripped)
        if parsed is None:
            logger.warning(
                "aci_action_response_not_json",
                response_preview=stripped[:200],
            )
            return [], None, None, None

        raw_actions: list[Any]
        raw_inspection: Any = None
        planner_note: str | None = None
        expected_outcome: dict[str, Any] | None = None
        if isinstance(parsed, list):
            raw_actions = parsed
        elif isinstance(parsed, dict):
            candidate_actions = parsed.get("actions", parsed.get("plan", []))
            if not isinstance(candidate_actions, list):
                logger.warning(
                    "aci_action_response_invalid_shape",
                    payload_type=str(type(parsed)),
                )
                return [], None, None, None
            raw_actions = candidate_actions
            raw_inspection = parsed.get("inspection")
            planner_note = self._normalize_planner_note(
                parsed.get("planner_note", parsed.get("note", parsed.get("analysis")))
            )
            expected_outcome = self._normalize_expected_outcome(
                parsed.get("expected_outcome")
            )
        else:
            logger.warning(
                "aci_action_response_invalid_shape",
                payload_type=str(type(parsed)),
            )
            return [], None, None, None

        raw_actions = self._lower_path_primitive_actions(raw_actions)
        normalized_actions: list[dict[str, Any]] = []
        for item in raw_actions:
            normalized = self._normalize_action(item)
            if normalized is None:
                continue
            normalized_actions.append(normalized)
            if len(normalized_actions) >= _MAX_ACTIONS_PER_ROUND:
                break

        normalized_inspection = self._normalize_inspection(raw_inspection)
        if planner_note is None:
            planner_note = self._fallback_planner_note(
                actions=normalized_actions,
                inspection=normalized_inspection,
            )
        return (
            normalized_actions,
            normalized_inspection,
            planner_note,
            expected_outcome,
        )

    def _normalize_planner_note(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if len(normalized) > 300:
            return normalized[:300]
        return normalized

    def _normalize_expected_outcome(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        expected_changes = self._normalize_string_list(
            value.get("expected_changes"),
            limit=_MAX_PROMPT_CHECKS,
        )
        target_blockers = self._normalize_string_list(
            value.get("target_blockers"),
            limit=_MAX_PROMPT_CHECKS,
        )
        payload = {
            "surface_type": value.get("surface_type"),
            "summary": self._normalize_planner_note(value.get("summary")),
            "expected_changes": expected_changes,
            "target_blockers": target_blockers,
            "baseline": (
                value.get("baseline")
                if isinstance(value.get("baseline"), dict)
                else {}
            ),
        }
        if not payload["surface_type"] and not payload["summary"] and not expected_changes:
            return None
        return payload

    def _fallback_planner_note(
        self,
        actions: list[dict[str, Any]],
        inspection: dict[str, Any] | None,
    ) -> str | None:
        if actions:
            first_action = actions[0].get("action_type", "action")
            return f"Execute next step, starting with {first_action}."

        if not isinstance(inspection, dict):
            return None

        requested_sections = [
            key
            for key in (
                "query_snapshot",
                "query_geometry",
                "query_topology",
                "render_view",
                "validate_requirement",
            )
            if inspection.get(key) is True or isinstance(inspection.get(key), dict)
        ]
        if requested_sections:
            joined = ", ".join(requested_sections[:2])
            if len(requested_sections) > 2:
                joined = f"{joined}, ..."
            return f"Inspect {joined} before next CAD edit."
        return None

    def _parse_json_payload(self, content: str) -> Any | None:
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        fenced_match = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL)
        if fenced_match:
            try:
                return json.loads(fenced_match.group(1).strip())
            except json.JSONDecodeError:
                pass

        array_match = re.search(r"\[[\s\S]*\]", content)
        if array_match:
            try:
                return json.loads(array_match.group(0))
            except json.JSONDecodeError:
                return None

        return None

    def _lower_path_primitive_actions(self, raw_actions: list[Any]) -> list[Any]:
        rewritten: list[Any] = []
        buffered: list[dict[str, Any]] = []

        def flush_buffer() -> None:
            nonlocal buffered
            if not buffered:
                return
            lowered = self._lower_path_primitive_sequence(buffered)
            if lowered is not None:
                rewritten.append(lowered)
            else:
                rewritten.extend(buffered)
            buffered = []

        for item in raw_actions:
            action_type, _action_params = self._extract_raw_action_parts(item)
            if action_type in {
                "add_line",
                "line",
                "add_arc",
                "arc",
                "add_tangent_arc",
                "tangent_arc",
            }:
                if isinstance(item, dict):
                    buffered.append(item)
                else:
                    flush_buffer()
                    rewritten.append(item)
                continue
            flush_buffer()
            rewritten.append(item)

        flush_buffer()
        return rewritten

    def _lower_path_primitive_sequence(
        self,
        raw_actions: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        start_point: list[float] | None = None
        current_point: list[float] | None = None
        segments: list[dict[str, Any]] = []

        for item in raw_actions:
            action_type, action_params = self._extract_raw_action_parts(item)
            if action_type in {"add_line", "line"}:
                line_segment = self._lower_line_action_to_path_segment(
                    action_params=action_params,
                    current_point=current_point,
                )
                if line_segment is None:
                    return None
                if start_point is None:
                    start_point = line_segment["start"]
                current_point = line_segment.get("end")
                segments.append(line_segment["segment"])
                continue
            if action_type in {"add_arc", "arc", "add_tangent_arc", "tangent_arc"}:
                arc_segment = self._lower_arc_action_to_path_segment(
                    action_params=action_params,
                    current_point=current_point,
                )
                if arc_segment is None:
                    return None
                if start_point is None:
                    start_point = arc_segment["start"]
                current_point = arc_segment.get("end")
                segments.append(arc_segment["segment"])
                continue
            return None

        if not segments:
            return None

        return {
            "action_type": "add_path",
            "action_params": {
                "start": start_point or [0.0, 0.0],
                "segments": segments,
                "closed": False,
            },
        }

    def _extract_raw_action_parts(self, value: Any) -> tuple[str | None, dict[str, Any]]:
        if not isinstance(value, dict):
            return None, {}
        raw_type = value.get("action_type", value.get("type"))
        action_type = (
            raw_type.strip().lower() if isinstance(raw_type, str) else None
        )
        raw_params = value.get("action_params", value.get("params", {}))
        action_params = raw_params if isinstance(raw_params, dict) else {}
        return action_type, action_params

    def _lower_line_action_to_path_segment(
        self,
        action_params: dict[str, Any],
        current_point: list[float] | None,
    ) -> dict[str, Any] | None:
        start_point = self._coerce_point2(
            action_params.get("start", action_params.get("from"))
        ) or current_point
        if start_point is None:
            start_point = [0.0, 0.0]

        end_point = self._coerce_point2(
            action_params.get("end", action_params.get("to"))
        )
        if end_point is not None:
            return {
                "start": start_point,
                "end": end_point,
                "segment": {"type": "line", "to": end_point},
            }

        dx = action_params.get("dx")
        dy = action_params.get("dy")
        if isinstance(dx, (int, float)) or isinstance(dy, (int, float)):
            end_point = [
                float(start_point[0] + self._to_number(dx, 0.0)),
                float(start_point[1] + self._to_number(dy, 0.0)),
            ]
            return {
                "start": start_point,
                "end": end_point,
                "segment": {
                    "type": "line",
                    "dx": self._to_number(dx, 0.0),
                    "dy": self._to_number(dy, 0.0),
                },
            }

        length_value = action_params.get("length")
        if not isinstance(length_value, (int, float)):
            return None
        direction_token = self._normalize_direction_token(action_params.get("direction"))
        segment_payload: dict[str, Any] = {
            "type": "line",
            "length": float(length_value),
        }
        if direction_token is not None:
            segment_payload["direction"] = direction_token
        return {
            "start": start_point,
            "end": None,
            "segment": segment_payload,
        }

    def _lower_arc_action_to_path_segment(
        self,
        action_params: dict[str, Any],
        current_point: list[float] | None,
    ) -> dict[str, Any] | None:
        center_point = self._coerce_point2(action_params.get("center"))
        radius_value = action_params.get("radius")
        radius = (
            abs(float(radius_value))
            if isinstance(radius_value, (int, float))
            else None
        )
        start_angle = self._to_optional_number(action_params.get("start_angle"))
        end_angle = self._to_optional_number(action_params.get("end_angle"))

        start_point = self._coerce_point2(
            action_params.get("start", action_params.get("from"))
        ) or current_point
        if start_point is None and center_point is not None and radius is not None and start_angle is not None:
            start_point = self._point_from_center_radius_angle(
                center=center_point,
                radius=radius,
                angle_degrees=start_angle,
            )

        end_point = self._coerce_point2(
            action_params.get("end", action_params.get("to"))
        )
        if end_point is None and center_point is not None and radius is not None and end_angle is not None:
            end_point = self._point_from_center_radius_angle(
                center=center_point,
                radius=radius,
                angle_degrees=end_angle,
            )

        if start_point is None:
            return None

        delta_angle = None
        if start_angle is not None and end_angle is not None:
            delta_angle = float(end_angle - start_angle)
        turn = "left"
        clockwise = action_params.get("clockwise")
        if isinstance(clockwise, bool):
            turn = "right" if clockwise else "left"
        elif isinstance(delta_angle, (int, float)) and delta_angle < 0.0:
            turn = "right"
        tangent_direction = None
        if isinstance(start_angle, (int, float)):
            tangent_direction = self._arc_start_tangent_direction(
                angle_degrees=float(start_angle),
                turn=turn,
            )

        angle_value = self._to_optional_number(
            action_params.get("angle_degrees", action_params.get("angle"))
        )
        segment_payload: dict[str, Any] = {
            "type": "tangent_arc",
            "turn": turn,
        }
        if end_point is not None:
            segment_payload["to"] = end_point
        if isinstance(radius, (int, float)) and radius > 0.0:
            segment_payload["radius"] = float(radius)
        if isinstance(angle_value, (int, float)) and abs(float(angle_value)) > 0.0:
            segment_payload["angle_degrees"] = abs(float(angle_value))
        elif isinstance(delta_angle, (int, float)) and abs(float(delta_angle)) > 0.0:
            segment_payload["angle_degrees"] = abs(float(delta_angle))
        if isinstance(tangent_direction, str) and tangent_direction:
            segment_payload["direction"] = tangent_direction

        return {
            "start": start_point,
            "end": end_point,
            "segment": segment_payload,
        }

    def _coerce_point2(self, value: Any) -> list[float] | None:
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            return None
        if not isinstance(value[0], (int, float)) or not isinstance(
            value[1], (int, float)
        ):
            return None
        return [float(value[0]), float(value[1])]

    def _normalize_direction_token(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        token = value.strip().lower()
        return token or None

    def _direction_vector(self, token: str | None) -> list[float] | None:
        mapping = {
            "x": [1.0, 0.0],
            "+x": [1.0, 0.0],
            "x+": [1.0, 0.0],
            "right": [1.0, 0.0],
            "horizontal": [1.0, 0.0],
            "-x": [-1.0, 0.0],
            "x-": [-1.0, 0.0],
            "left": [-1.0, 0.0],
            "y": [0.0, 1.0],
            "+y": [0.0, 1.0],
            "y+": [0.0, 1.0],
            "up": [0.0, 1.0],
            "vertical": [0.0, 1.0],
            "-y": [0.0, -1.0],
            "y-": [0.0, -1.0],
            "down": [0.0, -1.0],
        }
        if token is None:
            return None
        vec = mapping.get(token)
        return list(vec) if vec is not None else None

    def _point_from_center_radius_angle(
        self,
        center: list[float],
        radius: float,
        angle_degrees: float,
    ) -> list[float]:
        radians = math.radians(float(angle_degrees))
        return [
            float(center[0] + (float(radius) * math.cos(radians))),
            float(center[1] + (float(radius) * math.sin(radians))),
        ]

    def _arc_start_tangent_direction(
        self,
        angle_degrees: float,
        turn: str,
    ) -> str | None:
        radians = math.radians(float(angle_degrees))
        if turn == "right":
            vec = [math.sin(radians), -math.cos(radians)]
        else:
            vec = [-math.sin(radians), math.cos(radians)]
        x_mag = abs(vec[0])
        y_mag = abs(vec[1])
        if x_mag >= y_mag and x_mag > 1e-6:
            return "x" if vec[0] >= 0.0 else "-x"
        if y_mag > 1e-6:
            return "y" if vec[1] >= 0.0 else "-y"
        return None

    def _to_number(self, value: Any, default: float) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        return float(default)

    def _to_optional_number(self, value: Any) -> float | None:
        if isinstance(value, (int, float)):
            return float(value)
        return None

    def _normalize_action(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None

        raw_type = value.get("action_type", value.get("type"))
        if not isinstance(raw_type, str):
            return None

        action_type = raw_type.strip().lower()
        if action_type not in _SUPPORTED_ACTION_TYPES:
            logger.warning("aci_action_type_unsupported", action_type=action_type)
            return None

        raw_params = value.get("action_params", value.get("params", {}))
        if not isinstance(raw_params, dict):
            raw_params = {}

        return {
            "action_type": action_type,
            "action_params": raw_params,
        }

    def _normalize_inspection(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None

        snapshot_raw = value.get("query_snapshot")
        if not isinstance(snapshot_raw, dict):
            snapshot_raw = None
        query_raw = value.get("query_geometry")
        if not isinstance(query_raw, dict):
            query_raw = None
        topology_raw = value.get("query_topology")
        if not isinstance(topology_raw, dict):
            topology_raw = None
        sketch_raw = value.get("query_sketch")
        if sketch_raw is True:
            sketch_raw = {}
        if not isinstance(sketch_raw, dict):
            sketch_raw = None
        render_raw = value.get("render_view")
        if not isinstance(render_raw, dict):
            render_raw = value.get("view")
        if not isinstance(render_raw, dict):
            render_raw = None
        validate_raw = value.get("validate_requirement")
        if validate_raw is True:
            validate_raw = {}
        if not isinstance(validate_raw, dict):
            validate_raw = None

        normalized: dict[str, Any] = {}

        if isinstance(snapshot_raw, dict):
            normalized["query_snapshot"] = {
                "step": self._to_optional_int_range(
                    snapshot_raw.get("step"),
                    minimum=1,
                    maximum=5000,
                ),
                "include_history": bool(snapshot_raw.get("include_history", False)),
            }

        if isinstance(query_raw, dict):
            normalized["query_geometry"] = {
                "include_solids": bool(query_raw.get("include_solids", True)),
                "include_faces": bool(query_raw.get("include_faces", True)),
                "include_edges": bool(query_raw.get("include_edges", False)),
                "max_items_per_type": self._to_int_range(
                    query_raw.get("max_items_per_type"),
                    default=24,
                    minimum=1,
                    maximum=80,
                ),
                "entity_ids": self._normalize_string_list(
                    query_raw.get("entity_ids"),
                    limit=_MAX_PROMPT_MATCHED_ENTITY_IDS,
                ),
                "step": self._to_optional_int_range(
                    query_raw.get("step"),
                    minimum=1,
                    maximum=5000,
                ),
                "solid_offset": self._to_int_range(
                    query_raw.get("solid_offset"),
                    default=0,
                    minimum=0,
                    maximum=5000,
                ),
                "face_offset": self._to_int_range(
                    query_raw.get("face_offset"),
                    default=0,
                    minimum=0,
                    maximum=5000,
                ),
                "edge_offset": self._to_int_range(
                    query_raw.get("edge_offset"),
                    default=0,
                    minimum=0,
                    maximum=5000,
                ),
            }

        if isinstance(sketch_raw, dict):
            normalized["query_sketch"] = {
                "step": self._to_optional_int_range(
                    sketch_raw.get("step"),
                    minimum=1,
                    maximum=5000,
                )
            }

        if isinstance(topology_raw, dict):
            normalized["query_topology"] = {
                "include_faces": bool(topology_raw.get("include_faces", True)),
                "include_edges": bool(topology_raw.get("include_edges", True)),
                "max_items_per_type": self._to_int_range(
                    topology_raw.get("max_items_per_type"),
                    default=20,
                    minimum=1,
                    maximum=80,
                ),
                "entity_ids": self._normalize_string_list(
                    topology_raw.get("entity_ids"),
                    limit=_MAX_PROMPT_MATCHED_ENTITY_IDS,
                ),
                "ref_ids": self._normalize_string_list(
                    topology_raw.get("ref_ids"),
                    limit=_MAX_PROMPT_MATCHED_ENTITY_IDS,
                ),
                "selection_hints": self._normalize_string_list(
                    topology_raw.get("selection_hints"),
                    limit=12,
                ),
                "step": self._to_optional_int_range(
                    topology_raw.get("step"),
                    minimum=1,
                    maximum=5000,
                ),
                "face_offset": self._to_int_range(
                    topology_raw.get("face_offset"),
                    default=0,
                    minimum=0,
                    maximum=5000,
                ),
                "edge_offset": self._to_int_range(
                    topology_raw.get("edge_offset"),
                    default=0,
                    minimum=0,
                    maximum=5000,
                ),
            }

        if isinstance(render_raw, dict):
            focus_center = render_raw.get("focus_center")
            normalized_focus_center: list[float] | None = None
            if isinstance(focus_center, list) and len(focus_center) >= 3:
                values = []
                for idx in range(3):
                    part = focus_center[idx]
                    if not isinstance(part, (int, float)):
                        values = []
                        break
                    values.append(float(part))
                if len(values) == 3:
                    normalized_focus_center = values

            style = str(render_raw.get("style", "shaded")).strip().lower()
            if style not in {"shaded", "wireframe"}:
                style = "shaded"

            normalized["render_view"] = {
                "intent": (
                    str(render_raw.get("intent", "")).strip().lower()
                    if isinstance(render_raw.get("intent"), str)
                    else ""
                ),
                "step": self._to_optional_int_range(
                    render_raw.get("step"),
                    minimum=1,
                    maximum=5000,
                ),
                "azimuth_deg": self._to_float_range(
                    render_raw.get("azimuth_deg"),
                    default=35.0,
                    minimum=-360.0,
                    maximum=360.0,
                ),
                "elevation_deg": self._to_float_range(
                    render_raw.get("elevation_deg"),
                    default=25.0,
                    minimum=-180.0,
                    maximum=180.0,
                ),
                "zoom": self._to_float_range(
                    render_raw.get("zoom"),
                    default=1.0,
                    minimum=0.2,
                    maximum=4.0,
                ),
                "width_px": self._to_int_range(
                    render_raw.get("width_px"),
                    default=800,
                    minimum=320,
                    maximum=1600,
                ),
                "height_px": self._to_int_range(
                    render_raw.get("height_px"),
                    default=600,
                    minimum=240,
                    maximum=1600,
                ),
                "style": style,
                "target_entity_ids": self._normalize_string_list(
                    render_raw.get("target_entity_ids"),
                    limit=_MAX_PROMPT_MATCHED_ENTITY_IDS,
                ),
                "focus_center": normalized_focus_center,
                "focus_span": self._to_float_range(
                    render_raw.get("focus_span"),
                    default=0.0,
                    minimum=0.0,
                    maximum=10000.0,
                ),
                "focus_padding_ratio": self._to_float_range(
                    render_raw.get("focus_padding_ratio"),
                    default=0.15,
                    minimum=0.0,
                    maximum=3.0,
                ),
            }
            if normalized["render_view"]["intent"] not in {
                "global_overview",
                "detail_check",
            }:
                normalized["render_view"]["intent"] = ""

        if isinstance(validate_raw, dict):
            normalized["validate_requirement"] = {
                "step": self._to_optional_int_range(
                    validate_raw.get("step"),
                    minimum=1,
                    maximum=5000,
                )
            }

        if not normalized:
            return None
        return normalized

    def _to_int_range(
        self,
        value: Any,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        if not isinstance(value, (int, float)):
            return default
        cast_value = int(value)
        if cast_value < minimum:
            return minimum
        if cast_value > maximum:
            return maximum
        return cast_value

    def _to_optional_int_range(
        self,
        value: Any,
        minimum: int,
        maximum: int,
    ) -> int | None:
        if not isinstance(value, (int, float)):
            return None
        cast_value = int(value)
        if cast_value < minimum:
            return minimum
        if cast_value > maximum:
            return maximum
        return cast_value

    def _to_float_range(
        self,
        value: Any,
        default: float,
        minimum: float,
        maximum: float,
    ) -> float:
        if not isinstance(value, (int, float)):
            return default
        cast_value = float(value)
        if cast_value < minimum:
            return minimum
        if cast_value > maximum:
            return maximum
        return cast_value

    def _normalize_string_list(self, value: Any, limit: int) -> list[str]:
        if not isinstance(value, list):
            return []
        normalized = [item.strip() for item in value if isinstance(item, str)]
        return normalized[:limit]

    def _compact_action_history_for_prompt(
        self,
        action_history: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        compacted: list[dict[str, Any]] = []
        latest_step = self._latest_history_step(action_history)
        previous_snapshot_signature: tuple[Any, ...] | None = None
        for entry in action_history:
            if not isinstance(entry, dict):
                continue
            result_snapshot = (
                entry.get("result_snapshot")
                if isinstance(entry.get("result_snapshot"), dict)
                else {}
            )
            geometry = (
                result_snapshot.get("geometry")
                if isinstance(result_snapshot.get("geometry"), dict)
                else {}
            )
            features = self._normalize_string_list(
                result_snapshot.get("features"),
                limit=_MAX_PROMPT_FEATURES,
            )
            issues = self._normalize_string_list(
                result_snapshot.get("issues"),
                limit=_MAX_PROMPT_ISSUES,
            )
            snapshot_step = result_snapshot.get("step")
            geometry_signature = self._history_geometry_signature(geometry)
            snapshot_signature = (
                tuple(features),
                tuple(issues),
                geometry_signature,
            )
            snapshot_summary: dict[str, Any] = {}
            if snapshot_step is not None:
                snapshot_summary["step"] = snapshot_step
            if (
                previous_snapshot_signature is not None
                and snapshot_signature == previous_snapshot_signature
            ):
                snapshot_summary["same_state_as_previous"] = True
            else:
                if features:
                    snapshot_summary["features"] = features
                if issues:
                    snapshot_summary["issues"] = issues
                compact_geometry = self._compact_history_geometry_for_prompt(
                    geometry=geometry,
                    previous_signature=(
                        previous_snapshot_signature[2]
                        if previous_snapshot_signature is not None
                        else None
                    ),
                    current_signature=geometry_signature,
                )
                if compact_geometry:
                    snapshot_summary["geometry"] = compact_geometry
            previous_snapshot_signature = snapshot_signature
            compacted.append(
                {
                    "step": entry.get("step"),
                    "action_type": entry.get("action_type"),
                    "action_params": self._sanitize_prompt_action_params(
                        entry.get("action_params", {}),
                        latest_step=latest_step,
                    )
                    if isinstance(entry.get("action_params"), dict)
                    else {},
                    "success": entry.get("success"),
                    "error": entry.get("error"),
                    "snapshot_summary": snapshot_summary,
                }
            )
        return compacted

    def _history_geometry_signature(
        self,
        geometry: dict[str, Any],
    ) -> tuple[Any, ...] | None:
        if not isinstance(geometry, dict) or not geometry:
            return None
        bbox = geometry.get("bbox")
        bbox_signature: tuple[Any, ...] | None = None
        if isinstance(bbox, list):
            bbox_signature = tuple(
                round(float(value), 6) if isinstance(value, (int, float)) else value
                for value in bbox[:3]
            )
        return (
            geometry.get("solids"),
            geometry.get("faces"),
            geometry.get("edges"),
            round(float(geometry.get("volume")), 6)
            if isinstance(geometry.get("volume"), (int, float))
            else None,
            round(float(geometry.get("surface_area")), 6)
            if isinstance(geometry.get("surface_area"), (int, float))
            else None,
            bbox_signature,
        )

    def _compact_history_geometry_for_prompt(
        self,
        *,
        geometry: dict[str, Any],
        previous_signature: tuple[Any, ...] | None,
        current_signature: tuple[Any, ...] | None,
    ) -> dict[str, Any]:
        if not isinstance(geometry, dict) or not geometry:
            return {}
        if previous_signature is not None and current_signature == previous_signature:
            return {
                "same_geometry_as_previous": True,
                "solids": geometry.get("solids"),
            }
        compact: dict[str, Any] = {
            "solids": geometry.get("solids"),
            "faces": geometry.get("faces"),
            "edges": geometry.get("edges"),
        }
        bbox = geometry.get("bbox")
        if isinstance(bbox, list):
            compact["bbox"] = bbox[:3]
        return compact

    def _latest_history_step(self, action_history: list[dict[str, Any]]) -> int | None:
        latest: int | None = None
        for entry in action_history:
            if not isinstance(entry, dict):
                continue
            step = entry.get("step")
            if isinstance(step, int) and step > 0:
                latest = step if latest is None else max(latest, step)
        return latest

    def _latest_evidence_step(self, evidence_status: dict[str, Any] | None) -> int | None:
        if not isinstance(evidence_status, dict):
            return None
        latest_step = evidence_status.get("latest_step")
        if isinstance(latest_step, int) and latest_step > 0:
            return latest_step
        current = evidence_status.get("current")
        if not isinstance(current, dict):
            return None
        candidate_steps: list[int] = []
        for payload in current.values():
            if not isinstance(payload, dict):
                continue
            step = payload.get("step")
            if isinstance(step, int) and step > 0:
                candidate_steps.append(step)
        return max(candidate_steps) if candidate_steps else None

    def _sanitize_prompt_action_params(
        self,
        action_params: dict[str, Any],
        latest_step: int | None,
    ) -> dict[str, Any]:
        sanitized: dict[str, Any] = {}
        for key, value in action_params.items():
            sanitized[key] = self._sanitize_prompt_action_value(
                key=key,
                value=value,
                latest_step=latest_step,
            )
        return sanitized

    def _sanitize_prompt_action_value(
        self,
        key: str,
        value: Any,
        latest_step: int | None,
    ) -> Any:
        if isinstance(value, str):
            return self._sanitize_step_local_ref_for_prompt(
                key=key,
                ref=value,
                latest_step=latest_step,
            )
        if isinstance(value, list):
            return [
                self._sanitize_prompt_action_value(
                    key=key,
                    value=item,
                    latest_step=latest_step,
                )
                for item in value
            ]
        if isinstance(value, dict):
            return {
                nested_key: self._sanitize_prompt_action_value(
                    key=nested_key,
                    value=nested_value,
                    latest_step=latest_step,
                )
                for nested_key, nested_value in value.items()
            }
        return value

    def _sanitize_step_local_ref_for_prompt(
        self,
        key: str,
        ref: str,
        latest_step: int | None,
    ) -> str:
        match = _STEP_LOCAL_REF_RE.match(ref.strip())
        if match is None:
            return ref
        kind = match.group(1)
        ref_step = int(match.group(2))
        ref_id = match.group(3)
        if latest_step is None or ref_step >= latest_step:
            return ref
        ref_label = key if key.endswith("_ref") or key.endswith("_refs") else f"{kind}_ref"
        return f"<stale {ref_label} from step {ref_step}; use current evidence>"

    def _compact_query_snapshot_for_prompt(
        self,
        query_snapshot: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(query_snapshot, dict):
            return {}
        snapshot = (
            query_snapshot.get("snapshot")
            if isinstance(query_snapshot.get("snapshot"), dict)
            else {}
        )
        geometry = (
            snapshot.get("geometry")
            if isinstance(snapshot.get("geometry"), dict)
            else {}
        )
        return {
            "success": bool(query_snapshot.get("success")),
            "error_code": query_snapshot.get("error_code"),
            "error_message": query_snapshot.get("error_message"),
            "session_id": query_snapshot.get("session_id"),
            "step": query_snapshot.get("step"),
            "snapshot_summary": {
                "step": snapshot.get("step"),
                "features": self._normalize_string_list(
                    snapshot.get("features"),
                    limit=_MAX_PROMPT_FEATURES,
                ),
                "issues": self._normalize_string_list(
                    snapshot.get("issues"),
                    limit=_MAX_PROMPT_ISSUES,
                ),
                "geometry": {
                    "solids": geometry.get("solids"),
                    "faces": geometry.get("faces"),
                    "edges": geometry.get("edges"),
                    "volume": geometry.get("volume"),
                    "surface_area": geometry.get("surface_area"),
                    "bbox": geometry.get("bbox"),
                },
            },
        }

    def _compact_query_geometry_for_prompt(
        self,
        query_geometry: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(query_geometry, dict):
            return {}
        object_index = (
            query_geometry.get("object_index")
            if isinstance(query_geometry.get("object_index"), dict)
            else {}
        )
        compact_index = self._compact_geometry_index_for_prompt(object_index)
        return {
            "success": bool(query_geometry.get("success")),
            "error_code": query_geometry.get("error_code"),
            "error_message": query_geometry.get("error_message"),
            "session_id": query_geometry.get("session_id"),
            "step": query_geometry.get("step"),
            "geometry": query_geometry.get("geometry"),
            "features": self._normalize_string_list(
                query_geometry.get("features"),
                limit=_MAX_PROMPT_FEATURES,
            ),
            "issues": self._normalize_string_list(
                query_geometry.get("issues"),
                limit=_MAX_PROMPT_ISSUES,
            ),
            "matched_entity_ids": self._normalize_string_list(
                query_geometry.get("matched_entity_ids"),
                limit=_MAX_PROMPT_MATCHED_ENTITY_IDS,
            ),
            "next_offsets": {
                "next_solid_offset": query_geometry.get("next_solid_offset"),
                "next_face_offset": query_geometry.get("next_face_offset"),
                "next_edge_offset": query_geometry.get("next_edge_offset"),
            },
            "object_index_window": compact_index,
        }

    def _compact_query_sketch_for_prompt(
        self,
        query_sketch: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(query_sketch, dict):
            return {}
        sketch_state = (
            query_sketch.get("sketch_state")
            if isinstance(query_sketch.get("sketch_state"), dict)
            else {}
        )
        paths_raw = sketch_state.get("paths")
        profiles_raw = sketch_state.get("profiles")
        paths = paths_raw if isinstance(paths_raw, list) else []
        profiles = profiles_raw if isinstance(profiles_raw, list) else []
        compact_paths: list[dict[str, Any]] = []
        for item in paths[:4]:
            if not isinstance(item, dict):
                continue
            compact_paths.append(
                {
                    "path_ref": item.get("path_ref"),
                    "plane": item.get("plane"),
                    "segment_types": self._normalize_string_list(
                        item.get("segment_types"),
                        limit=8,
                    ),
                    "connected": item.get("connected"),
                    "closed": item.get("closed"),
                    "start_point": item.get("start_point"),
                    "end_point": item.get("end_point"),
                    "start_tangent": item.get("start_tangent"),
                    "terminal_tangent": item.get("terminal_tangent"),
                    "total_length": item.get("total_length"),
                    "bbox": item.get("bbox"),
                }
            )
        compact_profiles: list[dict[str, Any]] = []
        for item in profiles[:4]:
            if not isinstance(item, dict):
                continue
            loops_raw = item.get("loops")
            loops = loops_raw if isinstance(loops_raw, list) else []
            compact_profiles.append(
                {
                    "profile_ref": item.get("profile_ref"),
                    "window_index": item.get("window_index"),
                    "source_sketch_step": item.get("source_sketch_step"),
                    "plane": item.get("plane"),
                    "outer_loop_count": item.get("outer_loop_count"),
                    "inner_loop_count": item.get("inner_loop_count"),
                    "closed": item.get("closed"),
                    "loftable": item.get("loftable"),
                    "nested_relationship": item.get("nested_relationship"),
                    "primitive_types": self._normalize_string_list(
                        item.get("primitive_types"),
                        limit=8,
                    ),
                    "point_count": item.get("point_count"),
                    "regular_sides": item.get("regular_sides"),
                    "regular_polygon_size_mode": item.get("regular_polygon_size_mode"),
                    "regular_polygon_circumradius": item.get("regular_polygon_circumradius"),
                    "regular_polygon_apothem": item.get("regular_polygon_apothem"),
                    "rotation_degrees": item.get("rotation_degrees"),
                    "attached_path_ref": item.get("attached_path_ref"),
                    "frame_mode": item.get("frame_mode"),
                    "centers": item.get("centers"),
                    "loops": [
                        {
                            "loop_id": loop.get("loop_id"),
                            "loop_type": loop.get("loop_type"),
                            "role": loop.get("role"),
                            "center": loop.get("center"),
                            "radius": loop.get("radius"),
                        }
                        for loop in loops[:4]
                        if isinstance(loop, dict)
                    ],
                    "loop_radii": item.get("loop_radii"),
                    "estimated_area": item.get("estimated_area"),
                    "bbox": item.get("bbox"),
                }
            )
        return {
            "success": bool(query_sketch.get("success")),
            "error_code": query_sketch.get("error_code"),
            "error_message": query_sketch.get("error_message"),
            "session_id": query_sketch.get("session_id"),
            "step": query_sketch.get("step"),
            "sketch_state": {
                "plane": sketch_state.get("plane"),
                "origin": sketch_state.get("origin"),
                "path_refs": self._normalize_string_list(
                    sketch_state.get("path_refs"),
                    limit=8,
                ),
                "profile_refs": self._normalize_string_list(
                    sketch_state.get("profile_refs"),
                    limit=8,
                ),
                "profile_stack_order": self._normalize_string_list(
                    sketch_state.get("profile_stack_order"),
                    limit=8,
                ),
                "sweep_ready_profile_refs": self._normalize_string_list(
                    sketch_state.get("sweep_ready_profile_refs"),
                    limit=8,
                ),
                "loft_ready_profile_refs": self._normalize_string_list(
                    sketch_state.get("loft_ready_profile_refs"),
                    limit=8,
                ),
                "issues_by_path_ref": (
                    sketch_state.get("issues_by_path_ref")
                    if isinstance(sketch_state.get("issues_by_path_ref"), dict)
                    else {}
                ),
                "issues_by_profile_ref": (
                    sketch_state.get("issues_by_profile_ref")
                    if isinstance(sketch_state.get("issues_by_profile_ref"), dict)
                    else {}
                ),
                "issues": self._normalize_string_list(
                    sketch_state.get("issues"),
                    limit=_MAX_PROMPT_ISSUES,
                ),
                "paths": compact_paths,
                "profiles": compact_profiles,
            },
            "relation_index": self._compact_relation_index_for_prompt(
                query_sketch.get("relation_index")
            ),
        }

    def _compact_query_topology_for_prompt(
        self,
        query_topology: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(query_topology, dict):
            return {}
        topology_index = (
            query_topology.get("topology_index")
            if isinstance(query_topology.get("topology_index"), dict)
            else {}
        )
        candidate_sets_raw = query_topology.get("candidate_sets")
        candidate_sets = candidate_sets_raw if isinstance(candidate_sets_raw, list) else []
        compact_candidate_sets = []
        for item in candidate_sets[:_MAX_PROMPT_TOPOLOGY_CANDIDATE_SETS]:
            if not isinstance(item, dict):
                continue
            compact_candidate_sets.append(
                {
                    "candidate_id": item.get("candidate_id"),
                    "label": item.get("label"),
                    "entity_type": item.get("entity_type"),
                    "entity_ids": self._normalize_string_list(
                        item.get("entity_ids"),
                        limit=_MAX_PROMPT_TOPOLOGY_CANDIDATE_REFS,
                    ),
                    "ref_ids": self._normalize_string_list(
                        item.get("ref_ids"),
                        limit=_MAX_PROMPT_TOPOLOGY_CANDIDATE_REFS,
                    ),
                    "rationale": item.get("rationale"),
                    "metadata": self._compact_topology_candidate_metadata(
                        item.get("metadata")
                    ),
                }
            )
        return {
            "success": bool(query_topology.get("success")),
            "error_code": query_topology.get("error_code"),
            "error_message": query_topology.get("error_message"),
            "session_id": query_topology.get("session_id"),
            "step": query_topology.get("step"),
            "applied_hints": self._normalize_string_list(
                query_topology.get("applied_hints"),
                limit=12,
            ),
            "matched_entity_ids": self._normalize_string_list(
                query_topology.get("matched_entity_ids"),
                limit=_MAX_PROMPT_MATCHED_ENTITY_IDS,
            ),
            "matched_ref_ids": self._normalize_string_list(
                query_topology.get("matched_ref_ids"),
                limit=_MAX_PROMPT_MATCHED_ENTITY_IDS,
            ),
            "candidate_sets": compact_candidate_sets,
            "next_offsets": {
                "next_face_offset": query_topology.get("next_face_offset"),
                "next_edge_offset": query_topology.get("next_edge_offset"),
            },
            "topology_window": self._compact_topology_index_for_prompt(topology_index),
            "relation_index": self._compact_relation_index_for_prompt(
                query_topology.get("relation_index")
            ),
        }

    def _compact_topology_candidate_metadata(self, metadata: Any) -> dict[str, Any]:
        if not isinstance(metadata, dict):
            return {}
        compact: dict[str, Any] = {}
        for key in (
            "primary_axis",
            "axis_midpoint",
            "axis_min",
            "axis_max",
            "axis_span",
            "outer_half_span_estimate",
            "suggested_sketch_planes",
            "dominant_ref_id",
            "alignment_axis",
            "target_axis_side",
            "target_coordinate",
            "sketch_plane",
            "sketch_u_axis",
            "sketch_v_axis",
            "anchor_role",
            "anchor_ref_id",
            "anchor_point",
        ):
            value = metadata.get(key)
            if value in (None, "", [], {}):
                continue
            compact[key] = value
        return compact

    def _compact_topology_index_for_prompt(
        self,
        topology_index: dict[str, Any],
    ) -> dict[str, Any]:
        faces_raw = topology_index.get("faces")
        edges_raw = topology_index.get("edges")
        faces = faces_raw if isinstance(faces_raw, list) else []
        edges = edges_raw if isinstance(edges_raw, list) else []

        def _compact_bbox(raw_bbox: Any) -> dict[str, Any]:
            if not isinstance(raw_bbox, dict):
                return {}
            return {
                "xlen": raw_bbox.get("xlen"),
                "ylen": raw_bbox.get("ylen"),
                "zlen": raw_bbox.get("zlen"),
                "xmin": raw_bbox.get("xmin"),
                "xmax": raw_bbox.get("xmax"),
                "ymin": raw_bbox.get("ymin"),
                "ymax": raw_bbox.get("ymax"),
                "zmin": raw_bbox.get("zmin"),
                "zmax": raw_bbox.get("zmax"),
            }

        compact_faces = []
        for item in faces[:_MAX_PROMPT_TOPOLOGY_ITEMS_PER_TYPE]:
            if not isinstance(item, dict):
                continue
            compact_faces.append(
                {
                    "face_ref": item.get("face_ref"),
                    "geom_type": item.get("geom_type"),
                    "center": item.get("center"),
                    "normal": item.get("normal"),
                    "axis_direction": item.get("axis_direction"),
                    "radius": item.get("radius"),
                    "bbox": _compact_bbox(item.get("bbox")),
                }
            )

        compact_edges = []
        for item in edges[:_MAX_PROMPT_TOPOLOGY_ITEMS_PER_TYPE]:
            if not isinstance(item, dict):
                continue
            compact_edges.append(
                {
                    "edge_ref": item.get("edge_ref"),
                    "geom_type": item.get("geom_type"),
                    "center": item.get("center"),
                    "axis_direction": item.get("axis_direction"),
                    "radius": item.get("radius"),
                    "length": item.get("length"),
                    "bbox": _compact_bbox(item.get("bbox")),
                }
            )

        return {
            "faces": compact_faces,
            "edges": compact_edges,
            "faces_truncated": bool(topology_index.get("faces_truncated", False)),
            "edges_truncated": bool(topology_index.get("edges_truncated", False)),
            "face_offset": topology_index.get("face_offset"),
            "edge_offset": topology_index.get("edge_offset"),
            "faces_total": topology_index.get("faces_total"),
            "edges_total": topology_index.get("edges_total"),
        }

    def _compact_geometry_index_for_prompt(
        self,
        object_index: dict[str, Any],
    ) -> dict[str, Any]:
        solids_raw = object_index.get("solids")
        faces_raw = object_index.get("faces")
        edges_raw = object_index.get("edges")
        solids = solids_raw if isinstance(solids_raw, list) else []
        faces = faces_raw if isinstance(faces_raw, list) else []
        edges = edges_raw if isinstance(edges_raw, list) else []

        def _compact_bbox(raw_bbox: Any) -> dict[str, Any]:
            if not isinstance(raw_bbox, dict):
                return {}
            return {
                "xlen": raw_bbox.get("xlen"),
                "ylen": raw_bbox.get("ylen"),
                "zlen": raw_bbox.get("zlen"),
            }

        compact_solids = []
        for item in solids[:_MAX_PROMPT_GEOMETRY_ITEMS_PER_TYPE]:
            if not isinstance(item, dict):
                continue
            compact_solids.append(
                {
                    "solid_id": item.get("solid_id"),
                    "volume": item.get("volume"),
                    "surface_area": item.get("surface_area"),
                    "bbox": _compact_bbox(item.get("bbox")),
                }
            )

        compact_faces = []
        for item in faces[:_MAX_PROMPT_GEOMETRY_ITEMS_PER_TYPE]:
            if not isinstance(item, dict):
                continue
            compact_faces.append(
                {
                    "face_id": item.get("face_id"),
                    "area": item.get("area"),
                    "geom_type": item.get("geom_type"),
                    "bbox": _compact_bbox(item.get("bbox")),
                }
            )

        compact_edges = []
        for item in edges[:_MAX_PROMPT_GEOMETRY_ITEMS_PER_TYPE]:
            if not isinstance(item, dict):
                continue
            compact_edges.append(
                {
                    "edge_id": item.get("edge_id"),
                    "length": item.get("length"),
                    "geom_type": item.get("geom_type"),
                    "bbox": _compact_bbox(item.get("bbox")),
                }
            )

        return {
            "solids": compact_solids,
            "faces": compact_faces,
            "edges": compact_edges,
            "solids_truncated": bool(object_index.get("solids_truncated")),
            "faces_truncated": bool(object_index.get("faces_truncated")),
            "edges_truncated": bool(object_index.get("edges_truncated")),
            "max_items_per_type": object_index.get("max_items_per_type"),
            "total_counts": {
                "solids_total": object_index.get("solids_total"),
                "faces_total": object_index.get("faces_total"),
                "edges_total": object_index.get("edges_total"),
            },
            "offsets": {
                "solid_offset": object_index.get("solid_offset"),
                "face_offset": object_index.get("face_offset"),
                "edge_offset": object_index.get("edge_offset"),
                "next_solid_offset": object_index.get("next_solid_offset"),
                "next_face_offset": object_index.get("next_face_offset"),
                "next_edge_offset": object_index.get("next_edge_offset"),
            },
        }

    def _compact_requirement_validation_for_prompt(
        self,
        requirement_validation: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(requirement_validation, dict):
            return {}
        checks_raw = requirement_validation.get("checks")
        checks = checks_raw if isinstance(checks_raw, list) else []
        compact_checks = []
        for item in checks[:_MAX_PROMPT_CHECKS]:
            if not isinstance(item, dict):
                continue
            compact_checks.append(
                {
                    "check_id": item.get("check_id"),
                    "status": item.get("status"),
                    "blocking": item.get("blocking"),
                    "label": item.get("label"),
                    "evidence": item.get("evidence"),
                }
            )
        return {
            "success": bool(requirement_validation.get("success")),
            "error_code": requirement_validation.get("error_code"),
            "error_message": requirement_validation.get("error_message"),
            "session_id": requirement_validation.get("session_id"),
            "step": requirement_validation.get("step"),
            "is_complete": bool(requirement_validation.get("is_complete")),
            "blockers": self._normalize_string_list(
                requirement_validation.get("blockers"),
                limit=_MAX_PROMPT_CHECKS,
            ),
            "summary": requirement_validation.get("summary"),
            "checks": compact_checks,
            "relation_index": self._compact_relation_index_for_prompt(
                requirement_validation.get("relation_index")
            ),
        }

    def _compact_relation_index_for_prompt(
        self,
        relation_index: Any,
    ) -> dict[str, Any]:
        if not isinstance(relation_index, dict):
            return {}
        entities_raw = relation_index.get("entities")
        relations_raw = relation_index.get("relations")
        groups_raw = relation_index.get("relation_groups")
        entities = entities_raw if isinstance(entities_raw, list) else []
        relations = relations_raw if isinstance(relations_raw, list) else []
        relation_groups = groups_raw if isinstance(groups_raw, list) else []

        compact_entities: list[dict[str, Any]] = []
        for raw_entity in entities[:_MAX_PROMPT_RELATION_ENTITIES]:
            if not isinstance(raw_entity, dict):
                continue
            compact_entities.append(
                {
                    "entity_id": raw_entity.get("entity_id"),
                    "entity_type": raw_entity.get("entity_type"),
                    "ref": raw_entity.get("ref"),
                    "label": raw_entity.get("label"),
                    "attributes": self._compact_relation_mapping(
                        raw_entity.get("attributes")
                    ),
                }
            )

        compact_relations: list[dict[str, Any]] = []
        relation_type_counts: dict[str, int] = {}
        for raw_relation in relations[:_MAX_PROMPT_RELATIONS]:
            if not isinstance(raw_relation, dict):
                continue
            relation_type = str(raw_relation.get("relation_type", "")).strip()
            if relation_type:
                relation_type_counts[relation_type] = (
                    relation_type_counts.get(relation_type, 0) + 1
                )
            compact_relations.append(
                {
                    "relation_id": raw_relation.get("relation_id"),
                    "relation_type": raw_relation.get("relation_type"),
                    "lhs": raw_relation.get("lhs"),
                    "rhs": raw_relation.get("rhs"),
                    "members": self._normalize_string_list(
                        raw_relation.get("members"),
                        limit=8,
                    ),
                    "metrics": self._compact_relation_mapping(raw_relation.get("metrics")),
                    "evidence": raw_relation.get("evidence"),
                }
            )

        compact_groups: list[dict[str, Any]] = []
        group_type_counts: dict[str, int] = {}
        for raw_group in relation_groups[:_MAX_PROMPT_RELATION_GROUPS]:
            if not isinstance(raw_group, dict):
                continue
            group_type = str(raw_group.get("group_type", "")).strip()
            if group_type:
                group_type_counts[group_type] = group_type_counts.get(group_type, 0) + 1
            compact_groups.append(
                {
                    "group_id": raw_group.get("group_id"),
                    "group_type": raw_group.get("group_type"),
                    "members": self._normalize_string_list(
                        raw_group.get("members"),
                        limit=8,
                    ),
                    "derived": self._compact_relation_mapping(raw_group.get("derived")),
                    "evidence": raw_group.get("evidence"),
                }
            )
        return {
            "version": relation_index.get("version"),
            "source_tool": relation_index.get("source_tool"),
            "step": relation_index.get("step"),
            "summary": relation_index.get("summary"),
            "focus_entity_ids": self._normalize_string_list(
                relation_index.get("focus_entity_ids"),
                limit=8,
            ),
            "entity_count": len(entities),
            "relation_count": len(relations),
            "relation_group_count": len(relation_groups),
            "relation_type_counts": relation_type_counts,
            "group_type_counts": group_type_counts,
            "planner_digest": self._build_relation_index_digest(
                relation_index=relation_index,
                relation_type_counts=relation_type_counts,
                group_type_counts=group_type_counts,
            ),
            "entities": compact_entities,
            "relations": compact_relations,
            "relation_groups": compact_groups,
        }

    def _compact_relation_focus_for_prompt(
        self,
        relation_focus: Any,
    ) -> dict[str, Any]:
        if not isinstance(relation_focus, dict):
            return {}
        items_raw = relation_focus.get("items")
        items = items_raw if isinstance(items_raw, list) else []
        compact_items: list[dict[str, Any]] = []
        for raw_item in items[:_MAX_PROMPT_RELATION_FOCUS_ITEMS]:
            if not isinstance(raw_item, dict):
                continue
            compact_items.append(
                {
                    "focus_id": raw_item.get("focus_id"),
                    "focus_type": raw_item.get("focus_type"),
                    "priority": raw_item.get("priority"),
                    "required_tools": self._normalize_string_list(
                        raw_item.get("required_tools"),
                        limit=6,
                    ),
                    "expected_relation_types": self._normalize_string_list(
                        raw_item.get("expected_relation_types"),
                        limit=8,
                    ),
                    "expected_metrics": self._compact_relation_mapping(
                        raw_item.get("expected_metrics")
                    ),
                    "supporting_entity_ids": self._normalize_string_list(
                        raw_item.get("supporting_entity_ids"),
                        limit=8,
                    ),
                    "observation": raw_item.get("observation"),
                }
            )
        return {
            "version": relation_focus.get("version"),
            "step": relation_focus.get("step"),
            "state_mode": relation_focus.get("state_mode"),
            "selection_basis": relation_focus.get("selection_basis"),
            "summary": relation_focus.get("summary"),
            "item_count": len(items),
            "planner_digest": self._build_relation_focus_digest(items=items),
            "items": compact_items,
        }

    def _compact_relation_eval_for_prompt(
        self,
        relation_eval: Any,
    ) -> dict[str, Any]:
        if not isinstance(relation_eval, dict):
            return {}
        items_raw = relation_eval.get("items")
        items = items_raw if isinstance(items_raw, list) else []
        compact_items: list[dict[str, Any]] = []
        for raw_item in items[:_MAX_PROMPT_RELATION_EVAL_ITEMS]:
            if not isinstance(raw_item, dict):
                continue
            compact_items.append(
                {
                    "eval_id": raw_item.get("eval_id"),
                    "focus_id": raw_item.get("focus_id"),
                    "relation_family": raw_item.get("relation_family"),
                    "status": raw_item.get("status"),
                    "blocking": raw_item.get("blocking"),
                    "score": raw_item.get("score"),
                    "expected": self._compact_relation_mapping(raw_item.get("expected")),
                    "observed": self._compact_relation_mapping(raw_item.get("observed")),
                    "deviation": self._compact_relation_mapping(raw_item.get("deviation")),
                    "supporting_relation_ids": self._normalize_string_list(
                        raw_item.get("supporting_relation_ids"),
                        limit=8,
                    ),
                    "supporting_group_ids": self._normalize_string_list(
                        raw_item.get("supporting_group_ids"),
                        limit=8,
                    ),
                    "observation": raw_item.get("observation"),
                }
            )
        return {
            "version": relation_eval.get("version"),
            "step": relation_eval.get("step"),
            "state_mode": relation_eval.get("state_mode"),
            "selection_basis": relation_eval.get("selection_basis"),
            "summary": relation_eval.get("summary"),
            "blocking_eval_ids": self._normalize_string_list(
                relation_eval.get("blocking_eval_ids"),
                limit=8,
            ),
            "item_count": len(items),
            "planner_digest": self._build_relation_eval_digest(items=items),
            "items": compact_items,
        }

    def _sorted_count_items(
        self,
        counts: dict[str, int],
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        if not counts:
            return []
        valid_items: list[tuple[str, int]] = []
        for key, value in counts.items():
            name = str(key).strip()
            if not name or not isinstance(value, int) or value <= 0:
                continue
            valid_items.append((name, int(value)))
        valid_items.sort(key=lambda item: (-item[1], item[0]))
        return [
            {"name": name, "count": count}
            for name, count in valid_items[: max(1, limit)]
        ]

    def _build_relation_index_digest(
        self,
        relation_index: dict[str, Any],
        relation_type_counts: dict[str, int],
        group_type_counts: dict[str, int],
    ) -> dict[str, Any]:
        return {
            "focus_entity_ids": self._normalize_string_list(
                relation_index.get("focus_entity_ids"),
                limit=6,
            ),
            "top_relation_types": self._sorted_count_items(relation_type_counts, limit=6),
            "top_group_types": self._sorted_count_items(group_type_counts, limit=4),
        }

    def _build_relation_focus_digest(
        self,
        items: list[Any],
    ) -> dict[str, Any]:
        focus_types: dict[str, int] = {}
        required_tools: dict[str, int] = {}
        top_focuses: list[dict[str, Any]] = []
        for raw_item in items:
            if not isinstance(raw_item, dict):
                continue
            focus_type = str(raw_item.get("focus_type", "")).strip()
            if focus_type:
                focus_types[focus_type] = focus_types.get(focus_type, 0) + 1
            for tool_name in self._normalize_string_list(
                raw_item.get("required_tools"),
                limit=6,
            ):
                required_tools[tool_name] = required_tools.get(tool_name, 0) + 1
            priority_raw = raw_item.get("priority")
            priority = (
                float(priority_raw)
                if isinstance(priority_raw, (int, float))
                else -1.0
            )
            top_focuses.append(
                {
                    "focus_id": raw_item.get("focus_id"),
                    "focus_type": raw_item.get("focus_type"),
                    "priority": priority,
                }
            )
        top_focuses.sort(
            key=lambda item: (
                -float(item.get("priority", -1.0)),
                str(item.get("focus_id", "")),
            )
        )
        return {
            "top_focus_types": self._sorted_count_items(focus_types, limit=6),
            "required_tools": self._sorted_count_items(required_tools, limit=6),
            "top_focuses": top_focuses[:4],
        }

    def _build_relation_eval_digest(
        self,
        items: list[Any],
    ) -> dict[str, Any]:
        status_counts: dict[str, int] = {}
        blocking_families: dict[str, int] = {}
        blocking_examples: list[dict[str, Any]] = []
        for raw_item in items:
            if not isinstance(raw_item, dict):
                continue
            status = str(raw_item.get("status", "")).strip().lower()
            if status:
                status_counts[status] = status_counts.get(status, 0) + 1
            if raw_item.get("blocking") is True:
                family = str(raw_item.get("relation_family", "")).strip()
                if family:
                    blocking_families[family] = blocking_families.get(family, 0) + 1
                if len(blocking_examples) < 4:
                    blocking_examples.append(
                        {
                            "eval_id": raw_item.get("eval_id"),
                            "relation_family": raw_item.get("relation_family"),
                            "observation": raw_item.get("observation"),
                        }
                    )
        return {
            "status_counts": self._sorted_count_items(status_counts, limit=6),
            "blocking_families": self._sorted_count_items(blocking_families, limit=6),
            "blocking_examples": blocking_examples,
        }

    def _compact_active_surface_for_prompt(
        self,
        active_surface: Any,
    ) -> dict[str, Any]:
        if not isinstance(active_surface, dict):
            return {}
        return {
            "surface_id": active_surface.get("surface_id"),
            "surface_type": active_surface.get("surface_type"),
            "state_mode": active_surface.get("state_mode"),
            "latest_action_type": active_surface.get("latest_action_type"),
            "target_ref_ids": self._normalize_string_list(
                active_surface.get("target_ref_ids"),
                limit=8,
            ),
            "blocker_codes": self._normalize_string_list(
                active_surface.get("blocker_codes"),
                limit=8,
            ),
            "path_count": active_surface.get("path_count"),
            "profile_count": active_surface.get("profile_count"),
            "rationale": active_surface.get("rationale"),
        }

    def _compact_surface_policy_for_prompt(
        self,
        surface_policy: Any,
    ) -> dict[str, Any]:
        if not isinstance(surface_policy, dict):
            return {}
        return {
            "surface_type": surface_policy.get("surface_type"),
            "state_mode": surface_policy.get("state_mode"),
            "allowed_actions": self._normalize_string_list(
                surface_policy.get("allowed_actions"),
                limit=12,
            ),
            "required_evidence": self._normalize_string_list(
                surface_policy.get("required_evidence"),
                limit=8,
            ),
            "preferred_inspection": self._normalize_string_list(
                surface_policy.get("preferred_inspection"),
                limit=8,
            ),
            "inspection_partitions": self._compact_relation_mapping(
                surface_policy.get("inspection_partitions"),
            ),
            "joint_request_groups": surface_policy.get("joint_request_groups"),
            "rollback_scope": surface_policy.get("rollback_scope"),
        }

    def _compact_expected_outcome_for_prompt(
        self,
        expected_outcome: Any,
    ) -> dict[str, Any]:
        if not isinstance(expected_outcome, dict):
            return {}
        baseline = (
            expected_outcome.get("baseline")
            if isinstance(expected_outcome.get("baseline"), dict)
            else {}
        )
        return {
            "surface_type": expected_outcome.get("surface_type"),
            "summary": expected_outcome.get("summary"),
            "expected_changes": self._normalize_string_list(
                expected_outcome.get("expected_changes"),
                limit=10,
            ),
            "target_blockers": self._normalize_string_list(
                expected_outcome.get("target_blockers"),
                limit=8,
            ),
            "baseline": self._compact_relation_mapping(baseline),
        }

    def _compact_feature_agenda_for_prompt(
        self,
        feature_agenda: Any,
    ) -> dict[str, Any]:
        if not isinstance(feature_agenda, dict):
            return {}
        items_raw = feature_agenda.get("items")
        items = items_raw if isinstance(items_raw, list) else []
        compact_items: list[dict[str, Any]] = []
        for item in items[:6]:
            if not isinstance(item, dict):
                continue
            compact_items.append(
                {
                    "phase": item.get("phase"),
                    "status": item.get("status"),
                    "action_family": item.get("action_family"),
                    "face_targets": self._normalize_string_list(
                        item.get("face_targets"),
                        limit=3,
                    ),
                    "summary": item.get("summary"),
                }
            )
        return {
            "summary": feature_agenda.get("summary"),
            "next_pending_phase": feature_agenda.get("next_pending_phase"),
            "items": compact_items,
        }

    def _compact_outcome_delta_for_prompt(
        self,
        outcome_delta: Any,
    ) -> dict[str, Any]:
        if not isinstance(outcome_delta, dict):
            return {}
        results_raw = outcome_delta.get("change_results")
        results = results_raw if isinstance(results_raw, list) else []
        compact_results: list[dict[str, Any]] = []
        for item in results[:8]:
            if not isinstance(item, dict):
                continue
            compact_results.append(
                {
                    "change": item.get("change"),
                    "achieved": item.get("achieved"),
                    "observed": self._compact_relation_mapping(item.get("observed")),
                }
            )
        return {
            "surface_type": outcome_delta.get("surface_type"),
            "status": outcome_delta.get("status"),
            "summary": outcome_delta.get("summary"),
            "expected_changes": self._normalize_string_list(
                outcome_delta.get("expected_changes"),
                limit=10,
            ),
            "achieved_changes": self._normalize_string_list(
                outcome_delta.get("achieved_changes"),
                limit=10,
            ),
            "missing_changes": self._normalize_string_list(
                outcome_delta.get("missing_changes"),
                limit=10,
            ),
            "current_blockers": self._normalize_string_list(
                outcome_delta.get("current_blockers"),
                limit=8,
            ),
            "change_results": compact_results,
        }

    def _compact_relation_mapping(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        compact: dict[str, Any] = {}
        for key, raw_item in value.items():
            if raw_item in (None, "", [], {}):
                continue
            if isinstance(raw_item, dict):
                nested: dict[str, Any] = {}
                for nested_key, nested_value in raw_item.items():
                    if nested_value in (None, "", [], {}):
                        continue
                    nested[nested_key] = nested_value
                if nested:
                    compact[str(key)] = nested
                continue
            compact[str(key)] = raw_item
        return compact

    def _compact_evidence_status_for_prompt(
        self,
        evidence_status: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(evidence_status, dict):
            return {}
        current = evidence_status.get("current")
        stale = evidence_status.get("stale")
        return {
            "current": current if isinstance(current, dict) else {},
            "stale": stale if isinstance(stale, dict) else {},
            "required_missing": self._normalize_string_list(
                evidence_status.get("required_missing"),
                limit=_MAX_PROMPT_CHECKS,
            ),
            "required_for_next_safe_step": self._normalize_string_list(
                evidence_status.get("required_for_next_safe_step"),
                limit=_MAX_PROMPT_CHECKS,
            ),
            "latest_step": evidence_status.get("latest_step"),
            "state_mode": evidence_status.get("state_mode"),
        }

    def _compact_latest_action_result_for_prompt(
        self,
        latest_action_result: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(latest_action_result, dict):
            return {}
        execution_diagnostics = (
            latest_action_result.get("execution_diagnostics")
            if isinstance(latest_action_result.get("execution_diagnostics"), dict)
            else {}
        )
        return {
            "success": latest_action_result.get("success"),
            "error_message": latest_action_result.get("error_message"),
            "stderr": latest_action_result.get("stderr"),
            "snapshot_step": (
                latest_action_result.get("snapshot", {}).get("step")
                if isinstance(latest_action_result.get("snapshot"), dict)
                else None
            ),
            "execution_diagnostics": execution_diagnostics,
        }

    def _extract_code(self, content: str) -> str:
        """Extract Python code from LLM response."""
        content = content.strip()
        code_block_pattern = r"```(?:python)?\s*\n(.*?)\n```"
        match = re.search(code_block_pattern, content, re.DOTALL)
        if match:
            return match.group(1).strip()
        return content
