from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from llm.interface import LLMMessage
from sub_agent_runtime.compact import (
    apply_turn_budget_with_report,
    compact_jsonish,
    render_json_length,
)
from sub_agent_runtime.prompting.diagnostics_policy import should_include_diagnostics
from sub_agent_runtime.prompting.failures import (
    RETAINABLE_EXECUTE_BUILD123D_FAILURE_KINDS as _RETAINABLE_EXECUTE_CADQUERY_FAILURE_KINDS,
    build_previous_tool_failure_summary as _build_previous_tool_failure_summary,
    classify_write_failure as _classify_write_failure,
    failure_recommended_next_steps as _failure_recommended_next_steps,
    failure_recommended_next_tools as _failure_recommended_next_tools,
    failure_recovery_bias as _failure_recovery_bias,
    is_pre_solid_action_type as _is_pre_solid_action_type,
    summarize_failure_lint_hits as _summarize_failure_lint_hits_impl,
    summarize_failure_repair_recipe as _summarize_failure_repair_recipe_impl,
)
from sub_agent_runtime.prompting.skill_assembly import build_runtime_skill_pack
from sub_agent_runtime.semantic_kernel.digest import build_domain_kernel_digest
from sub_agent_runtime.turn_state import (
    RunState,
    ToolCategory,
    TurnToolPolicy,
    build_feature_chain_budget_risk,
    build_post_solid_semantic_admission_signal,
)

_STALE_AFTER_SUCCESSFUL_WRITE_TOOL_NAMES = {
    "query_kernel_state",
    "query_snapshot",
    "query_sketch",
    "query_geometry",
    "query_topology",
    "query_feature_probes",
    "validate_requirement",
    "render_view",
    "get_history",
    "execute_build123d_probe",
}

_AUTHORITATIVE_POST_WRITE_JUDGMENT_TOOLS = {
    "validate_requirement",
    "query_kernel_state",
    "query_feature_probes",
    "execute_build123d_probe",
}

_CONFLICT_CAPABLE_STALE_TOOLS = {
    "validate_requirement",
    "query_kernel_state",
    "query_feature_probes",
    "execute_build123d_probe",
}


@dataclass(slots=True)
class PromptBuildResult:
    payload: dict[str, Any]
    messages: list[LLMMessage]
    metrics: dict[str, Any]
    compaction_report: dict[str, Any]


class V2ContextManager:
    """Build compact turn-oriented model context for the V2 runtime."""

    def __init__(
        self,
        *,
        soft_chars: int = 20000,
        hard_chars: int = 35000,
    ) -> None:
        self._soft_chars = soft_chars
        self._hard_chars = hard_chars

    def build_prompt_payload(
        self,
        *,
        run_state: RunState,
        diagnostics: dict[str, Any] | None = None,
        tool_partitions: dict[str, Any] | None = None,
        turn_tool_policy: TurnToolPolicy | None = None,
        max_rounds: int | None = None,
    ) -> dict[str, Any]:
        return self.build_prompt_bundle(
            run_state=run_state,
            diagnostics=diagnostics,
            tool_partitions=tool_partitions,
            turn_tool_policy=turn_tool_policy,
            max_rounds=max_rounds,
        ).payload

    def build_messages(
        self,
        *,
        run_state: RunState,
        diagnostics: dict[str, Any] | None = None,
        tool_partitions: dict[str, Any] | None = None,
        turn_tool_policy: TurnToolPolicy | None = None,
        max_rounds: int | None = None,
    ) -> list[LLMMessage]:
        return self.build_prompt_bundle(
            run_state=run_state,
            diagnostics=diagnostics,
            tool_partitions=tool_partitions,
            turn_tool_policy=turn_tool_policy,
            max_rounds=max_rounds,
        ).messages

    def build_prompt_bundle(
        self,
        *,
        run_state: RunState,
        diagnostics: dict[str, Any] | None = None,
        tool_partitions: dict[str, Any] | None = None,
        turn_tool_policy: TurnToolPolicy | None = None,
        max_rounds: int | None = None,
    ) -> PromptBuildResult:
        raw_payload = self._build_prompt_payload(
            run_state=run_state,
            diagnostics=diagnostics,
            tool_partitions=tool_partitions,
            turn_tool_policy=turn_tool_policy,
            max_rounds=max_rounds,
        )
        raw_chars = render_json_length(raw_payload)
        payload, compaction_report = apply_turn_budget_with_report(
            raw_payload,
            soft_chars=self._soft_chars,
            hard_chars=self._hard_chars,
        )
        final_chars = render_json_length(payload)
        system_prompt = (
            "You are the CAD Agent V2. "
            "Choose tools directly. "
            "Work like a tool-using coding agent: inspect only what is needed for the next move. "
            "You may call multiple read tools in one response. "
            "Do not mix read tools with a write tool in the same response. "
            "At most one write tool is allowed per response. "
            "If a local sketch edit will require several apply_cad_action steps, emit only the next write for this turn and wait for the updated sketch/session state before sending the next local step. "
            "Default to execute_build123d as the first write. "
            "Only deviate on the initial write when the user explicitly asked for a local edit and a stable topology anchor already exists. "
            "Use apply_cad_action only when the edit is already local, topology-anchored, and obviously cheaper than a rebuild. "
            "If the requirement asks for separate parts and also declares overall dimensions or an assembled envelope, keep those parts in one shared assembled coordinate frame rather than translating them apart for visibility unless an exploded view is explicitly requested. "
            "When query_topology has already returned exact face_ref or edge_refs for a local finish, place those exact refs under action_params.face_ref or action_params.edge_refs instead of downgrading them to broad aliases such as face='top' or face='bottom'. "
            "Use validate_requirement only when completion judgment is actually needed. "
            "Use query_feature_probes when the remaining uncertainty is family-specific geometry rather than raw entity targeting. "
            "Use execute_build123d_probe when you need a one-off diagnostic Build123d/OCP script and the standard read tools are not enough. "
            "Avoid repeating broad inspection or validation when current evidence is already sufficient. "
            "Treat latest_write_health as the authoritative objective readback of the latest write. "
            "Treat domain_kernel_digest as the authoritative semantic state for active targets, blocked targets, and completed targets. "
            "Use query_kernel_state when you need a semantic readback without replaying long history. "
            "Use patch_domain_kernel only to refine semantic decomposition or blocker/completion tracking; it never changes geometry. "
            "If previous_tool_failure_summary is present, repair that concrete failure before broad re-inspection unless the current state evidence proves a different blocker is dominant. "
            "If previous_tool_failure_summary exposes a normalized failure kind or recovery bias, avoid repeating the same failing write pattern. "
            "If turn_tool_policy is present, obey it strictly for this turn and only call tools that remain exposed. "
            "If turn_tool_policy exposes one or more non-finish tools for this turn, do not stop at a prose-only repair sketch; emit at least one allowed tool call now. "
            "When the policy exposes exactly one write tool such as `execute_build123d`, call that tool directly instead of returning a code block or repair plan without a tool invocation. "
            "If runtime skill notes are present, treat them as concise operational guidance for the current failure mode. "
            "Sketch primitives such as `Circle(...)`, `Ellipse(...)`, and `Rectangle(...)` belong inside `BuildSketch`, not directly inside an active `BuildPart`. "
            "Do not mix `Circle(...)` with `BuildLine` + `make_face()` in the same `BuildSketch` to fake a semicircle or rounded notch; `Circle(...)` already creates a full disk. For a half-round profile, build the arc in `BuildLine`, close it with `Line(...)`, then call `make_face()`. "
            "Build123d `Cylinder(...)` accepts literal radius/height only; do not pass `axis=` or `length=`. If a cylinder must point along X or Y, create it first, close that builder, then orient the detached solid with `Rot(...)` and place it with `Pos(...)` or `Locations(...)`. "
            "Do not write `with Rot(...):` or `with Pos(...):`; they are transforms, not builder context managers. "
            "Do not import `ocp_vscode` or call `show(...)` / `show_object(...)`; sandbox execution must return geometry through `result = ...` only. "
            "Inside an active BuildPart, do not create a primitive and then relocate it with `Pos(...) * solid` or `Rot(...) * solid`; use `Locations(...)` at creation time, or close the builder first and transform the detached solid afterward. "
            "Do not open a nested `BuildPart` cutter inside an active host and then mutate `host.part -= cutter.part`; keep repeated cuts in the same authoritative host builder with `mode=Mode.SUBTRACT`, or close the host first and subtract the detached positive cutter afterward. "
            "Do not open a detached builder whose first real operation is subtractive. If the cut belongs to an existing host, keep it inside that authoritative host with `mode=Mode.SUBTRACT`; if a detached cutter is truly required, build it as a positive or `mode=Mode.PRIVATE` solid first and subtract it only after the host builder closes. "
            "Map named host faces to plane families by host normal: top/bottom -> `Plane.XY`, front/back -> `Plane.XZ`, and left/right -> `Plane.YZ`. For front/back notches, pockets, or label recesses, do not sketch them on `Plane.XY` or `Plane.YZ` and hope later transforms recover the host. "
            "For centered clamshells with depth on Y, keep the back seam at `y = -depth/2` and the front opening/notch boundary at `y = +depth/2`; do not swap those front/back seam signs during hinge or notch placement. "
            "For a back-edge clamshell pin hinge, the pin/barrel axis usually runs along X/width; do not leave hinge barrels or hinge pins as default Z-axis cylinders when their height or span is meant to follow the enclosure width. "
            "do not drop an unrotated default `Cylinder(...)` directly onto `(x, hinge_y, split_z)` or `(x, -depth/2, z)` inside lid/base builders and assume it became an X-axis hinge barrel or pin; without a supported rotation/orientation lane that cylinder still runs along Z. "
            "A plain `pin hinge` or `mechanical hinge` on a two-part lid/base enclosure does not by itself authorize extra detached hinge solids or a third physical part. Keep the default physical-part target at lid/base, and only detach the pin/hardware when the prompt explicitly asks for a removable pin, separate hinge parts, or an exposed hinge assembly. "
            "Build123d `extrude(amount=h)` grows one-sided from the active sketch plane; it does not automatically create a centered `[-h/2, +h/2]` shell interval around that plane. "
            "For centered lid/base intervals, sketch on the real start face plane or translate the finished solid afterward; do not assume `Locations((0, 0, center_z))` plus `extrude(amount=h)` creates a centered shell interval by itself. "
            "For rounded rectangular shells or bodies, do not invent `Box(..., radius=...)`; use `RectangleRounded(...)` plus BuildSketch/extrude or add explicit fillets after a plain box. For rounded pillbox or enclosure shells, prefer that rounded footprint directly over a first-pass broad shell-edge fillet across `edges().filter_by(Axis.Z)`. `RectangleRounded(width, depth, radius=...)` already uses the outer footprint spans, so do not shrink the requested outer width/depth to `width - 2*radius` / `depth - 2*radius` before the outer profile call unless the requirement explicitly defines inner straight spans instead of the overall envelope. "
            "If a detached helper, cavity proxy, or cutter needs anisotropic scaling, use lowercase `scale(shape, by=(sx, sy, sz))`; do not invent `Scale(...)` or `Scale.by(...)`. "
            "For detached hinge barrels, hinge pins, or other rotated helper solids, build them positively first, close that builder, then orient the closed solid with `Rot(...) * part` or `Pos(...) * Rot(...) * part`. "
            "Treat stale read evidence from before the latest successful write as expired, especially old probe or validation results. "
            "Trust freshest_evidence, latest_write_health, and evidence_status.stale_evidence_invalidated over older contradictory diagnostics. "
            "If stall_summary says the last turns were repeated read-only checks without state change, prefer a concrete repair write or an explicit finish decision over more of the same read tools. "
            "If round_budget pressure is high and the current incremental path likely needs more writes than rounds left, prefer a whole-part execute_build123d repair over extending a bounded local-finishing tail. "
            "If feature_completion_risk says several unsatisfied semantic features still remain after runtime has already entered a bounded local-finishing tail and the remaining round budget is tight, stop extending that tail and switch to execute_build123d now. "
            "If post_write_validation_recommended is true after a whole-part rewrite, prefer validate_requirement before broad inspection so you act on fresh blocker truth. "
            "Use the tool catalog summary, freshest evidence attachment, recent turn summaries, and artifact index as your primary working context. "
            "Use finish_run when you believe the requirement is complete or no further useful progress is possible. "
            "Write a concise public decision summary in normal text before or alongside your tool call. "
            "Your public summary should explain what you are checking or changing next without exposing private chain-of-thought."
        )
        messages = self._build_message_stack(
            run_state=run_state,
            payload=payload,
            system_prompt=system_prompt,
        )
        return PromptBuildResult(
            payload=payload,
            messages=messages,
            metrics={
                "raw_chars": raw_chars,
                "final_chars": final_chars,
                "used_diagnostics": "diagnostics" in payload,
                "turn_count": len(run_state.turns),
                "evidence_tool_count": len(run_state.evidence.latest_by_tool),
                "message_count": len(messages),
                "message_roles": [message.role for message in messages],
                "compaction": compaction_report,
            },
            compaction_report=compaction_report,
        )

    def _build_prompt_payload(
        self,
        *,
        run_state: RunState,
        diagnostics: dict[str, Any] | None = None,
        tool_partitions: dict[str, Any] | None = None,
        turn_tool_policy: TurnToolPolicy | None = None,
        max_rounds: int | None = None,
    ) -> dict[str, Any]:
        latest_write_health = self._build_latest_write_health(run_state)
        previous_tool_failure_summary = self.build_previous_tool_failure_summary(
            run_state
        )
        round_budget = self._build_round_budget(run_state, max_rounds=max_rounds)
        fresh_evidence, stale_evidence_invalidated = self._build_prompt_evidence_view(
            run_state
        )
        raw_kernel_digest = (
            build_domain_kernel_digest(run_state.feature_graph)
            if run_state.feature_graph is not None
            else {}
        )
        runtime_skills = build_runtime_skill_pack(
            requirements=run_state.requirements,
            latest_validation=run_state.latest_validation,
            latest_write_health=latest_write_health,
            previous_tool_failure_summary=previous_tool_failure_summary,
            domain_kernel_digest=raw_kernel_digest,
        )
        objective_health = self._build_objective_health(
            run_state,
            round_budget=round_budget,
        )
        freshness_source_round = self._freshness_source_round(
            run_state,
            fresh_evidence=fresh_evidence,
        )
        fresh_write_pending_judgment = self._fresh_write_pending_judgment(
            run_state,
            fresh_evidence=fresh_evidence,
        )
        evidence_conflict_detected = self._has_conflicting_stale_evidence(
            stale_evidence_invalidated
        )
        kernel_digest = compact_jsonish(
            raw_kernel_digest,
            max_depth=3,
            max_items=24,
            max_string_chars=160,
        )
        if isinstance(kernel_digest, dict):
            for key in (
                "feature_instance_count",
                "active_feature_instances",
                "kernel_patch_count",
                "kernel_patch_kinds",
                "repair_packet_count",
                "repair_packet_kinds",
                "latest_patch_repair_mode",
                "latest_patch_feature_instance_ids",
                "latest_patch_affected_hosts",
                "latest_patch_anchor_keys",
                "latest_patch_parameter_keys",
                "latest_patch_feature_instances",
                "latest_patch_repair_intent",
                "latest_repair_packet_family_id",
                "latest_repair_packet_feature_instance_id",
                "latest_repair_packet_repair_mode",
                "latest_repair_packet_host_frame",
                "latest_repair_packet_target_anchor_summary",
                "latest_repair_packet_realized_anchor_summary",
                "latest_repair_packet_recipe_id",
                "latest_repair_packet_recipe_summary",
                "latest_repair_packet_recipe_skeleton",
            ):
                if key in raw_kernel_digest:
                    kernel_digest[key] = raw_kernel_digest.get(key)
        topology_targeting_summary = self._summarize_topology_targeting(
            fresh_evidence.get("query_topology")
            if isinstance(fresh_evidence.get("query_topology"), dict)
            else None
        )
        payload: dict[str, Any] = {
            "requirements": run_state.requirements,
            "domain_kernel_digest": kernel_digest,
            "turn_status": self._build_turn_status(
                run_state,
                round_budget=round_budget,
            ),
            "round_budget": compact_jsonish(
                round_budget,
                max_depth=3,
                max_items=8,
                max_string_chars=160,
            ),
            "evidence_status": self._build_evidence_status(
                run_state,
                fresh_evidence=fresh_evidence,
                stale_evidence_invalidated=stale_evidence_invalidated,
                freshness_source_round=freshness_source_round,
                fresh_write_pending_judgment=fresh_write_pending_judgment,
                evidence_conflict_detected=evidence_conflict_detected,
            ),
            "freshest_evidence": self._summarize_evidence(fresh_evidence),
            "topology_targeting_summary": topology_targeting_summary,
            "local_finish_contract": self._build_local_finish_contract(
                turn_tool_policy=turn_tool_policy,
                topology_targeting_summary=topology_targeting_summary,
                domain_kernel_digest=raw_kernel_digest,
            ),
            "stale_evidence_invalidated": stale_evidence_invalidated,
            "evidence_conflict_detected": evidence_conflict_detected,
            "fresh_write_pending_judgment": fresh_write_pending_judgment,
            "freshness_source_round": freshness_source_round,
            "tool_partitions": compact_jsonish(
                tool_partitions or {},
                max_depth=3,
                max_items=8,
                max_string_chars=160,
            ),
            "primary_write_mode": "code_first",
            "objective_health": compact_jsonish(
                objective_health,
                max_depth=3,
                max_items=12,
                max_string_chars=160,
            ),
            "latest_write_health": compact_jsonish(
                latest_write_health,
                max_depth=3,
                max_items=10,
                max_string_chars=160,
            ),
            "runtime_skills": self._prepare_runtime_skills_payload(runtime_skills),
            "latest_write_summary": compact_jsonish(
                run_state.latest_write_payload,
                max_depth=3,
                max_items=6,
                max_string_chars=160,
            )
            if isinstance(run_state.latest_write_payload, dict)
            else None,
            "previous_tool_failure_summary": self._compact_previous_tool_failure_summary(
                previous_tool_failure_summary
            ),
            "turn_tool_policy": self._summarize_turn_tool_policy(turn_tool_policy),
            "stall_summary": compact_jsonish(
                self._build_stall_summary(run_state),
                max_depth=3,
                max_items=8,
                max_string_chars=160,
            ),
            "recent_turns": [
                self._summarize_turn(turn) for turn in run_state.recent_turns[-3:]
            ],
            "artifact_index": self._artifact_index(run_state),
            "recent_public_transcript": self._build_recent_public_transcript(run_state),
        }
        if self._should_include_diagnostics(run_state, diagnostics):
            payload["diagnostics"] = diagnostics or {}
        return payload

    def _compact_previous_tool_failure_summary(
        self,
        previous_tool_failure_summary: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(previous_tool_failure_summary, dict):
            return None
        compacted = compact_jsonish(
            previous_tool_failure_summary,
            max_depth=3,
            max_items=8,
            max_string_chars=240,
        )
        if not isinstance(compacted, dict):
            return compacted
        for key in (
            "failure_kind",
            "effective_failure_kind",
            "recovery_bias",
            "recommended_next_steps",
            "recommended_next_tools",
            "lint_hits",
            "repair_recipe",
        ):
            if key in previous_tool_failure_summary:
                compacted[key] = previous_tool_failure_summary.get(key)
        return compacted

    def _summarize_failure_lint_hits(
        self,
        lint_hits: Any,
    ) -> list[dict[str, Any]] | None:
        return _summarize_failure_lint_hits_impl(lint_hits)

    def _summarize_failure_repair_recipe(
        self,
        repair_recipe: Any,
    ) -> dict[str, Any] | None:
        return _summarize_failure_repair_recipe_impl(repair_recipe)

    def _build_message_stack(
        self,
        *,
        run_state: RunState,
        payload: dict[str, Any],
        system_prompt: str,
    ) -> list[LLMMessage]:
        messages: list[LLMMessage] = [LLMMessage(role="system", content=system_prompt)]
        messages.append(
            LLMMessage(
                role="user",
                content=(
                    "Requirement attachment:\n"
                    f"{json.dumps(run_state.requirements, ensure_ascii=False, indent=2)}"
                ),
            )
        )
        objective_health = payload.get("objective_health")
        if objective_health:
            messages.append(
                LLMMessage(
                    role="user",
                    content=(
                        "Objective health update:\n"
                        f"{json.dumps(objective_health, ensure_ascii=False, indent=2)}"
                    ),
                )
            )
        previous_tool_failure_summary = payload.get("previous_tool_failure_summary")
        if previous_tool_failure_summary:
            messages.append(
                LLMMessage(
                    role="user",
                    content=(
                        "Most recent write failure to repair before broad re-inspection:\n"
                        f"{json.dumps(previous_tool_failure_summary, ensure_ascii=False, indent=2)}"
                    ),
                )
            )
        runtime_skills = payload.get("runtime_skills")
        if runtime_skills:
            messages.append(
                LLMMessage(
                    role="user",
                    content=(
                        "Relevant CAD skill notes for this turn:\n"
                        f"{json.dumps(runtime_skills, ensure_ascii=False, indent=2)}"
                    ),
                )
            )
        feature_graph_attachment = self._build_feature_graph_attachment(payload)
        if feature_graph_attachment is not None:
            messages.append(feature_graph_attachment)
        transcript_attachment = self._build_transcript_attachment(run_state)
        if transcript_attachment is not None:
            messages.append(transcript_attachment)
        tool_catalog_attachment = self._build_tool_catalog_attachment(payload)
        if tool_catalog_attachment is not None:
            messages.append(tool_catalog_attachment)
        evidence_attachment = self._build_evidence_attachment(payload)
        if evidence_attachment is not None:
            messages.append(evidence_attachment)
        local_finish_focus_attachment = self._build_local_finish_focus_attachment(payload)
        if local_finish_focus_attachment is not None:
            messages.append(local_finish_focus_attachment)
        recent_turns_attachment = self._build_recent_turns_attachment(payload)
        if recent_turns_attachment is not None:
            messages.append(recent_turns_attachment)
        artifact_index_attachment = self._build_artifact_index_attachment(payload)
        if artifact_index_attachment is not None:
            messages.append(artifact_index_attachment)
        diagnostics_attachment = self._build_diagnostics_attachment(payload)
        if diagnostics_attachment is not None:
            messages.append(diagnostics_attachment)
        state_payload = self._build_turn_coordinator_attachment_payload(payload)
        messages.append(
            LLMMessage(
                role="user",
                content=(
                    "Current turn coordinator state:\n"
                    f"{json.dumps(state_payload, ensure_ascii=False, indent=2)}"
                ),
            )
        )
        return messages

    def _build_transcript_attachment(self, run_state: RunState) -> LLMMessage | None:
        transcript_payload = self._build_recent_public_transcript(run_state)
        if not transcript_payload:
            return None
        return LLMMessage(
            role="user",
            content=(
                "Recent conversation transcript for continuity only. "
                "Use it as prior context and continue with the next tool decision. "
                "Do not repeat it verbatim.\n"
                f"{json.dumps(compact_jsonish(transcript_payload, max_depth=3, max_items=6, max_string_chars=160), ensure_ascii=False, indent=2)}"
            ),
        )

    def _build_feature_graph_attachment(self, payload: dict[str, Any]) -> LLMMessage | None:
        kernel_digest = payload.get("domain_kernel_digest")
        if not kernel_digest:
            return None
        return LLMMessage(
            role="user",
            content=(
                "Authoritative semantic domain kernel digest:\n"
                f"{json.dumps(kernel_digest, ensure_ascii=False, indent=2)}"
            ),
        )

    def _build_tool_catalog_attachment(self, payload: dict[str, Any]) -> LLMMessage | None:
        tool_partitions = payload.get("tool_partitions")
        if not tool_partitions:
            return None
        return LLMMessage(
            role="user",
            content=(
                "Tool catalog summary for this turn:\n"
                f"{json.dumps(tool_partitions, ensure_ascii=False, indent=2)}"
            ),
        )

    def _build_evidence_attachment(self, payload: dict[str, Any]) -> LLMMessage | None:
        evidence_payload = {
            "evidence_status": payload.get("evidence_status"),
            "freshest_evidence": payload.get("freshest_evidence"),
            "topology_targeting_summary": payload.get("topology_targeting_summary"),
            "stale_evidence_invalidated": payload.get("stale_evidence_invalidated"),
            "latest_write_summary": payload.get("latest_write_summary"),
        }
        compacted = {
            key: value for key, value in evidence_payload.items() if value not in (None, {}, [])
        }
        if not compacted:
            return None
        return LLMMessage(
            role="user",
            content=(
                "Freshest tool evidence and write readback:\n"
                f"{json.dumps(compacted, ensure_ascii=False, indent=2)}"
            ),
        )

    def _build_local_finish_focus_attachment(self, payload: dict[str, Any]) -> LLMMessage | None:
        contract = payload.get("local_finish_contract")
        if not isinstance(contract, dict) or not contract:
            return None
        preferred_face_refs = [
            str(ref_id).strip()
            for ref_id in (contract.get("preferred_face_refs") or [])
            if str(ref_id).strip()
        ]
        preferred_edge_refs = [
            str(ref_id).strip()
            for ref_id in (contract.get("preferred_edge_refs") or [])
            if str(ref_id).strip()
        ]
        candidate_lines: list[str] = []
        for item in contract.get("candidate_sets") or []:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or item.get("candidate_id") or "").strip()
            preferred_ref_id = str(item.get("preferred_ref_id") or "").strip()
            if label and preferred_ref_id:
                candidate_lines.append(f"- {label}: {preferred_ref_id}")
        lines = ["Exact topology refs to consume now:"]
        if preferred_face_refs:
            lines.append(f"- preferred_face_refs: {', '.join(preferred_face_refs[:4])}")
        if preferred_edge_refs:
            lines.append(f"- preferred_edge_refs: {', '.join(preferred_edge_refs[:6])}")
        if candidate_lines:
            lines.append("- candidate set preferred refs:")
            lines.extend(candidate_lines[:4])
        preferred_action_types = [
            str(action_type).strip()
            for action_type in (contract.get("preferred_action_types") or [])
            if str(action_type).strip()
        ]
        if preferred_action_types:
            lines.append(f"- preferred_action_types: {', '.join(preferred_action_types[:4])}")
            lines.append(
                "- If one of those direct host-face actions already expresses the next edit, use it before opening create_sketch(face_ref=...)."
            )
        lines.append(
            "- Use one preferred exact ref directly in the next apply_cad_action instead of selecting a different ref from the broader matched list."
        )
        return LLMMessage(role="user", content="\n".join(lines))

    def _prepare_runtime_skills_payload(
        self,
        runtime_skills: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        if not isinstance(runtime_skills, list) or not runtime_skills:
            return []
        max_visible_skills = 8
        prepared: list[dict[str, Any]] = []
        sortable_skills: list[tuple[int, int, dict[str, Any]]] = []
        for index, item in enumerate(runtime_skills):
            if not isinstance(item, dict):
                continue
            priority_raw = item.get("context_priority")
            priority = 100
            if isinstance(priority_raw, (int, float)):
                priority = int(priority_raw)
            sortable_skills.append((priority, index, item))
        for _, _, item in sorted(sortable_skills, key=lambda entry: (entry[0], entry[1]))[:max_visible_skills]:
            skill_id = str(item.get("skill_id") or "").strip()
            when_relevant = str(item.get("when_relevant") or "").strip()
            guidance_raw = item.get("guidance")
            guidance: list[str] = []
            if isinstance(guidance_raw, list):
                for entry in guidance_raw[:6]:
                    if isinstance(entry, str) and entry.strip():
                        guidance.append(
                            self._clip_runtime_skill_text(entry.strip(), max_chars=520)
                        )
            elif isinstance(guidance_raw, str) and guidance_raw.strip():
                guidance.append(
                    self._clip_runtime_skill_text(guidance_raw.strip(), max_chars=520)
                )
            prepared_item: dict[str, Any] = {}
            if skill_id:
                prepared_item["skill_id"] = skill_id
            if when_relevant:
                prepared_item["when_relevant"] = self._clip_runtime_skill_text(
                    when_relevant,
                    max_chars=240,
                )
            if guidance:
                prepared_item["guidance"] = guidance
            if prepared_item:
                prepared.append(prepared_item)
        if len(runtime_skills) > len(prepared):
            prepared.append({"__truncated_skills__": len(runtime_skills) - len(prepared)})
        return prepared

    def _clip_runtime_skill_text(self, text: str, *, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return f"{text[:max_chars]}...[truncated {len(text) - max_chars} chars]"

    def _build_recent_turns_attachment(self, payload: dict[str, Any]) -> LLMMessage | None:
        recent_turns = payload.get("recent_turns")
        if not recent_turns:
            return None
        return LLMMessage(
            role="user",
            content=(
                "Recent turn summaries:\n"
                f"{json.dumps(recent_turns, ensure_ascii=False, indent=2)}"
            ),
        )

    def _build_artifact_index_attachment(self, payload: dict[str, Any]) -> LLMMessage | None:
        artifact_index = payload.get("artifact_index")
        if not artifact_index:
            return None
        return LLMMessage(
            role="user",
            content=(
                "Artifact index for inspectability:\n"
                f"{json.dumps(artifact_index, ensure_ascii=False, indent=2)}"
            ),
        )

    def _build_diagnostics_attachment(self, payload: dict[str, Any]) -> LLMMessage | None:
        diagnostics = payload.get("diagnostics")
        if not diagnostics:
            return None
        return LLMMessage(
            role="user",
            content=(
                "Diagnostics attachment (use only if the current blocker cannot be resolved from the latest evidence and tool catalog):\n"
                f"{json.dumps(diagnostics, ensure_ascii=False, indent=2)}"
            ),
        )

    def _build_turn_coordinator_attachment_payload(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        coordinator_payload = {
            "domain_kernel_digest": payload.get("domain_kernel_digest"),
            "turn_status": payload.get("turn_status"),
            "round_budget": payload.get("round_budget"),
            "objective_health": payload.get("objective_health"),
            "latest_write_health": payload.get("latest_write_health"),
            "previous_tool_failure_summary": payload.get("previous_tool_failure_summary"),
            "turn_tool_policy": payload.get("turn_tool_policy"),
            "stall_summary": payload.get("stall_summary"),
            "stale_evidence_invalidated": payload.get("stale_evidence_invalidated"),
            "evidence_conflict_detected": payload.get("evidence_conflict_detected"),
        }
        return {
            key: value for key, value in coordinator_payload.items() if value not in (None, {}, [])
        }

    def _summarize_turn_tool_policy(
        self,
        turn_tool_policy: TurnToolPolicy | None,
    ) -> dict[str, Any] | None:
        if turn_tool_policy is None:
            return None
        policy_payload = {
            "policy_id": turn_tool_policy.policy_id,
            "mode": turn_tool_policy.mode,
            "reason": turn_tool_policy.reason,
            "allowed_tool_names": turn_tool_policy.allowed_tool_names,
            "blocked_tool_names": turn_tool_policy.blocked_tool_names,
            "preferred_tool_names": turn_tool_policy.preferred_tool_names,
            "preferred_probe_families": turn_tool_policy.preferred_probe_families,
        }
        return {
            key: value
            for key, value in policy_payload.items()
            if value not in (None, {}, [])
        }

    def _build_recent_public_transcript(self, run_state: RunState) -> list[dict[str, Any]]:
        transcript: list[dict[str, Any]] = []
        recent_turns = {turn.round_no: turn for turn in run_state.turns[-3:]}
        for log in run_state.visible_decision_logs[-3:]:
            entry = {
                "round": log.round_no,
                "decision_summary": log.summary,
                "why_next": log.why_next,
                "tool_names": log.tool_names,
                "requested_finish": log.requested_finish,
                "stop_reason": log.stop_reason,
            }
            matching_turn = recent_turns.get(log.round_no)
            if matching_turn is not None:
                entry["tool_results"] = [
                    {
                        "name": result.name,
                        "success": result.success,
                        "error": result.error,
                    }
                    for result in matching_turn.tool_results
                ]
            transcript.append(entry)
        return transcript

    def _build_objective_health(
        self,
        run_state: RunState,
        *,
        round_budget: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        latest_write_health = self._build_latest_write_health(run_state)
        stall_summary = self._build_stall_summary(run_state)
        previous_tool_failure_summary = self.build_previous_tool_failure_summary(run_state)
        current_sketch_completion_risk = self._build_current_sketch_completion_risk(
            run_state,
            round_budget=round_budget,
        )
        feature_completion_risk = self._build_feature_completion_risk(
            run_state,
            round_budget=round_budget,
        )
        post_solid_semantic_admission = self._build_post_solid_semantic_admission(
            run_state,
            round_budget=round_budget,
        )
        post_write_validation_recommended = self._latest_validation_needs_recheck(
            run_state
        )
        latest_validation = run_state.latest_validation or {}
        blockers = (
            latest_validation.get("blockers")[:6]
            if isinstance(latest_validation.get("blockers"), list)
            else []
        )
        invalid_signals = (
            latest_write_health.get("invalid_signals", [])
            if isinstance(latest_write_health, dict)
            else []
        )
        pre_solid_in_progress = bool(
            isinstance(latest_write_health, dict)
            and latest_write_health.get("pre_solid_in_progress")
        )
        stale_validation_blockers = bool(
            blockers and post_write_validation_recommended and not invalid_signals
        )
        status = "stable_or_unknown"
        recommended_bias = "continue_with_targeted_next_step"
        recommended_next_tools: list[str] = []
        reasons: list[str] = []

        if latest_write_health is None:
            status = "no_write_yet"
        elif pre_solid_in_progress:
            status = "pre_solid_progress"
            recommended_bias = "complete_pre_solid_window_before_whole_part_rebuild"
            pre_solid_action_type = latest_write_health.get("pre_solid_action_type")
            if isinstance(pre_solid_action_type, str) and pre_solid_action_type.strip():
                reasons.append(f"pre_solid:{pre_solid_action_type.strip()}")
        elif invalid_signals:
            status = "repair_needed"
            recommended_bias = "repair_last_write_before_broad_reinspection"
            reasons.extend(str(item) for item in invalid_signals[:4])
        elif previous_tool_failure_summary is not None:
            status = "repair_needed"
            recommended_bias = str(
                previous_tool_failure_summary.get("recovery_bias")
                or "repair_last_failed_write_before_more_reads"
            )
            reasons.append("latest_write_failed")
            next_tools = previous_tool_failure_summary.get("recommended_next_tools")
            if isinstance(next_tools, list):
                recommended_next_tools = [
                    str(item) for item in next_tools if isinstance(item, str)
                ]
        elif (
            blockers
            and not bool(latest_validation.get("is_complete"))
            and not stale_validation_blockers
        ):
            status = "semantic_gap"
            recommended_bias = "address_named_blockers_with_targeted_step"
            reasons.extend(f"blocker:{item}" for item in blockers[:4])
            if (
                isinstance(latest_write_health, dict)
                and str(latest_write_health.get("tool") or "").strip() == "execute_build123d"
            ):
                recommended_bias = "repair_last_code_write_before_generic_reads"
                recommended_next_tools = ["execute_build123d", "query_kernel_state"]

        if post_write_validation_recommended and not invalid_signals:
            status = "revalidate_after_whole_part_write"
            recommended_bias = "refresh_blocker_truth_before_more_broad_reads"
            reasons.append("whole_part_write_replaced_state_under_existing_blockers")

        if (
            isinstance(current_sketch_completion_risk, dict)
            and current_sketch_completion_risk.get("risk") in {"high", "critical"}
            and not invalid_signals
        ):
            if status in {"stable_or_unknown", "no_write_yet"}:
                status = "budget_constrained"
            if (
                current_sketch_completion_risk.get("recommended_fallback")
                == "prefer_apply_cad_action_material_write"
            ):
                recommended_bias = "prefer_materializing_active_local_sketch_over_rebuild"
                recommended_next_tools = ["apply_cad_action", "query_sketch"]
                reasons.append("active_sketch_profile_ready_for_material_write")
            else:
                recommended_bias = "prefer_whole_part_write_over_partial_sketch_step"
            reasons.append("unfinished_sketch_window_under_round_budget")

        if (
            isinstance(feature_completion_risk, dict)
            and feature_completion_risk.get("risk") in {"high", "critical"}
            and not invalid_signals
        ):
            if status in {"stable_or_unknown", "no_write_yet"}:
                status = "feature_budget_constrained"
            recommended_bias = "prefer_whole_part_write_over_partial_feature_chain"
            recommended_next_tools = ["execute_build123d", "query_kernel_state"]
            reasons.append("multi_feature_chain_exceeds_remaining_round_budget")

        if (
            isinstance(post_solid_semantic_admission, dict)
            and not invalid_signals
        ):
            if bool(post_solid_semantic_admission.get("direct_code_escape")):
                if status in {"stable_or_unknown", "no_write_yet", "semantic_gap"}:
                    status = "feature_budget_constrained"
                recommended_bias = "prefer_execute_build123d_over_local_feature_continuation"
                recommended_next_tools = ["execute_build123d", "query_kernel_state"]
                reasons.append("first_stable_solid_semantic_admission_would_exceed_budget")
            else:
                if status in {"stable_or_unknown", "no_write_yet", "semantic_gap"}:
                    status = "semantic_admission_required"
                recommended_bias = "refresh_semantic_state_before_reopening_whole_part_write"
                recommended_next_tools = [
                    "query_kernel_state",
                    "query_feature_probes",
                ]
                reasons.append("first_stable_solid_requires_semantic_admission")

        if stall_summary is not None and bool(stall_summary.get("active")):
            if status == "stable_or_unknown":
                status = "stalled"
            reasons.append("repeated_read_only_turns")
            if not invalid_signals and status in {"stalled", "stable_or_unknown", "no_write_yet"}:
                recommended_bias = str(
                    stall_summary.get("recommended_bias") or "avoid_repeating_read_only_turns"
                )
                next_tools = stall_summary.get("recommended_next_tools")
                if isinstance(next_tools, list):
                    recommended_next_tools = [
                        str(item) for item in next_tools if isinstance(item, str)
                    ]

        if (
            run_state.turns
            and run_state.turns[-1].requested_finish
            and latest_validation
            and not bool(latest_validation.get("is_complete"))
        ):
            status = "semantic_refresh_required"
            recommended_bias = (
                "refresh_semantic_state_with_query_kernel_state_before_more_reads_or_finish"
            )
            recommended_next_tools = ["query_kernel_state"]
            reasons.append("finish_attempt_left_validation_incomplete")

        previous_error = str(run_state.previous_error or "").strip()
        if previous_error:
            reasons.append("previous_error")
            if recommended_bias == "continue_with_targeted_next_step":
                recommended_bias = "recover_from_recent_error_with_targeted_step"

        payload = {
            "status": status,
            "recommended_bias": recommended_bias,
            "recommended_next_tools": recommended_next_tools,
            "reasons": reasons,
            "latest_write_round": (
                run_state.latest_write_turn.round_no if run_state.latest_write_turn else None
            ),
            "latest_validation_complete": bool(latest_validation.get("is_complete")),
            "latest_validation_summary": latest_validation.get("summary"),
            "latest_validation_blockers": blockers,
            "post_write_validation_recommended": post_write_validation_recommended,
        }
        if stale_validation_blockers:
            payload["stale_validation_blockers"] = True
        if current_sketch_completion_risk is not None:
            payload["current_sketch_completion_risk"] = current_sketch_completion_risk
        if feature_completion_risk is not None:
            payload["feature_completion_risk"] = feature_completion_risk
        if post_solid_semantic_admission is not None:
            payload["post_solid_semantic_admission"] = post_solid_semantic_admission
        return payload

    def _summarize_turn(self, turn: Any) -> dict[str, Any]:
        return {
            "round": turn.round_no,
            "decision_summary": turn.decision_summary,
            "tool_calls": [
                {"name": tool.name, "category": tool.category.value}
                for tool in turn.tool_calls
            ],
            "tool_results": [
                {
                    "name": result.name,
                    "success": result.success,
                    "error": result.error,
                }
                for result in turn.tool_results
            ],
            "requested_finish": turn.requested_finish,
            "error": turn.error,
        }

    def _build_turn_status(
        self,
        run_state: RunState,
        *,
        round_budget: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        latest_turn = run_state.turns[-1] if run_state.turns else None
        latest_validation = run_state.latest_validation or {}
        payload = {
            "turn_count": len(run_state.turns),
            "executed_action_count": run_state.executed_action_count,
            "inspection_only_rounds": run_state.inspection_only_rounds,
            "latest_write_tool": latest_turn.write_tool_name if latest_turn else None,
            "previous_error": run_state.previous_error,
            "latest_validation_complete": bool(latest_validation.get("is_complete")),
            "latest_validation_summary": latest_validation.get("summary"),
        }
        if round_budget:
            payload["remaining_rounds"] = round_budget.get("remaining_rounds")
            payload["budget_pressure"] = round_budget.get("pressure")
        return payload

    def _build_evidence_status(
        self,
        run_state: RunState,
        *,
        fresh_evidence: dict[str, dict[str, Any]],
        stale_evidence_invalidated: list[str],
        freshness_source_round: int | None,
        fresh_write_pending_judgment: bool,
        evidence_conflict_detected: bool,
    ) -> dict[str, Any]:
        available_tools = sorted(fresh_evidence.keys())
        by_tool: dict[str, Any] = {}
        for tool_name, payload in fresh_evidence.items():
            if not isinstance(payload, dict):
                by_tool[tool_name] = {"available": True}
                continue
            entry: dict[str, Any] = {"available": True}
            step = payload.get("step")
            if isinstance(step, int):
                entry["step"] = step
            success = payload.get("success")
            if isinstance(success, bool):
                entry["success"] = success
            summary = payload.get("summary")
            if isinstance(summary, str) and summary.strip():
                entry["summary"] = summary.strip()
            by_tool[tool_name] = entry
        return {
            "available_tools": available_tools,
            "by_tool": by_tool,
            "stale_tools": stale_evidence_invalidated,
            "stale_evidence_invalidated": stale_evidence_invalidated,
            "evidence_conflict_detected": evidence_conflict_detected,
            "fresh_write_pending_judgment": fresh_write_pending_judgment,
            "freshness_source_round": freshness_source_round,
            "latest_step_file": run_state.latest_step_file,
        }

    def _summarize_evidence(self, fresh_evidence: dict[str, dict[str, Any]]) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        for tool_name, payload in fresh_evidence.items():
            if tool_name == "validate_requirement":
                summary[tool_name] = self._summarize_validation_evidence(payload)
                continue
            if tool_name == "query_topology":
                summary[tool_name] = self._summarize_query_topology_evidence(payload)
                continue
            summary[tool_name] = compact_jsonish(
                payload,
                max_depth=3,
                max_items=6,
                max_string_chars=160,
            )
        return summary

    def _summarize_query_topology_evidence(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        summary = compact_jsonish(
            payload,
            max_depth=3,
            max_items=6,
            max_string_chars=160,
        )
        if not isinstance(summary, dict):
            return {}
        topology_summary = self._summarize_topology_targeting(payload)
        if topology_summary:
            summary["targeting_summary"] = topology_summary
        return summary

    def _summarize_topology_targeting(
        self,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(payload, dict) or not payload:
            return None
        matched_ref_ids = [
            str(item).strip()
            for item in (payload.get("matched_ref_ids") or [])
            if str(item).strip()
        ]
        candidate_sets_raw = (
            payload.get("candidate_sets")
            if isinstance(payload.get("candidate_sets"), list)
            else []
        )
        summarized_candidate_sets: list[dict[str, Any]] = []
        for item in candidate_sets_raw[:4]:
            if not isinstance(item, dict):
                continue
            candidate_ref_ids = [
                str(ref_id).strip()
                for ref_id in (item.get("ref_ids") or [])
                if str(ref_id).strip()
            ]
            semantic_host_roles = [
                str(role).strip()
                for role in (item.get("semantic_host_roles") or [])
                if str(role).strip()
            ]
            candidate_summary: dict[str, Any] = {}
            for key in ("candidate_id", "label", "entity_type", "host_role"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    candidate_summary[key] = value.strip()
            preferred_ref_id = str(item.get("preferred_ref_id") or "").strip()
            if preferred_ref_id:
                candidate_summary["preferred_ref_id"] = preferred_ref_id
            if semantic_host_roles:
                candidate_summary["semantic_host_roles"] = semantic_host_roles[:4]
            if candidate_ref_ids:
                candidate_summary["ref_ids"] = candidate_ref_ids[:8]
                candidate_summary["ref_count"] = len(candidate_ref_ids)
            if candidate_summary:
                summarized_candidate_sets.append(candidate_summary)
        matched_ref_id_count = payload.get("matched_ref_id_count")
        topology_index = payload.get("topology_index")
        summary: dict[str, Any] = {}
        if matched_ref_ids:
            summary["matched_ref_ids"] = matched_ref_ids[:8]
        if isinstance(matched_ref_id_count, int) and matched_ref_id_count > 0:
            summary["matched_ref_id_count"] = matched_ref_id_count
        elif matched_ref_ids:
            summary["matched_ref_id_count"] = len(matched_ref_ids)
        if summarized_candidate_sets:
            summary["candidate_sets"] = summarized_candidate_sets
        if isinstance(topology_index, dict):
            overview: dict[str, Any] = {}
            for key in ("faces_total", "edges_total", "faces_truncated", "edges_truncated"):
                value = topology_index.get(key)
                if value not in (None, {}, []):
                    overview[key] = value
            if overview:
                summary["topology_index_overview"] = overview
        return summary or None

    def _build_local_finish_contract(
        self,
        *,
        turn_tool_policy: TurnToolPolicy | None,
        topology_targeting_summary: dict[str, Any] | None,
        domain_kernel_digest: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if turn_tool_policy is None or str(turn_tool_policy.mode or "").strip() != "local_finish":
            return None
        if not isinstance(topology_targeting_summary, dict) or not topology_targeting_summary:
            return None
        matched_ref_ids = [
            str(item).strip()
            for item in (topology_targeting_summary.get("matched_ref_ids") or [])
            if str(item).strip()
        ]
        candidate_sets = (
            topology_targeting_summary.get("candidate_sets")
            if isinstance(topology_targeting_summary.get("candidate_sets"), list)
            else []
        )
        preferred_face_refs: list[str] = []
        preferred_edge_refs: list[str] = []

        def _append_unique(refs: list[str], ref_id: str) -> None:
            if ref_id and ref_id not in refs:
                refs.append(ref_id)

        candidate_set_ref_ids: list[str] = []
        for item in candidate_sets:
            if not isinstance(item, dict):
                continue
            preferred_ref_id = str(item.get("preferred_ref_id") or "").strip()
            ref_ids = [
                str(ref_id).strip()
                for ref_id in (item.get("ref_ids") or [])
                if str(ref_id).strip()
            ]
            if preferred_ref_id.startswith("face:"):
                _append_unique(preferred_face_refs, preferred_ref_id)
            elif preferred_ref_id.startswith("edge:"):
                _append_unique(preferred_edge_refs, preferred_ref_id)
            for ref_id in ref_ids:
                if ref_id:
                    candidate_set_ref_ids.append(ref_id)
        for ref_id in candidate_set_ref_ids:
            if ref_id.startswith("face:"):
                _append_unique(preferred_face_refs, ref_id)
            elif ref_id.startswith("edge:"):
                _append_unique(preferred_edge_refs, ref_id)
        for ref_id in matched_ref_ids:
            if ref_id.startswith("face:"):
                _append_unique(preferred_face_refs, ref_id)
            elif ref_id.startswith("edge:"):
                _append_unique(preferred_edge_refs, ref_id)
        active_family_ids = {
            str(item.get("family_id") or "").strip()
            for item in (domain_kernel_digest or {}).get("active_feature_instances", [])
            if isinstance(item, dict) and str(item.get("family_id") or "").strip()
        }
        preferred_action_types: list[str] = []
        if "explicit_anchor_hole" in active_family_ids:
            preferred_action_types.extend(["hole", "countersink", "counterbore"])
        preserved_layout = self._build_local_finish_preserved_layout(domain_kernel_digest)
        instructions = [
            "Consume the freshest query_topology refs directly in apply_cad_action.action_params; do not replace them with broad aliases like face='top' or face='bottom'.",
            "For hole, countersink, or sketch-on-face edits on an existing solid, pass action_params.face_ref from query_topology. When the next edit is directly expressible as a hole or countersink on that host face, prefer a direct hole/countersink apply_cad_action on that exact face_ref before opening a new sketch window.",
            "Keep centers in the local face frame after attaching to the host face instead of mixing them with guessed world-space coordinates.",
            "During a local_finish turn, do not spend apply_cad_action on get_history or any other session-control escape action; the next apply_cad_action should be a real geometry mutation that consumes one of the preferred face/edge refs when they already exist.",
        ]
        if preserved_layout is not None:
            instructions.append(
                "When semantic evidence already exposes a valid local center layout, reuse that exact center set for the remaining host-face-local detail instead of inventing new positions."
            )
        if preferred_edge_refs:
            instructions.append(
                "For fillet or chamfer local finishes, pass explicit action_params.edge_refs from query_topology instead of retrying selector guesses."
            )
        contract = {
            "must_consume_exact_topology_refs": True,
            "preferred_face_refs": preferred_face_refs[:4],
            "preferred_edge_refs": preferred_edge_refs[:8],
            "preferred_action_types": preferred_action_types[:4],
            "candidate_sets": compact_jsonish(
                candidate_sets[:4],
                max_depth=3,
                max_items=6,
                max_string_chars=160,
            ),
            "instructions": instructions,
        }
        if preserved_layout is not None:
            contract["preserve_existing_local_layout"] = preserved_layout
        return contract

    def _build_local_finish_preserved_layout(
        self,
        domain_kernel_digest: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(domain_kernel_digest, dict):
            return None
        active_feature_instances = domain_kernel_digest.get("active_feature_instances")
        if not isinstance(active_feature_instances, list):
            return None
        for item in active_feature_instances:
            if not isinstance(item, dict):
                continue
            parameter_bindings = (
                item.get("parameter_bindings")
                if isinstance(item.get("parameter_bindings"), dict)
                else {}
            )
            realized_centers = self._coerce_xy_point_list(
                parameter_bindings.get("realized_centers")
            )
            expected_centers = self._coerce_xy_point_list(
                parameter_bindings.get("expected_local_centers")
            )
            expected_count_raw = parameter_bindings.get("expected_local_center_count")
            expected_count = (
                int(expected_count_raw)
                if isinstance(expected_count_raw, (int, float))
                else (len(expected_centers) if expected_centers else None)
            )
            if not realized_centers or expected_count is None:
                continue
            if len(realized_centers) != expected_count:
                continue
            family_id = str(item.get("family_id") or "").strip()
            if not family_id:
                continue
            host_face = str(
                parameter_bindings.get("host_face")
                or item.get("host_ids", [""])[0]
                or ""
            ).strip()
            return {
                "family_id": family_id,
                "host_face": host_face,
                "expected_center_count": expected_count,
                "realized_centers": realized_centers[:6],
                "source": "domain_kernel_active_feature_instances",
            }
        return None

    def _coerce_xy_point_list(self, value: Any) -> list[list[float]]:
        if not isinstance(value, list):
            return []
        normalized: list[list[float]] = []
        for item in value:
            if (
                isinstance(item, (list, tuple))
                and len(item) >= 2
                and isinstance(item[0], (int, float))
                and isinstance(item[1], (int, float))
            ):
                normalized.append([float(item[0]), float(item[1])])
        return normalized

    def _summarize_validation_evidence(self, payload: dict[str, Any]) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        for key in (
            "success",
            "is_complete",
            "summary",
            "blockers",
            "blocker_taxonomy",
            "failed_checks",
            "core_check_count",
            "diagnostic_check_count",
        ):
            value = payload.get(key)
            if value in (None, {}, []):
                continue
            if key == "blocker_taxonomy":
                summary[key] = compact_jsonish(
                    value,
                    max_depth=3,
                    max_items=10,
                    max_string_chars=160,
                )
            elif key == "failed_checks":
                summary[key] = compact_jsonish(
                    value,
                    max_depth=2,
                    max_items=4,
                    max_string_chars=120,
                )
            else:
                summary[key] = value
        return summary

    def _build_prompt_evidence_view(
        self,
        run_state: RunState,
    ) -> tuple[dict[str, dict[str, Any]], list[str]]:
        latest_write_turn = run_state.latest_write_turn
        latest_write_round = latest_write_turn.round_no if latest_write_turn is not None else None
        latest_write_success = bool(
            isinstance(run_state.latest_write_payload, dict)
            and run_state.latest_write_payload.get("success")
        )
        fresh_evidence: dict[str, dict[str, Any]] = {}
        stale_evidence_invalidated: list[str] = []
        for tool_name, payload in run_state.evidence.latest_by_tool.items():
            evidence_round = run_state.evidence.rounds_by_tool.get(tool_name)
            if (
                tool_name == "query_kernel_state"
                and isinstance(payload, dict)
                and bool(payload.get("_synthetic_kernel_sync"))
            ):
                continue
            if (
                latest_write_success
                and
                latest_write_round is not None
                and tool_name in _STALE_AFTER_SUCCESSFUL_WRITE_TOOL_NAMES
                and isinstance(evidence_round, int)
                and evidence_round < latest_write_round
            ):
                stale_evidence_invalidated.append(tool_name)
                continue
            if self._should_suppress_prompt_evidence(tool_name, payload):
                continue
            if isinstance(payload, dict):
                fresh_evidence[tool_name] = payload
        current_round = len(run_state.turns) + 1
        for tool_name in stale_evidence_invalidated:
            if tool_name in {"query_feature_probes", "execute_build123d_probe"}:
                run_state.note_stale_probe_carry(current_round, tool_name)
        if latest_write_success and self._has_conflicting_stale_evidence(
            stale_evidence_invalidated
        ):
            run_state.note_evidence_conflict(current_round)
        return fresh_evidence, sorted(set(stale_evidence_invalidated))

    def _should_suppress_prompt_evidence(
        self,
        tool_name: str,
        payload: dict[str, Any] | Any,
    ) -> bool:
        if not isinstance(payload, dict):
            return False
        if tool_name != "query_feature_probes":
            return False
        if payload.get("success") is not False:
            return False
        error_code = str(payload.get("error_code") or "").strip().lower()
        summary = str(payload.get("summary") or "").strip().lower()
        return error_code == "invalid_request" and "no usable snapshot" in summary

    def _freshness_source_round(
        self,
        run_state: RunState,
        *,
        fresh_evidence: dict[str, dict[str, Any]],
    ) -> int | None:
        latest_write_turn = run_state.latest_write_turn
        if latest_write_turn is not None and bool(
            isinstance(run_state.latest_write_payload, dict)
            and run_state.latest_write_payload.get("success")
        ):
            return latest_write_turn.round_no
        evidence_rounds = [
            round_no
            for tool_name in fresh_evidence
            for round_no in [run_state.evidence.rounds_by_tool.get(tool_name)]
            if isinstance(round_no, int)
        ]
        if evidence_rounds:
            return max(evidence_rounds)
        return None

    def _fresh_write_pending_judgment(
        self,
        run_state: RunState,
        *,
        fresh_evidence: dict[str, dict[str, Any]],
    ) -> bool:
        latest_write_turn = run_state.latest_write_turn
        if latest_write_turn is None:
            return False
        latest_write_round = latest_write_turn.round_no
        if not bool(
            isinstance(run_state.latest_write_payload, dict)
            and run_state.latest_write_payload.get("success")
        ):
            return False
        for tool_name in _AUTHORITATIVE_POST_WRITE_JUDGMENT_TOOLS:
            evidence_round = run_state.evidence.rounds_by_tool.get(tool_name)
            if (
                tool_name in fresh_evidence
                and isinstance(evidence_round, int)
                and evidence_round >= latest_write_round
            ):
                return False
        return True

    def _has_conflicting_stale_evidence(self, stale_tool_names: list[str]) -> bool:
        return any(tool_name in _CONFLICT_CAPABLE_STALE_TOOLS for tool_name in stale_tool_names)

    def _artifact_index(self, run_state: RunState) -> dict[str, Any]:
        return compact_jsonish(
            {
                "latest_step_file": run_state.latest_step_file,
                "latest_output_files": run_state.latest_output_files,
                "evidence_artifacts": run_state.evidence.artifacts_by_tool,
            },
            max_depth=3,
            max_items=8,
            max_string_chars=160,
        )

    def _build_latest_write_health(self, run_state: RunState) -> dict[str, Any] | None:
        payload = run_state.latest_write_payload
        if not isinstance(payload, dict):
            return None
        latest_write_turn = run_state.latest_write_turn
        latest_action_type = self._latest_write_action_type(run_state)
        snapshot = payload.get("snapshot")
        geometry = snapshot.get("geometry") if isinstance(snapshot, dict) else {}
        solids = int(geometry.get("solids", 0) or 0) if isinstance(geometry, dict) else 0
        faces = int(geometry.get("faces", 0) or 0) if isinstance(geometry, dict) else 0
        edges = int(geometry.get("edges", 0) or 0) if isinstance(geometry, dict) else 0
        volume = (
            float(geometry.get("volume", 0.0) or 0.0) if isinstance(geometry, dict) else 0.0
        )
        material_volume = abs(volume)
        bbox_raw = geometry.get("bbox") if isinstance(geometry, dict) else []
        bbox = (
            [float(value) for value in bbox_raw[:3]]
            if isinstance(bbox_raw, list) and len(bbox_raw) >= 3
            else []
        )
        bbox_min_raw = geometry.get("bbox_min") if isinstance(geometry, dict) else []
        bbox_max_raw = geometry.get("bbox_max") if isinstance(geometry, dict) else []
        bbox_min = (
            [float(value) for value in bbox_min_raw[:3]]
            if isinstance(bbox_min_raw, list) and len(bbox_min_raw) >= 3
            else []
        )
        bbox_max = (
            [float(value) for value in bbox_max_raw[:3]]
            if isinstance(bbox_max_raw, list) and len(bbox_max_raw) >= 3
            else []
        )
        positive_bbox_axes = sum(1 for value in bbox if abs(float(value)) > 1e-6)
        has_snapshot_geometry = isinstance(snapshot, dict) and isinstance(geometry, dict)
        step_file_hint = payload.get("step_file")
        flags = {
            "has_step_file": (
                bool(step_file_hint)
                if (payload.get("success") is False and not has_snapshot_geometry)
                else bool(run_state.latest_step_file)
            ),
            "has_solids": solids > 0,
            "has_positive_volume": material_volume > 1e-6,
            "has_signed_negative_volume": volume < -1e-6,
            "has_nonzero_bbox": positive_bbox_axes >= 2,
            "has_three_dimensional_bbox": positive_bbox_axes >= 3,
        }
        pre_solid_in_progress = (
            bool(payload.get("success"))
            and _is_pre_solid_action_type(latest_action_type)
            and solids == 0
        )
        invalid_signals: list[str] = []
        if payload.get("success") is False:
            invalid_signals.append("write_tool_reported_failure")
        if flags["has_step_file"] and not flags["has_solids"] and not pre_solid_in_progress:
            invalid_signals.append("step_file_without_solids")
        if flags["has_solids"] and not flags["has_positive_volume"]:
            invalid_signals.append("non_positive_volume")
        if bbox and not flags["has_nonzero_bbox"] and not pre_solid_in_progress:
            invalid_signals.append("degenerate_bbox")
        if flags["has_solids"] and bbox and not flags["has_three_dimensional_bbox"]:
            invalid_signals.append("flat_solid_bbox")
        summary: dict[str, Any] = {
            "tool": latest_write_turn.write_tool_name if latest_write_turn else None,
            "write_round": latest_write_turn.round_no if latest_write_turn else None,
            "success": payload.get("success"),
            "step_file": run_state.latest_step_file,
            "session_state_persisted": payload.get("session_state_persisted"),
            "geometry": {
                "solids": solids,
                "faces": faces,
                "edges": edges,
                "volume": volume,
                "volume_magnitude": material_volume,
                "bbox": bbox,
                "bbox_min": bbox_min,
                "bbox_max": bbox_max,
            },
            "flags": flags,
            "invalid_signals": invalid_signals,
        }
        if pre_solid_in_progress:
            summary["pre_solid_in_progress"] = True
            summary["pre_solid_action_type"] = latest_action_type
        if not has_snapshot_geometry:
            summary["geometry_unknown"] = True
        latest_turn = run_state.turns[-1] if run_state.turns else None
        if latest_turn is not None and latest_turn.write_tool_name is None:
            latest_validation = run_state.latest_validation or {}
            blockers = latest_validation.get("blockers")
            if isinstance(blockers, list) and blockers:
                summary["latest_validation_blockers"] = blockers[:6]
            if latest_validation:
                summary["latest_validation_complete"] = bool(
                    latest_validation.get("is_complete")
                )
        return summary

    def _build_round_budget(
        self,
        run_state: RunState,
        *,
        max_rounds: int | None,
    ) -> dict[str, Any]:
        if not isinstance(max_rounds, int) or max_rounds <= 0:
            return {}
        remaining_rounds = max(max_rounds - len(run_state.turns), 0)
        pressure = "normal"
        if remaining_rounds <= 1:
            pressure = "critical"
        elif remaining_rounds <= 2:
            pressure = "high"
        return {
            "max_rounds": max_rounds,
            "used_rounds": len(run_state.turns),
            "remaining_rounds": remaining_rounds,
            "pressure": pressure,
        }

    def _build_current_sketch_completion_risk(
        self,
        run_state: RunState,
        *,
        round_budget: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(round_budget, dict) or not round_budget:
            return None
        query_sketch_payload = run_state.evidence.latest_by_tool.get("query_sketch")
        sketch_state = (
            query_sketch_payload.get("sketch_state")
            if isinstance(query_sketch_payload, dict)
            else None
        )
        latest_action_type = self._latest_write_action_type(run_state)
        if not isinstance(sketch_state, dict) and latest_action_type != "create_sketch":
            return None
        profile_refs = (
            sketch_state.get("profile_refs")
            if isinstance(sketch_state, dict)
            and isinstance(sketch_state.get("profile_refs"), list)
            else []
        )
        path_refs = (
            sketch_state.get("path_refs")
            if isinstance(sketch_state, dict)
            and isinstance(sketch_state.get("path_refs"), list)
            else []
        )
        remaining_rounds = int(round_budget.get("remaining_rounds", 0) or 0)
        min_write_steps_remaining = 0
        open_window_reason: str | None = None
        if latest_action_type == "create_sketch":
            min_write_steps_remaining = 2
            open_window_reason = "new_sketch_without_profile"
        elif profile_refs:
            min_write_steps_remaining = 1
            open_window_reason = "profile_exists_but_material_write_pending"
        elif path_refs:
            min_write_steps_remaining = 2
            open_window_reason = "path_exists_but_profile_and_material_write_pending"
        if min_write_steps_remaining <= 0:
            return None
        risk = "normal"
        if remaining_rounds < min_write_steps_remaining:
            risk = "critical"
        elif remaining_rounds == min_write_steps_remaining:
            risk = "high"
        return {
            "risk": risk,
            "remaining_rounds": remaining_rounds,
            "min_write_steps_remaining": min_write_steps_remaining,
            "open_window_reason": open_window_reason,
            "recommended_fallback": (
                (
                    "prefer_apply_cad_action_material_write"
                    if profile_refs and risk in {"high", "critical"}
                    else "prefer_execute_build123d"
                )
                if risk in {"high", "critical"}
                else "incremental_write_is_still_viable"
            ),
            "active_plane": (
                str(sketch_state.get("plane"))
                if isinstance(sketch_state, dict)
                and isinstance(sketch_state.get("plane"), str)
                else None
            ),
        }

    def _build_feature_completion_risk(
        self,
        run_state: RunState,
        *,
        round_budget: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        return build_feature_chain_budget_risk(
            run_state,
            round_budget=round_budget,
        )

    def _build_post_solid_semantic_admission(
        self,
        run_state: RunState,
        *,
        round_budget: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        return build_post_solid_semantic_admission_signal(
            run_state,
            round_budget=round_budget,
        )

    def _latest_write_action_type(self, run_state: RunState) -> str | None:
        latest_write_turn = run_state.latest_write_turn
        if latest_write_turn is None:
            return None
        for tool_call in latest_write_turn.tool_calls:
            if tool_call.category != ToolCategory.WRITE:
                continue
            if tool_call.name != "apply_cad_action":
                return tool_call.name
            action_type = tool_call.arguments.get("action_type")
            if isinstance(action_type, str) and action_type.strip():
                return action_type.strip().lower()
            return "apply_cad_action"
        return None

    def _latest_validation_needs_recheck(self, run_state: RunState) -> bool:
        latest_write_turn = run_state.latest_write_turn
        if latest_write_turn is None or latest_write_turn.write_tool_name != "execute_build123d":
            return False
        latest_validation = run_state.latest_validation or {}
        blockers = latest_validation.get("blockers")
        if not isinstance(blockers, list) or not blockers:
            return False
        last_validation_round = max(
            (
                int(event.round_no)
                for event in run_state.agent_events
                if event.kind == "validation_result"
                and isinstance(event.round_no, int)
            ),
            default=-1,
        )
        return latest_write_turn.round_no > last_validation_round

    def build_previous_tool_failure_summary(
        self,
        run_state: RunState,
    ) -> dict[str, Any] | None:
        return _build_previous_tool_failure_summary(run_state)

    def _count_recent_write_failures(
        self,
        run_state: RunState,
        *,
        tool_name: str | None = None,
    ) -> int:
        count = 0
        normalized_tool = str(tool_name or "").strip().lower()
        for turn in reversed(run_state.turns):
            if turn.write_tool_name is None:
                continue
            failed_result = next(
                (
                    result
                    for result in turn.tool_results
                    if result.category == ToolCategory.WRITE
                    and (not result.success or bool(result.error))
                ),
                None,
            )
            if failed_result is None and not turn.error:
                break
            turn_tool = str(turn.write_tool_name or "").strip().lower()
            if normalized_tool and turn_tool != normalized_tool:
                break
            count += 1
        return count

    def _recent_write_failure_kinds(
        self,
        run_state: RunState,
        *,
        tool_name: str | None = None,
        max_items: int = 4,
    ) -> list[str]:
        kinds: list[str] = []
        normalized_tool = str(tool_name or "").strip().lower()
        for turn in reversed(run_state.turns):
            if turn.write_tool_name is None:
                continue
            failed_result = next(
                (
                    result
                    for result in turn.tool_results
                    if result.category == ToolCategory.WRITE
                    and (not result.success or bool(result.error))
                ),
                None,
            )
            if failed_result is None and not turn.error:
                break
            turn_tool = str(turn.write_tool_name or "").strip().lower()
            if normalized_tool and turn_tool != normalized_tool:
                break
            payload = failed_result.payload if failed_result is not None else {}
            stderr_text = (
                str(payload.get("stderr")).strip()
                if isinstance(payload, dict)
                and isinstance(payload.get("stderr"), str)
                and str(payload.get("stderr")).strip()
                else None
            )
            error_text = None
            if (
                failed_result is not None
                and isinstance(failed_result.error, str)
                and failed_result.error.strip()
            ):
                error_text = failed_result.error.strip()
            elif isinstance(turn.error, str) and turn.error.strip():
                error_text = turn.error.strip()
            elif isinstance(payload, dict) and isinstance(payload.get("error_message"), str):
                error_text = str(payload.get("error_message")).strip() or None
            payload_failure_kind = (
                str(payload.get("failure_kind")).strip()
                if isinstance(payload, dict)
                and isinstance(payload.get("failure_kind"), str)
                and str(payload.get("failure_kind")).strip()
                else None
            )
            failure_kind = payload_failure_kind or _classify_write_failure(
                tool_name=str(turn.write_tool_name or "").strip(),
                error_text=error_text,
                stderr_text=stderr_text,
            )
            if failure_kind:
                kinds.append(failure_kind)
                if len(kinds) >= max_items:
                    break
        return kinds

    def _build_stall_summary(self, run_state: RunState) -> dict[str, Any] | None:
        consecutive_read_only = run_state.consecutive_inspection_only_rounds
        if consecutive_read_only < 2:
            return None
        recent_patterns: list[list[str]] = []
        for turn in reversed(run_state.turns):
            if not turn.read_only:
                break
            recent_patterns.append([tool.name for tool in turn.tool_calls])
            if len(recent_patterns) >= 3:
                break
        latest_validation = run_state.latest_validation or {}
        blockers = latest_validation.get("blockers")
        return {
            "active": True,
            "consecutive_inspection_only_rounds": consecutive_read_only,
            "latest_write_round": (
                run_state.latest_write_turn.round_no if run_state.latest_write_turn else None
            ),
            "latest_step_file": run_state.latest_step_file,
            "latest_validation_blockers": blockers[:6]
            if isinstance(blockers, list)
            else [],
            "recent_read_patterns": list(reversed(recent_patterns)),
            "recommended_bias": (
                "refresh_semantic_state_with_query_kernel_state_before_more_reads_or_finish"
            ),
            "recommended_next_tools": ["query_kernel_state"],
        }

    def _should_include_diagnostics(
        self,
        run_state: RunState,
        diagnostics: dict[str, Any] | None,
    ) -> bool:
        return should_include_diagnostics(run_state, diagnostics)
