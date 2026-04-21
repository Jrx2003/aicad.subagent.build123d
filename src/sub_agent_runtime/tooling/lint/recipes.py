from __future__ import annotations

import re
from typing import Any

def _build_preflight_repair_recipe(
    *,
    family_ids: list[str],
    lint_hits: list[dict[str, Any]],
    requirement_text: str = "",
) -> dict[str, Any]:
    requirement_lower = requirement_text.lower()
    living_hinge_requested = (
        "living hinge" in requirement_lower or "living-hinge" in requirement_lower
    )
    plain_pin_or_mechanical_hinge_requested = any(
        token in requirement_lower
        for token in (
            "pin hinge",
            "mechanical hinge",
        )
    )
    detached_hinge_requested = any(
        token in requirement_lower
        for token in (
            "removable pin",
            "removable hinge pin",
            "detachable pin",
            "exposed hinge",
            "exposed hinge assembly",
            "external hinge assembly",
            "hinge assembly",
            "hinge barrel",
            "hinge barrels",
            "hinge pin",
            "hinge pins",
            "detached hinge",
            "detached hinge hardware",
            "separate hinge part",
            "separate hinge parts",
        )
    )

    def _explicit_cylindrical_slot_boolean_safe_recipe() -> dict[str, Any]:
        return {
            "recipe_id": "explicit_cylindrical_slot_boolean_safe_recipe",
            "recipe_summary": (
                "For an explicit cutting-cylinder slot, keep the host builder authoritative. "
                "Prefer one builder-native subtractive placement on the stable host; if a "
                "detached boolean is still required, close the host builder first, then build "
                "one literal Cylinder cutter, orient it with Rot(...), place it with Pos(...), "
                "and subtract it from the detached host solid."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "with BuildPart() as host: build the target body first and keep the active host authoritative",
                    "if the slot can stay builder-native, place the literal Cylinder cutter with `mode=Mode.SUBTRACT` and explicit `Locations(...)` on that host",
                    "if detached solid arithmetic is still required, close the host builder first before creating or positioning the cutter",
                    "create the cutter as `cutter = Cylinder(radius=..., height=..., align=(Align.CENTER, Align.CENTER, Align.CENTER))` without `axis=` or `length=`",
                    "orient the detached cutter with `Rot(...)` and place it with `Pos(...)` or `Locations(...)`",
                    "compute the final detached boolean only after the host builder closes, for example `result = host.part - cutter`",
                ],
            },
        }

    def _nested_hollow_section_same_builder_subtract_recipe() -> dict[str, Any]:
        return {
            "recipe_id": "nested_hollow_section_same_builder_subtract_contract",
            "recipe_summary": (
                "For hollow enclosures, shells, lids, and bases, keep the host builder "
                "authoritative. Realize cavities, slots, notches, and side pockets with "
                "same-builder subtractive geometry when possible, and only fall back to "
                "detached booleans after the host builder closes. Do not mutate `host.part` "
                "inside the active builder or reuse temporary primitive staging solids as CSG."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "open one active `BuildPart` for the host shell/body and build the outer envelope first",
                    "keep cavity, notch, slot, and pocket edits builder-native while that host builder is open; do not assign into `host.part`, `base.part`, or `lid.part` there",
                    "if a local cut uses sketch primitives such as Rectangle, SlotOverall, Circle, or Ellipse, open `BuildSketch(target_plane)` first, realize the 2D profile there, and only then extrude/cut against the host",
                    "if a cut truly needs detached solid arithmetic, close the host builder first, then create and place the detached cutter outside it before one explicit `result = host.part - cutter` boolean",
                    "if the requirement names separate physical parts such as lid/base or body/cover, realize each physical part in its own closed `BuildPart` before combining detached results",
                    "only after the shell/body/lid/base envelope is stable should magnets, thumb notches, hinge features, posts, or side pockets be added",
                ],
            },
        }

    def _clamshell_host_local_cut_recipe() -> dict[str, Any]:
        hinge_summary = (
            "keep the hinge host-owned as an integrated living hinge instead of detached hardware."
            if living_hinge_requested and not detached_hinge_requested
            else (
                "only realize detached hinge hardware when the requirement explicitly asks for a removable pin, separate hinge parts, or an exposed hinge assembly."
                if detached_hinge_requested
                else "treat a plain pin/mechanical hinge as host-owned lid/base geometry unless the prompt explicitly asks for detachable hinge hardware."
            )
        )
        return {
            "recipe_id": "clamshell_host_local_cut_contract",
            "recipe_summary": (
                "For clamshell lid/base shells, keep each shell host authoritative, "
                "finish host-owned local cuts before that shell closes, and "
                f"{hinge_summary}"
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "realize the base and lid in one authoritative `BuildPart` per shell host, keeping both parts in the closed assembled envelope instead of reopening the same alias later",
                    "finish host-owned local cuts such as magnet recesses, thumb notches, slots, and pockets inside that shell builder before that shell builder closes",
                    (
                        "if the requirement says `living hinge`, keep that hinge as an integrated host-owned thin back-edge strip or flexure between lid and base and preserve the default two physical parts; do not create detached `hinge_barrel` or `hinge_pin` solids unless the prompt explicitly switches to pin/mechanical/removable hinge hardware"
                        if living_hinge_requested and not detached_hinge_requested
                        else (
                            "if the requirement explicitly requests detachable hinge hardware such as a removable pin, separate hinge parts, or an exposed hinge assembly, detached hinge barrels or hinge pins are allowed, but keep the default physical-part target at lid/base only and avoid inventing extra hinge solids beyond the requested hardware"
                            if detached_hinge_requested
                            else "a plain `pin hinge` or `mechanical hinge` still defaults to a two-part lid/base target: keep the hinge knuckles/barrels host-owned on lid/base and only detach the pin/hardware when the prompt explicitly asks for a removable pin, separate hinge parts, or an exposed hinge assembly"
                        )
                    ),
                    (
                        "plain `pin hinge` or `mechanical hinge` still defaults to a two-part lid/base target: keep the hinge knuckles/barrels host-owned on lid/base and only detach the pin/hardware when the prompt explicitly asks for a removable pin, separate hinge parts, or an exposed hinge assembly"
                        if plain_pin_or_mechanical_hinge_requested and not detached_hinge_requested
                        else "only use detached hinge hardware when the prompt explicitly names detachable hinge hardware such as a removable pin, separate hinge parts, or an exposed hinge assembly"
                    ),
                    "Build123d `extrude(amount=h)` grows one-sided from the active sketch plane; it does not automatically create a centered `[-h/2, +h/2]` shell interval around that plane.",
                    "For centered lid/base intervals, sketch on the real start face plane or translate the finished solid afterward; do not assume `Locations((0, 0, center_z))` plus `extrude(amount=h)` creates a centered shell interval by itself.",
                    "for a living hinge, the back-edge seam coordinate belongs to the hinge strip itself, not to the whole shell envelope; do not translate the whole lid or base to the back seam coordinate just to make the hinge touch",
                    "do not drop an unrotated default `Cylinder(...)` directly onto `(x, hinge_y, split_z)` or `(x, -depth/2, z)` inside lid/base builders and assume it became an X-axis hinge barrel or pin; without a supported rotation/orientation lane that cylinder still runs along Z",
                    "for front/back clamshell-local edits such as a thumb notch, front label recess, or mating-face pocket, treat the host as Y-normal and start from `Plane.XZ.offset(±depth/2)` or `Plane(face)` instead of `Plane.XY`/`Plane.YZ` plus guessed 3D placement",
                    "for top/bottom mating-face edits, keep the host plane in the XY family at the real face datum such as `Plane.XY.offset(z_face)` and place only in-plane `(x, y)` coordinates locally",
                    "if a local cut uses sketch primitives such as `SlotOverall(...)` or `Rectangle(...)`, open `BuildSketch(target_plane)` on the intended host plane first, then extrude/subtract from that same shell host",
                    "if a thumb notch or front label recess is externalized as a detached cutter such as `notch_cutter` or `label_recess`, that detached builder must stay positive or `mode=Mode.PRIVATE`; do not write `with BuildPart() as notch_cutter:` followed by `extrude(..., mode=Mode.SUBTRACT)` because a detached cutter builder has no host yet",
                    "do not try to rescue a wrong host plane by nesting `BuildSketch(Plane.XY)` or `BuildSketch(Plane.YZ)` inside `Locations((x, y, z))`, `shift_origin(...)`, or extra rotations when the requested host is front/back",
                    "do not reopen `with BuildPart() as lid:` or `with BuildPart() as base:` later just to start detached subtractive mini-builders for late local cuts",
                    "only when the prompt explicitly requests detached back-edge hinge barrels or pins should you separate the hinge seam location from the hinge axis direction: the seam still sits at `y = ±depth/2`, while the cylinder axis is chosen separately by transform; do not reinterpret the back-edge hinge seam as a `Plane.YZ` sketch family just because the hinge sits at the back edge",
                    "when detached hinge hardware is explicitly requested, keep hinge barrels, hinge pins, and other rotated hardware as detached separate positive solids after the shell hosts close, then assemble those detached solids in the shared closed pose",
                    "a safe detached hinge lane is `Pos(0, ±depth/2, split_z) * (Rot(Y=90) * hinge_barrel.part)` after the hinge builder closes, keeping the Y seam coordinate explicit instead of rebuilding the hinge on `Plane.YZ`",
                    "choose one axis-orientation lane for a detached hinge cylinder: either create it with one supported primitive rotation lane, or build it unrotated and orient the closed solid afterward, but do not stack `Cylinder(..., rotation=...)` and a second `Rot(...) * hinge_barrel.part` just to realize one hinge axis",
                    "if a detached boolean is still required for one local cutter, build that cutter as a positive/private solid after the shell hosts close and do one explicit final boolean outside the active host builders",
                ],
            },
        }

    lint_ids = {
        str(item.get("rule_id") or "").strip()
        for item in lint_hits
        if isinstance(item, dict)
    }
    family_id_set = {str(item).strip() for item in family_ids if str(item).strip()}
    clamshell_half_shell_context = (
        "half_shell" in family_id_set and "nested_hollow_section" in family_id_set
    )
    if not lint_ids:
        return {}
    if (
        "nested_hollow_section" in family_id_set
        and "slots" in family_id_set
        and lint_ids.intersection(
            {
                "invalid_build123d_contract.active_builder_part_mutation",
                "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic",
            }
        )
    ):
        if clamshell_half_shell_context:
            return _clamshell_host_local_cut_recipe()
        return _nested_hollow_section_same_builder_subtract_recipe()
    if (
        "nested_hollow_section" in family_id_set
        and lint_ids.intersection(
            {
                "invalid_build123d_context.nested_subtractive_buildpart_inside_active_builder",
                "invalid_build123d_api.nested_buildpart_cutter_part_arithmetic",
                "invalid_build123d_api.nested_buildpart_part_transform",
            }
        )
    ):
        if clamshell_half_shell_context:
            return _clamshell_host_local_cut_recipe()
        return _nested_hollow_section_same_builder_subtract_recipe()
    if (
        clamshell_half_shell_context
        and "invalid_build123d_contract.named_face_plane_family_mismatch" in lint_ids
    ):
        return _clamshell_host_local_cut_recipe()
    if "slots" in family_ids and lint_ids.intersection(
        {
            "invalid_build123d_api.bare_subtract_helper",
            "invalid_build123d_api.bare_rotate_helper",
            "invalid_build123d_keyword.cylinder_axis",
            "invalid_build123d_keyword.cylinder_length_alias",
            "invalid_build123d_contract.active_builder_cutter_primitive_boolean",
        }
    ):
        return _explicit_cylindrical_slot_boolean_safe_recipe()
    if (
        "spherical_recess" in family_ids
        and "pattern_distribution" in family_ids
        and "invalid_build123d_api.buildpart_solid_method_arithmetic" in lint_ids
    ):
        return {
            "recipe_id": "spherical_recess_pattern_builder_subtract_recipe",
            "recipe_summary": (
                "For repeated hemispherical recesses, keep the host in one BuildPart, compute the "
                "centered pattern offsets, and subtract the recess bodies with builder-native "
                "`mode=Mode.SUBTRACT` placements instead of mutating `part.solid`."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "with BuildPart() as part: build the base body first",
                    "compute the centered pattern offsets explicitly from the requested spacing/count",
                    "with Locations((x, y, top_z), ...): Sphere(radius=..., mode=Mode.SUBTRACT)",
                    "result = part.part",
                ],
            },
        }
    if (
        "explicit_anchor_hole" in family_ids
        and "pattern_distribution" in family_ids
        and "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic"
        in lint_ids
    ):
        return {
            "recipe_id": "explicit_anchor_hole_same_builder_subtract_recipe",
            "recipe_summary": (
                "For repeated countersunk hole layouts, keep the host in one BuildPart, "
                "convert explicit point coordinates into the correct host-face frame, and "
                "realize the hole/countersink cutters through one supported subtractive "
                "pattern instead of nesting BuildPart cutters or reusing temporary "
                "primitive staging solids for later `part.part` arithmetic."
            ),
            "recipe_skeleton": {
                "mode": "subtree_rebuild_via_execute_build123d",
                "steps": [
                    "with BuildPart() as part: build the host body first",
                    "compute the full hole center set in the host-face coordinate frame before cutting",
                    "for explicit planar countersink arrays where the requirement already gives the through-hole diameter, head diameter, and cone angle, prefer one `CounterSinkHole(...)` pass first with the exact helper contract and explicit host-face placement",
                    "Only fall back to an explicit same-builder cylinder+cone or revolved countersink recipe when the helper contract cannot express the host/placement semantics cleanly or when prior validation/evaluation evidence shows the helper result is dimensionally wrong for that family",
                    "either keep the cutters in the same active BuildPart with explicit subtractive placement, or close the host builder and subtract fully positioned cutters with `result = host.part - cutter`",
                    "do not use nested `with BuildPart() as cutter:` blocks followed by `part.part -= cutter.part` inside the host builder",
                    "do not create `cone = Cone(...)` or `cyl = Cylinder(...)` staging solids inside the active host and reuse them later in explicit boolean arithmetic unless they were created as `mode=Mode.PRIVATE`",
                ],
            },
        }
    if (
        "explicit_anchor_hole" in family_ids
        and lint_ids.intersection(
            {
                "invalid_build123d_api.nested_buildpart_cutter_part_arithmetic",
                "invalid_build123d_context.nested_subtractive_buildpart_inside_active_builder",
                "invalid_build123d_contract.explicit_anchor_manual_cutter_requires_subtract_mode",
            }
        )
    ):
        return {
            "recipe_id": "explicit_anchor_hole_same_builder_subtract_recipe",
            "recipe_summary": (
                "For repeated countersunk hole layouts, keep the host in one BuildPart, "
                "convert explicit point coordinates into the correct host-face frame, and "
                "realize the hole/countersink cutters through one supported subtractive "
                "pattern instead of nesting BuildPart cutters or reusing temporary "
                "primitive staging solids for later `part.part` arithmetic."
            ),
            "recipe_skeleton": {
                "mode": "subtree_rebuild_via_execute_build123d",
                "steps": [
                    "with BuildPart() as part: build the host body first",
                    "compute the full hole center set in the host-face coordinate frame before cutting",
                    "for explicit planar countersink arrays where the requirement already gives the through-hole diameter, head diameter, and cone angle, prefer one `CounterSinkHole(...)` pass first with the exact helper contract and explicit host-face placement",
                    "Only fall back to an explicit same-builder cylinder+cone or revolved countersink recipe when the helper contract cannot express the host/placement semantics cleanly or when prior validation/evaluation evidence shows the helper result is dimensionally wrong for that family",
                    "either keep the cutters in the same active BuildPart with explicit subtractive placement, or close the host builder and subtract fully positioned cutters with `result = host.part - cutter`",
                    "do not use nested `with BuildPart() as cutter:` blocks followed by `part.part -= cutter.part` inside the host builder",
                    "do not create `cone = Cone(...)` or `cyl = Cylinder(...)` staging solids inside the active host and reuse them later in explicit boolean arithmetic unless they were created as `mode=Mode.PRIVATE`",
                ],
            },
        }
    if (
        "explicit_anchor_hole" in family_ids
        and lint_ids.intersection(
            {
                "invalid_build123d_keyword.cylinder_axis",
                "invalid_build123d_keyword.cylinder_taper",
                "invalid_build123d_keyword.cylinder_length_alias",
            }
        )
    ):
        return {
            "recipe_id": "explicit_anchor_directional_hole_cylinder_contract",
            "recipe_summary": (
                "For explicit directional through-holes, keep the host body authoritative, "
                "place the hole centers with literal local anchors, build a plain Cylinder "
                "cutter without `axis=`, and orient it with `Rot(...)` plus explicit "
                "placement instead of guessing unsupported cylinder keywords."
            ),
            "recipe_skeleton": {
                "mode": "subtree_rebuild_via_execute_build123d",
                "steps": [
                    "with BuildPart() as part: build the host solid and any shell/pad geometry first",
                    "keep the requested hole centers literal in the target local frame, for example explicit `(x, y, z)` anchors or a face-local workplane placement",
                    "create a plain cutter such as `cutter = Cylinder(radius=..., height=..., align=(Align.CENTER, Align.CENTER, Align.CENTER))` without an `axis=` keyword",
                    "orient the cutter with `Rot(...)`, for example `Rot(Y=90) * cutter` for a Y-direction drill, then place it with `Pos(...)` or a non-origin `Locations(...)` anchor",
                    "either subtract inside the active builder with a verified subtractive path, or close the host builder and do one explicit boolean such as `result = part.part - cutter`",
                    "before finishing, verify that the realized centers still match the requested anchor coordinates on the actual host geometry",
                ],
            },
        }
    if (
        ("annular_groove" in family_ids or "axisymmetric_profile" in family_ids)
        and lint_ids.intersection(
            {
                "invalid_build123d_api.nested_buildpart_cutter_part_arithmetic",
                "invalid_build123d_api.ring_helper_name",
            }
        )
    ):
        return {
            "recipe_id": "annular_groove_same_builder_band_subtract_recipe",
            "recipe_summary": (
                "For annular grooves on a code-first Build123d path, keep the host geometry "
                "authoritative and realize the groove band through one same-builder subtractive "
                "pattern or one post-host boolean, not a nested BuildPart cutter inside the host."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "with BuildPart() as part: build the base solid first and keep its outer envelope authoritative",
                    "derive the groove outer_radius, inner_radius, and axial window directly from the requirement",
                    "either keep the annular groove subtraction in the same active `BuildPart` with builder-native subtractive geometry, or close the host and subtract the annular groove band once",
                    "do not guess `Ring(...)`; realize the band as an outer coaxial solid/profile minus the inner coaxial solid/profile",
                    "do not use `with BuildPart() as groove_band:` inside the host builder followed by `part.part -= groove_band.part`",
                ],
            },
        }
    if (
        "nested_hollow_section" in family_ids
        and "axisymmetric_profile" in family_ids
        and "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic"
        in lint_ids
    ):
        return {
            "recipe_id": "half_shell_semi_profile_extrude_contract",
            "recipe_summary": (
                "For half-shell or split-shell hosts, do not stage full cylinders and trim "
                "them inside an active BuildPart. Build one closed semi-profile on the named "
                "plane, extrude it once for the host envelope, then add pads and explicit "
                "hole cutters after the host geometry is stable."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "open `with BuildSketch(named_plane):` on the requirement plane and build the shell cross-section there first",
                    "inside `BuildLine`, draw the outer semicircle and inner semicircle, then close the split side with explicit `Line(...)` segments so the semi-annulus becomes one closed face",
                    "call `make_face()` and `extrude(amount=...)` once to create the half-shell host, preserving the named-plane lower bound and the one-sided split envelope",
                    "realize any bottom pad or lug body as a separate additive host step after the shell profile is valid, not by trimming temporary cylinders inside the same active builder",
                    "if an inner clearance or drill cutter still needs explicit solid arithmetic, close the host builder first and subtract that external cutter from `host.part` afterward",
                    "only after the host shell and pad are stable should directional holes be placed with literal anchors and rotated cutters",
                ],
            },
        }
    if clamshell_half_shell_context and lint_ids.intersection(
        {
            "invalid_build123d_contract.clamshell_hinge_unrotated_default_cylinder",
            "invalid_build123d_contract.detached_subtractive_builder_without_host",
            "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic",
            "invalid_build123d_contract.active_builder_temporary_primitive_transform_rebind",
            "invalid_build123d_contract.active_builder_part_mutation",
            "invalid_build123d_context.sketch_primitive_requires_buildsketch",
            "invalid_build123d_context.transform_context_manager",
        }
    ):
        return _clamshell_host_local_cut_recipe()
    if "invalid_build123d_contract.active_builder_part_mutation" in lint_ids:
        return {
            "recipe_id": "active_builder_part_mutation_contract",
            "recipe_summary": (
                "Do not mutate `host.part` while the host BuildPart is still open. Keep the "
                "active builder authoritative for adds/cuts, or close it first before detached "
                "boolean arithmetic."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "inside `with BuildPart() as host:`, do not write `host.part = ...`, `host.part += ...`, or `host.part -= ...`",
                    "express bosses, pockets, shells, notches, magnet recesses, and similar edits as builder-native primitives with `mode=Mode.ADD` / `mode=Mode.SUBTRACT` plus explicit `Locations(...)` placement",
                    "if a feature truly needs detached solid arithmetic, close the host builder first and then compute `result = host.part +/- detached_feature` outside the active builder",
                    "only after the detached boolean is complete should the final geometry be assigned to `result`",
                ],
            },
        }
    if "invalid_build123d_contract.detached_subtractive_builder_without_host" in lint_ids:
        return {
            "recipe_id": "detached_subtractive_builder_without_host_contract",
            "recipe_summary": (
                "Do not start a detached BuildPart with a subtractive operation when no host "
                "solid exists yet. Keep the cut inside the authoritative host builder, or build "
                "a positive/private cutter first and subtract it only after the host closes."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "do not open a standalone `BuildPart` whose first materializing operation is `mode=Mode.SUBTRACT`, `Hole(...)`, `CounterBoreHole(...)`, or `CounterSinkHole(...)`",
                    "if the feature belongs to an existing host body, keep that subtractive operation inside the authoritative host builder with explicit `Locations(...)`, target plane placement, or a topology-targeted local edit",
                    "if a detached cutter is truly required, create it as a positive or `mode=Mode.PRIVATE` solid first, close that builder, then do one explicit boolean such as `result = host.part - cutter.part` outside the active host builder",
                    "for repeated magnet recesses, thumb notches, pockets, and similar enclosure cuts, prefer repeated same-host subtractive placements instead of detached subtractive mini-builders",
                ],
            },
        }
    if "invalid_build123d_api.nested_buildpart_part_transform" in lint_ids:
        return {
            "recipe_id": "nested_buildpart_part_transform_contract",
            "recipe_summary": (
                "Do not transform `nested_builder.part` as though it were a stable detached "
                "solid while an outer BuildPart host is still active. Keep the host builder "
                "authoritative, or close the host first before transforming detached solids."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "if the nested builder is only a local cutter or pocket feature, do not open a second BuildPart just to move its `.part`; keep the cut builder-native on the authoritative host with `Locations(...)` and subtractive mode",
                    "if a truly detached local feature is required, close the outer host builder first and only then transform the detached solid with `Pos(...)`, `Rot(...)`, or `Location(...)` outside the active host",
                    "do not call `nested_builder.part.move(...)`, `.rotate(...)`, `.located(...)`, or similar transform methods while the outer host BuildPart is still open",
                    "after the detached transform is complete, do one explicit boolean such as `result = host.part - cutter` outside the active builder",
                ],
            },
        }
    if "invalid_build123d_contract.plane_tuple_multiplication" in lint_ids:
        return {
            "recipe_id": "build123d_plane_tuple_multiplication_contract",
            "recipe_summary": (
                "Do not treat `Plane.XY/XZ/YZ` like a tuple-transform surface. Use translated "
                "planes for sketch/workplane placement and `Locations((x, y, z))` for point "
                "placement."
            ),
            "recipe_skeleton": {
                "mode": "subtree_rebuild_via_execute_build123d",
                "steps": [
                    "when the feature is defined by a sketch/workplane, translate the named plane with its normal-aware offset, for example `Plane.XY.offset(z0)`, `Plane.XZ.offset(y0)`, or `Plane.YZ.offset(x0)`",
                    "when the feature is defined by an explicit point placement, keep the workplane unchanged and place the operation with `Locations((x, y, z))` instead of multiplying a plane by a raw tuple",
                    "if both a translated workplane and an explicit local point are needed, build the shifted plane first and then apply a local `Locations((u, v))` placement on that plane",
                    "do not write `Plane.XY * (x, y, z)`, `Plane.XZ * (x, z, y)`, or similar tuple multiplication forms",
                ],
            },
        }
    if lint_ids.intersection(
        {
            "invalid_build123d_api.loc_helper_name",
            "invalid_build123d_api.bare_move_helper",
        }
    ) and not lint_ids.intersection(
        {
            "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic",
            "invalid_build123d_contract.active_builder_temporary_primitive_transform_rebind",
        }
    ):
        return {
            "recipe_id": "build123d_location_helper_contract",
            "recipe_summary": (
                "Use supported Build123d placement primitives: `Location(...)` for a location "
                "object, or `Pos(...)` / `Rot(...)` for pure transforms. Do not invent `Loc(...)`."
            ),
            "recipe_skeleton": {
                "mode": "subtree_rebuild_via_execute_build123d",
                "steps": [
                    "replace every `Loc(...)` call with the supported placement surface that matches the intent",
                    "for an explicit location object, use `Location(...)` with the supported constructor shape",
                    "for a pure translation, use `Pos(x, y, z)`; for a pure rotation, use `Rot(...)`",
                    "if you are moving a detached solid, apply the supported transform or location object directly and keep the final geometry assignment explicit",
                ],
            },
        }
    if "invalid_build123d_api.scale_helper_case" in lint_ids:
        return {
            "recipe_id": "build123d_scale_helper_contract",
            "recipe_summary": (
                "Use lowercase `scale(...)` on a detached shape with the explicit `by=` argument; "
                "do not invent a capitalized `Scale.by(...)` / `Scale(...)` helper."
            ),
            "recipe_skeleton": {
                "mode": "subtree_rebuild_via_execute_build123d",
                "steps": [
                    "keep the source solid detached and explicitly named before scaling it",
                    "replace `Scale.by((sx, sy, sz)) * shape` with `scale(shape, by=(sx, sy, sz))`",
                    "if the scaled shape still needs placement, apply `Pos(...)`, `Rot(...)`, or `Location(...)` after the supported lowercase `scale(...)` call",
                    "assign the scaled detached result explicitly instead of mutating an active builder host through an invented scaling helper",
                ],
            },
        }
    if "invalid_build123d_api.split_helper_case" in lint_ids:
        return {
            "recipe_id": "build123d_split_function_contract",
            "recipe_summary": (
                "For clamshell or split-body workflows, use lowercase `split(...)` on a finished "
                "host solid and keep lid/base extraction outside the active builder."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "finish the outer shell/body first and assign the authoritative host solid to a detached variable",
                    "do not call `Split(...)`; use lowercase `split(host_solid, ...)` on the verified Build123d contract",
                    "perform split/lid-base extraction only after the host builder closes, not while the host `BuildPart` is still active",
                    "after the split, derive lid/base solids explicitly and then continue with hinge, notch, magnet, or cavity features on the detached parts",
                ],
            },
        }
    if "invalid_build123d_contract.compound_positional_children_contract" in lint_ids:
        return {
            "recipe_id": "build123d_compound_children_contract",
            "recipe_summary": (
                "Build123d Compound is not a variadic part constructor. Keep detached child "
                "shapes in one iterable or an explicit `children=[...]` payload instead of "
                "passing each child as its own positional argument."
            ),
            "recipe_skeleton": {
                "mode": "subtree_rebuild_via_execute_build123d",
                "steps": [
                    "finish each physical part as its own detached `Part`/`Shape` first",
                    "when returning a multi-part assembly, combine those detached children with `Compound([part_a, part_b, part_c])` or another explicit iterable form",
                    "do not write `Compound(part_a, part_b, part_c)` because only the first positional argument is the shape payload and later positional slots map to metadata like label/color",
                    "if an explicit children-style assembly is clearer, pass `children=[...]` with the intended detached parts instead of overloading positional arguments",
                ],
            },
        }
    if (
        "nested_hollow_section" in family_ids
        and "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic"
        in lint_ids
    ):
        if clamshell_half_shell_context:
            return _clamshell_host_local_cut_recipe()
        return _nested_hollow_section_same_builder_subtract_recipe()
    if "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic" in lint_ids:
        return {
            "recipe_id": "active_builder_temporary_primitive_boolean_contract",
            "recipe_summary": (
                "Temporary Box/Cylinder/Cone/Sphere/Torus values created inside an active "
                "BuildPart are already part of that host, so later boolean/intersection "
                "arithmetic on those staging solids does not behave like isolated CSG."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "with BuildPart() as host: build only the intended host solids inside the active builder",
                    "do not create temporary primitive staging solids inside that active builder just for later boolean or trim arithmetic",
                    "if the prompt asks for multiple physical parts, do not keep them in one shared active host builder; close each part builder first and combine the detached results afterward",
                    "if the requirement is a split shell or half-profile body, prefer one closed semi-profile and extrude it for the base envelope",
                    "otherwise close the host builder first, then create any temporary solids outside it before doing explicit solid arithmetic such as `result = host.part - cutter` or `result = host.part & trim_box`",
                ],
            },
        }
    if lint_ids.intersection(
        {
            "invalid_build123d_keyword.cylinder_axis",
            "invalid_build123d_keyword.cylinder_length_alias",
        }
    ):
        return {
            "recipe_id": "build123d_cylinder_axis_transform_contract",
            "recipe_summary": (
                "Build123d Cylinder keeps a literal radius/height contract and does not "
                "accept `axis=` or `length=`. Create a plain detached cylinder first, then "
                "orient and place it with Rot/Pos/Locations instead of rebinding an already-"
                "consumed primitive inside an active builder."
            ),
            "recipe_skeleton": {
                "mode": "subtree_rebuild_via_execute_build123d",
                "steps": [
                    "create the primitive as `Cylinder(radius=..., height=...)` or `Cylinder(radius, height)`",
                    "do not pass `axis=` or `length=` into Cylinder",
                    "if the feature must point along X or Y, orient the detached cylinder with `Rot(...)` before the final add/subtract step",
                    "do not create a cylinder inside an active `BuildPart` and then try to relocate it with `solid = Rot(...) * solid` or `solid = Pos(...) * solid` after the builder already consumed that primitive",
                    "place the rotated cutter or feature with `Pos(...)` or `Locations(...)` on the intended host",
                ],
            },
        }
    if "invalid_build123d_contract.active_builder_temporary_primitive_transform_rebind" in lint_ids:
        return {
            "recipe_id": "active_builder_temporary_primitive_transform_contract",
            "recipe_summary": (
                "A primitive created inside an active BuildPart is already part of that host, "
                "so `solid = Pos(...) * solid` or `solid = Rot(...) * solid` only rebinds a "
                "temporary Python value instead of relocating the geometry already added to the "
                "builder."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "inside an active `BuildPart`, express placement with `Locations(...)`, an explicit sketch/workplane, or another builder-native local frame instead of transforming an already-added primitive variable",
                    "do not rely on `solid = Pos(...) * solid`, `solid = Rot(...) * solid`, or similar transform multiplication to mutate geometry that the active builder already consumed",
                    "if the feature truly needs detached transform-first solid composition, close the host builder first, then create and transform the detached solid outside the active builder before the final boolean/add step",
                ],
            },
        }
    if "invalid_build123d_api.vector_lowercase_component_attribute" in lint_ids:
        return {
            "recipe_id": "build123d_vector_component_attribute_contract",
            "recipe_summary": (
                "Build123d vector and point components use uppercase attributes such as "
                "`.X`, `.Y`, and `.Z`; do not guess lowercase `.x/.y/.z` accessors."
            ),
            "recipe_skeleton": {
                "mode": "local_edit_via_execute_build123d",
                "steps": [
                    "replace lowercase vector component access such as `.z` with the Build123d attribute `.Z`",
                    "if you need numeric indexing, explicitly convert the vector or point to a tuple before subscripting it",
                    "keep the surrounding edge/face filtering logic unchanged unless separate topology evidence says the selector itself is too broad",
                ],
            },
        }
    if "invalid_build123d_api.topology_geometry_attribute" in lint_ids:
        return {
            "recipe_id": "build123d_topology_geometry_attribute_contract",
            "recipe_summary": (
                "Build123d topology entities do not expose a generic `.geometry` attribute; "
                "selection logic must use explicit topology measurements or typed properties."
            ),
            "recipe_skeleton": {
                "mode": "local_edit_via_execute_build123d",
                "steps": [
                    "replace `.geometry` checks on edges/faces/solids with Build123d-native measurements such as `.geom_type`, `.length`, `.radius`, `.bounding_box()`, `.center()`, or another explicit query that matches the selector intent",
                    "keep the selection focused on the intended host subset instead of treating topology entities like generic CAD kernel wrappers",
                    "if the selector still feels broad after removing `.geometry`, narrow it with an explicit edge/face subset rather than retrying the same broad filter",
                ],
            },
        }
    if "invalid_build123d_context.transform_context_manager" in lint_ids:
        return {
            "recipe_id": "build123d_transform_placement_contract",
            "recipe_summary": (
                "Build123d transform helpers such as Rot/Pos/Location are not builder "
                "context managers. Keep placement in builder-native `Locations(...)` "
                "blocks, or build a detached solid first and then transform it with "
                "`Rot(...) * solid` / `Pos(...) * solid` before the final boolean step."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "inside an active BuildPart, express repeated feature placement with `Locations(...)` rather than `with Rot(...):` or `with Pos(...):` blocks",
                    "if the feature is a subtractive primitive on the current host, keep the cutter primitive directly inside the active builder and orient it with builder-native placement or a detached-transform-first pattern",
                    "if rotation truly needs to happen before the boolean, create the detached solid first, transform it with `Rot(...) * solid` or `Pos(...) * solid`, then add/subtract that transformed solid in an explicit final step",
                    "do not rely on transform helpers as if they opened a temporary local builder scope",
                ],
            },
        }
    if "invalid_build123d_api.bare_shell_helper" in lint_ids:
        preserve_target_face_material = bool(
            {"named_face_local_edit", "explicit_anchor_hole", "pattern_distribution"}
            .intersection(family_ids)
        )
        return {
            "recipe_id": "build123d_shell_offset_contract",
            "recipe_summary": (
                "For shelled bodies, keep the outer host build explicit and realize wall "
                "thickness with Build123d shell/offset semantics or an explicit inner-solid "
                "subtraction, not with a guessed bare `shell(...)` helper."
                + (
                    " When later local edits target a named face, preserve that face as "
                    "material and open the opposite face by default unless the requirement "
                    "explicitly says otherwise."
                    if preserve_target_face_material
                    else ""
                )
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "build the outer host solid first inside BuildPart",
                    "for true shell semantics, use `offset(amount=-wall_thickness, openings=...)` on the host-facing opening set",
                    "if the body is a simple box-like enclosure, subtract an explicitly placed inner solid instead of calling `shell(...)`",
                    *(
                        [
                            "if a later recess, hole set, or reference pattern targets a named face, keep that target face on surviving host material and open the opposite face when the opening face is unspecified"
                        ]
                        if preserve_target_face_material
                        else []
                    ),
                ],
            },
        }
    if "invalid_build123d_keyword.offset_opening_singular" in lint_ids:
        preserve_target_face_material = bool(
            {"named_face_local_edit", "explicit_anchor_hole", "pattern_distribution"}
            .intersection(family_ids)
        )
        return {
            "recipe_id": "build123d_shell_offset_contract",
            "recipe_summary": (
                "Build123d shell offsets use `openings=...` for the opening-face set. "
                "Do not guess a singular `opening=` keyword."
                + (
                    " When later local edits target a named face, preserve that face as "
                    "material and open the opposite face by default unless the requirement "
                    "explicitly says otherwise."
                    if preserve_target_face_material
                    else ""
                )
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "build the outer host solid first inside BuildPart",
                    "when using shell semantics, call `offset(amount=-wall_thickness, openings=...)` with the opening face set",
                    "if the body is simple and the opening face choice is still ambiguous, subtract an explicitly placed inner solid instead",
                    *(
                        [
                            "if a later recess, hole set, or reference pattern targets a named face, keep that target face on surviving host material and open the opposite face when the opening face is unspecified"
                        ]
                        if preserve_target_face_material
                        else []
                    ),
                ],
            },
        }
    if lint_ids.intersection(
        {
            "invalid_build123d_keyword.box_depth_alias",
            "invalid_build123d_keyword.box_radius_alias",
        }
    ):
        return {
            "recipe_id": "build123d_box_keyword_contract",
            "recipe_summary": (
                "When using Build123d Box primitives, stay on the native `length / width / "
                "height` contract and do not use guessed keyword aliases such as `depth=` "
                "or `radius=`."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "define the three host spans explicitly as length, width, and height",
                    "call `Box(length=..., width=..., height=...)` or `Box(length, width, height)`",
                    "if your variable name is `depth`, pass that variable as the second width dimension instead of `depth=...`",
                    "if the body needs rounded plan corners, use `RectangleRounded(...)` + `extrude(...)` or apply explicit edge fillets after the Box is created",
                ],
            },
        }
    if "invalid_build123d_keyword.regular_polygon_sides_alias" in lint_ids:
        return {
            "recipe_id": "build123d_regular_polygon_keyword_contract",
            "recipe_summary": (
                "When using Build123d `RegularPolygon(...)`, keep the side-count contract "
                "literal with `side_count=` instead of guessed aliases such as `sides=`."
            ),
            "recipe_skeleton": {
                "mode": "local_edit_via_execute_build123d",
                "steps": [
                    "rewrite the polygon call as `RegularPolygon(radius=..., side_count=..., major_radius=True)`",
                    "if the requirement gives side length instead of circumradius, derive the radius first and still pass the polygon count with `side_count=`",
                    "keep same-sketch nested polygon subtraction builder-native with `mode=Mode.SUBTRACT` instead of changing the overall recipe structure just to repair the keyword",
                ],
            },
        }
    if "invalid_build123d_keyword.pos_lowercase_axis_keyword" in lint_ids:
        return {
            "recipe_id": "build123d_pos_keyword_contract",
            "recipe_summary": (
                "When positioning solids in Build123d, use positional `Pos(x, y, z)` "
                "placement instead of lowercase axis keyword guesses such as `Pos(z=...)`."
            ),
            "recipe_skeleton": {
                "mode": "local_edit_via_execute_build123d",
                "steps": [
                    "rewrite the placement with positional arguments such as `Pos(0, 0, z_offset)`",
                    "compose that positional `Pos(...)` with `Rot(...)` or the solid on the correct side of the multiplication",
                    "rerun the same geometry recipe after only the placement expression is repaired",
                ],
            },
        }
    if (
        "invalid_build123d_api.plane_rotated_origin_guess" in lint_ids
        or "invalid_build123d_api.plane_rotate_shape_method_guess" in lint_ids
    ):
        return {
            "recipe_id": "build123d_plane_rotation_contract",
            "recipe_summary": (
                "When orienting Build123d workplanes, treat `Plane.rotated(rotation, "
                "ordering=...)` as an orientation-only operation; it does not accept or "
                "apply a guessed origin tuple."
            ),
            "recipe_skeleton": {
                "mode": "local_edit_via_execute_build123d",
                "steps": [
                    "keep the named workplane when it already has the requested normal, for example use `Plane.XZ` directly for Y-direction drilling",
                    "store in-plane coordinates in the sketch or `Locations(...)` data instead of trying to encode them with a rotated-plane origin guess",
                    "if translation is needed, use `Plane.offset(...)` only along the plane normal or place the cutter/feature with `Pos(...)`",
                    "do not call `.rotate(...)` on Plane objects; use `Plane.rotated((rx, ry, rz), ordering=...)` when orientation must change",
                    "only call `Plane.rotated((rx, ry, rz), ordering=...)` when you truly need a different orientation, and do not pass a second `(x, y, z)` tuple",
                ],
            },
        }
    if (
        "invalid_build123d_api.plane_located_shape_method_guess" in lint_ids
        or "invalid_build123d_api.plane_moved_shape_method_guess" in lint_ids
    ):
        return {
            "recipe_id": "build123d_plane_translation_contract",
            "recipe_summary": (
                "When repositioning a Build123d workplane, use the Plane translation "
                "APIs (`offset`, `move`, `shift_origin`) instead of guessing a "
                "shape-style `.located(...)` or `.moved(...)` method."
            ),
            "recipe_skeleton": {
                "mode": "local_edit_via_execute_build123d",
                "steps": [
                    "keep the named workplane when its orientation is already correct",
                    "translate the workplane with `Plane.offset(amount)` along the plane normal when only a datum shift is needed",
                    "use `Plane.move(Location(...))` or `Plane.shift_origin(...)` when the workplane origin itself must move in a more explicit way",
                    "only call `.located(...)` or `.moved(...)` on actual solids/shapes, not on Plane objects",
                ],
            },
        }
    if "invalid_build123d_contract.face_plane_shift_origin_global_coordinate_guess" in lint_ids:
        return {
            "recipe_id": "build123d_face_plane_shift_origin_contract",
            "recipe_summary": (
                "For face-derived workplanes, do not guess a world-space XYZ tuple inside "
                "`Plane(face).shift_origin(...)`; keep the host plane and place the profile "
                "with local sketch coordinates, or rebuild the plane from the host face's "
                "own origin/normal."
            ),
            "recipe_skeleton": {
                "mode": "local_edit_via_execute_build123d",
                "steps": [
                    "capture the host face once, for example `front_face = part.faces().sort_by(...)[0]`",
                    "either open `with BuildSketch(Plane(front_face)):` directly and draw the local notch/profile with 2D sketch coordinates",
                    "or rebuild the plane from the host face datum, such as `Plane(origin=front_face.center(), z_dir=front_face.normal_at())`, before any further in-plane placement",
                    "avoid passing a guessed global `(x, y, z)` tuple to `shift_origin(...)` unless that point is explicitly guaranteed to lie on the host face plane",
                ],
            },
        }
    if "invalid_build123d_contract.named_face_plane_family_mismatch" in lint_ids and "explicit_anchor_hole" not in family_id_set:
        return {
            "recipe_id": "build123d_named_face_plane_family_contract",
            "recipe_summary": (
                "Named-face local edits must start from the plane family whose normal matches the "
                "requested face instead of sketching on the wrong global plane and hoping later "
                "offsets or rotations recover the host."
            ),
            "recipe_skeleton": {
                "mode": "local_edit_via_execute_build123d",
                "steps": [
                    "map the named face to the matching plane family before any sketch or cutter placement: `top/bottom -> Plane.XY`, `front/back -> Plane.XZ`, `left/right -> Plane.YZ`",
                    "translate that plane only along its own normal axis, or bind directly to `Plane(face)` if topology already selected the host face",
                    "keep the host-face normal authoritative; do not compensate for a wrong plane family with extra `rotated(...)`, `shift_origin(...)`, or guessed world-space offsets",
                    "if the host solid is centered, combine the correct plane family with the correct face datum such as `depth/2`, `width/2`, or `height/2` rather than the full span",
                ],
            },
        }
    if "invalid_build123d_contract.directional_drill_plane_offset_coordinate_mixup" in lint_ids:
        return {
            "recipe_id": "directional_drill_workplane_coordinate_contract",
            "recipe_summary": (
                "For directional drilling, keep the XZ/YZ workplane on the correct "
                "normal-axis datum and put the named hole-center coordinates inside that "
                "workplane instead of encoding an in-plane anchor with `Plane.offset(...)`."
            ),
            "recipe_skeleton": {
                "mode": "local_edit_via_execute_build123d",
                "steps": [
                    "choose the workplane whose normal matches the drill direction, for example `Plane.XZ` for Y-direction holes",
                    "keep the named in-plane coordinates in `Locations((x, z), ...)` or an equivalent local sketch placement",
                    "only use `Plane.offset(...)` for a true translation along the workplane normal axis",
                    "if you need a 3D cutter instead of a sketch, place it explicitly at `(x, normal_axis_value, z)` with `Pos(...)` and orient it with `Rot(...)`",
                ],
            },
        }
    if lint_ids.intersection(
        {
            "invalid_build123d_api.shapelist_filter_by_direction",
            "invalid_build123d_api.edge_is_parallel_axis",
            "invalid_build123d_api.filter_by_position_keyword_band",
            "invalid_build123d_api.filter_by_position_plane_axis",
        }
    ):
        return {
            "recipe_id": "build123d_shapelist_axis_filter_contract",
            "recipe_summary": (
                "When selecting edges or faces by axis direction, use ShapeList "
                "`filter_by(Axis.X/Y/Z)` or an explicit predicate; do not rely on "
                "`filter_by_direction(...)` or `edge.is_parallel(Axis.*)` helpers that "
                "do not exist in Build123d."
            ),
            "recipe_skeleton": {
                "mode": "local_edit_via_execute_build123d",
                "steps": [
                    "extract the target ShapeList, for example `edges = part.edges()`",
                    "use `edges.filter_by(Axis.Y)` for linear edges parallel to the Y axis, or a Python predicate if you need a custom test",
                    "chain `filter_by_position(...)` separately when the selection also depends on a face/edge band such as the bottom Z range",
                    "apply fillet/chamfer/other local edits to the filtered ShapeList",
                ],
            },
        }
    if lint_ids.intersection(
        {
            "invalid_build123d_context.buildpart_topology_access_inside_buildsketch",
        }
    ):
        return {
            "recipe_id": "build123d_buildsketch_builder_boundary_contract",
            "recipe_summary": (
                "Keep `BuildSketch` profile construction separate from enclosing "
                "`BuildPart` topology access; do not use the host part's "
                "edges/faces/vertices as if they were sketch geometry before the solid exists."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "inside `with BuildSketch(...):`, construct the 2D profile directly with sketch-native geometry instead of calling the enclosing `BuildPart` alias",
                    "if the requirement needs rounded profile corners, encode them in the sketch recipe itself rather than querying `part.vertices()` / `part.edges()` inside the sketch",
                    "only after `extrude(...)`, `revolve(...)`, or another solid-forming step should you select solid edges/faces from the finished part for fillet/chamfer/local edits",
                    "keep the final host solid authoritative and assign it to `result`",
                ],
            },
        }
    if "invalid_build123d_context.sketch_primitive_requires_buildsketch" in lint_ids:
        return {
            "recipe_id": "build123d_sketch_primitive_builder_contract",
            "recipe_summary": (
                "Sketch primitives such as SlotOverall, Rectangle, Circle, Ellipse, and "
                "RegularPolygon belong inside BuildSketch on the intended plane; build the "
                "2D profile there first, then realize material from that sketch."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "choose the intended profile plane, then open `with BuildSketch(target_plane):`",
                    "move SlotOverall/Rectangle/Circle/Ellipse/RegularPolygon calls into that BuildSketch instead of dropping them directly into BuildPart",
                    "after the sketch profile is complete, use `extrude(...)`, `revolve(...)`, or another supported solid step to add or remove material",
                    "keep host edits builder-native with `mode=Mode.ADD/SUBTRACT`, or close the builder before detached booleans",
                ],
            },
        }
    if lint_ids.intersection(
        {
            "invalid_build123d_contract.member_fillet_radius_keyword_conflict",
            "invalid_build123d_contract.global_fillet_helper_argument_contract",
        }
    ):
        return {
            "recipe_id": "build123d_fillet_member_contract",
            "recipe_summary": (
                "Keep fillet calls on the verified Build123d signatures instead of "
                "mixing host-shape and edge-list arguments across the global helper "
                "and member-style contracts."
            ),
            "recipe_skeleton": {
                "mode": "local_edit_via_execute_build123d",
                "steps": [
                    "prefer the global `fillet(edge_list, radius=...)` helper when you already have a ShapeList selection from the active part",
                    "if you use member-style `solid.fillet(...)`, keep the radius in the method's expected position and pass the selected edge/edge-list on the verified contract instead of `solid.fillet(edge, radius=...)`",
                    "when a broad edge set still fails, retry with a smaller radius or a narrower edge subset rather than reusing the same invalid call shape",
                ],
            },
        }
    if "invalid_build123d_contract.broad_shell_axis_fillet_on_fresh_host" in lint_ids:
        return {
            "recipe_id": "shell_edge_fillet_postpone_contract",
            "recipe_summary": (
                "Delay broad shell-edge fillets until the enclosure/clamshell host is already "
                "valid, or narrow the fillet to a verified outer-edge subset instead of "
                "filleting every `Axis.Z` edge on the fresh host."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "build the enclosure/lid/base shell hosts and host-owned local cuts first, keeping the shell host authoritative",
                    "if the intended enclosure silhouette is rounded-rect or pillbox-like, build that rounded footprint directly with `RectangleRounded(...)` in `BuildSketch(...)` instead of using a fresh-host broad edge fillet to create the overall shape",
                    "validate that the part count, assembled pose, hinge/local cuts, and requested bbox are already stable before adding broad shell-edge fillets",
                    "if a fillet is still needed in the rebuild lane, fillet only a verified outer-edge subset instead of `builder.edges().filter_by(Axis.Z)` across the whole host",
                    "if only the finishing fillet remains after the host is valid, prefer query_topology plus a local finishing step over another broad whole-part fillet pass",
                ],
            },
        }
    if "invalid_build123d_contract.broad_local_finish_tail_fillet_on_first_write" in lint_ids:
        return {
            "recipe_id": "local_finish_fillet_postpone_contract",
            "recipe_summary": (
                "When the requirement already frames a fillet/chamfer as later local finishing, "
                "do not spend the first whole-part rebuild on a broad edge selector. Stabilize "
                "the host first, then finish that edge set from exact topology refs."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "build the primary host geometry and the directly expressible pockets, holes, and recesses first",
                    "if the fillet/chamfer is already described as a later local finish, leave it out of the first whole-part write instead of guessing a broad edge selector",
                    "do not fillet `builder.edges().filter_by(...)`, `filter_by_position(...)`, or stored broad edge sets before exact topology refs exist",
                    "once the host is stable, use query_topology to identify the exact target edges and finish that detail through the bounded local-finish lane",
                    "if a rebuild still needs a fillet, narrow the selector to a verified edge subset instead of broad whole-part edge bands",
                ],
            },
        }
    explicit_anchor_hole_countersink_recipe_lint_ids = {
        "legacy_api.countersink_workplane_method",
        "legacy_api.cut_extrude_helper",
        "invalid_build123d_api.countersink_helper_name",
        "invalid_build123d_api.workplanes_helper_name",
        "invalid_build123d_api.lowercase_hole_helper_name",
        "invalid_build123d_keyword.cone_radius_alias",
        "invalid_build123d_keyword.countersink_radius_alias",
        "invalid_build123d_keyword.countersink_head_diameter_alias",
        "invalid_build123d_keyword.countersink_through_diameter_alias",
        "invalid_build123d_keyword.countersink_angle_alias",
        "invalid_build123d_keyword.countersink_depth_alias",
        "invalid_build123d_context.countersinkhole_requires_buildpart",
        "invalid_build123d_contract.centered_box_face_plane_full_span_offset",
        "invalid_build123d_contract.named_face_plane_family_mismatch",
        "legacy_api.workplane_chain",
    }
    if lint_ids.intersection(
        {
            "invalid_build123d_api.makeface_helper_case",
            "invalid_build123d_contract.buildsketch_wire_requires_make_face",
        }
    ) and not (
        {"explicit_anchor_hole", "pattern_distribution"} & family_id_set
        and lint_ids.intersection(explicit_anchor_hole_countersink_recipe_lint_ids)
    ):
        return {
            "recipe_id": "build123d_make_face_helper_contract",
            "recipe_summary": (
                "When converting a closed `BuildLine` wire into a sketch face, use "
                "lowercase `make_face()` in the same builder context; `MakeFace()` is "
                "not a Build123d helper."
            ),
            "recipe_skeleton": {
                "mode": "local_edit_via_execute_build123d",
                "steps": [
                    "finish the closed wire inside `with BuildLine() as profile:`",
                    "call lowercase `make_face()` after the closed wire is complete",
                    "continue with `extrude(...)`, `mode=Mode.SUBTRACT`, or another builder-native solid operation from that resulting face",
                ],
            },
        }
    if lint_ids.intersection(
        {
            "invalid_build123d_context.curve_requires_buildline",
            "invalid_build123d_keyword.revolve_angle_alias",
        }
    ):
        return {
            "recipe_id": "build123d_revolve_profile_contract",
            "recipe_summary": (
                "For Build123d revolve profiles, keep curve construction inside "
                "`BuildLine`, convert the closed wire with `make_face()`, and call "
                "`revolve(...)` with the supported default or `revolution_arc=` keyword."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "open `with BuildSketch(target_plane):` on the plane containing the rotation axis",
                    "inside `BuildLine`, create the closed profile with `Polyline(...)`, `Line(...)`, and/or arc helpers",
                    "call lowercase `make_face()` after the wire closes",
                    "revolve that profile with `revolve(axis=Axis.Z)` or `revolve(axis=Axis.Z, revolution_arc=360)` instead of using `angle=`",
                ],
            },
        }
    path_sweep_signature_lint_ids = {
        "invalid_build123d_contract.explicit_radius_arc_prefers_center_arc",
        "invalid_build123d_keyword.center_arc_arc_angle_alias",
        "invalid_build123d_keyword.center_arc_end_angle_alias",
        "invalid_build123d_contract.center_arc_missing_start_angle",
    }
    path_sweep_specific_lint_ids = {
        "invalid_build123d_contract.sweep_path_wire_method_reference",
        "invalid_build123d_contract.sweep_path_line_alias",
        "invalid_build123d_keyword.sweep_section_alias",
        "invalid_build123d_keyword.solid_sweep_unsupported_keyword",
        "invalid_build123d_contract.sweep_profile_face_method_reference",
        "invalid_build123d_contract.annular_profile_face_splitting",
        "invalid_build123d_contract.annular_profile_face_extraction",
        "invalid_build123d_contract.vector_component_indexing",
        "invalid_build123d_keyword.plane_normal_alias",
    }
    if lint_ids.intersection(path_sweep_specific_lint_ids) or (
        "path_sweep" in family_ids
        and (
            lint_ids.intersection(path_sweep_signature_lint_ids)
            or "invalid_build123d_contract.builder_method_reference_assignment" in lint_ids
            or "invalid_build123d_api.symbolic_degree_constant" in lint_ids
        )
    ):
        return {
            "recipe_id": "build123d_path_sweep_contract",
            "recipe_summary": (
                "For Build123d path sweeps, keep the rail in `BuildLine`, keep the profile "
                "as a real closed section, and keep annular same-sketch sweeps on the verified "
                "Build123d API contract before escalating to more fragile split-profile lanes."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "open `with BuildLine() as path:` and build the full rail there",
                    "when the requirement gives an explicit elbow radius or quarter-turn, prefer a directly specified `CenterArc(...)` rail segment over guessed `TangentArc(...)` / `JernArc(...)` endpoint constructions",
                    "construct the profile plane with `Plane(origin=..., z_dir=path_tangent)` or an equivalent named plane; do not pass `normal=` to `Plane(...)`",
                    "if the section is one same-sketch annular profile built with an outer loop plus `mode=Mode.SUBTRACT`, treat it as one face with inner wires and prefer `sweep(profile.sketch, path=path_wire)`",
                    "do not split one subtractive annular sketch into guessed `outer_face` / `inner_face` objects by indexing `profile.faces()[1]` or similar sorted-face shortcuts",
                    "only if the annular sketch sweep already produced shell/null geometry should you rebuild truly separate outer/inner closed section faces and then compute one explicit solid boolean such as `result = outer_tube - inner_tube`",
                    "when using `Solid.sweep(...)`, stay on the verified signature such as `Solid.sweep(section_face, path_wire)` or `Solid.sweep(section=..., path=...)`; do not invent keywords like `path_wire=` or `profile_plane=`",
                    "if the requested world-space rail orientation keeps collapsing to zero-volume sweep output, rebuild the rail/profile in a stable local frame first and rotate/translate the finished tube into the target pose afterward",
                    "assign the final material solid back to `result` or `pipe.part` instead of leaving only builders in scope",
                ],
            },
        }
    if lint_ids.intersection(
        {
            "invalid_build123d_keyword.circle_arc_size",
            "invalid_build123d_keyword.center_arc_arc_angle_alias",
            "invalid_build123d_keyword.center_arc_end_angle_alias",
            "invalid_build123d_contract.center_arc_missing_start_angle",
            "invalid_build123d_contract.circle_make_face_trim_profile",
            "invalid_build123d_api.semicircle_helper_name",
            "invalid_build123d_api.symbolic_degree_constant",
        }
    ):
        return {
            "recipe_id": "build123d_arc_profile_contract",
            "recipe_summary": (
                "When a Build123d profile needs a semicircle or circular arc, use "
                "`CenterArc(...)` or `RadiusArc(...)` inside `BuildLine`; `Circle(...)` "
                "stays full-circle geometry and there is no `Semicircle(...)` helper."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "open `with BuildSketch(target_plane):` for the profile plane",
                    "inside `BuildLine`, draw the needed outer/inner `CenterArc(...)` or `RadiusArc(...)` segments",
                    "close the split edge explicitly with `Line(...)` segments and call `make_face()`",
                    "extrude the resulting closed profile instead of guessing `Circle(..., arc_size=...)` or `Semicircle(...)`",
                ],
            },
        }
    if "invalid_build123d_contract.builder_method_reference_assignment" in lint_ids:
        return {
            "recipe_id": "build123d_builder_method_reference_contract",
            "recipe_summary": (
                "When capturing geometry from a Build123d builder, call accessor methods "
                "such as `.wire()` / `.face()` instead of storing the bound method object."
            ),
            "recipe_skeleton": {
                "mode": "subtree_rebuild_via_execute_build123d",
                "steps": [
                    "identify builder-derived assignments such as `path_builder.wire` or `profile_builder.face`",
                    "call the accessor method when you need the actual geometry, for example `path_builder.wire()` or `profile_builder.face()`",
                    "if a builder-native object is already sufficient, prefer `profile_builder.sketch` or the direct builder output instead of intermediate method references",
                    "propagate the real geometry object into later sweep/revolve/boolean calls",
                ],
            },
        }
    if (
        "invalid_build123d_keyword.extrude_direction_alias" in lint_ids
        or "invalid_build123d_contract.centered_box_breaks_plane_anchored_positive_extrude"
        in lint_ids
    ):
        return {
            "recipe_id": "build123d_plane_anchored_extrude_contract",
            "recipe_summary": (
                "When the requirement explicitly says to sketch on a named plane and extrude "
                "upward/positively, preserve that plane-anchored span literally instead of "
                "switching to a default centered primitive or an unsupported extrude keyword."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "open `with BuildSketch(Plane.XY/XZ/YZ):` on the named requirement plane",
                    "draw the required square/rectangle/profile there instead of defaulting to a centered Box(...)",
                    "use `extrude(amount=...)` from that sketch, or only use a primitive-solid equivalent when its alignment/placement keeps the named plane as the lower bound",
                    "if you need a non-default extrusion direction, use the supported `dir=` keyword or change the sketch plane/orientation explicitly",
                ],
            },
        }
    if "explicit_anchor_hole" not in family_id_set and "pattern_distribution" not in family_id_set:
        return {}
    if not lint_ids.intersection(explicit_anchor_hole_countersink_recipe_lint_ids):
        return {}
    return {
        "recipe_id": "explicit_anchor_hole_countersink_array_safe_recipe",
        "recipe_summary": (
            "For countersunk hole arrays, map the point coordinates into the correct host-face "
            "frame and realize the through-hole plus countersink with an explicit same-builder "
            "subtractive recipe on the actual target face plane instead of relying on guessed "
            "helper names or a default mid-plane placement."
        ),
        "recipe_skeleton": {
            "mode": "subtree_rebuild_via_execute_build123d",
            "steps": [
                "with BuildPart() as part: ...",
                "compute the full local hole center set in the host-face frame, including any centered-host translation from corner-based sketch coordinates",
                "if the holes belong on a specific face such as the top face of a centered plate, include that face-plane translation in each placement, for example `Locations((x, y, top_z), ...)`",
                "for explicit planar countersink arrays where the requirement already gives the through-hole diameter, head diameter, and cone angle, prefer one `CounterSinkHole(...)` pass first with the exact helper contract and explicit host-face placement",
                "Only fall back to an explicit same-builder cylinder+cone or revolved countersink recipe when the helper contract cannot express the host/placement semantics cleanly or when prior validation/evaluation evidence shows the helper result is dimensionally wrong for that family",
                "if you use `CounterSinkHole(...)`, keep it in BuildPart, not BuildSketch, and keep the keyword names literal",
                "result = part.part",
            ],
        },
    }

def _preflight_lint_failure_kind(lint_hits: list[dict[str, Any]]) -> str:
    lint_ids = {
        str(item.get("rule_id") or "").strip()
        for item in lint_hits
        if isinstance(item, dict) and str(item.get("rule_id") or "").strip()
    }
    if "python_syntax.invalid_script" in lint_ids:
        return "execute_build123d_python_syntax_failure"
    return "execute_build123d_api_lint_failure"

def _requirement_mentions_explicit_cylindrical_slot(requirement_lower: str) -> bool:
    if not requirement_lower:
        return False
    slot_tokens = ("slot", "groove", "notch")
    if not any(token in requirement_lower for token in slot_tokens):
        return False
    cylindrical_tokens = (
        "cylinder",
        "cylindrical",
        "semicircular",
        "centerline",
        "axis along",
    )
    if not any(token in requirement_lower for token in cylindrical_tokens):
        return False
    return "boolean difference" in requirement_lower or "tool body" in requirement_lower

def _requirement_mentions_half_shell_with_split_surface(requirement_lower: str) -> bool:
    if not requirement_lower:
        return False
    half_shell_tokens = (
        "half-cylindrical",
        "half cylindrical",
        "half cylinder",
        "half a cylinder",
        "semi-cylindrical",
        "semi cylindrical",
        "semicylindrical",
        "half-shell",
        "half shell",
    )
    if not any(token in requirement_lower for token in half_shell_tokens):
        return False
    return any(
        token in requirement_lower
        for token in (
            "split surface",
            "split line",
            "semicircle",
            "semi-circle",
            "bearing housing",
            "bore",
            "lug",
            "flange",
        )
    )

def _requirement_mentions_plane_anchored_positive_extrude(requirement_lower: str) -> bool:
    if not requirement_lower:
        return False
    if not re.search(r"\b(?:xy|xz|yz)\s+plane\b", requirement_lower):
        return False
    if "extrude" not in requirement_lower:
        return False
    if not any(
        token in requirement_lower
        for token in ("rectangle", "square", "profile", "draw ")
    ):
        return False
    return any(
        token in requirement_lower
        for token in ("upward", "positive", "to form", "to create")
    )


__all__ = [
    "_build_preflight_repair_recipe",
    "_preflight_lint_failure_kind",
    "_requirement_mentions_explicit_cylindrical_slot",
    "_requirement_mentions_half_shell_with_split_surface",
    "_requirement_mentions_plane_anchored_positive_extrude",
]
