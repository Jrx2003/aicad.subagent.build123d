from __future__ import annotations

from sandbox_mcp_server.contracts import CADStateSnapshot, GeometryInfo, RequirementCheck, RequirementCheckStatus
from sandbox_mcp_server.registry import analyze_requirement_semantics
from sandbox_mcp_server.service import SandboxMCPService


class _DummyRunner:
    async def execute(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("not used in this test")


def _snapshot(
    *,
    step: int,
    solids: int = 1,
    bbox: list[float] | None = None,
    bbox_min: list[float] | None = None,
    bbox_max: list[float] | None = None,
) -> CADStateSnapshot:
    bbox_values = bbox or [10.0, 10.0, 10.0]
    return CADStateSnapshot(
        step=step,
        features=[],
        geometry=GeometryInfo(
            solids=solids,
            faces=6,
            edges=12,
            volume=100.0,
            bbox=bbox_values,
            center_of_mass=[0.0, 0.0, 0.0],
            surface_area=50.0,
            bbox_min=bbox_min or [0.0, 0.0, 0.0],
            bbox_max=bbox_max or bbox_values,
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


def _failed_check(check_id: str) -> RequirementCheck:
    return RequirementCheck(
        check_id=check_id,
        label=check_id,
        status=RequirementCheckStatus.FAIL,
        blocking=True,
        evidence="missing",
    )


def test_nested_hollow_section_probe_carries_bbox_anchor_summary() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    requirement_text = "Create a hollow enclosure shell with wall thickness 2.4 mm."
    semantics = analyze_requirement_semantics(
        {"description": requirement_text},
        requirement_text,
    )

    probe = service._build_feature_probe_record(
        family="nested_hollow_section",
        snapshot=_snapshot(
            step=1,
            solids=2,
            bbox=[78.0, 56.0, 32.0],
            bbox_min=[-39.0, -28.0, 0.0],
            bbox_max=[39.0, 28.0, 32.0],
        ),
        history=[],
        check_index={"feature_notch_or_profile_cut": _failed_check("feature_notch_or_profile_cut")},
        semantics=semantics,
        requirement_text=requirement_text,
    )

    assert probe.required_evidence_kinds == ["geometry", "topology"]
    assert probe.anchor_summary["solid_count"] == 2
    assert probe.anchor_summary["bbox"] == [78.0, 56.0, 32.0]
    assert probe.anchor_summary["bbox_min_span"] == 32.0
    assert "prefers_explicit_inner_void_cut" in probe.anchor_summary


def test_slots_probe_requests_topology_host_ranking_when_blocked() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    requirement_text = "Add a front thumb notch about 10 mm wide."
    semantics = analyze_requirement_semantics(
        {"description": requirement_text},
        requirement_text,
    )

    probe = service._build_feature_probe_record(
        family="slots",
        snapshot=_snapshot(
            step=1,
            solids=1,
            bbox=[78.0, 56.0, 16.0],
            bbox_min=[-39.0, -28.0, 0.0],
            bbox_max=[39.0, 28.0, 16.0],
        ),
        history=[],
        check_index={"feature_notch_or_profile_cut": _failed_check("feature_notch_or_profile_cut")},
        semantics=semantics,
        requirement_text=requirement_text,
    )

    assert probe.required_evidence_kinds == ["geometry", "topology"]
    assert probe.anchor_summary["requires_topology_host_ranking"] is True
    assert probe.anchor_summary["bbox_max_span"] == 78.0
    assert "need_topology_host_selection" in probe.grounding_blockers
    assert probe.recommended_next_tools[0] == "query_topology"
    assert "query_feature_probes" in probe.recommended_next_tools
