from sandbox_mcp_server.registry import (
    analyze_requirement_semantics,
    infer_requirement_probe_families,
)


def test_analyze_requirement_semantics_detects_hyphenated_face_targets() -> None:
    semantics = analyze_requirement_semantics(
        {},
        "Create a shelled block with a shallow top-face recess and a reference hole pattern.",
    )

    assert semantics.face_targets == ("top",)
    assert semantics.mentions_face_edit is True


def test_infer_requirement_probe_families_marks_full_span_top_face_channel_as_nested_hollow_section() -> None:
    requirement = (
        "Select the XY plane and create a box-shaped base 80.0 millimeters long, "
        "50.0 millimeters wide, and 40.0 millimeters high, with the bottom on Z=0. "
        "Select the top face and cut a centered rectangular slot that spans the full "
        "80.0 millimeter length, is 30.0 millimeters wide in Y, and is 25.0 "
        "millimeters deep, leaving a U-shaped channel section."
    )

    families = infer_requirement_probe_families(
        {"description": requirement},
        requirement,
    )

    assert "nested_hollow_section" in families
