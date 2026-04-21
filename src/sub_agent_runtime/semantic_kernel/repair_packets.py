from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sub_agent_runtime.semantic_kernel.models import FamilyRepairPacket


@dataclass(frozen=True, slots=True)
class RepairPacketRecipeSpec:
    recipe_id: str
    supported_family_ids: tuple[str, ...]
    required_anchor_keys: tuple[str, ...]
    required_parameters: tuple[str, ...]
    compiler_kind: str
    can_execute_deterministically: bool
    fallback_lane: str


_RUNTIME_REPAIR_PACKET_RECIPES: dict[str, RepairPacketRecipeSpec] = {
    "half_shell_profile_global_xz_lug_hole_recipe": RepairPacketRecipeSpec(
        recipe_id="half_shell_profile_global_xz_lug_hole_recipe",
        supported_family_ids=("half_shell_profile", "directional_hole", "axisymmetric_profile"),
        required_anchor_keys=(),
        required_parameters=(),
        compiler_kind="half_shell_profile_global_xz_lug_hole",
        can_execute_deterministically=True,
        fallback_lane="execute_build123d",
    ),
    "spherical_recess_host_face_center_set": RepairPacketRecipeSpec(
        recipe_id="spherical_recess_host_face_center_set",
        supported_family_ids=("spherical_recess", "explicit_anchor_hole"),
        required_anchor_keys=("expected_local_centers",),
        required_parameters=("geometry_summary",),
        compiler_kind="spherical_recess_host_face_center_set",
        can_execute_deterministically=True,
        fallback_lane="execute_build123d",
    ),
    "explicit_anchor_hole_centered_host_frame_array": RepairPacketRecipeSpec(
        recipe_id="explicit_anchor_hole_centered_host_frame_array",
        supported_family_ids=("explicit_anchor_hole",),
        required_anchor_keys=("normalized_local_centers",),
        required_parameters=("geometry_summary",),
        compiler_kind="explicit_anchor_hole_host_face_array",
        can_execute_deterministically=True,
        fallback_lane="execute_build123d",
    ),
    "explicit_anchor_hole_local_anchor_array": RepairPacketRecipeSpec(
        recipe_id="explicit_anchor_hole_local_anchor_array",
        supported_family_ids=("explicit_anchor_hole",),
        required_anchor_keys=("requested_centers",),
        required_parameters=("geometry_summary",),
        compiler_kind="explicit_anchor_hole_host_face_array",
        can_execute_deterministically=True,
        fallback_lane="execute_build123d",
    ),
}


def _recipe_contract_payload(
    recipe_spec: RepairPacketRecipeSpec | None,
) -> dict[str, Any] | None:
    if recipe_spec is None:
        return None
    return {
        "recipe_id": recipe_spec.recipe_id,
        "supported_family_ids": list(recipe_spec.supported_family_ids),
        "required_anchor_keys": list(recipe_spec.required_anchor_keys),
        "required_parameters": list(recipe_spec.required_parameters),
        "compiler_kind": recipe_spec.compiler_kind,
        "can_execute_deterministically": recipe_spec.can_execute_deterministically,
        "fallback_lane": recipe_spec.fallback_lane,
    }


def describe_runtime_repair_packet_support(
    packet: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(packet, dict):
        return {
            "recipe_id": None,
            "runtime_supported": False,
            "support_reason": "missing_packet",
            "recipe_contract": None,
        }
    recipe_id = str(packet.get("recipe_id") or "").strip()
    if not recipe_id:
        return {
            "recipe_id": None,
            "runtime_supported": False,
            "support_reason": "missing_recipe_id",
            "recipe_contract": None,
        }
    recipe_spec = _RUNTIME_REPAIR_PACKET_RECIPES.get(recipe_id)
    recipe_contract = _recipe_contract_payload(recipe_spec)
    return {
        "recipe_id": recipe_id,
        "runtime_supported": bool(
            recipe_spec is not None and recipe_spec.can_execute_deterministically
        ),
        "support_reason": "supported_recipe"
        if recipe_spec is not None
        else "unsupported_recipe",
        "recipe_contract": recipe_contract,
    }


def supports_runtime_repair_packet(packet: dict[str, Any] | None) -> bool:
    return bool(describe_runtime_repair_packet_support(packet).get("runtime_supported"))


def select_preferred_repair_packet(
    packets: dict[str, FamilyRepairPacket] | None,
    *,
    packet_id: str | None = None,
) -> FamilyRepairPacket | None:
    if not isinstance(packets, dict) or not packets:
        return None
    normalized_packet_id = str(packet_id or "").strip()
    if normalized_packet_id:
        packet = packets.get(normalized_packet_id)
        if packet is not None and not bool(getattr(packet, "stale", False)):
            return packet

    active_packets = [
        packet
        for packet in packets.values()
        if not bool(getattr(packet, "stale", False))
    ]
    if not active_packets:
        return None

    for packet in reversed(active_packets):
        packet_payload = packet.to_dict() if hasattr(packet, "to_dict") else None
        if supports_runtime_repair_packet(packet_payload):
            return packet
    for packet in reversed(active_packets):
        if str(getattr(packet, "recipe_id", "") or "").strip():
            return packet
    return active_packets[-1]


__all__ = [
    "RepairPacketRecipeSpec",
    "describe_runtime_repair_packet_support",
    "select_preferred_repair_packet",
    "supports_runtime_repair_packet",
]
