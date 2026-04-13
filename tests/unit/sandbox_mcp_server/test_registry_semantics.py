from sandbox_mcp_server.registry import analyze_requirement_semantics


def test_analyze_requirement_semantics_detects_hyphenated_face_targets() -> None:
    semantics = analyze_requirement_semantics(
        {},
        "Create a shelled block with a shallow top-face recess and a reference hole pattern.",
    )

    assert semantics.face_targets == ("top",)
    assert semantics.mentions_face_edit is True
