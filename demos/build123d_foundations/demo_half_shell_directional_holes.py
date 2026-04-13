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
        title="半壳体与定向打孔",
        narrative="把半壳、底部 pad 和侧向打孔保持在同一个构建器里，让减料与相交语义始终显式可见。",
        talking_points=[
            "`Mode.INTERSECT` 能在同一构建器里干净地保留半壳，而不是依赖脆弱的后处理裁切。",
            "`Plane.XZ.offset(0)` 能保证打孔 frame 语义正确：局部坐标是 `(x, z)`，不是 `(x, y)`。",
            "这正是 L2_130 最近暴露和收紧的契约区域。",
        ],
    )


if __name__ == "__main__":
    artifact = export_demo()
    print(f"已写出 {artifact['step_path']}")
    print(artifact["narrative"])
