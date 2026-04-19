from sandbox_mcp_server.contracts import BoundingBox3D, TopologyEdgeEntity, TopologyFaceEntity
from sandbox_mcp_server.registry import collect_requirement_topology_hints
from sandbox_mcp_server.service import SandboxMCPService


class _DummyRunner:
    async def execute(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("not used in this test")


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


def test_collect_requirement_topology_hints_adds_host_role_hints_for_enclosure_notch_requirements() -> None:
    hints = collect_requirement_topology_hints(
        {
            "description": (
                "Create a two-part clamshell enclosure with lid and base, use mating surfaces, "
                "and add a front thumb notch."
            )
        }
    )

    assert "shell_exterior_faces" in hints
    assert "shell_interior_faces" in hints
    assert "mating_faces" in hints
    assert "opening_rim_edges" in hints
    assert "split_plane_faces" in hints


def test_build_requirement_topology_candidate_sets_emits_host_role_metadata() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    faces = [
        TopologyFaceEntity(
            face_ref="face:top_outer",
            face_id="F_TOP_OUTER",
            step=1,
            area=4800.0,
            center=[0.0, 0.0, 20.0],
            normal=[0.0, 0.0, 1.0],
            geom_type="PLANE",
            bbox=_bbox(xlen=80.0, ylen=60.0, zlen=0.5, xmin=-40.0, xmax=40.0, ymin=-30.0, ymax=30.0, zmin=19.5, zmax=20.0),
            edge_refs=["edge:rim_front", "edge:rim_back"],
            adjacent_face_refs=["face:side_front"],
        ),
        TopologyFaceEntity(
            face_ref="face:top_inner",
            face_id="F_TOP_INNER",
            step=1,
            area=3600.0,
            center=[0.0, 0.0, 16.0],
            normal=[0.0, 0.0, 1.0],
            geom_type="PLANE",
            bbox=_bbox(xlen=70.0, ylen=50.0, zlen=0.5, xmin=-35.0, xmax=35.0, ymin=-25.0, ymax=25.0, zmin=15.5, zmax=16.0),
            edge_refs=["edge:opening_1", "edge:opening_2"],
            adjacent_face_refs=["face:side_front"],
        ),
        TopologyFaceEntity(
            face_ref="face:split_mid",
            face_id="F_SPLIT",
            step=1,
            area=2500.0,
            center=[0.0, 0.0, 10.0],
            normal=[0.0, 0.0, 1.0],
            geom_type="PLANE",
            bbox=_bbox(xlen=50.0, ylen=50.0, zlen=0.5, xmin=-25.0, xmax=25.0, ymin=-25.0, ymax=25.0, zmin=9.75, zmax=10.25),
            edge_refs=["edge:opening_1", "edge:opening_2"],
            adjacent_face_refs=["face:top_inner"],
        ),
        TopologyFaceEntity(
            face_ref="face:side_front",
            face_id="F_FRONT",
            step=1,
            area=1200.0,
            center=[0.0, 30.0, 10.0],
            normal=[0.0, 1.0, 0.0],
            geom_type="PLANE",
            bbox=_bbox(xlen=80.0, ylen=0.5, zlen=20.0, xmin=-40.0, xmax=40.0, ymin=29.5, ymax=30.0, zmin=0.0, zmax=20.0),
            edge_refs=["edge:rim_front"],
            adjacent_face_refs=["face:top_outer", "face:top_inner"],
        ),
    ]
    edges = [
        TopologyEdgeEntity(
            edge_ref="edge:opening_1",
            edge_id="E_OPENING_1",
            step=1,
            length=24.0,
            geom_type="LINE",
            center=[0.0, 10.0, 16.0],
            bbox=_bbox(xlen=20.0, ylen=0.5, zlen=0.5, xmin=-10.0, xmax=10.0, ymin=9.75, ymax=10.25, zmin=15.75, zmax=16.25),
            adjacent_face_refs=["face:top_inner", "face:split_mid"],
        ),
        TopologyEdgeEntity(
            edge_ref="edge:opening_2",
            edge_id="E_OPENING_2",
            step=1,
            length=24.0,
            geom_type="LINE",
            center=[0.0, -10.0, 16.0],
            bbox=_bbox(xlen=20.0, ylen=0.5, zlen=0.5, xmin=-10.0, xmax=10.0, ymin=-10.25, ymax=-9.75, zmin=15.75, zmax=16.25),
            adjacent_face_refs=["face:top_inner", "face:split_mid"],
        ),
        TopologyEdgeEntity(
            edge_ref="edge:rim_front",
            edge_id="E_RIM_FRONT",
            step=1,
            length=80.0,
            geom_type="LINE",
            center=[0.0, 30.0, 20.0],
            bbox=_bbox(xlen=80.0, ylen=0.5, zlen=0.5, xmin=-40.0, xmax=40.0, ymin=29.75, ymax=30.25, zmin=19.75, zmax=20.25),
            adjacent_face_refs=["face:top_outer", "face:side_front"],
        ),
        TopologyEdgeEntity(
            edge_ref="edge:rim_back",
            edge_id="E_RIM_BACK",
            step=1,
            length=80.0,
            geom_type="LINE",
            center=[0.0, -30.0, 20.0],
            bbox=_bbox(xlen=80.0, ylen=0.5, zlen=0.5, xmin=-40.0, xmax=40.0, ymin=-30.25, ymax=-29.75, zmin=19.75, zmax=20.25),
            adjacent_face_refs=["face:top_outer"],
        ),
    ]

    candidate_sets = service._build_requirement_topology_candidate_sets(
        faces=faces,
        edges=edges,
        selection_hints=[
            "shell_exterior_faces",
            "shell_interior_faces",
            "mating_faces",
            "split_plane_faces",
            "opening_rim_edges",
        ],
        family_ids=[],
    )
    by_id = {item.candidate_id: item for item in candidate_sets}

    assert by_id["shell_exterior_faces"].metadata["host_role"] == "shell_exterior"
    assert by_id["shell_interior_faces"].metadata["host_role"] == "shell_interior"
    assert by_id["mating_faces"].metadata["host_role"] == "mating_face"
    assert by_id["split_plane_faces"].metadata["host_role"] == "split_plane"
    assert by_id["opening_rim_edges"].metadata["host_role"] == "opening_rim"
    assert "face:top_inner" in by_id["mating_faces"].ref_ids
    assert "edge:opening_1" in by_id["opening_rim_edges"].ref_ids


def test_normalize_topology_selection_hints_maps_generic_face_aliases_to_candidate_sets() -> None:
    service = SandboxMCPService(runner=_DummyRunner())

    hints = service._normalize_topology_selection_hints(
        selection_hints=["bottom", "front", "top_faces"],
        requirement_text=None,
    )

    assert hints[:3] == ["bottom_faces", "front_faces", "top_faces"]


def test_normalize_topology_selection_hints_expands_mounting_and_planar_aliases() -> None:
    service = SandboxMCPService(runner=_DummyRunner())

    hints = service._normalize_topology_selection_hints(
        selection_hints=["bottom", "mounting", "planar", "rim"],
        requirement_text=None,
    )

    assert "bottom_faces" in hints
    assert "mating_faces" in hints
    assert "upward_planar_faces" in hints
    assert "downward_planar_faces" in hints
    assert "opening_rim_edges" in hints


def test_build_requirement_topology_candidate_sets_prefers_directional_host_over_mating_when_directional_hint_is_explicit() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    faces = [
        TopologyFaceEntity(
            face_ref="face:top_outer",
            face_id="F_TOP_OUTER",
            step=1,
            area=4800.0,
            center=[0.0, 0.0, 20.0],
            normal=[0.0, 0.0, 1.0],
            geom_type="PLANE",
            bbox=_bbox(xlen=80.0, ylen=60.0, zlen=0.5, xmin=-40.0, xmax=40.0, ymin=-30.0, ymax=30.0, zmin=19.5, zmax=20.0),
            edge_refs=["edge:rim_front", "edge:rim_back"],
            adjacent_face_refs=["face:side_front"],
        ),
        TopologyFaceEntity(
            face_ref="face:top_inner",
            face_id="F_TOP_INNER",
            step=1,
            area=3600.0,
            center=[0.0, 0.0, 16.0],
            normal=[0.0, 0.0, 1.0],
            geom_type="PLANE",
            bbox=_bbox(xlen=70.0, ylen=50.0, zlen=0.5, xmin=-35.0, xmax=35.0, ymin=-25.0, ymax=25.0, zmin=15.5, zmax=16.0),
            edge_refs=["edge:opening_1", "edge:opening_2"],
            adjacent_face_refs=["face:side_front"],
        ),
        TopologyFaceEntity(
            face_ref="face:side_front",
            face_id="F_FRONT",
            step=1,
            area=1200.0,
            center=[0.0, 30.0, 10.0],
            normal=[0.0, 1.0, 0.0],
            geom_type="PLANE",
            bbox=_bbox(xlen=80.0, ylen=0.5, zlen=20.0, xmin=-40.0, xmax=40.0, ymin=29.5, ymax=30.0, zmin=0.0, zmax=20.0),
            edge_refs=["edge:rim_front"],
            adjacent_face_refs=["face:top_outer", "face:top_inner"],
        ),
    ]
    edges = [
        TopologyEdgeEntity(
            edge_ref="edge:opening_1",
            edge_id="E_OPENING_1",
            step=1,
            length=24.0,
            geom_type="LINE",
            center=[0.0, 10.0, 16.0],
            bbox=_bbox(xlen=20.0, ylen=0.5, zlen=0.5, xmin=-10.0, xmax=10.0, ymin=9.75, ymax=10.25, zmin=15.75, zmax=16.25),
            adjacent_face_refs=["face:top_inner"],
        ),
    ]

    candidate_sets = service._build_requirement_topology_candidate_sets(
        faces=faces,
        edges=edges,
        selection_hints=["top_faces", "mating_faces", "opening_rim_edges"],
        family_ids=["explicit_anchor_hole"],
    )

    assert candidate_sets[0].candidate_id == "top_faces"
    assert candidate_sets[0].family_id == "explicit_anchor_hole"
    assert candidate_sets[0].family_ids == ["explicit_anchor_hole"]
    assert candidate_sets[0].preferred_ref_id == "face:top_outer"
    assert candidate_sets[0].preferred_entity_id == "F_TOP_OUTER"


def test_build_requirement_topology_candidate_sets_keeps_mating_priority_without_directional_hint() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    faces = [
        TopologyFaceEntity(
            face_ref="face:top_outer",
            face_id="F_TOP_OUTER",
            step=1,
            area=4800.0,
            center=[0.0, 0.0, 20.0],
            normal=[0.0, 0.0, 1.0],
            geom_type="PLANE",
            bbox=_bbox(xlen=80.0, ylen=60.0, zlen=0.5, xmin=-40.0, xmax=40.0, ymin=-30.0, ymax=30.0, zmin=19.5, zmax=20.0),
            edge_refs=["edge:rim_front", "edge:rim_back"],
            adjacent_face_refs=["face:side_front"],
        ),
        TopologyFaceEntity(
            face_ref="face:top_inner",
            face_id="F_TOP_INNER",
            step=1,
            area=3600.0,
            center=[0.0, 0.0, 16.0],
            normal=[0.0, 0.0, 1.0],
            geom_type="PLANE",
            bbox=_bbox(xlen=70.0, ylen=50.0, zlen=0.5, xmin=-35.0, xmax=35.0, ymin=-25.0, ymax=25.0, zmin=15.5, zmax=16.0),
            edge_refs=["edge:opening_1", "edge:opening_2"],
            adjacent_face_refs=["face:side_front"],
        ),
        TopologyFaceEntity(
            face_ref="face:side_front",
            face_id="F_FRONT",
            step=1,
            area=1200.0,
            center=[0.0, 30.0, 10.0],
            normal=[0.0, 1.0, 0.0],
            geom_type="PLANE",
            bbox=_bbox(xlen=80.0, ylen=0.5, zlen=20.0, xmin=-40.0, xmax=40.0, ymin=29.5, ymax=30.0, zmin=0.0, zmax=20.0),
            edge_refs=["edge:rim_front"],
            adjacent_face_refs=["face:top_outer", "face:top_inner"],
        ),
    ]
    edges = [
        TopologyEdgeEntity(
            edge_ref="edge:opening_1",
            edge_id="E_OPENING_1",
            step=1,
            length=24.0,
            geom_type="LINE",
            center=[0.0, 10.0, 16.0],
            bbox=_bbox(xlen=20.0, ylen=0.5, zlen=0.5, xmin=-10.0, xmax=10.0, ymin=9.75, ymax=10.25, zmin=15.75, zmax=16.25),
            adjacent_face_refs=["face:top_inner"],
        ),
    ]

    candidate_sets = service._build_requirement_topology_candidate_sets(
        faces=faces,
        edges=edges,
        selection_hints=["mating_faces", "opening_rim_edges"],
        family_ids=["explicit_anchor_hole"],
    )

    assert candidate_sets[0].candidate_id == "mating_faces"
    assert candidate_sets[0].preferred_ref_id == "face:top_inner"
    assert candidate_sets[0].preferred_entity_id == "F_TOP_INNER"


def test_build_requirement_topology_candidate_sets_prefers_bottom_host_when_directional_hint_conflicts_with_mating_face() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    faces = [
        TopologyFaceEntity(
            face_ref="face:bottom_outer",
            face_id="F_BOTTOM_OUTER",
            step=1,
            area=4800.0,
            center=[0.0, 0.0, 0.0],
            normal=[0.0, 0.0, -1.0],
            geom_type="PLANE",
            bbox=_bbox(
                xlen=80.0,
                ylen=60.0,
                zlen=0.5,
                xmin=-40.0,
                xmax=40.0,
                ymin=-30.0,
                ymax=30.0,
                zmin=-0.5,
                zmax=0.0,
            ),
            edge_refs=["edge:bottom_rim_front", "edge:bottom_rim_back"],
            adjacent_face_refs=["face:side_front"],
        ),
        TopologyFaceEntity(
            face_ref="face:top_inner",
            face_id="F_TOP_INNER",
            step=1,
            area=3600.0,
            center=[0.0, 0.0, 16.0],
            normal=[0.0, 0.0, 1.0],
            geom_type="PLANE",
            bbox=_bbox(
                xlen=70.0,
                ylen=50.0,
                zlen=0.5,
                xmin=-35.0,
                xmax=35.0,
                ymin=-25.0,
                ymax=25.0,
                zmin=15.5,
                zmax=16.0,
            ),
            edge_refs=["edge:opening_1", "edge:opening_2"],
            adjacent_face_refs=["face:side_front"],
        ),
        TopologyFaceEntity(
            face_ref="face:side_front",
            face_id="F_FRONT",
            step=1,
            area=1200.0,
            center=[0.0, 30.0, 8.0],
            normal=[0.0, 1.0, 0.0],
            geom_type="PLANE",
            bbox=_bbox(
                xlen=80.0,
                ylen=0.5,
                zlen=16.0,
                xmin=-40.0,
                xmax=40.0,
                ymin=29.5,
                ymax=30.0,
                zmin=0.0,
                zmax=16.0,
            ),
            edge_refs=["edge:opening_1"],
            adjacent_face_refs=["face:bottom_outer", "face:top_inner"],
        ),
    ]
    edges = [
        TopologyEdgeEntity(
            edge_ref="edge:opening_1",
            edge_id="E_OPENING_1",
            step=1,
            length=24.0,
            geom_type="LINE",
            center=[0.0, 10.0, 16.0],
            bbox=_bbox(
                xlen=20.0,
                ylen=0.5,
                zlen=0.5,
                xmin=-10.0,
                xmax=10.0,
                ymin=9.75,
                ymax=10.25,
                zmin=15.75,
                zmax=16.25,
            ),
            adjacent_face_refs=["face:top_inner", "face:side_front"],
        ),
    ]

    candidate_sets = service._build_requirement_topology_candidate_sets(
        faces=faces,
        edges=edges,
        selection_hints=["bottom_faces", "primary_outer_faces", "mating_faces"],
        family_ids=["explicit_anchor_hole"],
    )

    assert candidate_sets[0].candidate_id == "bottom_faces"
    assert candidate_sets[0].preferred_ref_id == "face:bottom_outer"
    assert candidate_sets[0].preferred_entity_id == "F_BOTTOM_OUTER"


def test_build_requirement_topology_candidate_sets_prefers_planar_directional_face_for_local_edit() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    faces = [
        TopologyFaceEntity(
            face_ref="face:front_cyl",
            face_id="F_FRONT_CYL",
            step=1,
            area=2600.0,
            center=[0.0, 30.0, 10.0],
            normal=None,
            geom_type="CYLINDER",
            bbox=_bbox(
                xlen=26.0,
                ylen=1.0,
                zlen=20.0,
                xmin=-13.0,
                xmax=13.0,
                ymin=29.0,
                ymax=30.0,
                zmin=0.0,
                zmax=20.0,
            ),
            edge_refs=["edge:front_cyl_top"],
            adjacent_face_refs=["face:top_outer"],
        ),
        TopologyFaceEntity(
            face_ref="face:front_plane",
            face_id="F_FRONT_PLANE",
            step=1,
            area=1200.0,
            center=[0.0, 30.0, 10.0],
            normal=[0.0, 1.0, 0.0],
            geom_type="PLANE",
            bbox=_bbox(
                xlen=60.0,
                ylen=0.5,
                zlen=20.0,
                xmin=-30.0,
                xmax=30.0,
                ymin=29.5,
                ymax=30.0,
                zmin=0.0,
                zmax=20.0,
            ),
            edge_refs=["edge:front_plane_top"],
            adjacent_face_refs=["face:top_outer"],
        ),
        TopologyFaceEntity(
            face_ref="face:top_outer",
            face_id="F_TOP_OUTER",
            step=1,
            area=4200.0,
            center=[0.0, 0.0, 20.0],
            normal=[0.0, 0.0, 1.0],
            geom_type="PLANE",
            bbox=_bbox(
                xlen=80.0,
                ylen=60.0,
                zlen=0.5,
                xmin=-40.0,
                xmax=40.0,
                ymin=-30.0,
                ymax=30.0,
                zmin=19.5,
                zmax=20.0,
            ),
            edge_refs=["edge:rim_front"],
            adjacent_face_refs=["face:front_plane", "face:front_cyl"],
        ),
    ]

    candidate_sets = service._build_requirement_topology_candidate_sets(
        faces=faces,
        edges=[],
        selection_hints=["front_faces"],
        family_ids=["named_face_local_edit"],
    )

    front_faces = next(item for item in candidate_sets if item.candidate_id == "front_faces")

    assert front_faces.ref_ids[0] == "face:front_plane"
    assert front_faces.preferred_ref_id == "face:front_plane"
    assert front_faces.preferred_entity_id == "F_FRONT_PLANE"
