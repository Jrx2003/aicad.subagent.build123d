from sub_agent_runtime.prompting import failures, requirements


def test_requirement_detectors_live_in_requirements_module() -> None:
    assert (
        requirements._detect_positive_extrude_plane.__module__
        == requirements.__name__
    )
    assert (
        requirements._requirement_mentions_explicit_path_sweep.__module__
        == requirements.__name__
    )
    assert (
        requirements._requirement_prefers_named_face_local_feature_sequence.__module__
        == requirements.__name__
    )
    assert (
        requirements._requirement_mentions_enclosure_host.__module__
        == requirements.__name__
    )
    assert (
        requirements._requirement_mentions_multi_part_assembled_envelope.__module__
        == requirements.__name__
    )
    assert (
        requirements._requirement_mentions_half_shell_with_split_surface.__module__
        == requirements.__name__
    )
    assert (
        requirements._requirement_explicitly_requests_detached_hinge_hardware.__module__
        == requirements.__name__
    )


def test_failure_summary_helpers_live_in_failures_module() -> None:
    assert failures.classify_write_failure.__module__ == failures.__name__
    assert failures.build_previous_tool_failure_summary.__module__ == failures.__name__
    assert failures.summarize_failure_lint_hits.__module__ == failures.__name__
    assert failures.summarize_failure_repair_recipe.__module__ == failures.__name__
