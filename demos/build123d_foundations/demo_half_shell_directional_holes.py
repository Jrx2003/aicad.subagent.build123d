from __future__ import annotations

from build123d import (
    Align,
    BuildPart,
    BuildSketch,
    Circle,
    Locations,
    Mode,
    Plane,
    Rectangle,
    extrude,
)

from common import export_artifact

SHELL_LENGTH = 40.0
OUTER_RADIUS = 25.0
INNER_RADIUS = 17.5
PAD_WIDTH = 54.0
PAD_HEIGHT = 8.0
HOLE_DIAMETER = 6.0
HOLE_Z = 20.0
HOLE_CENTERS = [(-22.25, HOLE_Z), (22.25, HOLE_Z)]


def build_demo():
    with BuildPart() as housing:
        with BuildSketch(Plane.XY):
            Circle(OUTER_RADIUS)
            Circle(INNER_RADIUS, mode=Mode.SUBTRACT)
        extrude(amount=SHELL_LENGTH)

        with BuildSketch(Plane.XY):
            Rectangle(2.0 * OUTER_RADIUS, OUTER_RADIUS, align=(Align.CENTER, Align.MIN))
        extrude(amount=SHELL_LENGTH, mode=Mode.INTERSECT)

        with BuildSketch(Plane.XY):
            Rectangle(PAD_WIDTH, PAD_HEIGHT, align=(Align.CENTER, Align.MIN))
            Circle(INNER_RADIUS, mode=Mode.SUBTRACT)
        extrude(amount=SHELL_LENGTH)

        with BuildSketch(Plane.XZ.offset(0)):
            with Locations(*HOLE_CENTERS):
                Circle(HOLE_DIAMETER / 2.0)
        extrude(amount=OUTER_RADIUS + PAD_HEIGHT, both=True, mode=Mode.SUBTRACT)

    return housing.part


def export_demo() -> dict[str, object]:
    return export_artifact(
        "demo_02_half_shell_directional_holes",
        build_demo(),
        title="Half-shell with directional holes",
        narrative=(
            "Keep the half-shell, pad, and lug drilling in one builder so subtract/intersect "
            "semantics stay explicit and lintable."
        ),
        talking_points=[
            "Mode.INTERSECT gives a clean same-builder half-shell instead of fragile post-hoc trimming.",
            "Plane.XZ.offset(0) keeps the drilling frame honest: local coordinates are (x, z), not (x, y).",
            "This is the contract area exposed by the L2_130 repair work.",
        ],
    )


if __name__ == "__main__":
    artifact = export_demo()
    print(f"Wrote {artifact['step_path']}")
    print(artifact["narrative"])
