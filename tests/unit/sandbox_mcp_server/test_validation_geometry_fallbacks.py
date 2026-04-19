import math
import sys
import types

import pytest

from sandbox.interface import SandboxResult
from sandbox.docker_runner import _build_runtime_code
from sandbox_mcp_server.contracts import (
    ActionHistoryEntry,
    BoundingBox3D,
    CADActionType,
    CADStateSnapshot,
    FaceEntity,
    GeometryInfo,
    GeometryObjectIndex,
    QueryFeatureProbesInput,
    RequirementCheck,
    RequirementCheckStatus,
    RequirementClauseStatus,
    SolidEntity,
    TopologyEdgeEntity,
    TopologyFaceEntity,
    TopologyObjectIndex,
    ValidateRequirementInput,
)
from sandbox_mcp_server.service import SandboxMCPService
from sandbox_mcp_server.validation_evidence import RequirementEvidenceBuilder
from sandbox_mcp_server.validation_interpretation import interpret_requirement_clauses


class _DummyRunner:
    async def execute(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("not used in this test")


class _FakeShape:
    def __init__(self, label: str) -> None:
        self.label = label
        self.wrapped = f"wrapped:{label}"

    def solids(self) -> list["_FakeShape"]:
        return [self]


class _FakeShapeList(list):
    def solids(self) -> list[_FakeShape]:
        solids: list[_FakeShape] = []
        for child in self:
            if hasattr(child, "solids"):
                solids.extend(list(child.solids()))
        return solids


class _FakeCompound:
    def __init__(self, children=None, **kwargs) -> None:  # type: ignore[no-untyped-def]
        if children is None:
            children = kwargs.get("children") or []
        self.children = list(children)
        self.wrapped = "wrapped:compound"

    def solids(self) -> list[_FakeShape]:
        solids: list[_FakeShape] = []
        for child in self.children:
            if hasattr(child, "solids"):
                solids.extend(list(child.solids()))
        return solids


class _FakePart:
    def __init__(self, source=None) -> None:  # type: ignore[no-untyped-def]
        self.source = source
        self.wrapped = "wrapped:part"

    def solids(self) -> list[_FakeShape]:
        if self.source is None or not hasattr(self.source, "solids"):
            return []
        return list(self.source.solids())


def _install_fake_build123d(
    monkeypatch: pytest.MonkeyPatch,
    *,
    exported: list[tuple[object, str]] | None = None,
) -> None:
    module = types.ModuleType("build123d")
    module.__all__ = ["Box", "Compound", "Part", "ShapeList", "export_step"]
    module.ShapeList = _FakeShapeList
    module.Compound = _FakeCompound
    module.Part = _FakePart

    def _fake_box(*args, **kwargs):  # type: ignore[no-untyped-def]
        label = kwargs.get("label") or f"box:{len(args)}"
        return _FakeShape(str(label))

    def _fake_export_step(obj, path):  # type: ignore[no-untyped-def]
        if not hasattr(obj, "wrapped"):
            raise AttributeError(f"{type(obj).__name__!s} missing wrapped")
        if exported is not None:
            exported.append((obj, path))

    module.Box = _fake_box
    module.export_step = _fake_export_step
    monkeypatch.setitem(sys.modules, "build123d", module)


def _exec_runtime_code(
    code: str,
    monkeypatch: pytest.MonkeyPatch,
    *,
    capture_export: bool = False,
) -> tuple[dict[str, object], list[tuple[object, str]]]:
    exported: list[tuple[object, str]] = []
    _install_fake_build123d(
        monkeypatch,
        exported=exported if capture_export else None,
    )
    namespace: dict[str, object] = {}
    exec(code, namespace, namespace)
    return namespace, exported


def test_runtime_wrap_build123d_code_falls_back_to_empty_result_for_probe_analysis() -> None:
    service = SandboxMCPService(runner=_DummyRunner())

    wrapped = service._runtime_wrap_build123d_code("x = 1")

    assert "else:\n    result = Part()\n    __aicad_last_result = result\n" in wrapped


def test_runtime_wrap_build123d_code_canonicalizes_multi_shape_shapelist_to_compound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SandboxMCPService(runner=_DummyRunner())

    wrapped = service._runtime_wrap_build123d_code(
        "result = ShapeList([Box(1, 1, 1, label='base'), Box(1, 1, 1, label='lid')])"
    )

    namespace, _ = _exec_runtime_code(wrapped, monkeypatch)

    result = namespace["result"]
    assert isinstance(result, _FakeCompound)
    assert [child.label for child in result.children] == ["base", "lid"]


def test_runtime_wrap_build123d_code_unwraps_singleton_shapelist_to_child_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SandboxMCPService(runner=_DummyRunner())

    wrapped = service._runtime_wrap_build123d_code(
        "result = ShapeList([Box(1, 1, 1, label='base')])"
    )

    namespace, _ = _exec_runtime_code(wrapped, monkeypatch)

    result = namespace["result"]
    assert isinstance(result, _FakeShape)
    assert result.label == "base"


def test_docker_runtime_code_canonicalizes_multi_shape_shapelist_before_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapped = _build_runtime_code(
        "result = ShapeList([Box(1, 1, 1, label='base'), Box(1, 1, 1, label='lid')])"
    )

    import pathlib

    monkeypatch.setattr(pathlib.Path, "mkdir", lambda self, parents=False, exist_ok=False: None)
    _, exported = _exec_runtime_code(wrapped, monkeypatch, capture_export=True)

    assert len(exported) == 1
    exported_root, exported_path = exported[0]
    assert isinstance(exported_root, _FakeCompound)
    assert [child.label for child in exported_root.children] == ["base", "lid"]
    assert exported_path == "/output/model.step"


def test_runtime_wrap_build123d_code_normalizes_shapelist_results_for_export() -> None:
    service = SandboxMCPService(runner=_DummyRunner())

    wrapped = service._runtime_wrap_build123d_code(
        "\n".join(
            [
                "left = Box(1, 1, 1)",
                "right = Pos(3, 0, 0) * Box(1, 1, 1)",
                "result = Compound([left, right]).solids()",
            ]
        )
    )

    namespace: dict[str, object] = {}
    exec(wrapped, namespace)

    export_part = namespace["__aicad_resolve_export_part"]()

    assert export_part is not None
    assert hasattr(export_part, "wrapped")
    assert len(list(export_part.solids())) == 2


def test_execute_probe_summary_marks_path_rail_diagnostics_actionable_for_path_sweep() -> None:
    service = SandboxMCPService(runner=_DummyRunner())

    result = SandboxResult(
        success=True,
        stdout=(
            "Line 1: start=Vector(0, 0, 0), end=Vector(50, 0, 0)\n"
            "Arc: start=Vector(50, 0, 0), end=Vector(80, 0, 30)\n"
            "Line 2: start=Vector(80, 0, 30), end=Vector(80, 0, 80)\n"
            "Path wire: <build123d.topology.Wire object at 0xffff47425990>\n"
            "Success!\n"
        ),
        stderr="",
        output_files=["geometry_info.json"],
        output_file_contents={"geometry_info.json": b"{}"},
    )

    summary = service._build_execute_probe_summary(
        result=result,
        filenames=["geometry_info.json"],
        requirement_text=(
            "Use the Sweep feature to construct. First, draw the path sketch on the "
            "front view: an L-shaped path consisting of a 50.0mm horizontal line, "
            "a 90-degree tangent arc with a radius of 30.0mm, and another 50.0mm "
            "tangent straight line. Exit the path sketch. Create a vertical "
            "reference plane at one endpoint of the path, and draw the profile "
            "sketch: two concentric circles, with an outer diameter of 20.0mm and "
            "an inner diameter of 16.0mm (wall thickness 2mm). Execute the sweep "
            "command, select the annular profile, and sweep along the L-shaped path "
            "to generate a hollow bent pipe solid."
        ),
    )

    assert summary["actionable"] is True
    assert summary["actionable_family_ids"] == ["path_sweep"]
    assert summary["signal_values_by_family"]["path_sweep"]["workplane_path_wire_valid"] is True


def test_execute_probe_summary_marks_center_arc_signature_probe_actionable_for_path_sweep() -> None:
    service = SandboxMCPService(runner=_DummyRunner())

    result = SandboxResult(
        success=True,
        stdout=(
            "=== Testing CenterArc signatures ===\n"
            "Positional args work: <build123d.objects_curve.CenterArc object at 0xffff>\n"
            "Keyword args failed: CenterArc.__init__() got an unexpected keyword argument 'arc_angle'\n"
            "\n=== Building L-shaped path ===\n"
            "Path edges: [<build123d.topology.Edge object at 0x1>, <build123d.topology.Edge object at 0x2>, <build123d.topology.Edge object at 0x3>]\n"
            "Path is valid: <bound method Shape.is_valid of Curve at 0xffff>\n"
            "\n=== Profile at path endpoint ===\n"
            "Path endpoint: Vector(80, 80, 0)\n"
            "Profile face count: 1\n"
            "Profile is valid: <bound method Shape.is_valid of Sketch at 0xffff>\n"
        ),
        stderr="",
        output_files=["geometry_info.json"],
        output_file_contents={"geometry_info.json": b"{}"},
    )

    summary = service._build_execute_probe_summary(
        result=result,
        filenames=["geometry_info.json"],
        requirement_text=(
            "Use the Sweep feature to construct. First, draw the path sketch on the front "
            "view: an L-shaped path consisting of a 50.0mm horizontal line, a 90-degree "
            "tangent arc with a radius of 30.0mm, and another 50.0mm tangent straight line. "
            "Exit the path sketch. Create a vertical reference plane at one endpoint of the "
            "path, and draw the profile sketch: two concentric circles, with an outer "
            "diameter of 20.0mm and an inner diameter of 16.0mm (wall thickness 2mm). "
            "Execute the sweep command, select the annular profile, and sweep along the "
            "L-shaped path to generate a hollow bent pipe solid."
        ),
    )

    assert summary["actionable"] is True
    assert summary["actionable_family_ids"] == ["path_sweep"]
    assert summary["signal_values_by_family"]["path_sweep"]["path_segment_count"] == 3
    assert summary["signal_values_by_family"]["path_sweep"]["profile_face_valid"] is True


def test_execute_probe_summary_marks_countersink_signature_probe_actionable_for_explicit_anchor_hole() -> None:
    service = SandboxMCPService(runner=_DummyRunner())

    result = SandboxResult(
        success=True,
        stdout=(
            "CounterSinkHole signature:\n"
            "(radius: 'float', counter_sink_radius: 'float', depth: 'float' = None, "
            "counter_sink_angle: 'float' = 82, mode: 'Mode' = <Mode.SUBTRACT>)\n\n"
            "CounterSinkHole docstring:\n"
            "Part Operation: Counter Sink Hole\n"
        ),
        stderr="",
        output_files=["geometry_info.json"],
        output_file_contents={"geometry_info.json": b"{}"},
    )

    summary = service._build_execute_probe_summary(
        result=result,
        filenames=["geometry_info.json"],
        requirement_text=(
            "Select the top reference plane, draw a 100.0x60.0 millimeter rectangle and "
            "extrude it by 8.0 millimeters. Select the plate surface, and use the sketch "
            "to draw four points with coordinates (25,15), (25,45), (75,15), and (75,45). "
            "Exit the sketch, and activate the Hole Wizard or the revolved cut tool. If "
            "using the Hole Wizard: select Countersink, head diameter 12.0 millimeters, "
            "cone angle 90 degrees, through-hole diameter 6.0 millimeters."
        ),
    )

    assert summary["actionable"] is True
    assert summary["actionable_family_ids"] == ["explicit_anchor_hole"]
    assert (
        summary["signal_values_by_family"]["explicit_anchor_hole"][
            "countersink_helper_signature_valid"
        ]
        is True
    )


def _bbox(
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    zmin: float,
    zmax: float,
) -> BoundingBox3D:
    return BoundingBox3D(
        xlen=xmax - xmin,
        ylen=ymax - ymin,
        zlen=zmax - zmin,
        xmin=xmin,
        xmax=xmax,
        ymin=ymin,
        ymax=ymax,
        zmin=zmin,
        zmax=zmax,
    )


def _solid(
    *,
    solid_id: str,
    volume: float,
    bbox: BoundingBox3D,
) -> SolidEntity:
    return SolidEntity(
        solid_id=solid_id,
        volume=volume,
        surface_area=max(volume, 1.0),
        center_of_mass=[
            (bbox.xmin + bbox.xmax) / 2.0,
            (bbox.ymin + bbox.ymax) / 2.0,
            (bbox.zmin + bbox.zmax) / 2.0,
        ],
        bbox=bbox,
    )


def _topology_face(
    *,
    step: int,
    face_id: str,
    center: list[float],
    normal: list[float],
    bbox: BoundingBox3D,
    geom_type: str = "PLANE",
    radius: float | None = None,
    axis_origin: list[float] | None = None,
    axis_direction: list[float] | None = None,
    area: float = 100.0,
    edge_refs: list[str] | None = None,
) -> TopologyFaceEntity:
    return TopologyFaceEntity(
        face_ref=f"face:{step}:{face_id}",
        face_id=face_id,
        step=step,
        area=area,
        center=center,
        normal=normal,
        axis_origin=axis_origin,
        axis_direction=axis_direction,
        radius=radius,
        geom_type=geom_type,
        bbox=bbox,
        parent_solid_id="S1",
        edge_refs=edge_refs or [],
        adjacent_face_refs=[],
    )


def _geometry_face(
    *,
    face_id: str,
    center: list[float],
    normal: list[float],
    bbox: BoundingBox3D,
    geom_type: str = "PLANE",
    radius: float | None = None,
    axis_origin: list[float] | None = None,
    axis_direction: list[float] | None = None,
    area: float = 100.0,
) -> FaceEntity:
    return FaceEntity(
        face_id=face_id,
        area=area,
        center=center,
        normal=normal,
        axis_origin=axis_origin,
        axis_direction=axis_direction,
        radius=radius,
        geom_type=geom_type,
        bbox=bbox,
    )


def _topology_edge(
    *,
    step: int,
    edge_id: str,
    center: list[float],
    bbox: BoundingBox3D,
    radius: float,
    length: float,
    axis_origin: list[float] | None = None,
    axis_direction: list[float] | None = None,
) -> TopologyEdgeEntity:
    return TopologyEdgeEntity(
        edge_ref=f"edge:{step}:{edge_id}",
        edge_id=edge_id,
        step=step,
        length=length,
        geom_type="CIRCLE",
        center=center,
        axis_origin=axis_origin,
        axis_direction=axis_direction,
        radius=radius,
        bbox=bbox,
        parent_solid_id="S1",
        adjacent_face_refs=[],
    )


def _snapshot(
    *,
    step: int,
    solids: int = 1,
    faces: int = 6,
    edges: int = 12,
    volume: float = 100.0,
    bbox: list[float] | None = None,
    bbox_min: list[float] | None = None,
    bbox_max: list[float] | None = None,
    geometry_objects: GeometryObjectIndex | None = None,
    topology_index: TopologyObjectIndex | None = None,
) -> CADStateSnapshot:
    bbox_value = bbox or [10.0, 10.0, 10.0]
    bbox_min_value = bbox_min or [0.0, 0.0, 0.0]
    bbox_max_value = bbox_max or bbox_value
    return CADStateSnapshot(
        step=step,
        features=[],
        geometry=GeometryInfo(
            solids=solids,
            faces=faces,
            edges=edges,
            volume=volume,
            bbox=bbox_value,
            center_of_mass=[0.0, 0.0, 0.0],
            surface_area=50.0,
            bbox_min=bbox_min_value,
            bbox_max=bbox_max_value,
        ),
        issues=[],
        warnings=[],
        blockers=[],
        images=[],
        sketch_state=None,
        geometry_objects=geometry_objects,
        topology_index=topology_index,
        success=True,
        error=None,
    )


_PLATE_COUNTERSINK_REQUIREMENT = (
    "Select the top reference plane, draw a 100.0x60.0 millimeter rectangle and extrude it by 8.0 millimeters. "
    "Select the plate surface, and use the sketch to draw four points with coordinates (25,15), (25,45), (75,15), and (75,45). "
    'Exit the sketch, and activate the Hole Wizard or the revolved cut tool. If using the Hole Wizard: select "Countersink," '
    "set the standard, head diameter 12.0 millimeters, cone angle 90 degrees, through-hole diameter 6.0 millimeters, and in the "
    "position tab, select the four points drawn earlier. If using manual modeling: at each point, first cut a through-hole with "
    "a diameter of 6.0 millimeters, then cut a conical recess with an upper diameter of 12.0 millimeters and a cone angle of "
    "90 degrees (pay attention to depth control to ensure the countersink face matches), and complete the operation."
)


def _build_centered_plate_countersink_topology(
    *,
    head_radius: float,
    shaft_radius: float = 3.0,
) -> TopologyObjectIndex:
    plate_top = _topology_face(
        step=1,
        face_id="F_plate_top",
        center=[0.0, 0.0, 4.0],
        normal=[0.0, 0.0, 1.0],
        bbox=_bbox(-50.0, 50.0, -30.0, 30.0, 4.0, 4.0),
        area=6000.0,
        edge_refs=["edge:1:E_plate_1", "edge:1:E_plate_2", "edge:1:E_plate_3", "edge:1:E_plate_4"],
    )
    local_centers = [(-25.0, -15.0), (-25.0, 15.0), (25.0, -15.0), (25.0, 15.0)]
    cone_depth = max(head_radius - shaft_radius, 0.5)
    throat_z = 4.0 - cone_depth

    faces: list[TopologyFaceEntity] = [plate_top]
    edges: list[TopologyEdgeEntity] = []
    for index, (x, y) in enumerate(local_centers, start=1):
        faces.append(
            _topology_face(
                step=1,
                face_id=f"F_hole_cone_{index}",
                center=[x + (head_radius + shaft_radius) / 2.0, y, 4.0 - cone_depth / 2.0],
                normal=[-0.70710678, 0.0, 0.70710678],
                bbox=_bbox(
                    x - head_radius,
                    x + head_radius,
                    y - head_radius,
                    y + head_radius,
                    throat_z,
                    4.0,
                ),
                geom_type="CONE",
                area=120.0,
            )
        )
        faces.append(
            _topology_face(
                step=1,
                face_id=f"F_hole_cyl_{index}",
                center=[x + shaft_radius, y, (throat_z - 4.0) / 2.0],
                normal=[-1.0, 0.0, 0.0],
                bbox=_bbox(
                    x - shaft_radius,
                    x + shaft_radius,
                    y - shaft_radius,
                    y + shaft_radius,
                    -4.0,
                    throat_z,
                ),
                geom_type="CYLINDER",
                radius=shaft_radius,
                axis_origin=[x, y, 12.0],
                axis_direction=[0.0, 0.0, -1.0],
                area=200.0,
            )
        )
        edges.append(
            _topology_edge(
                step=1,
                edge_id=f"E_hole_head_{index}",
                center=[x + head_radius, y, 4.0],
                bbox=_bbox(
                    x - head_radius,
                    x + head_radius,
                    y - head_radius,
                    y + head_radius,
                    4.0,
                    4.0,
                ),
                radius=head_radius,
                length=2.0 * math.pi * head_radius,
                axis_origin=[x, y, 4.0],
                axis_direction=[0.0, 0.0, 1.0],
            )
        )
        edges.append(
            _topology_edge(
                step=1,
                edge_id=f"E_hole_throat_{index}",
                center=[x + shaft_radius, y, throat_z],
                bbox=_bbox(
                    x - shaft_radius,
                    x + shaft_radius,
                    y - shaft_radius,
                    y + shaft_radius,
                    throat_z,
                    throat_z,
                ),
                radius=shaft_radius,
                length=2.0 * math.pi * shaft_radius,
                axis_origin=[x, y, throat_z],
                axis_direction=[0.0, 0.0, 1.0],
            )
        )
    return TopologyObjectIndex(
        faces=faces,
        edges=edges,
        faces_total=len(faces),
        edges_total=len(edges),
        max_items_per_type=64,
    )


def test_explicit_anchor_hole_family_text_prefers_hole_clause_and_bottom_face_target() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    requirement_text = (
        "Create a rectangular service bracket with two mounting holes on the bottom face. "
        "Add a centered rounded rectangle recess on the front face."
    )

    family_text = service._extract_family_specific_requirement_text(
        requirement_text,
        family="explicit_anchor_hole",
    )

    assert "two mounting holes on the bottom face" in family_text.lower()
    assert "front face" not in family_text.lower()
    assert service._extract_family_specific_face_targets(
        requirement_text,
        family="explicit_anchor_hole",
    ) == ("bottom",)
    assert (
        service._infer_expected_local_feature_count(
            family_text,
            family="explicit_anchor_hole",
        )
        == 2
    )


def test_interpretation_verifies_slot_cover_length_from_slot_alignment_check() -> None:
    requirement_text = "length set to 110.0 to cover the entire length"
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(step=1, solids=2, bbox=[100.0, 50.0, 20.0]),
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
                check_id="feature_cylindrical_slot_alignment",
                label="slot alignment",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence=(
                    "axis=X, expected_radius=12.0, expected_centerline=[0.0, 0.0, 8.0], "
                    "observed_axis_range=[-50.0, 50.0]"
                ),
            )
        ],
    )

    assert summary.clause_interpretations[0].status == RequirementClauseStatus.VERIFIED
    assert summary.insufficient_evidence == []


def test_interpretation_does_not_confuse_along_with_long_for_pattern_direction() -> None:
    requirement_text = "with direction 1 along the X-axis"
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(step=1, solids=1, bbox=[50.0, 50.0, 15.0]),
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
                label="local anchor alignment",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="required_centers=[[-15.0, -15.0], ..., [15.0, 15.0]], realized_centers=[[-15.0, -15.0], ..., [15.0, 15.0]]",
            ),
            RequirementCheck(
                check_id="feature_pattern",
                label="pattern",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="execute_build123d_geometry_fallback=true, found repeated spherical recess pattern in final geometry",
            ),
        ],
    )

    assert summary.clause_interpretations[0].status == RequirementClauseStatus.VERIFIED


def test_interpretation_verifies_vague_hole_pattern_clause_without_forcing_layout_unknown() -> None:
    requirement_text = "Create a shelled block with a shallow top-face recess and a reference hole pattern."
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(
            step=1,
            solids=1,
            bbox=[100.0, 80.0, 60.0],
            bbox_min=[-50.0, -40.0, -30.0],
            bbox_max=[50.0, 40.0, 30.0],
        ),
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
                check_id="feature_hole",
                label="feature hole",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="found hole/recess-like subtractive geometry in final snapshot execute_build123d_geometry_fallback=true",
            ),
            RequirementCheck(
                check_id="feature_pattern",
                label="feature pattern",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="execute_build123d_geometry_fallback=true, found repeated direct feature pattern in final geometry",
            ),
        ],
    )

    clause_status = {
        clause.clause_id: clause.status for clause in summary.clause_interpretations
    }
    assert clause_status["a_reference_hole_pattern"] == RequirementClauseStatus.VERIFIED
    assert "a_reference_hole_pattern" not in summary.insufficient_evidence


def test_interpretation_verifies_center_rectangle_clause_for_plane_anchored_base() -> None:
    requirement_text = "Draw a center rectangle 100.0x50.0 in the XY plane"
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(
            step=1,
            solids=1,
            bbox=[100.0, 50.0, 20.0],
            bbox_min=[-50.0, -25.0, 0.0],
            bbox_max=[50.0, 25.0, 20.0],
        ),
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
                label="plane anchored positive extrude",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence=(
                    "plane=XY, axis=Z, required_lower_bound=0.0, "
                    "required_minimum_extent=20.0, require_positive_direction=True, "
                    "observed_range=[0.0, 20.0]"
                ),
            )
        ],
    )

    assert summary.clause_interpretations[0].status == RequirementClauseStatus.VERIFIED


def test_interpretation_verifies_symmetric_extrude_clause_for_centered_block() -> None:
    requirement_text = (
        "extrude it symmetrically by 15.0 millimeters to make a 60x40x30 block centered about the XY plane"
    )
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(
            step=1,
            solids=1,
            bbox=[60.0, 40.0, 30.0],
            bbox_min=[-30.0, -20.0, -15.0],
            bbox_max=[30.0, 20.0, 15.0],
        ),
        history=[],
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
    )

    assert summary.clause_interpretations[0].status == RequirementClauseStatus.VERIFIED


def test_interpretation_verifies_triangle_pocket_and_named_fillet_clauses_from_local_feature_evidence() -> None:
    requirement_text = (
        "Create a 60.0x40.0 millimeter rectangle on the XY plane and extrude it symmetrically "
        "by 15.0 millimeters to make a 60x40x30 block centered about the XY plane. "
        "Select the top face and sketch an isosceles triangular pocket centered on the top surface "
        "with vertices at (-10.0, 0.0), (10.0, 0.0), and (0.0, 10.0). "
        "Cut-extrude this triangle downward by 10.0 millimeters. "
        "Fillet the two bottom outer edges that run parallel to the Y axis with a radius of 1.0 millimeter."
    )
    topology_index = TopologyObjectIndex(
        faces=[
            _topology_face(
                step=1,
                face_id="F_top",
                center=[0.0, 0.0, 15.0],
                normal=[0.0, 0.0, 1.0],
                bbox=_bbox(-30.0, 30.0, -20.0, 20.0, 15.0, 15.0),
                area=2300.0,
            ),
            _topology_face(
                step=1,
                face_id="F_triangle_floor",
                center=[0.0, 3.333, 5.0],
                normal=[0.0, 0.0, 1.0],
                bbox=_bbox(-10.0, 10.0, 0.0, 10.0, 5.0, 5.0),
                area=100.0,
            ),
            _topology_face(
                step=1,
                face_id="F_fillet_left",
                center=[-29.707, 0.0, -14.707],
                normal=[-0.707, 0.0, -0.707],
                bbox=_bbox(-30.0, -29.0, -20.0, 20.0, -15.0, -14.0),
                geom_type="CYLINDER",
                radius=1.0,
                axis_origin=[-29.0, -20.0, -14.0],
                axis_direction=[0.0, 1.0, 0.0],
                area=62.832,
            ),
            _topology_face(
                step=1,
                face_id="F_fillet_right",
                center=[29.707, 0.0, -14.707],
                normal=[0.707, 0.0, -0.707],
                bbox=_bbox(29.0, 30.0, -20.0, 20.0, -15.0, -14.0),
                geom_type="CYLINDER",
                radius=1.0,
                axis_origin=[29.0, -20.0, -14.0],
                axis_direction=[0.0, 1.0, 0.0],
                area=62.832,
            ),
        ],
        edges=[],
        faces_total=4,
        edges_total=0,
        max_items_per_type=20,
    )
    topology_index.faces[1].edge_refs = [
        "edge:1:E_tri_1",
        "edge:1:E_tri_2",
        "edge:1:E_tri_3",
    ]
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(
            step=1,
            solids=1,
            faces=12,
            edges=27,
            volume=70982.83185307187,
            bbox=[60.0, 40.0, 30.0],
            bbox_min=[-30.0, -20.0, -15.0],
            bbox_max=[30.0, 20.0, 15.0],
            topology_index=topology_index,
        ),
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
                label="target face edit",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="face_targets=['top'], execute_build123d_geometry_fallback=true",
            ),
            RequirementCheck(
                check_id="feature_target_face_subtractive_merge",
                label="target face subtractive merge",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="face_targets=['top'] merged_subtractive_feature execute_build123d_geometry_fallback=true",
            ),
            RequirementCheck(
                check_id="pre_solid_profile_shape_alignment",
                label="pre-solid profile shape alignment",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="required_pre_solid_shapes=['triangle', 'rectangle'], observed_pre_solid_shapes=['polygon', 'rectangle']",
            ),
            RequirementCheck(
                check_id="feature_profile_shape_alignment",
                label="profile shape alignment",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="required_shapes=['triangle'], observed_snapshot_profile_shapes=['polygon', 'rectangle', 'triangle'], execute_build123d_geometry_fallback=true",
            ),
            RequirementCheck(
                check_id="feature_fillet",
                label="feature fillet",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="execute_build123d_geometry_fallback=true, feature=fillet, labels=['back', 'bottom', 'front', 'left', 'outer', 'y_parallel'], radius=1.0",
            ),
        ],
    )

    clause_status = {
        clause.clause_id: clause.status for clause in summary.clause_interpretations
    }
    assert (
        clause_status["create_a_60_0x40_0_millimeter_rectangle_on_the_xy_plane"]
        == RequirementClauseStatus.VERIFIED
    )
    assert (
        clause_status[
            "sketch_an_isosceles_triangular_pocket_centered_on_the_top_surface_with_vertices_at_10_0_0_0"
        ]
        == RequirementClauseStatus.VERIFIED
    )
    assert clause_status["10_0_0_0"] == RequirementClauseStatus.NOT_APPLICABLE
    assert clause_status["0_0_10_0"] == RequirementClauseStatus.NOT_APPLICABLE
    assert (
        clause_status["cut_extrude_this_triangle_downward_by_10_0_millimeters"]
        == RequirementClauseStatus.VERIFIED
    )
    assert (
        clause_status[
            "fillet_the_two_bottom_outer_edges_that_run_parallel_to_the_y_axis_with_a_radius_of_1_0_millimeter"
        ]
        == RequirementClauseStatus.VERIFIED
    )


def test_interpretation_verifies_triangle_pocket_depth_when_topology_edge_refs_only_exist_in_topology_index() -> None:
    requirement_text = (
        "Create a 60.0x40.0 millimeter rectangle on the XY plane and extrude it symmetrically "
        "by 15.0 millimeters to make a 60x40x30 block centered about the XY plane. "
        "Select the top face and sketch an isosceles triangular pocket centered on the top surface "
        "with vertices at (-10.0, 0.0), (10.0, 0.0), and (0.0, 10.0). "
        "Cut-extrude this triangle downward by 10.0 millimeters."
    )
    geometry_objects = GeometryObjectIndex(
        solids=[],
        faces=[
            _geometry_face(
                face_id="F_top",
                center=[0.0, 0.0, 15.0],
                normal=[0.0, 0.0, 1.0],
                bbox=_bbox(-30.0, 30.0, -20.0, 20.0, 15.0, 15.0),
                area=2300.0,
            ),
            _geometry_face(
                face_id="F_triangle_floor",
                center=[0.0, 3.333, 5.0],
                normal=[0.0, 0.0, 1.0],
                bbox=_bbox(-10.0, 10.0, 0.0, 10.0, 5.0, 5.0),
                area=100.0,
            ),
        ],
        edges=[],
        faces_total=2,
        edges_total=0,
        max_items_per_type=20,
    )
    topology_index = TopologyObjectIndex(
        faces=[
            _topology_face(
                step=1,
                face_id="F_top",
                center=[0.0, 0.0, 15.0],
                normal=[0.0, 0.0, 1.0],
                bbox=_bbox(-30.0, 30.0, -20.0, 20.0, 15.0, 15.0),
                area=2300.0,
            ),
            _topology_face(
                step=1,
                face_id="F_triangle_floor",
                center=[0.0, 3.333, 5.0],
                normal=[0.0, 0.0, 1.0],
                bbox=_bbox(-10.0, 10.0, 0.0, 10.0, 5.0, 5.0),
                area=100.0,
            ),
        ],
        edges=[],
        faces_total=2,
        edges_total=0,
        max_items_per_type=20,
    )
    topology_index.faces[1].edge_refs = [
        "edge:1:E_tri_1",
        "edge:1:E_tri_2",
        "edge:1:E_tri_3",
    ]
    snapshot = _snapshot(
        step=1,
        solids=1,
        faces=12,
        edges=27,
        volume=70982.83185307187,
        bbox=[60.0, 40.0, 30.0],
        bbox_min=[-30.0, -20.0, -15.0],
        bbox_max=[30.0, 20.0, 15.0],
        topology_index=topology_index,
    )
    snapshot.geometry_objects = geometry_objects
    bundle = RequirementEvidenceBuilder.build(
        snapshot=snapshot,
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
                label="target face edit",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="face_targets=['top'], execute_build123d_geometry_fallback=true",
            ),
            RequirementCheck(
                check_id="feature_target_face_subtractive_merge",
                label="target face subtractive merge",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="face_targets=['top'] merged_subtractive_feature execute_build123d_geometry_fallback=true",
            ),
            RequirementCheck(
                check_id="feature_profile_shape_alignment",
                label="profile shape alignment",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="required_shapes=['triangle'], observed_snapshot_profile_shapes=['polygon', 'rectangle', 'triangle'], execute_build123d_geometry_fallback=true",
            ),
        ],
    )

    clause_status = {
        clause.clause_id: clause.status for clause in summary.clause_interpretations
    }
    assert (
        clause_status["cut_extrude_this_triangle_downward_by_10_0_millimeters"]
        == RequirementClauseStatus.VERIFIED
    )


def test_interpretation_verifies_axisymmetric_segment_clause_from_cylindrical_bands() -> None:
    requirement_text = (
        "Define the radii along the axial direction from 0 to 60.0 mm as follows: "
        "end radius 15 mm (length 20.0 mm) -> middle section radius 7.5 mm (length 20.0 mm) "
        "-> end radius 15 mm (length 20.0 mm)"
    )
    topology_index = TopologyObjectIndex(
        faces=[
            _topology_face(
                step=1,
                face_id="F_outer_low",
                center=[-15.0, 0.0, 10.0],
                normal=[-1.0, 0.0, 0.0],
                bbox=_bbox(-15.0, 15.0, -15.0, 15.0, 0.0, 20.0),
                geom_type="CYLINDER",
                radius=15.0,
                axis_origin=[0.0, 0.0, 0.0],
                axis_direction=[0.0, 0.0, 1.0],
                area=1884.955592154,
            ),
            _topology_face(
                step=1,
                face_id="F_mid",
                center=[-7.5, 0.0, 30.0],
                normal=[-1.0, 0.0, 0.0],
                bbox=_bbox(-7.5, 7.5, -7.5, 7.5, 20.0, 40.0),
                geom_type="CYLINDER",
                radius=7.5,
                axis_origin=[0.0, 0.0, 20.0],
                axis_direction=[0.0, 0.0, 1.0],
                area=942.477796077,
            ),
            _topology_face(
                step=1,
                face_id="F_outer_high",
                center=[-15.0, 0.0, 50.0],
                normal=[-1.0, 0.0, 0.0],
                bbox=_bbox(-15.0, 15.0, -15.0, 15.0, 40.0, 60.0),
                geom_type="CYLINDER",
                radius=15.0,
                axis_origin=[0.0, 0.0, 40.0],
                axis_direction=[0.0, 0.0, 1.0],
                area=1884.955592154,
            ),
        ],
        edges=[],
        faces_total=3,
        edges_total=0,
        max_items_per_type=20,
    )
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(
            step=1,
            solids=1,
            faces=7,
            edges=9,
            volume=31808.625617596772,
            bbox=[30.0, 30.0, 60.0],
            bbox_min=[-15.0, -15.0, 0.0],
            bbox_max=[15.0, 15.0, 60.0],
            topology_index=topology_index,
        ),
        history=[],
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
    )

    assert summary.clause_interpretations[0].status == RequirementClauseStatus.VERIFIED


@pytest.mark.asyncio
async def test_validate_requirement_accepts_axisymmetric_revolve_setup_prompt_without_clause_gaps() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    session_id = "session-validate-axisymmetric-revolve-setup"
    service._session_manager.clear_session(session_id)

    topology_index = TopologyObjectIndex(
        faces=[
            _topology_face(
                step=1,
                face_id="F_outer_low",
                center=[-15.0, 0.0, 10.0],
                normal=[-1.0, 0.0, 0.0],
                bbox=_bbox(-15.0, 15.0, -15.0, 15.0, 0.0, 20.0),
                geom_type="CYLINDER",
                radius=15.0,
                axis_origin=[0.0, 0.0, 0.0],
                axis_direction=[0.0, 0.0, 1.0],
                area=1884.955592154,
            ),
            _topology_face(
                step=1,
                face_id="F_mid",
                center=[-7.5, 0.0, 30.0],
                normal=[-1.0, 0.0, 0.0],
                bbox=_bbox(-7.5, 7.5, -7.5, 7.5, 20.0, 40.0),
                geom_type="CYLINDER",
                radius=7.5,
                axis_origin=[0.0, 0.0, 20.0],
                axis_direction=[0.0, 0.0, 1.0],
                area=942.477796077,
            ),
            _topology_face(
                step=1,
                face_id="F_outer_high",
                center=[-15.0, 0.0, 50.0],
                normal=[-1.0, 0.0, 0.0],
                bbox=_bbox(-15.0, 15.0, -15.0, 15.0, 40.0, 60.0),
                geom_type="CYLINDER",
                radius=15.0,
                axis_origin=[0.0, 0.0, 40.0],
                axis_direction=[0.0, 0.0, 1.0],
                area=1884.955592154,
            ),
        ],
        edges=[],
        faces_total=3,
        edges_total=0,
        max_items_per_type=20,
    )

    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(
                step=1,
                solids=1,
                faces=7,
                edges=9,
                volume=31808.625617596772,
                bbox=[30.0, 30.0, 60.0],
                bbox_min=[-15.0, -15.0, 0.0],
                bbox_max=[15.0, 15.0, 60.0],
                topology_index=topology_index,
            ),
            success=True,
            error=None,
        ),
    )

    requirement_text = (
        "First, create a new part file and set the units to millimeters (mm). "
        "Establish a global coordinate system: use the origin (0,0,0) as the reference, "
        "with the default XY plane as the base sketch plane and the Z-axis pointing upwards. "
        "Draw a half-sectional view of the stepped shaft in the XZ plane and revolve it around the Z-axis. "
        "Define the radii along the axial direction from 0 to 60.0 mm as follows: "
        "end radius 15 mm (length 20.0 mm) -> middle section radius 7.5 mm (length 20.0 mm) "
        "-> end radius 15 mm (length 20.0 mm). After closing the profile, perform a 360° revolution "
        "to generate the double-ended stud."
    )

    result = await service.validate_requirement(
        ValidateRequirementInput(
            session_id=session_id,
            requirement_text=requirement_text,
            requirements={"description": requirement_text},
        )
    )

    assert result.success is True
    assert result.is_complete is True
    assert result.insufficient_evidence is False
    clause_status = {
        clause.clause_id: clause.status for clause in result.clause_interpretations
    }
    assert clause_status["set_the_units_to_millimeters_mm"] == RequirementClauseStatus.NOT_APPLICABLE
    assert (
        clause_status[
            "establish_a_global_coordinate_system_use_the_origin_0_0_0_as_the_reference"
        ]
        == RequirementClauseStatus.NOT_APPLICABLE
    )
    assert (
        clause_status["with_the_default_xy_plane_as_the_base_sketch_plane"]
        == RequirementClauseStatus.NOT_APPLICABLE
    )
    assert clause_status["the_z_axis_pointing_upwards"] == RequirementClauseStatus.NOT_APPLICABLE
    assert (
        clause_status["draw_a_half_sectional_view_of_the_stepped_shaft_in_the_xz_plane"]
        == RequirementClauseStatus.NOT_APPLICABLE
    )
    assert clause_status["revolve_it_around_the_z_axis"] == RequirementClauseStatus.NOT_APPLICABLE
    assert clause_status["after_closing_the_profile"] == RequirementClauseStatus.NOT_APPLICABLE
    assert (
        clause_status["perform_a_360_revolution_to_generate_the_double_ended_stud"]
        == RequirementClauseStatus.NOT_APPLICABLE
    )


@pytest.mark.asyncio
async def test_validate_requirement_accepts_axisymmetric_directional_profile_prompt_without_clause_gaps() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    session_id = "session-validate-axisymmetric-directional-profile"
    service._session_manager.clear_session(session_id)

    topology_index = TopologyObjectIndex(
        faces=[
            _topology_face(
                step=1,
                face_id="F_outer_base",
                center=[-25.0, 0.0, 7.5],
                normal=[-1.0, 0.0, 0.0],
                bbox=_bbox(-25.0, 25.0, -25.0, 25.0, 0.0, 15.0),
                geom_type="CYLINDER",
                radius=25.0,
                axis_origin=[0.0, 0.0, 0.0],
                axis_direction=[0.0, 0.0, 1.0],
                area=2356.19,
            ),
            _topology_face(
                step=1,
                face_id="F_outer_step",
                center=[-20.0, 0.0, 17.5],
                normal=[-1.0, 0.0, 0.0],
                bbox=_bbox(-20.0, 20.0, -20.0, 20.0, 15.0, 20.0),
                geom_type="CYLINDER",
                radius=20.0,
                axis_origin=[0.0, 0.0, 15.0],
                axis_direction=[0.0, 0.0, 1.0],
                area=628.319,
            ),
            _topology_face(
                step=1,
                face_id="F_inner_bore",
                center=[-10.0, 0.0, 10.0],
                normal=[1.0, 0.0, 0.0],
                bbox=_bbox(-10.0, 10.0, -10.0, 10.0, 0.0, 20.0),
                geom_type="CYLINDER",
                radius=10.0,
                axis_origin=[0.0, 0.0, 0.0],
                axis_direction=[0.0, 0.0, 1.0],
                area=1256.637,
            ),
        ],
        edges=[],
        faces_total=3,
        edges_total=0,
        max_items_per_type=20,
    )

    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(
                step=1,
                solids=1,
                faces=6,
                edges=9,
                volume=29452.431127404176,
                bbox=[50.0, 50.0, 20.0],
                bbox_min=[-25.0, -25.0, 0.0],
                bbox_max=[25.0, 25.0, 20.0],
                topology_index=topology_index,
            ),
            success=True,
            error=None,
        ),
    )

    requirement_text = (
        "Initialize the modeling environment and select the Front plane as the sketch plane. "
        "To create an efficient revolved structure, draw a vertical centerline through the origin as the axis of rotation. "
        "Next, draw the cross-sectional profile: start from point (10.0, 0) [corresponding to inner diameter R10], "
        "draw horizontally outward to (25.0, 0) [corresponding to outer diameter R25], "
        "draw vertically upward to (25.0, 15.0) [base thickness], "
        "draw horizontally inward to (20.0, 15.0) [step R20], "
        "draw vertically upward to (20.0, 20.0) [total height], "
        "and finally draw horizontally inward to (10.0, 20.0) and close the profile by drawing vertically downward to the starting point. "
        "After completing the sketch, use the revolved boss command to rotate 360 degrees around the center axis to generate the solid, "
        "completing the spring seat construction."
    )

    result = await service.validate_requirement(
        ValidateRequirementInput(
            session_id=session_id,
            requirement_text=requirement_text,
            requirements={"description": requirement_text},
        )
    )

    assert result.success is True
    assert result.is_complete is True
    assert result.insufficient_evidence is False
    clause_status = {
        clause.clause_id: clause.status for clause in result.clause_interpretations
    }
    assert (
        clause_status["to_create_an_efficient_revolved_structure"]
        == RequirementClauseStatus.NOT_APPLICABLE
    )
    assert (
        clause_status["draw_a_vertical_centerline_through_the_origin_as_the_axis_of_rotation"]
        == RequirementClauseStatus.VERIFIED
    )
    assert (
        clause_status["draw_horizontally_inward_to_20_0_15_0_step_r20"]
        == RequirementClauseStatus.VERIFIED
    )
    assert (
        clause_status["draw_horizontally_inward_to_10_0_20_0"]
        == RequirementClauseStatus.VERIFIED
    )
    assert (
        clause_status["close_the_profile_by_drawing_vertically_downward_to_the_starting_point"]
        == RequirementClauseStatus.NOT_APPLICABLE
    )
    assert (
        clause_status[
            "use_the_revolved_boss_command_to_rotate_360_degrees_around_the_center_axis_to_generate_the_solid"
        ]
        == RequirementClauseStatus.NOT_APPLICABLE
    )
    assert (
        clause_status["completing_the_spring_seat_construction"]
        == RequirementClauseStatus.NOT_APPLICABLE
    )


@pytest.mark.asyncio
async def test_validate_requirement_blocks_multi_solid_result_for_explicit_boolean_difference_slot() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    session_id = "session-validate-single-body-slot"
    service._session_manager.clear_session(session_id)

    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(
                step=1,
                solids=2,
                faces=13,
                edges=27,
                volume=59717.03711648648,
                bbox=[100.0, 50.0, 20.0],
                bbox_min=[-50.0, -25.0, 0.0],
                bbox_max=[50.0, 25.0, 20.0],
            ),
            success=True,
            error=None,
        ),
    )

    requirement_text = (
        "Create a new part with units in millimeters. Draw a center rectangle 100.0×50.0 in the XY plane "
        "and extrude it by 20.0 to form a block. Create a cutting cylinder: radius 12.0, axis along the X-axis, "
        "cylinder centerline placed at (0,0,8.0), length set to 110.0 to cover the entire length. "
        "Perform a Boolean difference: the block as the target body and the cylinder as the tool body, "
        "resulting in a semicircular slot on the top surface."
    )

    result = await service.validate_requirement(
        ValidateRequirementInput(
            session_id=session_id,
            requirement_text=requirement_text,
            requirements={"description": requirement_text},
        )
    )

    assert result.success is True
    assert "feature_merged_body_result" in result.blockers


def test_interpretation_accepts_axisymmetric_bolt_circle_through_hole_clauses_without_gaps() -> None:
    bolt_radius = 27.5
    bolt_faces: list[TopologyFaceEntity] = []
    for index in range(6):
        angle = math.radians(index * 60.0)
        cx = round(bolt_radius * math.cos(angle), 6)
        cy = round(bolt_radius * math.sin(angle), 6)
        bolt_faces.append(
            _topology_face(
                step=1,
                face_id=f"F_bolt_{index}",
                center=[cx + 3.0 * math.cos(angle + math.pi), cy + 3.0 * math.sin(angle + math.pi), -7.5],
                normal=[math.cos(angle + math.pi), math.sin(angle + math.pi), 0.0],
                bbox=_bbox(cx - 3.0, cx + 3.0, cy - 3.0, cy + 3.0, -15.0, 0.0),
                geom_type="CYLINDER",
                radius=3.0,
                axis_origin=[cx, cy, 0.0],
                axis_direction=[0.0, 0.0, 1.0],
                area=282.743,
            )
        )

    topology_index = TopologyObjectIndex(
        faces=[
            _topology_face(
                step=1,
                face_id="F_outer_cap",
                center=[-35.0, 0.0, -2.5],
                normal=[-1.0, 0.0, 0.0],
                bbox=_bbox(-35.0, 35.0, -35.0, 35.0, -5.0, 0.0),
                geom_type="CYLINDER",
                radius=35.0,
                axis_origin=[0.0, 0.0, 0.0],
                axis_direction=[0.0, 0.0, 1.0],
                area=1099.557,
            ),
            _topology_face(
                step=1,
                face_id="F_outer_boss",
                center=[-25.0, 0.0, -10.0],
                normal=[-1.0, 0.0, 0.0],
                bbox=_bbox(-25.0, 25.0, -25.0, 25.0, -15.0, -5.0),
                geom_type="CYLINDER",
                radius=25.0,
                axis_origin=[0.0, 0.0, -5.0],
                axis_direction=[0.0, 0.0, 1.0],
                area=1570.796,
            ),
            _topology_face(
                step=1,
                face_id="F_center_hole",
                center=[-12.5, 0.0, -7.5],
                normal=[1.0, 0.0, 0.0],
                bbox=_bbox(-12.5, 12.5, -12.5, 12.5, -15.0, 0.0),
                geom_type="CYLINDER",
                radius=12.5,
                axis_origin=[0.0, 0.0, 0.0],
                axis_direction=[0.0, 0.0, 1.0],
                area=1178.097,
            ),
            *bolt_faces,
        ],
        edges=[],
        faces_total=9,
        edges_total=0,
        max_items_per_type=20,
    )

    requirement_text = (
        "Create a circular end cap by extruding a 70.0 millimeter diameter disk downward by 5.0 millimeters, "
        "then add a concentric 50.0 millimeter diameter boss that extends a further 10.0 millimeters downward "
        "from the disk bottom face. On the same top-face sketch, draw one concentric 25.0 millimeter center hole "
        "and six 6.0 millimeter bolt holes on a 55.0 millimeter pitch circle, with one bolt-hole center on the "
        "positive X axis (the 3 o'clock seed position). Use a single cut-extrude downward by 15.0 millimeters so "
        "both the center hole and the six bolt-circle holes cut through the full flange-plus-boss thickness in one operation."
    )
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(
            step=1,
            solids=1,
            faces=17,
            edges=60,
            volume=30601.74392805413,
            bbox=[70.0, 70.0, 15.0],
            bbox_min=[-35.0, -35.0, -15.0],
            bbox_max=[35.0, 35.0, 0.0],
            topology_index=topology_index,
        ),
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
                check_id="feature_hole",
                label="feature hole",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="found hole/recess-like subtractive geometry in final snapshot execute_build123d_geometry_fallback=true",
            ),
            RequirementCheck(
                check_id="feature_local_anchor_alignment",
                label="local anchor alignment",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence=(
                    "required_centers=[[27.5, 0.0], [13.75, 23.815699], [-13.75, 23.815699], "
                    "[-27.5, 0.0], [-13.75, -23.815699], [13.75, -23.815699]], "
                    "realized_centers=[[-13.75, -23.815699], [13.75, -23.815699], [-27.5, 0.0], "
                    "[-13.75, 23.815699], [27.5, 0.0], [13.75, 23.815699]]"
                ),
            ),
            RequirementCheck(
                check_id="feature_profile_shape_alignment",
                label="profile shape alignment",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="required_shapes=['circle'], observed_snapshot_profile_shapes=['polygon', 'circle', 'triangle'], execute_build123d_geometry_fallback=true",
            ),
            RequirementCheck(
                check_id="feature_pattern",
                label="feature pattern",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="execute_build123d_geometry_fallback=true, found repeated direct feature pattern in final geometry",
            ),
            RequirementCheck(
                check_id="feature_merged_body_result",
                label="merged body",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="final_solids=1, requires_merged_body=True",
            ),
        ],
    )

    assert summary.insufficient_evidence == []


def test_path_sweep_geometry_fallback_accepts_revolution_bend_faces() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    geometry_objects = GeometryObjectIndex(
        solids=[],
        faces=[
            _geometry_face(
                face_id="F_cyl_1",
                center=[25.0, 0.0, 0.0],
                normal=[0.0, 1.0, 0.0],
                bbox=_bbox(0.0, 50.0, -10.0, 10.0, -10.0, 10.0),
                geom_type="CYLINDER",
                radius=10.0,
                axis_origin=[0.0, 0.0, 0.0],
                axis_direction=[1.0, 0.0, 0.0],
            ),
            _geometry_face(
                face_id="F_bend",
                center=[70.0, 10.0, 0.0],
                normal=[0.707, -0.707, 0.0],
                bbox=_bbox(49.4, 90.0, -10.0, 31.6, -10.0, 10.0),
                geom_type="REVOLUTION",
            ),
            _geometry_face(
                face_id="F_cyl_2",
                center=[80.0, 55.0, 0.0],
                normal=[1.0, 0.0, 0.0],
                bbox=_bbox(70.0, 90.0, 30.0, 80.0, -10.0, 10.0),
                geom_type="CYLINDER",
                radius=10.0,
                axis_origin=[80.0, 30.0, 0.0],
                axis_direction=[0.0, 1.0, 0.0],
            ),
            _geometry_face(
                face_id="F_cap_start",
                center=[0.0, 0.0, 0.0],
                normal=[-1.0, 0.0, 0.0],
                bbox=_bbox(0.0, 0.0, -10.0, 10.0, -10.0, 10.0),
                geom_type="PLANE",
            ),
            _geometry_face(
                face_id="F_cap_end",
                center=[80.0, 80.0, 0.0],
                normal=[0.0, 1.0, 0.0],
                bbox=_bbox(70.0, 90.0, 80.0, 80.0, -10.0, 10.0),
                geom_type="PLANE",
            ),
        ],
        edges=[],
        faces_total=5,
        edges_total=0,
        max_items_per_type=20,
    )
    snapshot = CADStateSnapshot(
        step=1,
        features=[],
        geometry=GeometryInfo(
            solids=1,
            faces=5,
            edges=0,
            volume=-9829.660064683596,
            bbox=[90.0000002, 90.0000001, 20.0000002],
            center_of_mass=[0.0, 0.0, 0.0],
            surface_area=1000.0,
            bbox_min=[0.0, -10.0, -10.0],
            bbox_max=[90.0, 80.0, 10.0],
        ),
        issues=[],
        warnings=[],
        blockers=[],
        images=[],
        sketch_state=None,
        geometry_objects=geometry_objects,
        topology_index=None,
        success=True,
        error=None,
    )

    ok, evidence = service._snapshot_has_execute_build123d_path_sweep_fallback(
        snapshot=snapshot,
        hollow_profile_required=False,
        bend_required=True,
    )
    relation = service._relation_bend_realized(snapshot, blocking=True)

    assert ok is True
    assert "revolution_faces=1" in evidence
    assert relation.status.value == "pass"
    assert relation.measured["revolution_faces"] == 1
    assert relation.measured["cylinder_faces"] == 2


def test_path_sweep_recipe_detection_accepts_named_plane_offset_frames() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    history = [
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={
                "source": "execute_build123d",
                "build123d_code": (
                    "from build123d import *\n"
                    "with BuildLine() as path:\n"
                    "    Line((0, 0, 0), (50, 0, 0))\n"
                    "    RadiusArc((50, 0, 0), (80, 30, 0), 30)\n"
                    "    Line((80, 30, 0), (80, 80, 0))\n"
                    "profile_plane = Plane.YZ.offset(0)\n"
                    "with BuildSketch(profile_plane) as profile:\n"
                    "    Circle(10)\n"
                    "    Circle(8, mode=Mode.SUBTRACT)\n"
                    "with BuildPart() as pipe:\n"
                    "    sweep(profile.sketch, path=path.wire())\n"
                    "result = pipe.part\n"
                ),
            },
            result_snapshot=_snapshot(step=1),
            success=True,
            error=None,
        )
    ]

    assert service._history_has_execute_build123d_path_sweep_recipe(history) is True


def test_path_sweep_recipe_detection_survives_failed_pre_snapshot_history_entries() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    build123d_code = (
        "from build123d import *\n"
        "with BuildLine() as path:\n"
        "    Line((0, 0, 0), (50, 0, 0))\n"
        "    RadiusArc((50, 0, 0), (80, 30, 0), 30)\n"
        "    Line((80, 30, 0), (80, 80, 0))\n"
        "profile_plane = Plane(origin=(0, 0, 0), z_dir=(1, 0, 0))\n"
        "with BuildSketch(profile_plane) as profile:\n"
        "    Circle(10)\n"
        "    Circle(8, mode=Mode.SUBTRACT)\n"
        "with BuildPart() as pipe:\n"
        "    sweep(profile.sketch, path=path.wire())\n"
        "result = pipe.part\n"
    )
    history = [
        ActionHistoryEntry(
            step=0,
            action_type=CADActionType.MODIFY_ACTION,
            action_params={"reason": "preflight_lint_failed"},
            result_snapshot=_snapshot(step=0),
            success=False,
            error="lint failure",
        ),
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={
                "source": "execute_build123d",
                "build123d_code": build123d_code,
            },
            result_snapshot=_snapshot(step=1),
            success=True,
            error=None,
        ),
    ]

    assert service._history_has_execute_build123d_path_sweep_recipe(history) is True


def test_path_sweep_checks_use_execute_build123d_fallback_after_failed_pre_snapshot_turns() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    requirement_text = (
        "Use the Sweep feature to construct. First, draw the path sketch on the front view: "
        "an L-shaped path consisting of a 50.0mm horizontal line, a 90-degree tangent arc "
        "with a radius of 30.0mm, and another 50.0mm tangent straight line. Exit the path "
        "sketch. Create a vertical reference plane at one endpoint of the path, and draw the "
        "profile sketch: two concentric circles, with an outer diameter of 20.0mm and an "
        "inner diameter of 16.0mm (wall thickness 2mm). Execute the sweep command, select the "
        "annular profile, and sweep along the L-shaped path to generate a hollow bent pipe solid."
    )
    geometry_objects = GeometryObjectIndex(
        solids=[],
        faces=[
            _geometry_face(
                face_id="F_cyl_outer_1",
                center=[25.0, 0.0, 0.0],
                normal=[0.0, 1.0, 0.0],
                bbox=_bbox(0.0, 50.0, -10.0, 10.0, -10.0, 10.0),
                geom_type="CYLINDER",
                radius=10.0,
                axis_origin=[0.0, 0.0, 0.0],
                axis_direction=[1.0, 0.0, 0.0],
            ),
            _geometry_face(
                face_id="F_cyl_inner_1",
                center=[25.0, 0.0, 0.0],
                normal=[0.0, -1.0, 0.0],
                bbox=_bbox(0.0, 50.0, -8.0, 8.0, -8.0, 8.0),
                geom_type="CYLINDER",
                radius=8.0,
                axis_origin=[0.0, 0.0, 0.0],
                axis_direction=[1.0, 0.0, 0.0],
            ),
            _geometry_face(
                face_id="F_bend_outer",
                center=[70.0, 10.0, 0.0],
                normal=[0.707, -0.707, 0.0],
                bbox=_bbox(49.4, 90.0, -10.0, 31.6, -10.0, 10.0),
                geom_type="TORUS",
            ),
            _geometry_face(
                face_id="F_bend_inner",
                center=[70.0, 10.0, 0.0],
                normal=[0.707, 0.707, 0.0],
                bbox=_bbox(51.4, 88.0, -8.0, 29.6, -8.0, 8.0),
                geom_type="TORUS",
            ),
            _geometry_face(
                face_id="F_cyl_outer_2",
                center=[80.0, 55.0, 0.0],
                normal=[1.0, 0.0, 0.0],
                bbox=_bbox(70.0, 90.0, 30.0, 80.0, -10.0, 10.0),
                geom_type="CYLINDER",
                radius=10.0,
                axis_origin=[80.0, 30.0, 0.0],
                axis_direction=[0.0, 1.0, 0.0],
            ),
            _geometry_face(
                face_id="F_cyl_inner_2",
                center=[80.0, 55.0, 0.0],
                normal=[-1.0, 0.0, 0.0],
                bbox=_bbox(72.0, 88.0, 32.0, 78.0, -8.0, 8.0),
                geom_type="CYLINDER",
                radius=8.0,
                axis_origin=[80.0, 30.0, 0.0],
                axis_direction=[0.0, 1.0, 0.0],
            ),
            _geometry_face(
                face_id="F_cap_start",
                center=[0.0, 0.0, 0.0],
                normal=[-1.0, 0.0, 0.0],
                bbox=_bbox(0.0, 0.0, -10.0, 10.0, -10.0, 10.0),
                geom_type="PLANE",
            ),
            _geometry_face(
                face_id="F_cap_end",
                center=[80.0, 80.0, 0.0],
                normal=[0.0, 1.0, 0.0],
                bbox=_bbox(70.0, 90.0, 80.0, 80.0, -10.0, 10.0),
                geom_type="PLANE",
            ),
        ],
        edges=[],
        faces_total=8,
        edges_total=0,
        max_items_per_type=20,
    )
    snapshot = CADStateSnapshot(
        step=1,
        features=[],
        geometry=GeometryInfo(
            solids=1,
            faces=8,
            edges=14,
            volume=16639.319929511308,
            bbox=[90.0, 90.0, 20.0000001],
            center_of_mass=[52.997473765737176, 21.904781734715083, 0.0],
            surface_area=17344.422753420225,
            bbox_min=[0.0, -10.0, -10.0000001],
            bbox_max=[90.0, 80.0, 10.0],
        ),
        issues=[],
        warnings=[],
        blockers=[],
        images=[],
        sketch_state=None,
        geometry_objects=geometry_objects,
        topology_index=None,
        success=True,
        error=None,
    )
    history = [
        ActionHistoryEntry(
            step=0,
            action_type=CADActionType.MODIFY_ACTION,
            action_params={"reason": "preflight_lint_failed"},
            result_snapshot=_snapshot(step=0),
            success=False,
            error="lint failure",
        ),
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={
                "source": "execute_build123d",
                "build123d_code": (
                    "from build123d import *\n"
                    "with BuildLine() as path:\n"
                    "    Line((0, 0, 0), (50, 0, 0))\n"
                    "    RadiusArc((50, 0, 0), (80, 30, 0), 30)\n"
                    "    Line((80, 30, 0), (80, 80, 0))\n"
                    "profile_plane = Plane.YZ.offset(0)\n"
                    "with BuildSketch(profile_plane) as profile:\n"
                    "    Circle(10)\n"
                    "    Circle(8, mode=Mode.SUBTRACT)\n"
                    "with BuildPart() as pipe:\n"
                    "    sweep(profile.sketch, path=path.wire())\n"
                    "result = pipe.part\n"
                ),
            },
            result_snapshot=snapshot,
            success=True,
            error=None,
        ),
    ]

    checks = service._build_path_sweep_checks(
        snapshot=snapshot,
        history=history,
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
    )

    assert {check.check_id for check in checks} == {
        "feature_path_sweep_rail",
        "feature_path_sweep_profile",
        "feature_path_sweep_frame",
        "feature_path_sweep_result",
    }
    assert all(check.status == RequirementCheckStatus.PASS for check in checks)
    assert all(
        "execute_build123d_path_sweep_recipe=true" in str(check.evidence)
        for check in checks
    )


def test_path_sweep_geometry_fallback_rejects_hollow_bent_pipe_with_extrusion_straights() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    geometry_objects = GeometryObjectIndex(
        solids=[],
        faces=[
            _geometry_face(
                face_id="F_cyl_outer_1",
                center=[25.0, 0.0, 0.0],
                normal=[0.0, 1.0, 0.0],
                bbox=_bbox(0.0, 50.0, -10.0, 10.0, -10.0, 10.0),
                geom_type="CYLINDER",
                radius=10.0,
                axis_origin=[0.0, 0.0, 0.0],
                axis_direction=[1.0, 0.0, 0.0],
            ),
            _geometry_face(
                face_id="F_cyl_inner_1",
                center=[25.0, 0.0, 0.0],
                normal=[0.0, -1.0, 0.0],
                bbox=_bbox(0.0, 50.0, -8.0, 8.0, -8.0, 8.0),
                geom_type="CYLINDER",
                radius=8.0,
                axis_origin=[0.0, 0.0, 0.0],
                axis_direction=[1.0, 0.0, 0.0],
            ),
            _geometry_face(
                face_id="F_bend_outer",
                center=[70.0, 10.0, 0.0],
                normal=[0.707, -0.707, 0.0],
                bbox=_bbox(49.4, 90.0, -10.0, 31.6, -10.0, 10.0),
                geom_type="TORUS",
            ),
            _geometry_face(
                face_id="F_bend_inner",
                center=[70.0, 10.0, 0.0],
                normal=[0.707, 0.707, 0.0],
                bbox=_bbox(51.4, 88.0, -8.0, 29.6, -8.0, 8.0),
                geom_type="TORUS",
            ),
            _geometry_face(
                face_id="F_wrong_leg_outer",
                center=[65.0, 55.0, 0.0],
                normal=[0.0, 0.0, 1.0],
                bbox=_bbox(50.0, 80.0, 30.0, 80.0, -10.0, 10.0),
                geom_type="EXTRUSION",
            ),
            _geometry_face(
                face_id="F_wrong_leg_inner",
                center=[65.0, 55.0, 0.0],
                normal=[0.0, 0.0, -1.0],
                bbox=_bbox(52.0, 78.0, 32.0, 78.0, -8.0, 8.0),
                geom_type="EXTRUSION",
            ),
            _geometry_face(
                face_id="F_cap_start",
                center=[0.0, 0.0, 0.0],
                normal=[-1.0, 0.0, 0.0],
                bbox=_bbox(0.0, 0.0, -10.0, 10.0, -10.0, 10.0),
                geom_type="PLANE",
            ),
            _geometry_face(
                face_id="F_cap_end",
                center=[65.0, 55.0, 0.0],
                normal=[0.0, 1.0, 0.0],
                bbox=_bbox(55.0, 75.0, 80.0, 80.0, -10.0, 10.0),
                geom_type="PLANE",
            ),
        ],
        edges=[],
        faces_total=8,
        edges_total=0,
        max_items_per_type=20,
    )
    snapshot = CADStateSnapshot(
        step=1,
        features=[],
        geometry=GeometryInfo(
            solids=1,
            faces=8,
            edges=14,
            volume=16639.319929511308,
            bbox=[90.0, 90.0, 20.0000001],
            center_of_mass=[52.997473765737176, 21.904781734715083, 0.0],
            surface_area=17344.422753420225,
            bbox_min=[0.0, -10.0, -10.0000001],
            bbox_max=[90.0, 80.0, 10.0],
        ),
        issues=[],
        warnings=[],
        blockers=[],
        images=[],
        sketch_state=None,
        geometry_objects=geometry_objects,
        topology_index=None,
        success=True,
        error=None,
    )

    ok, evidence = service._snapshot_has_execute_build123d_path_sweep_fallback(
        snapshot=snapshot,
        hollow_profile_required=True,
        bend_required=True,
    )

    assert ok is False
    assert "cylinder_faces=2" in evidence
    assert "torus_faces=2" in evidence


@pytest.mark.parametrize(
    ("requirement_text", "expected_fragment"),
    [
        (
            "draw horizontally outward to (25.0, 0) [corresponding to outer diameter R25]",
            "matched_axisymmetric_point=[25.0, 0.0]",
        ),
        (
            "draw vertically upward to (25.0, 15.0) [base thickness]",
            "matched_axisymmetric_point=[25.0, 15.0]",
        ),
        (
            "draw vertically upward to (20.0, 20.0) [total height]",
            "matched_axisymmetric_point=[20.0, 20.0]",
        ),
    ],
)
def test_interpretation_verifies_axisymmetric_profile_point_clauses(
    requirement_text: str,
    expected_fragment: str,
) -> None:
    combined_requirement = (
        f"revolve the cross-sectional profile around the Z-axis. {requirement_text}"
    )
    topology_index = TopologyObjectIndex(
        faces=[
            _topology_face(
                step=1,
                face_id="F_inner",
                center=[-10.0, 0.0, 10.0],
                normal=[1.0, 0.0, 0.0],
                bbox=_bbox(-10.0, 10.0, -10.0, 10.0, 0.0, 20.0),
                geom_type="CYLINDER",
                radius=10.0,
                axis_origin=[0.0, 0.0, 0.0],
                axis_direction=[0.0, 0.0, 1.0],
                area=1256.637061436,
            ),
            _topology_face(
                step=1,
                face_id="F_outer",
                center=[-25.0, 0.0, 7.5],
                normal=[-1.0, 0.0, 0.0],
                bbox=_bbox(-25.0, 25.0, -25.0, 25.0, 0.0, 15.0),
                geom_type="CYLINDER",
                radius=25.0,
                axis_origin=[0.0, 0.0, 0.0],
                axis_direction=[0.0, 0.0, 1.0],
                area=2356.1944901921893,
            ),
            _topology_face(
                step=1,
                face_id="F_step",
                center=[-20.0, 0.0, 17.5],
                normal=[-1.0, 0.0, 0.0],
                bbox=_bbox(-20.0, 20.0, -20.0, 20.0, 15.0, 20.0),
                geom_type="CYLINDER",
                radius=20.0,
                axis_origin=[0.0, 0.0, 15.0],
                axis_direction=[0.0, 0.0, 1.0],
                area=628.318530718,
            ),
        ],
        edges=[],
        faces_total=3,
        edges_total=0,
        max_items_per_type=20,
    )
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(
            step=1,
            solids=1,
            faces=6,
            edges=9,
            volume=29452.431127404176,
            bbox=[50.0, 50.0, 20.0],
            bbox_min=[-25.0, -25.0, 0.0],
            bbox_max=[25.0, 25.0, 20.0],
            topology_index=topology_index,
        ),
        history=[],
        requirements={"description": combined_requirement},
        requirement_text=combined_requirement,
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": combined_requirement},
        requirement_text=combined_requirement,
    )

    matched_clause = next(
        item
        for item in summary.clause_interpretations
        if item.clause_text == requirement_text
    )
    assert matched_clause.status == RequirementClauseStatus.VERIFIED
    assert expected_fragment in str(matched_clause.evidence)


def test_interpretation_verifies_downward_disk_clause_from_axisymmetric_band() -> None:
    requirement_text = (
        "Create a circular end cap by extruding a 70.0 millimeter diameter disk downward by 5.0 millimeters"
    )
    topology_index = TopologyObjectIndex(
        faces=[
            _topology_face(
                step=1,
                face_id="F_flange",
                center=[-35.0, 0.0, -2.5],
                normal=[-1.0, 0.0, 0.0],
                bbox=_bbox(-35.0, 35.0, -35.0, 35.0, -5.0, 0.0),
                geom_type="CYLINDER",
                radius=35.0,
                axis_origin=[0.0, 0.0, -5.0],
                axis_direction=[0.0, 0.0, 1.0],
                area=1099.5574287565,
            ),
            _topology_face(
                step=1,
                face_id="F_boss",
                center=[-25.0, 0.0, -10.0],
                normal=[-1.0, 0.0, 0.0],
                bbox=_bbox(-25.0, 25.0, -25.0, 25.0, -15.0, -5.0),
                geom_type="CYLINDER",
                radius=25.0,
                axis_origin=[0.0, 0.0, -15.0],
                axis_direction=[0.0, 0.0, 1.0],
                area=1570.796326795,
            ),
        ],
        edges=[],
        faces_total=2,
        edges_total=0,
        max_items_per_type=20,
    )
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(
            step=1,
            solids=1,
            faces=17,
            edges=60,
            volume=30601.743928055705,
            bbox=[70.0, 70.0, 15.0],
            bbox_min=[-35.0, -35.0, -15.0],
            bbox_max=[35.0, 35.0, 0.0],
            topology_index=topology_index,
        ),
        history=[],
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
    )

    assert summary.clause_interpretations[0].status == RequirementClauseStatus.VERIFIED


def test_interpretation_closes_path_sweep_process_and_profile_clauses_from_family_checks() -> None:
    requirement_text = (
        "Use the Sweep feature to construct. First, draw the path sketch on the front view: "
        "an L-shaped path consisting of a 50.0mm horizontal line, a 90-degree tangent arc "
        "with a radius of 30.0mm, and another 50.0mm tangent straight line. Exit the path "
        "sketch. Create a vertical reference plane at one endpoint of the path, and draw the "
        "profile sketch: two concentric circles, with an outer diameter of 20.0mm and an "
        "inner diameter of 16.0mm (wall thickness 2mm). Execute the sweep command, select the "
        "annular profile, and sweep along the L-shaped path to generate a hollow bent pipe solid."
    )
    topology_index = TopologyObjectIndex(
        faces=[
            _topology_face(
                step=1,
                face_id="F_outer_pipe",
                center=[40.0, 0.0, 20.0],
                normal=[-1.0, 0.0, 0.0],
                bbox=_bbox(30.0, 50.0, -10.0, 10.0, 0.0, 40.0),
                geom_type="CYLINDER",
                radius=10.0,
                axis_origin=[40.0, 0.0, 0.0],
                axis_direction=[0.0, 0.0, 1.0],
                area=2513.274122872,
            ),
            _topology_face(
                step=1,
                face_id="F_inner_pipe",
                center=[40.0, 0.0, 20.0],
                normal=[1.0, 0.0, 0.0],
                bbox=_bbox(32.0, 48.0, -8.0, 8.0, 0.0, 40.0),
                geom_type="CYLINDER",
                radius=8.0,
                axis_origin=[40.0, 0.0, 0.0],
                axis_direction=[0.0, 0.0, 1.0],
                area=2010.619298298,
            ),
        ],
        edges=[],
        faces_total=2,
        edges_total=0,
        max_items_per_type=20,
    )
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(
            step=1,
            solids=1,
            faces=8,
            edges=14,
            volume=16013.742100620624,
            bbox=[94.06148056457118, 93.71988681140206, 20.0000002],
            bbox_min=[-0.0000001, -10.0, 0.0],
            bbox_max=[94.0614804, 83.7198868, 20.0000001],
            topology_index=topology_index,
        ),
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
                check_id="feature_path_sweep_rail",
                label="path-sweep rail",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence=(
                    "execute_build123d_path_sweep_recipe=true, "
                    "execute_build123d_geometry_fallback=true, torus_faces=0, "
                    "revolution_faces=2, cylinder_faces=2, plane_faces=2, "
                    "bbox=[94.06148056457118, 93.71988681140206, 20.0000002], "
                    "volume=16013.742100620624"
                ),
            ),
            RequirementCheck(
                check_id="feature_path_sweep_profile",
                label="path-sweep profile",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence=(
                    "execute_build123d_path_sweep_recipe=true, "
                    "execute_build123d_geometry_fallback=true, torus_faces=0, "
                    "revolution_faces=2, cylinder_faces=2, plane_faces=2, "
                    "bbox=[94.06148056457118, 93.71988681140206, 20.0000002], "
                    "volume=16013.742100620624"
                ),
            ),
            RequirementCheck(
                check_id="feature_path_sweep_frame",
                label="path-sweep frame",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence=(
                    "execute_build123d_path_sweep_recipe=true, "
                    "execute_build123d_geometry_fallback=true, torus_faces=0, "
                    "revolution_faces=2, cylinder_faces=2, plane_faces=2, "
                    "bbox=[94.06148056457118, 93.71988681140206, 20.0000002], "
                    "volume=16013.742100620624"
                ),
            ),
            RequirementCheck(
                check_id="feature_path_sweep_result",
                label="path-sweep result",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence=(
                    "execute_build123d_path_sweep_recipe=true, "
                    "execute_build123d_geometry_fallback=true, torus_faces=0, "
                    "revolution_faces=2, cylinder_faces=2, plane_faces=2, "
                    "bbox=[94.06148056457118, 93.71988681140206, 20.0000002], "
                    "volume=16013.742100620624"
                ),
            ),
            RequirementCheck(
                check_id="feature_profile_shape_alignment",
                label="profile shape alignment",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence=(
                    "required_shapes=['circle'], observed_post_solid_shapes=['<none>'], "
                    "missing_post_solid_profile_window=true, "
                    "observed_snapshot_profile_shapes=['circle'], "
                    "execute_build123d_geometry_fallback=true"
                ),
            ),
            RequirementCheck(
                check_id="feature_merged_body_result",
                label="merged body",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="final_solids=1, requires_merged_body=True",
            ),
        ],
    )

    clause_status = {
        item.clause_text: item.status for item in summary.clause_interpretations
    }

    assert (
        clause_status["draw the path sketch on the front view: an L-shaped path consisting of a 50.0mm horizontal line"]
        == RequirementClauseStatus.VERIFIED
    )
    assert (
        clause_status["a 90-degree tangent arc with a radius of 30.0mm"]
        == RequirementClauseStatus.VERIFIED
    )
    assert (
        clause_status["another 50.0mm tangent straight line"]
        == RequirementClauseStatus.VERIFIED
    )
    assert clause_status["Exit the path sketch"] == RequirementClauseStatus.NOT_APPLICABLE
    assert (
        clause_status["Create a vertical reference plane at one endpoint of the path"]
        == RequirementClauseStatus.VERIFIED
    )
    assert (
        clause_status["draw the profile sketch: two concentric circles"]
        == RequirementClauseStatus.VERIFIED
    )
    assert summary.insufficient_evidence == []


def test_interpretation_verifies_mixed_nested_section_and_annular_groove_clauses_from_passed_checks() -> None:
    requirement_text = (
        "Select the XY plane, draw a circle with a diameter of 50.0 mm and a square with a side "
        "length of 25.0 mm centered. Extrude the section by 60.0 mm. Select the front view plane, "
        "at a height of 30.0 mm, draw a 5.0 mm x 2.0 mm rectangle aligned with the edge, and use "
        "a revolved cut to create an annular groove."
    )
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(
            step=1,
            solids=1,
            faces=9,
            edges=18,
            volume=78801.76003589762,
            bbox=[50.0, 50.0, 60.0],
            bbox_min=[-25.0, -25.0, 0.0],
            bbox_max=[25.0, 25.0, 60.0],
        ),
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
                label="positive extrude span",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence=(
                    "plane=XY, axis=Z, required_lower_bound=0.0, required_minimum_extent=60.0, "
                    "require_positive_direction=True, observed_range=[0.0, 60.0]"
                ),
            ),
            RequirementCheck(
                check_id="feature_inner_void_cutout",
                label="mixed nested section",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence=(
                    "execute_build123d_geometry_fallback=true, outer_diameter=50.0, "
                    "inner_dims=[25.0, 25.0], outer_cylindrical_faces=2, inner_planar_faces=4, "
                    "outer_axis_coverage=60.0"
                ),
            ),
            RequirementCheck(
                check_id="feature_profile_shape_alignment",
                label="profile shape alignment",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence=(
                    "required_shapes=['circle', 'rectangle'], observed_snapshot_profile_shapes="
                    "['circle', 'rectangle'], execute_build123d_geometry_fallback=true"
                ),
            ),
            RequirementCheck(
                check_id="feature_revolved_groove_alignment",
                label="groove alignment",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence=(
                    "execute_build123d_geometry_fallback=true, outer_radius=25.0, groove_dims=[5.0, 2.0], "
                    "candidate_radius=23.0, axial_window=[25.0, 30.0], height_match_mode=world_space:top_edge"
                ),
            ),
            RequirementCheck(
                check_id="feature_revolved_groove_result",
                label="groove result",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence=(
                    "execute_build123d_geometry_fallback=true, outer_radius=25.0, groove_dims=[5.0, 2.0], "
                    "candidate_radius=23.0, axial_window=[25.0, 30.0], height_match_mode=world_space:top_edge"
                ),
            ),
        ],
    )

    clause_status = {
        clause.clause_text: clause.status for clause in summary.clause_interpretations
    }
    assert (
        clause_status["draw a circle with a diameter of 50.0 mm"]
        == RequirementClauseStatus.VERIFIED
    )
    assert (
        clause_status["a square with a side length of 25.0 mm centered"]
        == RequirementClauseStatus.VERIFIED
    )
    assert (
        clause_status["Extrude the section by 60.0 mm"]
        == RequirementClauseStatus.VERIFIED
    )
    assert (
        clause_status["at a height of 30.0 mm"]
        == RequirementClauseStatus.VERIFIED
    )
    assert (
        clause_status["draw a 5.0 mm x 2.0 mm rectangle aligned with the edge"]
        == RequirementClauseStatus.VERIFIED
    )
    assert summary.insufficient_evidence == []


def test_cylindrical_slot_alignment_recovers_centerline_from_top_surface_and_radius() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    requirement_text = (
        "Create a block by drawing a 100.0x50.0 rectangle in the XY plane and extruding it by 20.0. "
        "Create a cutting cylinder with radius 12.0, axis along the X-axis, and cylinder centerline placed at (0,0,8.0), "
        "with length set to 110.0 to cover the entire length. Perform a Boolean difference: the block as the target body "
        "and the cylinder as the tool body, resulting in a semicircular slot on the top surface."
    )
    top_face = _topology_face(
        step=1,
        face_id="F_top",
        center=[0.0, 0.0, 20.0],
        normal=[0.0, 0.0, 1.0],
        bbox=_bbox(-50.0, 50.0, -25.0, 25.0, 20.0, 20.0),
        area=5000.0,
    )
    slot_face = _topology_face(
        step=1,
        face_id="F_slot",
        center=[1.5, 0.0, 12.0],
        normal=[0.0, 0.0, 1.0],
        bbox=_bbox(-47.0, 50.0, -12.0, 12.0, 0.0, 20.0),
        geom_type="CYLINDER",
        radius=12.0,
        axis_origin=[-47.0, 0.0, 0.0],
        axis_direction=[1.0, 0.0, 0.0],
        area=2500.0,
    )
    snapshot = _snapshot(
        step=1,
        solids=1,
        faces=7,
        edges=15,
        volume=90952.21315766168,
        bbox=[100.0, 50.0, 20.0],
        bbox_min=[-50.0, -25.0, 0.0],
        bbox_max=[50.0, 25.0, 20.0],
        topology_index=TopologyObjectIndex(
            faces=[top_face, slot_face],
            edges=[],
            faces_total=2,
            edges_total=0,
            max_items_per_type=20,
        ),
    )

    checks = service._build_cylindrical_slot_alignment_checks(
        snapshot=snapshot,
        requirement_text=requirement_text,
    )

    assert len(checks) == 1
    assert checks[0].status == RequirementCheckStatus.PASS
    assert "observed_reference_point=[1.5, 0.0, 8.0]" in str(checks[0].evidence)


@pytest.mark.asyncio
async def test_validate_requirement_accepts_snapshot_only_spherical_recess_pattern_with_bbox_inferred_centers() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    session_id = "session-validate-snapshot-spherical-recess-bbox-centers"
    service._session_manager.clear_session(session_id)

    top_face = _topology_face(
        step=1,
        face_id="F_top",
        center=[0.0, 0.0, 15.0],
        normal=[0.0, 0.0, 1.0],
        bbox=_bbox(-25.0, 25.0, -25.0, 25.0, 15.0, 15.0),
        area=2500.0,
    )
    sphere_faces = [
        _topology_face(
            step=1,
            face_id=f"F_sphere_{index}",
            center=[float(x), float(y), 12.5],
            normal=[1.0, 0.0, 0.0],
            bbox=_bbox(float(x) - 5.0, float(x) + 5.0, float(y) - 5.0, float(y) + 5.0, 10.0, 15.0),
            geom_type="SPHERE",
            area=math.pi * 50.0,
        )
        for index, (x, y) in enumerate(
            (
                (-15.0, -15.0),
                (-15.0, 0.0),
                (-15.0, 15.0),
                (0.0, -15.0),
                (0.0, 0.0),
                (0.0, 15.0),
                (15.0, -15.0),
                (15.0, 0.0),
                (15.0, 15.0),
            ),
            start=1,
        )
    ]
    circle_edges = [
        _topology_edge(
            step=1,
            edge_id=f"E_open_{index}",
            center=[float(x), float(y), 15.0],
            bbox=_bbox(float(x) - 5.0, float(x) + 5.0, float(y) - 5.0, float(y) + 5.0, 15.0, 15.0),
            radius=5.0,
            length=2.0 * math.pi * 5.0,
            axis_origin=[float(x), float(y), 15.0],
            axis_direction=[0.0, 0.0, 1.0],
        )
        for index, (x, y) in enumerate(
            (
                (-15.0, -15.0),
                (-15.0, 0.0),
                (-15.0, 15.0),
                (0.0, -15.0),
                (0.0, 0.0),
                (0.0, 15.0),
                (15.0, -15.0),
                (15.0, 0.0),
                (15.0, 15.0),
            ),
            start=1,
        )
    ]
    topology_index = TopologyObjectIndex(
        faces=[top_face, *sphere_faces],
        edges=circle_edges,
        faces_total=len([top_face, *sphere_faces]),
        edges_total=len(circle_edges),
        max_items_per_type=20,
    )

    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(
                step=1,
                solids=1,
                faces=15,
                edges=30,
                volume=34000.0,
                bbox=[50.0, 50.0, 15.0],
                bbox_min=[-25.0, -25.0, 0.0],
                bbox_max=[25.0, 25.0, 15.0],
                topology_index=topology_index,
            ),
            success=True,
            error=None,
        ),
    )

    requirement_text = (
        "Draw a 50.0x50.0mm square in the XY plane and extrude it by 15.0mm to create the base. "
        "Select the top face as the reference and create a sketch for positioning the center of the recess. "
        "Draw the center point and use it as a reference to create an auxiliary plane perpendicular to the top face. "
        "On the auxiliary plane, draw a semicircle with a radius of 5.0mm (the diameter edge coincides with the top face) "
        "and use the revolve cut command to generate the first hemispherical recess. Then use the linear pattern command, "
        "with direction 1 along the X-axis, spacing 15.0mm, and quantity 3; direction 2 along the Y-axis, spacing 15.0mm, "
        "and quantity 3. Select \"Center the pattern\" or pre-calculate the starting position to ensure that the nine holes "
        "are completely symmetrically centered on the 50x50 face, completing the construction of the shock absorber pad."
    )
    result = await service.validate_requirement(
        ValidateRequirementInput(
            session_id=session_id,
            requirement_text=requirement_text,
            requirements={"description": requirement_text},
        )
    )

    assert result.success is True
    assert result.coverage_confidence == pytest.approx(1.0)
    assert result.insufficient_evidence is False
    assert "feature_local_anchor_alignment" not in result.blockers
    assert "feature_pattern" not in result.blockers


@pytest.mark.asyncio
async def test_validate_requirement_rejects_buried_full_sphere_void_pattern_without_host_plane_openings() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    session_id = "session-validate-buried-spherical-void-pattern"
    service._session_manager.clear_session(session_id)

    top_face = _topology_face(
        step=1,
        face_id="F_top",
        center=[0.0, 0.0, 15.0],
        normal=[0.0, 0.0, 1.0],
        bbox=_bbox(-25.0, 25.0, -25.0, 25.0, 15.0, 15.0),
        area=2500.0,
    )
    sphere_faces = [
        _topology_face(
            step=1,
            face_id=f"F_sphere_{index}",
            center=[float(x), float(y), 10.0],
            normal=[1.0, 0.0, 0.0],
            bbox=_bbox(float(x) - 5.0, float(x) + 5.0, float(y) - 5.0, float(y) + 5.0, 5.0, 15.0),
            geom_type="SPHERE",
            area=math.pi * 100.0,
        )
        for index, (x, y) in enumerate(
            (
                (-15.0, -15.0),
                (-15.0, 0.0),
                (-15.0, 15.0),
                (0.0, -15.0),
                (0.0, 0.0),
                (0.0, 15.0),
                (15.0, -15.0),
                (15.0, 0.0),
                (15.0, 15.0),
            ),
            start=1,
        )
    ]
    topology_index = TopologyObjectIndex(
        faces=[top_face, *sphere_faces],
        edges=[],
        faces_total=len([top_face, *sphere_faces]),
        edges_total=0,
        max_items_per_type=20,
    )

    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(
                step=1,
                solids=1,
                faces=15,
                edges=21,
                volume=32787.61101961529,
                bbox=[50.0, 50.0, 15.0],
                bbox_min=[-25.0, -25.0, 0.0],
                bbox_max=[25.0, 25.0, 15.0],
                topology_index=topology_index,
            ),
            success=True,
            error=None,
        ),
    )

    requirement_text = (
        "Draw a 50.0x50.0mm square in the XY plane and extrude it by 15.0mm to create the base. "
        "Select the top face as the reference and create a sketch for positioning the center of the recess. "
        "Draw the center point and use it as a reference to create an auxiliary plane perpendicular to the top face. "
        "On the auxiliary plane, draw a semicircle with a radius of 5.0mm (the diameter edge coincides with the top face) "
        "and use the revolve cut command to generate the first hemispherical recess. Then use the linear pattern command, "
        "with direction 1 along the X-axis, spacing 15.0mm, and quantity 3; direction 2 along the Y-axis, spacing 15.0mm, "
        "and quantity 3. Select \"Center the pattern\" or pre-calculate the starting position to ensure that the nine holes "
        "are completely symmetrically centered on the 50x50 face, completing the construction of the shock absorber pad."
    )
    result = await service.validate_requirement(
        ValidateRequirementInput(
            session_id=session_id,
            requirement_text=requirement_text,
            requirements={"description": requirement_text},
        )
    )

    assert result.success is True
    assert "feature_spherical_recess_host_plane_opening" in result.blockers


@pytest.mark.asyncio
async def test_validate_requirement_rejects_detached_countersink_solids_as_plate_holes() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    session_id = "session-validate-detached-countersink-solids"
    service._session_manager.clear_session(session_id)

    plate_top = _topology_face(
        step=1,
        face_id="F_plate_top",
        center=[0.0, 0.0, 4.0],
        normal=[0.0, 0.0, 1.0],
        bbox=_bbox(-50.0, 50.0, -30.0, 30.0, 4.0, 4.0),
        area=6000.0,
    )

    detached_faces: list[TopologyFaceEntity] = []
    for index, (x, y) in enumerate(((25.0, 15.0), (25.0, 45.0), (75.0, 15.0), (75.0, 45.0)), start=1):
        solid_id = f"S_detached_{index}"
        detached_top = _topology_face(
            step=1,
            face_id=f"F_detached_top_{index}",
            center=[x, y, 8.0],
            normal=[0.0, 0.0, 1.0],
            bbox=_bbox(x - 6.0, x + 6.0, y - 6.0, y + 6.0, 8.0, 8.0),
            area=113.097,
        ).model_copy(update={"parent_solid_id": solid_id})
        detached_cone = _topology_face(
            step=1,
            face_id=f"F_detached_cone_{index}",
            center=[x - 4.5, y, 6.5],
            normal=[-0.70710678, 0.0, -0.70710678],
            bbox=_bbox(x - 6.0, x + 6.0, y - 6.0, y + 6.0, 5.0, 8.0),
            geom_type="CONE",
            area=119.958,
        ).model_copy(update={"parent_solid_id": solid_id})
        detached_cyl = _topology_face(
            step=1,
            face_id=f"F_detached_cyl_{index}",
            center=[x - 3.0, y, -0.5],
            normal=[-1.0, 0.0, 0.0],
            bbox=_bbox(x - 3.0, x + 3.0, y - 3.0, y + 3.0, -6.0, 5.0),
            geom_type="CYLINDER",
            radius=3.0,
            axis_origin=[x, y, -6.0],
            axis_direction=[0.0, 0.0, 1.0],
            area=207.345,
        ).model_copy(update={"parent_solid_id": solid_id})
        detached_faces.extend([detached_top, detached_cone, detached_cyl])

    topology_index = TopologyObjectIndex(
        faces=[plate_top, *detached_faces],
        edges=[],
        faces_total=1 + len(detached_faces),
        edges_total=0,
        max_items_per_type=40,
    )

    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(
                step=1,
                solids=5,
                faces=1 + len(detached_faces),
                edges=24,
                volume=49809.55736846801,
                bbox=[131.0, 81.0, 14.0],
                bbox_min=[-50.0, -30.0, -6.0],
                bbox_max=[81.0, 51.0, 8.0],
                topology_index=topology_index,
            ),
            success=True,
            error=None,
        ),
    )

    requirement_text = (
        "Select the top reference plane, draw a 100.0x60.0 millimeter rectangle and extrude it by 8.0 millimeters. "
        "Select the plate surface, and use the sketch to draw four points with coordinates (25,15), (25,45), (75,15), and (75,45). "
        "Exit the sketch, and activate the Hole Wizard or the revolved cut tool. If using the Hole Wizard: select \"Countersink,\" "
        "set the standard, head diameter 12.0 millimeters, cone angle 90 degrees, through-hole diameter 6.0 millimeters, and in the "
        "position tab, select the four points drawn earlier. If using manual modeling: at each point, first cut a through-hole with "
        "a diameter of 6.0 millimeters, then cut a conical recess with an upper diameter of 12.0 millimeters and a cone angle of "
        "90 degrees (pay attention to depth control to ensure the countersink face matches), and complete the operation."
    )
    result = await service.validate_requirement(
        ValidateRequirementInput(
            session_id=session_id,
            requirement_text=requirement_text,
            requirements={"description": requirement_text},
        )
    )

    assert result.success is True
    assert "feature_hole" in result.blockers
    assert "feature_countersink" in result.blockers
    assert "feature_hole_position_alignment" in result.blockers


@pytest.mark.asyncio
async def test_validate_requirement_surfaces_countersink_head_diameter_mismatch_as_concrete_blocker() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    session_id = "session-validate-countersink-head-diameter-mismatch"
    service._session_manager.clear_session(session_id)

    topology_index = _build_centered_plate_countersink_topology(head_radius=4.5)
    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(
                step=1,
                solids=1,
                faces=9,
                edges=8,
                volume=46996.261147178,
                bbox=[100.0, 60.0, 8.0],
                bbox_min=[-50.0, -30.0, -4.0],
                bbox_max=[50.0, 30.0, 4.0],
                topology_index=topology_index,
            ),
            success=True,
            error=None,
        ),
    )

    result = await service.validate_requirement(
        ValidateRequirementInput(
            session_id=session_id,
            requirement_text=_PLATE_COUNTERSINK_REQUIREMENT,
            requirements={"description": _PLATE_COUNTERSINK_REQUIREMENT},
        )
    )

    assert result.success is True
    assert result.is_complete is False
    assert result.insufficient_evidence is False
    assert "head_diameter_12_0_millimeters" in result.blockers
    assert (
        "cut_a_conical_recess_with_an_upper_diameter_of_12_0_millimeters"
        in result.blockers
    )
    clause_status = {
        clause.clause_id: clause.status for clause in result.clause_interpretations
    }
    assert clause_status["extrude_it_by_8_0_millimeters"] == RequirementClauseStatus.VERIFIED
    assert clause_status["in_the_position_tab"] == RequirementClauseStatus.NOT_APPLICABLE
    assert (
        clause_status["if_using_manual_modeling_at_each_point"]
        == RequirementClauseStatus.NOT_APPLICABLE
    )
    assert clause_status["complete_the_operation"] == RequirementClauseStatus.NOT_APPLICABLE
    assert (
        clause_status["head_diameter_12_0_millimeters"]
        == RequirementClauseStatus.CONTRADICTED
    )
    assert (
        clause_status["cut_a_conical_recess_with_an_upper_diameter_of_12_0_millimeters"]
        == RequirementClauseStatus.CONTRADICTED
    )


@pytest.mark.asyncio
async def test_validate_requirement_accepts_exact_countersink_head_diameter_without_clause_gaps() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    session_id = "session-validate-exact-countersink-head-diameter"
    service._session_manager.clear_session(session_id)

    topology_index = _build_centered_plate_countersink_topology(head_radius=6.0)
    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(
                step=1,
                solids=1,
                faces=9,
                edges=8,
                volume=46574.806908831764,
                bbox=[100.0, 60.0, 8.0],
                bbox_min=[-50.0, -30.0, -4.0],
                bbox_max=[50.0, 30.0, 4.0],
                topology_index=topology_index,
            ),
            success=True,
            error=None,
        ),
    )

    result = await service.validate_requirement(
        ValidateRequirementInput(
            session_id=session_id,
            requirement_text=_PLATE_COUNTERSINK_REQUIREMENT,
            requirements={"description": _PLATE_COUNTERSINK_REQUIREMENT},
        )
    )

    assert result.success is True
    assert result.is_complete is True
    assert result.insufficient_evidence is False
    clause_status = {
        clause.clause_id: clause.status for clause in result.clause_interpretations
    }
    assert clause_status["extrude_it_by_8_0_millimeters"] == RequirementClauseStatus.VERIFIED
    assert clause_status["head_diameter_12_0_millimeters"] == RequirementClauseStatus.VERIFIED
    assert (
        clause_status["cut_a_conical_recess_with_an_upper_diameter_of_12_0_millimeters"]
        == RequirementClauseStatus.VERIFIED
    )
    assert clause_status["in_the_position_tab"] == RequirementClauseStatus.NOT_APPLICABLE
    assert (
        clause_status["if_using_manual_modeling_at_each_point"]
        == RequirementClauseStatus.NOT_APPLICABLE
    )
    assert clause_status["complete_the_operation"] == RequirementClauseStatus.NOT_APPLICABLE


@pytest.mark.asyncio
async def test_query_feature_probes_surfaces_centered_host_translation_hints_for_corner_based_points() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    session_id = "session-query-feature-probes-corner-frame-translation"
    service._session_manager.clear_session(session_id)

    plate_top = _topology_face(
        step=1,
        face_id="F_plate_top",
        center=[0.0, 0.0, 4.0],
        normal=[0.0, 0.0, 1.0],
        bbox=_bbox(-50.0, 50.0, -30.0, 30.0, 4.0, 4.0),
        area=6000.0,
    )
    hole_cone = _topology_face(
        step=1,
        face_id="F_hole_cone",
        center=[21.75, 15.0, 3.75],
        normal=[-0.70710678, 0.0, -0.70710678],
        bbox=_bbox(21.5, 28.5, 11.5, 18.5, 3.5, 4.0),
        geom_type="CONE",
        area=60.0,
    )
    hole_cyl = _topology_face(
        step=1,
        face_id="F_hole_cyl",
        center=[22.0, 15.0, 1.75],
        normal=[-1.0, 0.0, 0.0],
        bbox=_bbox(22.0, 28.0, 12.0, 18.0, -4.0, 3.5),
        geom_type="CYLINDER",
        radius=3.0,
        axis_origin=[25.0, 15.0, -4.0],
        axis_direction=[0.0, 0.0, 1.0],
        area=120.0,
    )
    hole_floor = _topology_face(
        step=1,
        face_id="F_hole_floor",
        center=[25.0, 15.0, -4.0],
        normal=[0.0, 0.0, -1.0],
        bbox=_bbox(22.0, 28.0, 12.0, 18.0, -4.0, -4.0),
        area=28.274,
    )
    topology_index = TopologyObjectIndex(
        faces=[plate_top, hole_cone, hole_cyl, hole_floor],
        edges=[],
        faces_total=4,
        edges_total=0,
        max_items_per_type=40,
    )

    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(
                step=1,
                solids=1,
                faces=4,
                edges=17,
                volume=47883.00185359251,
                bbox=[100.0, 60.0, 8.0],
                bbox_min=[-50.0, -30.0, -4.0],
                bbox_max=[50.0, 30.0, 4.0],
                topology_index=topology_index,
            ),
            success=True,
            error=None,
        ),
    )

    requirement_text = (
        "Select the top reference plane, draw a 100.0x60.0 millimeter rectangle and extrude it by 8.0 millimeters. "
        "Select the plate surface, and use the sketch to draw four points with coordinates (25,15), (25,45), (75,15), and (75,45). "
        "Exit the sketch, and activate the Hole Wizard or the revolved cut tool. If using the Hole Wizard: select \"Countersink,\" "
        "set the standard, head diameter 12.0 millimeters, cone angle 90 degrees, through-hole diameter 6.0 millimeters, and in the "
        "position tab, select the four points drawn earlier. If using manual modeling: at each point, first cut a through-hole with "
        "a diameter of 6.0 millimeters, then cut a conical recess with an upper diameter of 12.0 millimeters and a cone angle of "
        "90 degrees (pay attention to depth control to ensure the countersink face matches), and complete the operation."
    )
    result = await service.query_feature_probes(
        QueryFeatureProbesInput(
            session_id=session_id,
            requirement_text=requirement_text,
            requirements={"description": requirement_text},
            families=["explicit_anchor_hole"],
        )
    )

    assert result.success is True
    assert len(result.probes) == 1
    probe = result.probes[0]
    assert probe.family == "explicit_anchor_hole"
    assert probe.signals["realized_centers"] == [[25.0, 15.0]]
    assert probe.signals["normalized_local_centers"] == [
        [-25.0, -15.0],
        [-25.0, 15.0],
        [25.0, -15.0],
        [25.0, 15.0],
    ]
    assert probe.signals["host_frame_translation_from_corner"] == [-50.0, -30.0]
    assert probe.signals["host_frame_dimensions"] == [100.0, 60.0]
    assert probe.family_binding == "explicit_anchor_hole"
    assert probe.required_evidence_kinds == ["geometry", "topology"]
    assert probe.anchor_summary["expected_local_center_count"] == 4
    assert probe.anchor_summary["realized_local_center_count"] == 1
    assert probe.anchor_summary["host_frame_translation_from_corner"] == [-50.0, -30.0]
    assert "center_layout_not_fully_realized" in probe.grounding_blockers
    assert "centered host frame suggests normalized centers" in probe.summary


@pytest.mark.asyncio
async def test_query_feature_probes_tracks_expected_hole_count_without_explicit_coordinates() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    session_id = "session-query-feature-probes-hole-count-without-coordinates"
    service._session_manager.clear_session(session_id)

    topology_index = _build_centered_plate_countersink_topology(head_radius=6.0)
    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(
                step=1,
                solids=1,
                faces=9,
                edges=8,
                volume=46574.806908831764,
                bbox=[100.0, 60.0, 8.0],
                bbox_min=[-50.0, -30.0, -4.0],
                bbox_max=[50.0, 30.0, 4.0],
                topology_index=topology_index,
            ),
            success=True,
            error=None,
        ),
    )

    requirement_text = (
        "Create a rectangular electronics bracket sized 62mm x 40mm x 14mm with a top pocket, "
        "two mounting holes, a front thumb notch, local edge fillets around the top opening, "
        "and a countersink on the mounting face."
    )
    result = await service.query_feature_probes(
        QueryFeatureProbesInput(
            session_id=session_id,
            requirement_text=requirement_text,
            requirements={"description": requirement_text},
            families=["explicit_anchor_hole"],
        )
    )

    assert result.success is True
    assert len(result.probes) == 1
    probe = result.probes[0]
    assert probe.family == "explicit_anchor_hole"
    assert probe.anchor_summary["expected_local_center_count"] == 2
    assert probe.anchor_summary["realized_local_center_count"] == 4
    assert "missing_expected_local_centers" not in probe.grounding_blockers
    assert "center_count_mismatch" in probe.grounding_blockers


@pytest.mark.asyncio
async def test_validate_requirement_surfaces_count_mismatch_for_holes_without_explicit_coordinates() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    session_id = "session-validate-hole-count-without-coordinates"
    service._session_manager.clear_session(session_id)

    topology_index = _build_centered_plate_countersink_topology(head_radius=6.0)
    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(
                step=1,
                solids=1,
                faces=9,
                edges=8,
                volume=46574.806908831764,
                bbox=[100.0, 60.0, 8.0],
                bbox_min=[-50.0, -30.0, -4.0],
                bbox_max=[50.0, 30.0, 4.0],
                topology_index=topology_index,
            ),
            success=True,
            error=None,
        ),
    )

    requirement_text = (
        "Create a rectangular electronics bracket sized 62mm x 40mm x 14mm with a top pocket, "
        "two mounting holes, a front thumb notch, local edge fillets around the top opening, "
        "and a countersink on the mounting face."
    )
    result = await service.validate_requirement(
        ValidateRequirementInput(
            session_id=session_id,
            requirement_text=requirement_text,
            requirements={"description": requirement_text},
        )
    )

    assert result.success is True
    assert result.is_complete is False
    assert result.insufficient_evidence is False
    assert "two_mounting_holes" in result.blockers
    clause_status = {
        clause.clause_id: clause.status for clause in result.clause_interpretations
    }
    assert clause_status["two_mounting_holes"] == RequirementClauseStatus.CONTRADICTED


@pytest.mark.asyncio
async def test_query_feature_probes_general_geometry_flags_part_count_and_bbox_mismatch() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    session_id = "session-query-feature-probes-general-geometry-mismatch"
    service._session_manager.clear_session(session_id)

    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(
                step=1,
                solids=3,
                faces=56,
                edges=165,
                volume=20222.41657244004,
                bbox=[78.0, 58.5, 35.5],
                bbox_min=[-39.0, -30.5, -1.5],
                bbox_max=[39.0, 28.0, 34.0],
            ),
            success=True,
            error=None,
        ),
    )

    requirement_text = (
        "Create a two-part rounded clamshell storage enclosure with overall dimensions "
        "78mm x 56mm x 32mm. Use a pin hinge at the back, keep wall thickness near 2.4mm, "
        "add four corner magnet recesses on the mating faces, a front thumb notch about "
        "10mm wide, two shallow organic top cavities for small earphone shells, one bottom "
        "cable post, and one side plug pocket."
    )
    result = await service.query_feature_probes(
        QueryFeatureProbesInput(
            session_id=session_id,
            requirement_text=requirement_text,
            requirements={"description": requirement_text},
            families=["general_geometry"],
        )
    )

    assert result.success is True
    assert len(result.probes) == 1
    probe = result.probes[0]
    assert probe.family == "general_geometry"
    assert probe.success is False
    assert "unexpected_part_count_for_requirement" in probe.blockers
    assert "bbox_dimension_mismatch" in probe.blockers
    assert probe.anchor_summary["expected_part_count"] == 2
    assert probe.anchor_summary["expected_bbox"] == [78.0, 56.0, 32.0]
    assert "grounding blocker" in probe.summary


@pytest.mark.asyncio
async def test_query_feature_probes_general_geometry_flags_detached_minor_fragment_even_when_part_count_matches() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    session_id = "session-query-feature-probes-detached-minor-fragment"
    service._session_manager.clear_session(session_id)

    main_bbox = _bbox(-39.0, 39.0, -28.0, 28.0, -16.0, 16.0)
    fragment_bbox = _bbox(18.0, 26.0, 18.0, 26.0, 2.0, 6.0)
    geometry_objects = GeometryObjectIndex(
        solids=[
            _solid(solid_id="S_main", volume=44420.3, bbox=main_bbox),
            _solid(solid_id="S_fragment", volume=201.06, bbox=fragment_bbox),
        ],
        faces=[],
        edges=[],
        solids_total=2,
        faces_total=0,
        edges_total=0,
        max_items_per_type=20,
    )
    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(
                step=1,
                solids=2,
                faces=33,
                edges=71,
                volume=44621.36,
                bbox=[78.0, 56.0, 32.0],
                bbox_min=[-39.0, -28.0, -16.0],
                bbox_max=[39.0, 28.0, 16.0],
                geometry_objects=geometry_objects,
            ),
            success=True,
            error=None,
        ),
    )

    requirement_text = (
        "Create a two-part rounded clamshell storage enclosure with overall dimensions "
        "78mm x 56mm x 32mm. Use a pin hinge at the back, keep wall thickness near 2.4mm, "
        "add four corner magnet recesses on the mating faces, a front thumb notch about "
        "10mm wide, two shallow organic top cavities for small earphone shells, one bottom "
        "cable post, and one side plug pocket."
    )
    result = await service.query_feature_probes(
        QueryFeatureProbesInput(
            session_id=session_id,
            requirement_text=requirement_text,
            requirements={"description": requirement_text},
            families=["general_geometry"],
        )
    )

    assert result.success is True
    probe = result.probes[0]
    assert probe.family == "general_geometry"
    assert probe.success is False
    assert "suspected_detached_feature_fragment" in probe.blockers
    assert probe.anchor_summary["suspected_detached_fragment_count"] == 1
    assert probe.anchor_summary["suspected_detached_fragment_solid_ids"] == ["S_fragment"]
    assert probe.anchor_summary["dominant_solid_volume_fraction"] > 0.95


@pytest.mark.asyncio
async def test_query_feature_probes_detects_half_shell_hinge_signals_for_clamshell_requirements() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    session_id = "session-query-feature-probes-half-shell-hinge"
    service._session_manager.clear_session(session_id)

    hinge_face = _topology_face(
        step=1,
        face_id="F_hinge_barrel",
        center=[0.0, -32.0, 13.0],
        normal=[0.0, -1.0, 0.0],
        geom_type="CYLINDER",
        radius=3.0,
        axis_origin=[0.0, -32.0, 13.0],
        axis_direction=[1.0, 0.0, 0.0],
        bbox=_bbox(-34.0, 34.0, -35.0, -29.0, 10.0, 16.0),
        area=640.0,
        edge_refs=["edge:1:E_hinge_a", "edge:1:E_hinge_b"],
    )
    topology_index = TopologyObjectIndex(
        faces=[hinge_face],
        edges=[],
        faces_total=1,
        edges_total=0,
        max_items_per_type=20,
    )
    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(
                step=1,
                solids=1,
                faces=12,
                edges=24,
                volume=18000.0,
                bbox=[72.0, 64.0, 26.0],
                bbox_min=[-36.0, -32.0, 0.0],
                bbox_max=[36.0, 32.0, 26.0],
                topology_index=topology_index,
            ),
            success=True,
            error=None,
        ),
    )

    requirement_text = (
        "Create a two-part rounded clamshell enclosure with a top lid, bottom base, "
        "pin hinge at the back, and corner magnet slots on the mating faces."
    )
    result = await service.query_feature_probes(
        QueryFeatureProbesInput(
            session_id=session_id,
            requirement_text=requirement_text,
            requirements={"description": requirement_text},
        )
    )

    assert result.success is True
    assert "half_shell" in result.detected_families
    half_shell_probe = next(probe for probe in result.probes if probe.family == "half_shell")
    assert half_shell_probe.success is False
    assert half_shell_probe.signals["hinge_like_cylinder_count"] == 1
    assert half_shell_probe.signals["hinge_like_axis"] == "X"
    assert half_shell_probe.anchor_summary["requires_topology_host_ranking"] is True
    assert half_shell_probe.anchor_summary["hinge_like_face_ids"] == ["F_hinge_barrel"]
    assert "unexpected_part_count_for_requirement" in half_shell_probe.blockers
    assert "query_topology" in half_shell_probe.recommended_next_tools


@pytest.mark.asyncio
async def test_validate_requirement_accepts_face_sketch_coordinate_translation_for_centered_hosts() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    session_id = "session-validate-face-sketch-coordinate-translation"
    service._session_manager.clear_session(session_id)

    plate_top = _topology_face(
        step=1,
        face_id="F_plate_top",
        center=[0.0, 0.0, 4.0],
        normal=[0.0, 0.0, 1.0],
        bbox=_bbox(-50.0, 50.0, -30.0, 30.0, 4.0, 4.0),
        area=6000.0,
    )
    hole_faces: list[TopologyFaceEntity] = []
    for index, (x, y) in enumerate(((-25.0, -15.0), (-25.0, 15.0), (25.0, -15.0), (25.0, 15.0)), start=1):
        hole_faces.append(
            _topology_face(
                step=1,
                face_id=f"F_hole_cone_{index}",
                center=[x + 4.5, y, 2.3],
                normal=[-0.70710678, 0.0, 0.70710678],
                bbox=_bbox(x - 6.0, x + 6.0, y - 6.0, y + 6.0, 0.5, 4.0),
                geom_type="CONE",
                area=120.0,
            )
        )
        hole_faces.append(
            _topology_face(
                step=1,
                face_id=f"F_hole_cyl_{index}",
                center=[x + 3.0, y, -0.5],
                normal=[-1.0, 0.0, 0.0],
                bbox=_bbox(x - 3.0, x + 3.0, y - 3.0, y + 3.0, -4.0, 3.5),
                geom_type="CYLINDER",
                radius=3.0,
                axis_origin=[x, y, -4.0],
                axis_direction=[0.0, 0.0, 1.0],
                area=200.0,
            )
        )

    topology_index = TopologyObjectIndex(
        faces=[plate_top, *hole_faces],
        edges=[],
        faces_total=1 + len(hole_faces),
        edges_total=0,
        max_items_per_type=40,
    )

    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(
                step=1,
                solids=1,
                faces=1 + len(hole_faces),
                edges=32,
                volume=46574.806908831764,
                bbox=[100.0, 60.0, 8.0],
                bbox_min=[-50.0, -30.0, -4.0],
                bbox_max=[50.0, 30.0, 4.0],
                topology_index=topology_index,
            ),
            success=True,
            error=None,
        ),
    )

    requirement_text = (
        "Create a 100x60x8 mm plate. On the top face, place four countersunk through holes at "
        "sketch coordinates (25,15), (25,45), (75,15), and (75,45). Treat those coordinates as "
        "face-sketch coordinates, not already-centered offsets."
    )
    result = await service.validate_requirement(
        ValidateRequirementInput(
            session_id=session_id,
            requirement_text=requirement_text,
            requirements={"description": requirement_text},
        )
    )

    assert result.success is True
    assert result.is_complete is True
    assert result.insufficient_evidence is False
    assert "feature_countersink" not in result.blockers
    assert "feature_hole_position_alignment" not in result.blockers
    assert "feature_hole_exact_center_set" not in result.blockers
    assert "feature_local_anchor_alignment" not in result.blockers


@pytest.mark.asyncio
async def test_validate_requirement_accepts_hole_history_with_countersink_radius_alias() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    session_id = "session-validate-hole-history-countersink-radius-alias"
    service._session_manager.clear_session(session_id)

    topology_index = _build_centered_plate_countersink_topology(head_radius=4.5)
    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.HOLE,
            action_params={
                "diameter": 6.0,
                "depth": 8.0,
                "face_ref": "face:1:F_plate_top",
                "centers": [[-25.0, -15.0], [-25.0, 15.0], [25.0, -15.0], [25.0, 15.0]],
                "countersink_radius": 4.5,
                "countersink_angle": 90.0,
            },
            result_snapshot=_snapshot(
                step=1,
                solids=1,
                faces=9,
                edges=8,
                volume=46996.261147178,
                bbox=[100.0, 60.0, 8.0],
                bbox_min=[-50.0, -30.0, -4.0],
                bbox_max=[50.0, 30.0, 4.0],
                topology_index=topology_index,
            ),
            success=True,
            error=None,
        ),
    )

    requirement_text = "Create a 100x60x8 mm plate with four countersunk through holes on the top face."
    result = await service.validate_requirement(
        ValidateRequirementInput(
            session_id=session_id,
            requirement_text=requirement_text,
            requirements={"description": requirement_text},
        )
    )

    assert result.success is True
    assert "feature_hole" not in result.blockers
    assert "feature_countersink" not in result.blockers


@pytest.mark.asyncio
async def test_validate_requirement_uses_hole_clause_face_targets_for_local_anchor_count_on_mixed_face_requirement() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    session_id = "session-validate-hole-clause-face-targets"
    service._session_manager.clear_session(session_id)

    top_face = _topology_face(
        step=1,
        face_id="F_top",
        center=[0.0, 0.0, 8.0],
        normal=[0.0, 0.0, 1.0],
        bbox=_bbox(-33.0, 33.0, -21.0, 21.0, 8.0, 8.0),
        area=2772.0,
    )
    bottom_face = _topology_face(
        step=1,
        face_id="F_bottom",
        center=[0.0, 0.0, -8.0],
        normal=[0.0, 0.0, -1.0],
        bbox=_bbox(-33.0, 33.0, -21.0, 21.0, -8.0, -8.0),
        area=2772.0,
    )
    front_face = _topology_face(
        step=1,
        face_id="F_front",
        center=[0.0, 21.0, 0.0],
        normal=[0.0, 1.0, 0.0],
        bbox=_bbox(-33.0, 33.0, 21.0, 21.0, -8.0, 8.0),
        area=1056.0,
    )

    hole_faces: list[TopologyFaceEntity] = []
    for index, x in enumerate((-20.0, 20.0), start=1):
        hole_faces.append(
            _topology_face(
                step=1,
                face_id=f"F_bottom_hole_{index}",
                center=[x, 0.0, 0.0],
                normal=[1.0, 0.0, 0.0],
                bbox=_bbox(x - 3.0, x + 3.0, -3.0, 3.0, -8.0, 8.0),
                geom_type="CYLINDER",
                radius=3.0,
                axis_origin=[x, 0.0, -8.0],
                axis_direction=[0.0, 0.0, 1.0],
                area=300.0,
            )
        )

    topology_index = TopologyObjectIndex(
        faces=[top_face, bottom_face, front_face, *hole_faces],
        edges=[],
        faces_total=3 + len(hole_faces),
        edges_total=0,
        max_items_per_type=32,
    )

    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(
                step=1,
                solids=1,
                faces=3 + len(hole_faces),
                edges=36,
                volume=36000.0,
                bbox=[66.0, 42.0, 16.0],
                bbox_min=[-33.0, -21.0, -8.0],
                bbox_max=[33.0, 21.0, 8.0],
                topology_index=topology_index,
            ),
            success=True,
            error=None,
        ),
    )

    requirement_text = (
        "Create a rectangular service bracket with two mounting holes on the bottom face. "
        "Add a centered rounded rectangle recess on the front face."
    )
    result = await service.validate_requirement(
        ValidateRequirementInput(
            session_id=session_id,
            requirement_text=requirement_text,
            requirements={"description": requirement_text},
        )
    )

    assert result.success is True
    check = next(
        item
        for item in result.checks
        if item.check_id == "feature_local_anchor_count_alignment"
    )
    assert check.status == RequirementCheckStatus.PASS
    assert "required_center_count=2" in str(check.evidence)
    assert "realized_center_count=2" in str(check.evidence)


@pytest.mark.asyncio
async def test_validate_requirement_prefers_hole_count_over_centered_front_recess_count_for_explicit_anchor_hole() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    session_id = "session-validate-hole-count-over-front-recess-count"
    service._session_manager.clear_session(session_id)

    top_face = _topology_face(
        step=1,
        face_id="F_top",
        center=[0.0, 0.0, 8.0],
        normal=[0.0, 0.0, 1.0],
        bbox=_bbox(-33.0, 33.0, -21.0, 21.0, 8.0, 8.0),
        area=2772.0,
    )
    bottom_face = _topology_face(
        step=1,
        face_id="F_bottom",
        center=[0.0, 0.0, -8.0],
        normal=[0.0, 0.0, -1.0],
        bbox=_bbox(-33.0, 33.0, -21.0, 21.0, -8.0, -8.0),
        area=2772.0,
    )
    front_face = _topology_face(
        step=1,
        face_id="F_front",
        center=[0.0, 21.0, 0.0],
        normal=[0.0, 1.0, 0.0],
        bbox=_bbox(-33.0, 33.0, 21.0, 21.0, -8.0, 8.0),
        area=1056.0,
    )

    hole_faces: list[TopologyFaceEntity] = []
    for index, x in enumerate((-25.0, 25.0), start=1):
        hole_faces.append(
            _topology_face(
                step=1,
                face_id=f"F_bottom_hole_{index}",
                center=[x, 0.0, 0.0],
                normal=[1.0, 0.0, 0.0],
                bbox=_bbox(x - 3.0, x + 3.0, -3.0, 3.0, -8.0, 8.0),
                geom_type="CYLINDER",
                radius=3.0,
                axis_origin=[x, 0.0, -8.0],
                axis_direction=[0.0, 0.0, 1.0],
                area=300.0,
            )
        )

    topology_index = TopologyObjectIndex(
        faces=[top_face, bottom_face, front_face, *hole_faces],
        edges=[],
        faces_total=3 + len(hole_faces),
        edges_total=0,
        max_items_per_type=32,
    )

    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(
                step=1,
                solids=1,
                faces=3 + len(hole_faces),
                edges=36,
                volume=33323.28356347061,
                bbox=[66.0, 42.0, 16.0],
                bbox_min=[-33.0, -21.0, -8.0],
                bbox_max=[33.0, 21.0, 8.0],
                topology_index=topology_index,
            ),
            success=True,
            error=None,
        ),
    )

    requirement_text = (
        "Create a rectangular service bracket sized 66mm x 42mm x 16mm with a shallow top pocket "
        "and two mounting holes on the bottom face. Add a centered rounded-rectangle recess on the "
        "front face sized about 12mm x 6mm and 2mm deep, plus small fillets around the top opening "
        "and countersinks on the mounting holes, so that a topology-aware local finishing pass on "
        "the front face is useful."
    )
    result = await service.validate_requirement(
        ValidateRequirementInput(
            session_id=session_id,
            requirement_text=requirement_text,
            requirements={"description": requirement_text},
        )
    )

    assert result.success is True
    check = next(
        item
        for item in result.checks
        if item.check_id == "feature_local_anchor_count_alignment"
    )
    assert "required_center_count=2" in str(check.evidence)
    assert "realized_center_count=2" in str(check.evidence)


@pytest.mark.asyncio
async def test_validate_requirement_accepts_centered_stud_array_without_clause_gaps() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    session_id = "session-validate-centered-stud-array"
    service._session_manager.clear_session(session_id)

    base_top = _topology_face(
        step=1,
        face_id="F_base_top",
        center=[0.0, 0.0, 10.0],
        normal=[0.0, 0.0, 1.0],
        bbox=_bbox(-8.0, 8.0, -8.0, 8.0, 10.0, 10.0),
        area=256.0,
    )
    base_top.edge_refs = ["E_base_top_1", "E_base_top_2", "E_base_top_3", "E_base_top_4"]
    base_bottom = _topology_face(
        step=1,
        face_id="F_base_bottom",
        center=[0.0, 0.0, 0.0],
        normal=[0.0, 0.0, -1.0],
        bbox=_bbox(-8.0, 8.0, -8.0, 8.0, 0.0, 0.0),
        area=256.0,
    )
    base_bottom.edge_refs = [
        "E_base_bottom_1",
        "E_base_bottom_2",
        "E_base_bottom_3",
        "E_base_bottom_4",
    ]
    base_side_x_pos = _topology_face(
        step=1,
        face_id="F_base_side_x_pos",
        center=[8.0, 0.0, 5.0],
        normal=[1.0, 0.0, 0.0],
        bbox=_bbox(8.0, 8.0, -8.0, 8.0, 0.0, 10.0),
        area=160.0,
    )
    base_side_x_neg = _topology_face(
        step=1,
        face_id="F_base_side_x_neg",
        center=[-8.0, 0.0, 5.0],
        normal=[-1.0, 0.0, 0.0],
        bbox=_bbox(-8.0, -8.0, -8.0, 8.0, 0.0, 10.0),
        area=160.0,
    )
    base_side_y_pos = _topology_face(
        step=1,
        face_id="F_base_side_y_pos",
        center=[0.0, 8.0, 5.0],
        normal=[0.0, 1.0, 0.0],
        bbox=_bbox(-8.0, 8.0, 8.0, 8.0, 0.0, 10.0),
        area=160.0,
    )
    base_side_y_neg = _topology_face(
        step=1,
        face_id="F_base_side_y_neg",
        center=[0.0, -8.0, 5.0],
        normal=[0.0, -1.0, 0.0],
        bbox=_bbox(-8.0, 8.0, -8.0, -8.0, 0.0, 10.0),
        area=160.0,
    )

    stud_faces: list[TopologyFaceEntity] = []
    for index, (x, y) in enumerate(((4.0, 4.0), (4.0, -4.0), (-4.0, 4.0), (-4.0, -4.0)), start=1):
        stud_faces.append(
            _topology_face(
                step=1,
                face_id=f"F_stud_top_{index}",
                center=[x, y, 12.0],
                normal=[0.0, 0.0, 1.0],
                bbox=_bbox(x - 2.5, x + 2.5, y - 2.5, y + 2.5, 12.0, 12.0),
                area=19.635,
            )
        )
        stud_faces.append(
            _topology_face(
                step=1,
                face_id=f"F_stud_cyl_{index}",
                center=[x + 2.5, y, 11.0],
                normal=[1.0, 0.0, 0.0],
                bbox=_bbox(x - 2.5, x + 2.5, y - 2.5, y + 2.5, 10.0, 12.0),
                geom_type="CYLINDER",
                radius=2.5,
                axis_origin=[x, y, 10.0],
                axis_direction=[0.0, 0.0, 1.0],
                area=31.416,
            )
        )

    topology_index = TopologyObjectIndex(
        faces=[
            base_top,
            base_bottom,
            base_side_x_pos,
            base_side_x_neg,
            base_side_y_pos,
            base_side_y_neg,
            *stud_faces,
        ],
        edges=[],
        faces_total=6 + len(stud_faces),
        edges_total=0,
        max_items_per_type=40,
    )

    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(
                step=1,
                solids=1,
                faces=6 + len(stud_faces),
                edges=24,
                volume=2717.0796326794934,
                bbox=[16.0, 16.0, 12.0],
                bbox_min=[-8.0, -8.0, 0.0],
                bbox_max=[8.0, 8.0, 12.0],
                topology_index=topology_index,
            ),
            success=True,
            error=None,
        ),
    )

    requirement_text = (
        "First, draw a 16x16mm square in the XY plane and extrude it 10.0mm to create the base. "
        "Select the top surface of the base as the sketch plane, and draw four circles with a diameter of 5.0mm. "
        "Constrain the centers of these four circles to form a square array with a side length of 8.0mm, and ensure that "
        "the center of this array coincides with the center of the base (i.e., each circle's center is 4mm from the center "
        "in the X/Y direction). Select the profiles of these four circles and extrude them upward by 2.0mm to create the "
        "stud features on the top."
    )
    result = await service.validate_requirement(
        ValidateRequirementInput(
            session_id=session_id,
            requirement_text=requirement_text,
            requirements={"description": requirement_text},
        )
    )

    assert result.success is True
    assert result.is_complete is True
    assert result.insufficient_evidence is False
    clause_status = {
        clause.clause_id: clause.status for clause in result.clause_interpretations
    }
    assert clause_status["first"] == RequirementClauseStatus.NOT_APPLICABLE
    assert (
        clause_status["extrude_it_10_0mm_to_create_the_base"]
        == RequirementClauseStatus.VERIFIED
    )
    assert (
        clause_status["draw_four_circles_with_a_diameter_of_5_0mm"]
        == RequirementClauseStatus.VERIFIED
    )
