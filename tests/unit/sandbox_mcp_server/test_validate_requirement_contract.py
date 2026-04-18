import pytest

from sandbox_mcp_server.contracts import (
    ActionHistoryEntry,
    BoundingBox3D,
    CADActionType,
    CADStateSnapshot,
    GeometryInfo,
    QueryTopologyInput,
    RequirementClauseInterpretation,
    RequirementClauseStatus,
    RequirementCheck,
    RequirementCheckStatus,
    TopologyFaceEntity,
    TopologyObjectIndex,
    ValidateRequirementInput,
    ValidateRequirementOutput,
)
from sandbox_mcp_server.service import SandboxMCPService
from sandbox_mcp_server.service import build_validation_blocker_taxonomy
from sandbox_mcp_server.validation_evidence import RequirementEvidenceBuilder
from sandbox_mcp_server.validation_evidence import RequirementEvidenceBundle
from sandbox_mcp_server.validation_grounding import attach_clause_grounding_surface
from sandbox_mcp_server.validation_interpretation import interpret_requirement_clauses
from sandbox_mcp_server.validation_interpretation import RequirementInterpretationSummary
from sandbox_mcp_server.validation_llm import (
    ValidationLLMClauseDecision,
    ValidationLLMOutput,
    ValidationLLMAdjudicator,
)


class _DummyRunner:
    async def execute(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("not used in this test")


class _ValidationAdjudicatorStub:
    async def adjudicate(self, **kwargs):  # type: ignore[no-untyped-def]
        clauses = kwargs.get("clauses") or []
        if not clauses:
            return None
        first_clause = clauses[0]
        return ValidationLLMOutput(
            summary="llm resolved the generic clause",
            clauses=[
                ValidationLLMClauseDecision(
                    clause_id=first_clause.clause_id,
                    status=RequirementClauseStatus.VERIFIED,
                    evidence="llm adjudicator resolved the unresolved clause from broader evidence",
                    decision_hints=[],
                    confidence=0.95,
                )
            ],
        )


class _ValidationAdjudicatorProviderErrorStub:
    async def adjudicate(self, **kwargs):  # type: ignore[no-untyped-def]
        return ValidationLLMOutput(
            summary="__validation_llm_provider_error__:TimeoutError:validation timed out",
            clauses=[],
        )


class _RecordingValidationAdjudicatorStub:
    def __init__(self) -> None:
        self.called = False

    async def adjudicate(self, **kwargs):  # type: ignore[no-untyped-def]
        self.called = True
        return ValidationLLMOutput(summary="", clauses=[])


class _NoopLLMClient:
    async def complete(self, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("not used in timeout-budget test")


def _snapshot(
    *,
    step: int,
    solids: int = 1,
    faces: int = 6,
    edges: int = 12,
    volume: float = 100.0,
    bbox: list[float] | None = None,
) -> CADStateSnapshot:
    return CADStateSnapshot(
        step=step,
        features=[],
        geometry=GeometryInfo(
            solids=solids,
            faces=faces,
            edges=edges,
            volume=volume,
            bbox=bbox or [10.0, 10.0, 10.0],
            center_of_mass=[0.0, 0.0, 0.0],
            surface_area=50.0,
            bbox_min=[0.0, 0.0, 0.0],
            bbox_max=bbox or [10.0, 10.0, 10.0],
        ),
        issues=[],
        warnings=[],
        blockers=[],
        images=[],
        sketch_state=None,
        geometry_objects=None,
        topology_index=None,
        success=True,
        error=None,
    )


def _bbox(
    *,
    xlen: float,
    ylen: float,
    zlen: float,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    zmin: float,
    zmax: float,
) -> BoundingBox3D:
    return BoundingBox3D(
        xlen=xlen,
        ylen=ylen,
        zlen=zlen,
        xmin=xmin,
        xmax=xmax,
        ymin=ymin,
        ymax=ymax,
        zmin=zmin,
        zmax=zmax,
    )


def _snapshot_with_topology(*, step: int) -> CADStateSnapshot:
    topology_index = TopologyObjectIndex(
        faces=[
            TopologyFaceEntity(
                face_ref=f"face:{step}:F_top",
                face_id="F_top",
                step=step,
                area=100.0,
                center=[0.0, 0.0, 10.0],
                normal=[0.0, 0.0, 1.0],
                geom_type="PLANE",
                bbox=_bbox(
                    xlen=10.0,
                    ylen=10.0,
                    zlen=0.0,
                    xmin=-5.0,
                    xmax=5.0,
                    ymin=-5.0,
                    ymax=5.0,
                    zmin=10.0,
                    zmax=10.0,
                ),
                parent_solid_id="S_top",
                edge_refs=[],
                adjacent_face_refs=[],
            )
        ],
        edges=[],
        faces_total=1,
        edges_total=0,
        max_items_per_type=20,
    )
    return CADStateSnapshot(
        step=step,
        features=[],
        geometry=GeometryInfo(
            solids=1,
            faces=1,
            edges=0,
            volume=100.0,
            bbox=[10.0, 10.0, 10.0],
            center_of_mass=[0.0, 0.0, 5.0],
            surface_area=100.0,
            bbox_min=[-5.0, -5.0, 0.0],
            bbox_max=[5.0, 5.0, 10.0],
        ),
        issues=[],
        warnings=[],
        blockers=[],
        images=[],
        sketch_state=None,
        geometry_objects=None,
        topology_index=topology_index,
        success=True,
        error=None,
    )


def test_validate_requirement_output_accepts_clause_interpretation_fields() -> None:
    output = ValidateRequirementOutput(
        success=True,
        session_id="session-1",
        clause_interpretations=[
            RequirementClauseInterpretation(
                clause_id="clause-1",
                clause_text="Add a centered hole",
                status=RequirementClauseStatus.VERIFIED,
                evidence="hole center found in topology",
                observation_tags=["topology"],
                decision_hints=["keep"],
            )
        ],
        coverage_confidence=0.75,
        insufficient_evidence=True,
        observation_tags=["geometry", "process"],
        decision_hints=["inspect history"],
    )

    assert output.clause_interpretations[0].status == RequirementClauseStatus.VERIFIED
    assert output.coverage_confidence == 0.75
    assert output.insufficient_evidence is True
    assert output.observation_tags == ["geometry", "process"]
    assert output.decision_hints == ["inspect history"]


def test_validation_llm_default_timeout_stays_within_validate_requirement_budget() -> None:
    adjudicator = ValidationLLMAdjudicator(llm_client=_NoopLLMClient())

    assert adjudicator._request_timeout_seconds <= 20.0


def test_body_dimension_clause_verifies_from_bbox_geometry() -> None:
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(step=1, solids=1, bbox=[60.0, 40.0, 8.0]),
        history=[],
        requirements={"description": "Create a 60 mm by 40 mm by 8 mm plate."},
        requirement_text="Create a 60 mm by 40 mm by 8 mm plate.",
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": "Create a 60 mm by 40 mm by 8 mm plate."},
        requirement_text="Create a 60 mm by 40 mm by 8 mm plate.",
    )

    assert summary.clause_interpretations[0].status == RequirementClauseStatus.VERIFIED
    assert summary.coverage_confidence == 1.0


def test_path_sweep_profile_outer_diameter_clause_prefers_profile_diameter_over_global_bbox() -> None:
    bundle = RequirementEvidenceBundle(
        requirement_text=(
            "Use the Sweep feature to construct. "
            "Draw the profile sketch: two concentric circles. "
            "With an outer diameter of 20.0mm. "
            "An inner diameter of 16.0mm."
        ),
        requirement_clauses=[
            "Draw the profile sketch: two concentric circles",
            "With an outer diameter of 20.0mm",
            "An inner diameter of 16.0mm",
        ],
        snapshot_step=1,
        geometry_facts={
            "solids": 1,
            "bbox": [90.0, 90.0, 20.0],
            "through_axisymmetric_radii": [8.0, 10.0],
        },
        topology_facts={
            "face_radii": [8.0, 10.0],
        },
        process_facts={},
        observation_tags=[],
        decision_hints=[],
        coverage_confidence=0.0,
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": bundle.requirement_text},
        requirement_text=bundle.requirement_text,
    )

    outer_clause = next(
        clause
        for clause in summary.clause_interpretations
        if clause.clause_id == "with_an_outer_diameter_of_20_0mm"
    )

    assert outer_clause.status == RequirementClauseStatus.VERIFIED
    assert "matched_outer_diameter=20.0" in outer_clause.evidence


def test_multi_part_clause_verifies_from_solid_count() -> None:
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(step=1, solids=2, bbox=[78.0, 56.0, 32.0]),
        history=[],
        requirements={"description": "Output separate parts: lid and base."},
        requirement_text="Output separate parts: lid and base.",
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": "Output separate parts: lid and base."},
        requirement_text="Output separate parts: lid and base.",
    )

    clause = summary.clause_interpretations[0]
    assert clause.status == RequirementClauseStatus.VERIFIED
    assert "geometry_solids=2" in clause.evidence


def test_attach_clause_grounding_surface_marks_explicit_anchor_clause_as_geometry_topology_bound() -> None:
    interpretation = RequirementInterpretationSummary(
        clause_interpretations=[
            RequirementClauseInterpretation(
                clause_id="hole_center_clause",
                clause_text="Add a countersink hole centered at x = 10.0 mm.",
                status=RequirementClauseStatus.INSUFFICIENT_EVIDENCE,
                evidence="bbox width observed from geometry snapshot",
                observation_tags=["geometry"],
                decision_hints=["query_topology"],
            )
        ],
        coverage_confidence=0.0,
        insufficient_evidence=["hole_center_clause"],
        observation_tags=["geometry"],
        decision_hints=["query_topology"],
    )

    updated, grounding_surface = attach_clause_grounding_surface(interpretation)

    clause = updated.clause_interpretations[0]
    assert clause.grounding_sources == ["geometry"]
    assert clause.required_evidence_kinds == ["geometry", "topology"]
    assert clause.overclaim_guard == "geometry_grounding_required"
    assert clause.family_binding == "explicit_anchor_hole"
    assert "query_topology" in clause.repair_hints
    assert "query_feature_probes" in clause.repair_hints
    assert grounding_surface["family_bindings"] == ["explicit_anchor_hole"]
    assert grounding_surface["required_evidence_kinds"] == ["geometry", "topology"]
    assert grounding_surface["overclaim_guard"] == "geometry_grounding_required"


def test_attach_clause_grounding_surface_keeps_centered_front_recess_on_named_face_local_edit() -> None:
    interpretation = RequirementInterpretationSummary(
        clause_interpretations=[
            RequirementClauseInterpretation(
                clause_id="front_recess_clause",
                clause_text="Add a centered rounded rectangle recess on the front face sized about 12mm x 6mm.",
                status=RequirementClauseStatus.INSUFFICIENT_EVIDENCE,
                evidence="front face edit observed from topology snapshot",
                observation_tags=["clause:local_feature", "topology"],
                decision_hints=["query_topology"],
            )
        ],
        coverage_confidence=0.0,
        insufficient_evidence=["front_recess_clause"],
        observation_tags=["topology"],
        decision_hints=["query_topology"],
    )

    updated, grounding_surface = attach_clause_grounding_surface(interpretation)

    clause = updated.clause_interpretations[0]
    assert clause.family_binding == "named_face_local_edit"
    assert clause.required_evidence_kinds == ["topology"]
    assert clause.overclaim_guard is None
    assert "query_topology" in clause.repair_hints
    assert grounding_surface["family_bindings"] == ["named_face_local_edit"]
    assert "explicit_anchor_hole" not in grounding_surface["family_bindings"]


def test_build_validation_blocker_taxonomy_prefers_local_finish_when_clause_grounding_requests_topology_targeting() -> None:
    taxonomy = build_validation_blocker_taxonomy(
        core_checks=[
            RequirementCheck(
                check_id="feature_countersink",
                label="Hole feature preserves countersink geometry",
                status=RequirementCheckStatus.FAIL,
                blocking=True,
                evidence="countersink_action=False",
            )
        ],
        diagnostic_checks=[],
        clause_interpretations=[
            RequirementClauseInterpretation(
                clause_id="a_countersink_on_the_mounting_face",
                clause_text="a countersink on the mounting face",
                status=RequirementClauseStatus.CONTRADICTED,
                evidence="cone_like_face_present=False",
                observation_tags=["clause:hole"],
                decision_hints=["repair the contradicted clause before finishing"],
                grounding_sources=["topology"],
                grounding_strength="partial",
                required_evidence_kinds=["geometry", "topology"],
                repair_hints=["query_topology", "query_feature_probes"],
                family_binding="explicit_anchor_hole",
            )
        ],
    )

    assert len(taxonomy) == 1
    assert taxonomy[0].recommended_repair_lane == "local_finish"
    assert "query_topology" in taxonomy[0].decision_hints
    assert "family:explicit_anchor_hole" in taxonomy[0].observation_tags


def test_interpret_requirement_clauses_verifies_directional_notch_when_feature_and_host_face_grounding_exist() -> None:
    requirement_text = "Create a rounded enclosure with a front thumb notch."
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(step=1, solids=1, bbox=[62.0, 40.0, 14.0]),
        history=[],
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
        supplemental_checks=[
            RequirementCheck(
                check_id="feature_notch_or_profile_cut",
                label="Notch/profile cut is present",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="execute_build123d_geometry_fallback=true, observed_snapshot_profile_shapes=['polygon', 'rectangle'], feature=subtractive_notch_or_slot",
            ),
            RequirementCheck(
                check_id="feature_target_face_edit",
                label="Target-face edit is grounded on the front face",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="face_targets=['front'], execute_build123d_geometry_fallback=true",
            ),
            RequirementCheck(
                check_id="feature_target_face_subtractive_merge",
                label="Target-face subtractive feature stays merged",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="face_targets=['front'] merged_subtractive_feature execute_build123d_geometry_fallback=true",
            ),
        ],
    )

    assert summary.clause_interpretations[0].status == RequirementClauseStatus.VERIFIED
    assert summary.insufficient_evidence == []


def test_interpret_requirement_clauses_does_not_contradict_front_recess_from_hole_layout_only() -> None:
    requirement_text = "Add a centered rounded rectangle recess on the front face sized about 12mm x 6mm."
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(step=1, solids=1, bbox=[62.0, 40.0, 14.0]),
        history=[],
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
        supplemental_checks=[
            RequirementCheck(
                check_id="feature_local_anchor_count_alignment",
                label="Hole center count aligns with requested anchors",
                status=RequirementCheckStatus.FAIL,
                blocking=True,
                evidence=(
                    "required_center_count=1, realized_center_count=2, "
                    "realized_centers=[[-12.5, 0.0], [12.5, 0.0]]"
                ),
            )
        ],
    )

    clause = summary.clause_interpretations[0]
    assert clause.status == RequirementClauseStatus.INSUFFICIENT_EVIDENCE
    assert "realized_center_count" not in str(clause.evidence or "")


def test_interpret_requirement_clauses_keeps_cylindrical_magnet_slot_as_insufficient_without_slot_grounding() -> None:
    requirement_text = "Add four corner magnet slots on the mating faces with 6mm diameter and 2mm depth."
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(step=1, solids=2, bbox=[72.0, 64.0, 26.0]),
        history=[],
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
        supplemental_checks=[
            RequirementCheck(
                check_id="feature_notch_or_profile_cut",
                label="Notch/profile cut is present",
                status=RequirementCheckStatus.FAIL,
                blocking=True,
                evidence="no complex base profile or local subtractive notch window found",
            )
        ],
    )

    clause = summary.clause_interpretations[0]
    assert clause.status == RequirementClauseStatus.INSUFFICIENT_EVIDENCE
    assert "validation:legacy_fail" not in clause.observation_tags
    assert "notch window" not in str(clause.evidence or "")


def test_interpret_requirement_clauses_keeps_thumb_notch_as_insufficient_without_face_grounding() -> None:
    requirement_text = "Create a snap clamshell enclosure with a thumb notch."
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(step=1, solids=2, bbox=[72.0, 64.0, 26.0]),
        history=[],
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
        supplemental_checks=[
            RequirementCheck(
                check_id="feature_notch_or_profile_cut",
                label="Notch/profile cut is present",
                status=RequirementCheckStatus.FAIL,
                blocking=True,
                evidence="no complex base profile or local subtractive notch window found",
            )
        ],
    )

    clause = summary.clause_interpretations[0]
    assert clause.status == RequirementClauseStatus.INSUFFICIENT_EVIDENCE
    assert "validation:legacy_fail" not in clause.observation_tags
    assert "notch window" not in str(clause.evidence or "")


@pytest.mark.asyncio
async def test_validate_requirement_applies_validation_llm_adjudication_to_unresolved_clause() -> None:
    service = SandboxMCPService(
        runner=_DummyRunner(),
        validation_adjudicator=_ValidationAdjudicatorStub(),
    )
    session_id = "session-validation-llm-adjudication"
    service._session_manager.clear_session(session_id)
    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(step=1, solids=1),
            success=True,
            error=None,
        ),
    )

    result = await service.validate_requirement(
        ValidateRequirementInput(
            session_id=session_id,
            requirement_text="Make it elegant.",
            requirements={"description": "Make it elegant."},
        )
    )

    assert result.success is True
    assert result.clause_interpretations[0].status == RequirementClauseStatus.VERIFIED
    assert "llm:validation_adjudicated" in result.clause_interpretations[0].observation_tags
    assert result.insufficient_evidence is False


@pytest.mark.asyncio
async def test_validate_requirement_keeps_coordinate_clause_as_insufficient_evidence() -> None:
    service = SandboxMCPService(
        runner=_DummyRunner(),
        validation_adjudicator=_ValidationAdjudicatorStub(),
    )
    session_id = "session-validation-llm-localized-clause-guard"
    service._session_manager.clear_session(session_id)
    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(step=1, solids=1, bbox=[40.0, 20.0, 10.0]),
            success=True,
            error=None,
        ),
    )

    result = await service.validate_requirement(
        ValidateRequirementInput(
            session_id=session_id,
            requirement_text="Add a pocket on the top face.",
            requirements={"description": "Add a pocket on the top face."},
        )
    )

    assert result.success is True
    assert result.clause_interpretations[0].status != RequirementClauseStatus.VERIFIED
    assert "llm:validation_adjudicated" not in result.clause_interpretations[0].observation_tags
    assert result.is_complete is False


@pytest.mark.asyncio
async def test_validate_requirement_surfaces_validation_llm_provider_error_as_diagnostic() -> None:
    service = SandboxMCPService(
        runner=_DummyRunner(),
        validation_adjudicator=_ValidationAdjudicatorProviderErrorStub(),
    )
    session_id = "session-validation-llm-provider-error"
    service._session_manager.clear_session(session_id)
    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(step=1, solids=1, bbox=[40.0, 20.0, 10.0]),
            success=True,
            error=None,
        ),
    )

    result = await service.validate_requirement(
        ValidateRequirementInput(
            session_id=session_id,
            requirement_text="Make it elegant.",
            requirements={"description": "Make it elegant."},
        )
    )

    assert result.success is True
    assert "validation:llm_provider_error" in result.observation_tags
    assert "fallback_to_evidence_first_clause_interpretation" in result.decision_hints
    assert any(
        hint.startswith("validation_llm_provider_error:TimeoutError")
        for hint in result.decision_hints
    )
    assert result.is_complete is False


@pytest.mark.asyncio
async def test_validate_requirement_skips_validation_llm_when_no_clause_is_eligible() -> None:
    adjudicator = _RecordingValidationAdjudicatorStub()
    service = SandboxMCPService(
        runner=_DummyRunner(),
        validation_adjudicator=adjudicator,
    )
    session_id = "session-validation-llm-skip-no-eligible"
    service._session_manager.clear_session(session_id)
    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(step=1, solids=1, bbox=[40.0, 20.0, 10.0]),
            success=True,
            error=None,
        ),
    )

    result = await service.validate_requirement(
        ValidateRequirementInput(
            session_id=session_id,
            requirement_text="Add a pocket on the top face.",
            requirements={"description": "Add a pocket on the top face."},
        )
    )

    assert result.success is True
    assert adjudicator.called is False
    assert "validation:llm_skipped" in result.observation_tags
    assert "validation_llm_skipped:no_eligible_clause" in result.decision_hints


@pytest.mark.asyncio
async def test_validate_requirement_skips_validation_llm_when_budget_exceeded() -> None:
    adjudicator = _RecordingValidationAdjudicatorStub()
    service = SandboxMCPService(
        runner=_DummyRunner(),
        validation_adjudicator=adjudicator,
    )
    session_id = "session-validation-llm-skip-budget"
    service._session_manager.clear_session(session_id)
    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(step=1, solids=1, bbox=[40.0, 20.0, 10.0]),
            success=True,
            error=None,
        ),
    )

    result = await service.validate_requirement(
        ValidateRequirementInput(
            session_id=session_id,
            requirement_text="Make it elegant. Keep it smooth. Make it printable. Use a modern style.",
            requirements={
                "description": "Make it elegant. Keep it smooth. Make it printable. Use a modern style."
            },
        )
    )

    assert result.success is True
    assert adjudicator.called is False
    assert "validation:llm_skipped" in result.observation_tags
    assert any(
        hint.startswith("validation_llm_skipped:eligible_clause_budget_exceeded:")
        for hint in result.decision_hints
    )


@pytest.mark.asyncio
async def test_validate_requirement_skips_validation_llm_when_estimated_prompt_budget_exceeded() -> None:
    adjudicator = _RecordingValidationAdjudicatorStub()
    service = SandboxMCPService(
        runner=_DummyRunner(),
        validation_adjudicator=adjudicator,
    )
    session_id = "session-validation-llm-skip-estimated-prompt-budget"
    service._session_manager.clear_session(session_id)
    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(step=1, solids=1, bbox=[40.0, 20.0, 10.0]),
            success=True,
            error=None,
        ),
    )
    long_requirement = (
        "Create an elegant printable enclosure with balanced flowing surfaces and "
        "carefully controlled ergonomic curvature "
    ) * 80

    result = await service.validate_requirement(
        ValidateRequirementInput(
            session_id=session_id,
            requirement_text=long_requirement,
            requirements={"description": long_requirement},
        )
    )

    assert result.success is True
    assert adjudicator.called is False
    assert "validation:llm_skipped" in result.observation_tags
    assert any(
        hint.startswith("validation_llm_skipped:estimated_prompt_budget_exceeded:")
        for hint in result.decision_hints
    )


@pytest.mark.asyncio
async def test_validate_requirement_treats_signed_negative_volume_as_material_solid() -> None:
    service = SandboxMCPService(
        runner=_DummyRunner(),
        validation_adjudicator=_ValidationAdjudicatorStub(),
    )
    session_id = "session-validation-negative-signed-volume"
    service._session_manager.clear_session(session_id)
    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(
                step=1,
                solids=1,
                volume=-100.0,
                bbox=[10.0, 10.0, 10.0],
            ),
            success=True,
            error=None,
        ),
    )

    result = await service.validate_requirement(
        ValidateRequirementInput(
            session_id=session_id,
            requirement_text="Create a 10 mm by 10 mm by 10 mm block.",
            requirements={"description": "Create a 10 mm by 10 mm by 10 mm block."},
        )
    )

    positive_volume_check = next(
        check for check in result.checks if check.check_id == "solid_positive_volume"
    )

    assert result.success is True
    assert positive_volume_check.status == RequirementCheckStatus.PASS


@pytest.mark.asyncio
async def test_validate_requirement_defaults_to_latest_successful_snapshot_after_failed_write() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    session_id = "session-validation-latest-successful-snapshot"
    service._session_manager.clear_session(session_id)
    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(step=1, solids=1, bbox=[10.0, 10.0, 10.0]),
            success=True,
            error=None,
        ),
    )
    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=2,
            action_type=CADActionType.MODIFY_ACTION,
            action_params={"source": "apply_cad_action"},
            result_snapshot=_snapshot(step=2, solids=0, volume=0.0, bbox=[0.0, 0.0, 0.0]),
            success=False,
            error="Exit code: 1",
        ),
    )

    result = await service.validate_requirement(
        ValidateRequirementInput(
            session_id=session_id,
            requirement_text="Create a 10 mm by 10 mm by 10 mm block.",
            requirements={"description": "Create a 10 mm by 10 mm by 10 mm block."},
        )
    )

    solid_exists = next(check for check in result.checks if check.check_id == "solid_exists")
    assert result.step == 1
    assert result.success is True
    assert solid_exists.status == RequirementCheckStatus.PASS


@pytest.mark.asyncio
async def test_query_topology_defaults_to_latest_successful_snapshot_after_failed_write() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    session_id = "session-topology-latest-successful-snapshot"
    service._session_manager.clear_session(session_id)
    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot_with_topology(step=1),
            success=True,
            error=None,
        ),
    )
    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=2,
            action_type=CADActionType.MODIFY_ACTION,
            action_params={"source": "apply_cad_action"},
            result_snapshot=_snapshot(step=2, solids=0, volume=0.0, bbox=[0.0, 0.0, 0.0]),
            success=False,
            error="Exit code: 1",
        ),
    )

    result = await service.query_topology(
        QueryTopologyInput(
            session_id=session_id,
            include_faces=True,
            include_edges=False,
        )
    )

    assert result.success is True
    assert result.step == 1
    assert result.topology_index is not None
    assert result.topology_index.faces[0].face_ref == "face:1:F_top"


def test_interpretation_projects_unknown_high_level_clause_without_marking_complete() -> None:
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(step=1, solids=1),
        history=[],
        requirements={"description": "Make it elegant."},
        requirement_text="Make it elegant.",
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": "Make it elegant."},
        requirement_text="Make it elegant.",
    )

    assert summary.clause_interpretations[0].status == RequirementClauseStatus.INSUFFICIENT_EVIDENCE
    assert summary.insufficient_evidence == ["make_it_elegant"]
    assert summary.legacy_checks[0].status == RequirementCheckStatus.UNKNOWN


def test_single_dimension_clauses_reuse_passed_dimension_checks() -> None:
    requirement_text = "width 40, height 20, thickness 10"
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(step=1, solids=1, bbox=[40.0, 20.0, 10.0]),
        history=[],
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
        supplemental_checks=[
            RequirementCheck(
                check_id="dimension_width",
                label="Dimension 'width' is satisfied",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="target=40.0, matched=40.0, bbox=[40.0, 20.0, 10.0]",
            ),
            RequirementCheck(
                check_id="dimension_height",
                label="Dimension 'height' is satisfied",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="target=20.0, matched=20.0, bbox=[40.0, 20.0, 10.0]",
            ),
            RequirementCheck(
                check_id="dimension_thickness",
                label="Dimension 'thickness' is satisfied",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="target=10.0, matched=10.0, bbox=[40.0, 20.0, 10.0]",
            ),
        ],
    )

    clause_status = {clause.clause_id: clause.status for clause in summary.clause_interpretations}
    assert clause_status["width_40"] == RequirementClauseStatus.VERIFIED
    assert clause_status["height_20"] == RequirementClauseStatus.VERIFIED
    assert clause_status["thickness_10"] == RequirementClauseStatus.VERIFIED


def test_clamshell_wall_thickness_clause_requires_shell_grounding_instead_of_bbox_min_span() -> None:
    requirement_text = (
        "Create a snap clamshell enclosure with overall dimensions 72mm x 64mm x 26mm. "
        "Use a pin hinge and keep wall thickness near 2.0mm."
    )
    bundle = RequirementEvidenceBundle(
        requirement_text=requirement_text,
        requirement_clauses=["keep wall thickness near 2.0mm"],
        snapshot_step=1,
        geometry_facts={
            "solids": 4,
            "faces": 28,
            "edges": 54,
            "volume": 31279.5,
            "bbox": [72.0, 65.0, 26.0],
            "bbox_min": [-36.0, -33.0, -13.0],
            "bbox_max": [36.0, 32.0, 13.0],
        },
        topology_facts={},
        process_facts={},
        observation_tags=["geometry:object_index_available"],
        decision_hints=[],
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
    )

    clause = summary.clause_interpretations[0]
    assert clause.status == RequirementClauseStatus.INSUFFICIENT_EVIDENCE
    assert "bbox_min_span is not a reliable wall-thickness proxy" in clause.evidence


def test_full_span_channel_dimension_clauses_reuse_bbox_and_notch_alignment_evidence() -> None:
    requirement_text = (
        "Select the XY plane and create a box-shaped base 80.0 millimeters long, "
        "50.0 millimeters wide, and 40.0 millimeters high, with the bottom on Z=0. "
        "Select the top face and cut a centered rectangular slot that spans the full "
        "80.0 millimeter length, is 30.0 millimeters wide in Y, and is 25.0 "
        "millimeters deep, leaving a U-shaped channel section."
    )
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(step=1, solids=1, bbox=[80.0, 50.0, 40.0]),
        history=[],
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
        supplemental_checks=[
            RequirementCheck(
                check_id="feature_target_face_edit",
                label="Target-face edit is grounded on the top face",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="face_targets=['top'], execute_build123d_geometry_fallback=true",
            ),
            RequirementCheck(
                check_id="feature_target_face_subtractive_merge",
                label="Target-face subtractive feature stays merged",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="face_targets=['top'] merged_subtractive_feature execute_build123d_geometry_fallback=true",
            ),
            RequirementCheck(
                check_id="feature_notch_or_profile_cut",
                label="Notch/profile cut is present",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="execute_build123d_geometry_fallback=true, observed_snapshot_profile_shapes=['polygon', 'rectangle'], feature=subtractive_notch_or_slot",
            ),
            RequirementCheck(
                check_id="feature_notch_profile_alignment",
                label="Notch/profile dimensions align",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="execute_build123d_geometry_fallback=true, preferred_plane=YZ, notch_dims=[30.0, 25.0], floor_axis_value=15.0, floor_center=0.0, matched_side_faces=2",
            ),
            RequirementCheck(
                check_id="pre_solid_profile_shape_alignment",
                label="Pre-solid profile shape aligns",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="required_pre_solid_shapes=['rectangle'], observed_pre_solid_shapes=['polygon', 'rectangle']",
            ),
            RequirementCheck(
                check_id="feature_merged_body_result",
                label="Merged body result exists",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="final_solids=1, requires_merged_body=True",
            ),
        ],
    )

    clause_status = {clause.clause_id: clause.status for clause in summary.clause_interpretations}

    assert clause_status["50_0_millimeters_wide"] == RequirementClauseStatus.VERIFIED
    assert clause_status["40_0_millimeters_high"] == RequirementClauseStatus.VERIFIED
    assert clause_status["with_the_bottom_on_z_0"] == RequirementClauseStatus.VERIFIED
    assert clause_status["is_30_0_millimeters_wide_in_y"] == RequirementClauseStatus.VERIFIED
    assert clause_status["is_25_0_millimeters_deep"] == RequirementClauseStatus.VERIFIED


def test_equilateral_triangle_frame_clauses_reuse_frame_scale_and_positive_extrude_evidence() -> None:
    requirement_text = (
        "Select the XY plane. Draw two concentric equilateral triangles with their centroids coinciding, "
        "the outer triangle having a side length of 50.0 mm and the inner triangle having a side length of 30.0 mm. "
        "Select the frame-shaped region between the two triangles. Extrude to any length, such as 100.0 mm."
    )
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(step=1, solids=1, bbox=[43.30127018922102, 50.0, 100.0]),
        history=[],
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
        supplemental_checks=[
            RequirementCheck(
                check_id="feature_named_plane_positive_extrude_span",
                label="Named-plane positive extrude span is preserved",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="plane=XY, axis=Z, required_lower_bound=0.0, required_minimum_extent=100.0, require_positive_direction=True, observed_range=[0.0, 100.0]",
            ),
            RequirementCheck(
                check_id="feature_inner_void_cutout",
                label="Frame profile preserves inner void cutout",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="same_shape_frame_snapshot_geometry=true, shape=triangle, cap_faces=2, lateral_faces=6, triangle_frame_area_match=true, expected_cap_area=692.8203, observed_cap_area=692.8203, execute_build123d_geometry_fallback=true",
            ),
            RequirementCheck(
                check_id="feature_regular_polygon_scale_alignment",
                label="Regular polygon side lengths remain scale-correct",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="triangle_frame_area_match=true, expected_cap_area=692.8203, observed_cap_area=692.8203",
            ),
        ],
    )

    clause_status = {clause.clause_id: clause.status for clause in summary.clause_interpretations}

    assert clause_status["draw_two_concentric_equilateral_triangles_with_their_centroids_coinciding"] == RequirementClauseStatus.VERIFIED
    assert clause_status["the_outer_triangle_having_a_side_length_of_50_0_mm"] == RequirementClauseStatus.VERIFIED
    assert clause_status["the_inner_triangle_having_a_side_length_of_30_0_mm"] == RequirementClauseStatus.VERIFIED
    assert clause_status["extrude_to_any_length"] == RequirementClauseStatus.VERIFIED
    assert clause_status["such_as_100_0_mm"] == RequirementClauseStatus.VERIFIED
    assert summary.insufficient_evidence == []


def test_pre_solid_rectangle_clause_reuses_bbox_and_profile_evidence() -> None:
    requirement_text = (
        "Select the XY plane, draw an 80.0x40.0 millimeter rectangle, and extrude it by 20.0 millimeters."
    )
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(step=1, solids=1, bbox=[80.0, 40.0, 20.0]),
        history=[],
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
        supplemental_checks=[
            RequirementCheck(
                check_id="feature_named_plane_positive_extrude_span",
                label="Named-plane positive extrude span is preserved",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="plane=XY, axis=Z, required_lower_bound=0.0, required_minimum_extent=20.0, require_positive_direction=True, observed_range=[0.0, 20.0]",
            ),
            RequirementCheck(
                check_id="pre_solid_profile_shape_alignment",
                label="Pre-solid profile shape aligns",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="required_pre_solid_shapes=['rectangle'], observed_pre_solid_shapes=['polygon', 'rectangle']",
            ),
        ],
    )

    clause_status = {clause.clause_id: clause.status for clause in summary.clause_interpretations}

    assert clause_status["draw_an_80_0x40_0_millimeter_rectangle"] == RequirementClauseStatus.VERIFIED
    assert clause_status["extrude_it_by_20_0_millimeters"] == RequirementClauseStatus.VERIFIED
    assert summary.insufficient_evidence == []


def test_multi_plane_additive_plane_context_reuses_plane_specific_specs() -> None:
    requirement_text = (
        "Select the XY plane, draw a center rectangle of 10.0x10.0 millimeters, and extrude it 80.0 millimeters "
        "symmetrically in the Z direction. Then select the YZ plane, draw a center-aligned 80.0x10.0 millimeter "
        "rectangle, and extrude it 10.0 millimeters symmetrically in the X direction. Perform a Boolean union on "
        "the two solids so they intersect at the origin, forming a 3D cross with two orthogonal 80 millimeter bars."
    )
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(step=1, solids=1, bbox=[10.0, 80.0, 80.0]),
        history=[],
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
        supplemental_checks=[
            RequirementCheck(
                check_id="feature_multi_plane_additive_union",
                label="Orthogonal additive union contributes the required unique additive span signatures and remains one merged solid",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence=(
                    "additive_planes=['XY', 'YZ'], required_planes_explicit=['XY', 'YZ'], "
                    "signatures=[(10.0, 10.0, 80.0), (10.0, 80.0, 10.0)], required_signature_segments=4, "
                    "covered_signature_segments=4, required_planes=2, final_solids=1, execute_build123d_geometry_fallback=true"
                ),
            ),
            RequirementCheck(
                check_id="feature_multi_plane_additive_specs",
                label="Each named datum-plane additive window preserves the requested rectangle dimension order and extrusion distance",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence=(
                    "matched_plane_specs=[{'plane': 'XY', 'width': 10.0, 'height': 10.0, 'distance': 80.0}, "
                    "{'plane': 'YZ', 'width': 80.0, 'height': 10.0, 'distance': 10.0}], "
                    "execute_build123d_geometry_fallback=true"
                ),
            ),
        ],
    )

    clause_status = {clause.clause_id: clause.status for clause in summary.clause_interpretations}

    assert clause_status["draw_a_center_rectangle_of_10_0x10_0_millimeters"] == RequirementClauseStatus.VERIFIED
    assert clause_status["extrude_it_80_0_millimeters_symmetrically_in_the_z_direction"] == RequirementClauseStatus.VERIFIED
    assert clause_status["draw_a_center_aligned_80_0x10_0_millimeter_rectangle"] == RequirementClauseStatus.VERIFIED
    assert clause_status["extrude_it_10_0_millimeters_symmetrically_in_the_x_direction"] == RequirementClauseStatus.VERIFIED
    assert clause_status["forming_a_3d_cross_with_two_orthogonal_80_millimeter_bars"] == RequirementClauseStatus.VERIFIED
    assert summary.insufficient_evidence == []


def test_blind_hole_depth_clause_reuses_topology_face_span_evidence() -> None:
    requirement_text = "cut it downward by 10.0 millimeters"
    bundle = RequirementEvidenceBundle(
        requirement_text=requirement_text,
        requirement_clauses=[requirement_text],
        snapshot_step=1,
        geometry_facts={
            "solids": 1,
            "faces": 8,
            "edges": 15,
            "volume": 63214.6018,
            "bbox": [80.0, 40.0, 20.0],
            "bbox_min": [-40.0, -20.0, 0.0],
            "bbox_max": [40.0, 20.0, 20.0],
        },
        topology_facts={
            "face_summaries": [
                {
                    "face_id": "cyl-hole-wall",
                    "geom_type": "CYLINDER",
                    "radius": 5.0,
                    "axis_direction": [0.0, 0.0, 1.0],
                    "bbox": {
                        "xmin": 25.0,
                        "xmax": 35.0,
                        "ymin": -5.0,
                        "ymax": 5.0,
                        "zmin": 10.0,
                        "zmax": 20.0,
                    },
                },
                {
                    "face_id": "hole-floor",
                    "geom_type": "PLANE",
                    "normal": [0.0, 0.0, 1.0],
                    "bbox": {
                        "xmin": 25.0,
                        "xmax": 35.0,
                        "ymin": -5.0,
                        "ymax": 5.0,
                        "zmin": 10.0,
                        "zmax": 10.0,
                    },
                },
            ]
        },
        process_facts={},
        observation_tags=["geometry:solid_present", "geometry:object_index_available"],
        decision_hints=[],
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
        supplemental_checks=[
            RequirementCheck(
                check_id="feature_hole",
                label="Contains required hole feature",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="found hole/recess-like subtractive geometry in final snapshot execute_build123d_geometry_fallback=true",
            ),
            RequirementCheck(
                check_id="feature_target_face_edit",
                label="Target-face edit exists",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="face_targets=['top'], execute_build123d_geometry_fallback=true",
            ),
            RequirementCheck(
                check_id="feature_target_face_subtractive_merge",
                label="Target-face subtractive feature is merged",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="face_targets=['top'] merged_subtractive_feature execute_build123d_geometry_fallback=true",
            ),
        ],
    )

    assert summary.clause_interpretations[0].status == RequirementClauseStatus.VERIFIED
    assert summary.insufficient_evidence == []


def test_axis_direction_clause_is_treated_as_process_setup() -> None:
    requirement_text = "the Z-axis as the upward direction"
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(step=1, solids=1, bbox=[80.0, 40.0, 30.0]),
        history=[],
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
    )

    assert summary.clause_interpretations[0].status == RequirementClauseStatus.NOT_APPLICABLE
    assert summary.insufficient_evidence == []


def test_pin_hinge_clause_reuses_boundary_cylindrical_topology_evidence() -> None:
    requirement_text = "Use a pin hinge at the back."
    bundle = RequirementEvidenceBundle(
        requirement_text=requirement_text,
        requirement_clauses=[requirement_text],
        snapshot_step=1,
        geometry_facts={
            "solids": 1,
            "faces": 12,
            "edges": 24,
            "volume": 18000.0,
            "bbox": [72.0, 64.0, 26.0],
            "bbox_min": [-36.0, -32.0, 0.0],
            "bbox_max": [36.0, 32.0, 26.0],
        },
        topology_facts={
            "face_summaries": [
                {
                    "face_id": "F_hinge_barrel",
                    "geom_type": "CYLINDER",
                    "radius": 3.0,
                    "axis_origin": [0.0, -32.0, 13.0],
                    "axis_direction": [1.0, 0.0, 0.0],
                    "bbox": {
                        "xmin": -34.0,
                        "xmax": 34.0,
                        "ymin": -35.0,
                        "ymax": -29.0,
                        "zmin": 10.0,
                        "zmax": 16.0,
                    },
                }
            ]
        },
        process_facts={},
        observation_tags=["geometry:object_index_available"],
        decision_hints=[],
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
    )

    clause = summary.clause_interpretations[0]
    assert clause.status == RequirementClauseStatus.VERIFIED
    assert "matched_hinge_face=F_hinge_barrel" in clause.evidence
    assert "hinge_axis=X" in clause.evidence
    assert "topology:hinge_face" in clause.observation_tags


def test_left_and_right_end_height_clauses_reuse_end_face_topology_spans() -> None:
    requirement_text = "the left end height is 30.0 mm and the right end height is 5.0 mm"
    bundle = RequirementEvidenceBundle(
        requirement_text=requirement_text,
        requirement_clauses=[
            "the left end height is 30.0 mm",
            "the right end height is 5.0 mm",
        ],
        snapshot_step=1,
        geometry_facts={
            "solids": 1,
            "faces": 6,
            "edges": 12,
            "volume": 56000.0,
            "bbox": [80.0, 40.0, 30.0],
            "bbox_min": [0.0, -40.0, 0.0],
            "bbox_max": [80.0, 0.0, 30.0],
        },
        topology_facts={
            "face_summaries": [
                {
                    "face_id": "left-end",
                    "geom_type": "PLANE",
                    "normal": [-1.0, 0.0, 0.0],
                    "bbox": {
                        "xmin": 0.0,
                        "xmax": 0.0,
                        "ymin": -40.0,
                        "ymax": 0.0,
                        "zmin": 0.0,
                        "zmax": 30.0,
                    },
                },
                {
                    "face_id": "right-end",
                    "geom_type": "PLANE",
                    "normal": [1.0, 0.0, 0.0],
                    "bbox": {
                        "xmin": 80.0,
                        "xmax": 80.0,
                        "ymin": -40.0,
                        "ymax": 0.0,
                        "zmin": 0.0,
                        "zmax": 5.0,
                    },
                },
            ]
        },
        process_facts={},
        observation_tags=["geometry:solid_present", "geometry:object_index_available"],
        decision_hints=[],
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
    )

    clause_status = {clause.clause_id: clause.status for clause in summary.clause_interpretations}

    assert clause_status["the_left_end_height_is_30_0_mm"] == RequirementClauseStatus.VERIFIED
    assert clause_status["the_right_end_height_is_5_0_mm"] == RequirementClauseStatus.VERIFIED
    assert summary.insufficient_evidence == []


def test_half_shell_inner_clearance_clause_reuses_axisymmetric_inner_diameter_evidence() -> None:
    requirement_text = (
        "Remove the inner 35.0 millimeter diameter clearance so the shell remains open above the split line."
    )
    bundle = RequirementEvidenceBundle(
        requirement_text=requirement_text,
        requirement_clauses=[requirement_text],
        snapshot_step=1,
        geometry_facts={
            "solids": 1,
            "faces": 12,
            "edges": 36,
            "volume": 20945.9,
            "bbox": [54.0, 25.0, 40.0],
            "bbox_min": [-27.0, 0.0, 0.0],
            "bbox_max": [27.0, 25.0, 40.0],
            "through_axisymmetric_radii": [17.5],
            "axisymmetric_bands": [
                {
                    "axis": "Z",
                    "radius": 25.0,
                    "axial_range": [0.0, 40.0],
                    "face_count": 1,
                },
                {
                    "axis": "Z",
                    "radius": 17.5,
                    "axial_range": [0.0, 40.0],
                    "face_count": 1,
                },
            ],
        },
        topology_facts={},
        process_facts={},
        observation_tags=[],
        decision_hints=[],
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
        supplemental_checks=[
            RequirementCheck(
                check_id="feature_half_shell_profile_envelope",
                label="Half-shell envelope is correct",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence=(
                    "execute_build123d_geometry_fallback=true, expected_half_profile_span=25.0, "
                    "observed_half_profile_span=25.0, half_profile_axis=Y, observed_bounds=[0.0, 25.0]"
                ),
            )
        ],
    )

    clause = summary.clause_interpretations[0]
    assert clause.status == RequirementClauseStatus.VERIFIED
    assert "matched_inner_diameter=35.0" in clause.evidence
    assert "geometry:axisymmetric_radius" in clause.observation_tags


def test_half_shell_inner_semicircle_radius_clause_reuses_axisymmetric_inner_radius_evidence() -> None:
    requirement_text = "an inner semicircle of radius 17.5 millimeters on the XY plane"
    bundle = RequirementEvidenceBundle(
        requirement_text=requirement_text,
        requirement_clauses=[requirement_text],
        snapshot_step=1,
        geometry_facts={
            "solids": 1,
            "faces": 12,
            "edges": 36,
            "volume": 20945.9,
            "bbox": [54.0, 25.0, 40.0],
            "bbox_min": [-27.0, 0.0, 0.0],
            "bbox_max": [27.0, 25.0, 40.0],
            "through_axisymmetric_radii": [17.5],
            "axisymmetric_bands": [
                {
                    "axis": "Z",
                    "radius": 25.0,
                    "axial_range": [0.0, 40.0],
                    "face_count": 1,
                },
                {
                    "axis": "Z",
                    "radius": 17.5,
                    "axial_range": [0.0, 40.0],
                    "face_count": 1,
                },
            ],
        },
        topology_facts={},
        process_facts={},
        observation_tags=[],
        decision_hints=[],
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
        supplemental_checks=[
            RequirementCheck(
                check_id="feature_half_shell_profile_envelope",
                label="Half-shell envelope is correct",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="expected_half_profile_span=25.0, observed_half_profile_span=25.0",
            )
        ],
    )

    clause = summary.clause_interpretations[0]
    assert clause.status == RequirementClauseStatus.VERIFIED
    assert "matched_inner_radius=17.5" in clause.evidence
    assert "geometry:axisymmetric_radius" in clause.observation_tags


def test_half_shell_split_line_clause_is_not_applicable_once_profile_envelope_is_verified() -> None:
    requirement_text = "closing the profile along the split line"
    bundle = RequirementEvidenceBundle(
        requirement_text=requirement_text,
        requirement_clauses=[requirement_text],
        snapshot_step=1,
        geometry_facts={
            "solids": 1,
            "faces": 12,
            "edges": 36,
            "volume": 20945.9,
            "bbox": [54.0, 25.0, 40.0],
            "bbox_min": [-27.0, 0.0, 0.0],
            "bbox_max": [27.0, 25.0, 40.0],
            "through_axisymmetric_radii": [17.5],
            "axisymmetric_bands": [
                {
                    "axis": "Z",
                    "radius": 25.0,
                    "axial_range": [0.0, 40.0],
                    "face_count": 1,
                }
            ],
        },
        topology_facts={},
        process_facts={},
        observation_tags=[],
        decision_hints=[],
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
        supplemental_checks=[
            RequirementCheck(
                check_id="feature_half_shell_profile_envelope",
                label="Half-shell envelope is correct",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="expected_half_profile_span=25.0, observed_half_profile_span=25.0",
            )
        ],
    )

    clause = summary.clause_interpretations[0]
    assert clause.status == RequirementClauseStatus.NOT_APPLICABLE
    assert "final artifact" in clause.evidence


def test_precise_pattern_dimension_clause_reuses_local_alignment_instead_of_bbox() -> None:
    requirement_text = (
        "Constrain the centers of these four circles to form a square array with a side length of 8.0mm."
    )
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(step=1, solids=1, bbox=[16.0, 16.0, 12.0]),
        history=[],
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
        supplemental_checks=[
            RequirementCheck(
                check_id="feature_local_anchor_alignment",
                label="Direct feature centers stay aligned with the requested local anchor layout",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence=(
                    "required_centers=[[4.0, 4.0], [4.0, -4.0], [-4.0, 4.0], [-4.0, -4.0]], "
                    "realized_centers=[[-4.0, -4.0], [4.0, -4.0], [-4.0, 4.0], [4.0, 4.0]]"
                ),
            ),
            RequirementCheck(
                check_id="feature_pattern",
                label="Contains required pattern feature",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="execute_build123d_geometry_fallback=true, found repeated direct feature pattern in final geometry",
            ),
        ],
    )

    assert summary.clause_interpretations[0].status == RequirementClauseStatus.VERIFIED
    assert "requested_dimensions=[8.0]" not in summary.clause_interpretations[0].evidence


def test_pattern_layout_dimension_without_local_checks_stays_unresolved_instead_of_bbox_contradicted() -> None:
    requirement_text = (
        "Arrange the feature centers into a square array with a side length of 8.0mm."
    )
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(step=1, solids=1, bbox=[16.0, 16.0, 12.0]),
        history=[],
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
        supplemental_checks=[],
    )

    clause = summary.clause_interpretations[0]
    assert clause.status == RequirementClauseStatus.INSUFFICIENT_EVIDENCE
    assert "requested_dimensions=[8.0]" not in clause.evidence
