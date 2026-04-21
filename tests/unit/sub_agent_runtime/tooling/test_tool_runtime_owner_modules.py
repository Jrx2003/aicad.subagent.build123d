from sub_agent_runtime.tooling.execution import cancellation, dispatch, writes
from sub_agent_runtime.tooling.lint import (
    ast_utils,
    routing,
)
from sub_agent_runtime.tooling.lint.families import (
    builders,
    countersinks,
    keywords,
    path_profiles,
    planes,
    structural,
)


def test_execution_helpers_live_in_specialized_execution_modules() -> None:
    assert dispatch._gather_results.__module__ == dispatch.__name__
    assert (
        cancellation._clear_current_task_cancellation_state.__module__
        == cancellation.__name__
    )
    assert writes._normalize_multi_write_batch.__module__ == writes.__name__
    assert writes._strip_runtime_managed_fields.__module__ == writes.__name__


def test_lint_routing_helpers_live_in_lint_routing_module() -> None:
    assert routing._candidate_lint_family_ids.__module__ == routing.__name__
    assert (
        routing._requirement_mentions_explicit_hole_anchors.__module__
        == routing.__name__
    )
    assert (
        routing._requirement_mentions_local_finish_fillet_tail.__module__
        == routing.__name__
    )


def test_lint_family_detectors_live_in_family_owner_modules() -> None:
    assert (
        planes._find_plane_located_call_hits.__module__
        == planes.__name__
    )
    assert (
        planes._buildsketch_candidate_is_host_profile.__module__
        == planes.__name__
    )
    assert (
        planes._collect_named_plane_aliases.__module__
        == planes.__name__
    )
    assert (
        planes._named_face_requirement_plane_groups.__module__
        == planes.__name__
    )
    assert (
        planes._find_named_face_plane_family_mismatch_hits.__module__
        == planes.__name__
    )
    assert (
        builders._find_nested_buildpart_part_arithmetic_hits.__module__
        == builders.__name__
    )
    assert (
        builders._find_builder_method_reference_assignment_hits.__module__
        == builders.__name__
    )
    assert (
        builders._find_broad_local_finish_tail_fillet_hits.__module__
        == builders.__name__
    )
    assert (
        builders._find_buildpart_topology_access_inside_buildsketch_hits.__module__
        == builders.__name__
    )
    assert (
        countersinks._find_buildsketch_countersink_context_hits.__module__
        == countersinks.__name__
    )
    assert (
        countersinks._find_countersink_keyword_alias_hits.__module__
        == countersinks.__name__
    )
    assert (
        keywords._find_cone_keyword_alias_hits.__module__
        == keywords.__name__
    )
    assert (
        keywords._find_slot_center_to_center_keyword_alias_hits.__module__
        == keywords.__name__
    )
    assert (
        path_profiles._find_center_arc_keyword_alias_hits.__module__
        == path_profiles.__name__
    )
    assert (
        path_profiles._find_buildsketch_curve_context_hits.__module__
        == path_profiles.__name__
    )
    assert (
        path_profiles._find_annular_profile_face_extraction_sweep_hits.__module__
        == path_profiles.__name__
    )
    assert (
        path_profiles._find_sweep_profile_face_method_reference_hits.__module__
        == path_profiles.__name__
    )
    assert (
        structural._find_buildpart_sketch_primitive_context_hits.__module__
        == structural.__name__
    )
    assert (
        structural._find_transform_context_manager_hits.__module__
        == structural.__name__
    )
    assert (
        structural._find_topology_geometry_attribute_hits.__module__
        == structural.__name__
    )
    assert (
        structural._find_rectanglerounded_radius_bounds_hits.__module__
        == structural.__name__
    )


def test_lint_ast_helpers_live_in_ast_utils_module() -> None:
    assert ast_utils._ast_expr_is_zero_like.__module__ == ast_utils.__name__
    assert ast_utils._ast_expr_is_plane_like.__module__ == ast_utils.__name__
    assert ast_utils._ast_expr_is_face_plane_constructor.__module__ == ast_utils.__name__
    assert ast_utils._build_parent_map.__module__ == ast_utils.__name__
    assert ast_utils._subscript_index_value.__module__ == ast_utils.__name__
    assert ast_utils._ast_dotted_name.__module__ == ast_utils.__name__
    assert ast_utils._ast_is_mode_subtract.__module__ == ast_utils.__name__
    assert ast_utils._call_name.__module__ == ast_utils.__name__
    assert ast_utils._call_uses_mode_subtract.__module__ == ast_utils.__name__
    assert ast_utils._call_uses_mode_private.__module__ == ast_utils.__name__
    assert ast_utils._call_materializes_additive_host.__module__ == ast_utils.__name__
    assert (
        ast_utils._call_subtractive_without_host_operation_name.__module__
        == ast_utils.__name__
    )
    assert ast_utils._ast_name_matches.__module__ == ast_utils.__name__
    assert ast_utils._ast_expr_text.__module__ == ast_utils.__name__
    assert ast_utils._with_context_builder_name.__module__ == ast_utils.__name__
    assert ast_utils._with_context_is_locations.__module__ == ast_utils.__name__
    assert ast_utils._looks_like_plane_expr.__module__ == ast_utils.__name__
    assert ast_utils._looks_like_vector_tuple.__module__ == ast_utils.__name__
    assert ast_utils._looks_like_xyz_coordinate_tuple.__module__ == ast_utils.__name__
    assert ast_utils._strip_python_comments_and_strings.__module__ == ast_utils.__name__
