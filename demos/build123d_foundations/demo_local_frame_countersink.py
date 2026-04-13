from __future__ import annotations

from build123d import Align, Box, BuildPart, Cone, Cylinder, Locations, Mode

from common import export_artifact

PLATE_X = 100.0
PLATE_Y = 60.0
PLATE_Z = 8.0
HOLE_DIAMETER = 6.0
COUNTERSINK_DIAMETER = 12.0
COUNTERSINK_DEPTH = (COUNTERSINK_DIAMETER - HOLE_DIAMETER) / 2.0
CORNER_FRAME_POINTS = [(25.0, 15.0), (25.0, 45.0), (75.0, 15.0), (75.0, 45.0)]


def corner_to_centered(x: float, y: float) -> tuple[float, float, float]:
    return (x - PLATE_X / 2.0, y - PLATE_Y / 2.0, PLATE_Z / 2.0)


def build_demo():
    hole_centers = [corner_to_centered(x, y) for x, y in CORNER_FRAME_POINTS]
    with BuildPart() as plate:
        Box(PLATE_X, PLATE_Y, PLATE_Z)
        with Locations(*hole_centers):
            Cylinder(
                HOLE_DIAMETER / 2.0,
                PLATE_Z + 2.0,
                align=(Align.CENTER, Align.CENTER, Align.MAX),
                mode=Mode.SUBTRACT,
            )
            Cone(
                COUNTERSINK_DIAMETER / 2.0,
                HOLE_DIAMETER / 2.0,
                COUNTERSINK_DEPTH,
                align=(Align.CENTER, Align.CENTER, Align.MAX),
                mode=Mode.SUBTRACT,
            )
    return plate.part


def export_demo() -> dict[str, object]:
    return export_artifact(
        "demo_01_local_frame_countersink",
        build_demo(),
        title="Local frame countersink plate",
        narrative=(
            "Translate corner-based sketch coordinates into the centered host frame, "
            "then place repeated cutters with Locations."
        ),
        talking_points=[
            "Explicit coordinate remapping makes face-local arrays deterministic.",
            "Locations removes repeated placement boilerplate from the model code.",
            "The same pattern matches the successful L2_172 runtime path.",
        ],
    )


if __name__ == "__main__":
    artifact = export_demo()
    print(f"Wrote {artifact['step_path']}")
    print(artifact["narrative"])
