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
# 这里的两个孔位是 XZ 工作平面中的局部坐标 `(x, z)`，用来说明定向打孔的 frame 语义。
HOLE_CENTERS = [(-22.25, HOLE_Z), (22.25, HOLE_Z)]


def build_demo():
    with BuildPart() as housing:
        # 先做完整的外圆减内圆截面，再沿长度方向拉伸成壳体。
        with BuildSketch(Plane.XY):
            Circle(OUTER_RADIUS)
            Circle(INNER_RADIUS, mode=Mode.SUBTRACT)
        extrude(amount=SHELL_LENGTH)

        # 用同一个构建器里的 `Mode.INTERSECT` 保留半壳，而不是把裁切延后到外部布尔阶段。
        with BuildSketch(Plane.XY):
            Rectangle(2.0 * OUTER_RADIUS, OUTER_RADIUS, align=(Align.CENTER, Align.MIN))
        extrude(amount=SHELL_LENGTH, mode=Mode.INTERSECT)

        # pad 仍然留在同一个 builder 生命周期里，便于解释 shell + pad 是一个连续宿主。
        with BuildSketch(Plane.XY):
            Rectangle(PAD_WIDTH, PAD_HEIGHT, align=(Align.CENTER, Align.MIN))
            Circle(INNER_RADIUS, mode=Mode.SUBTRACT)
        extrude(amount=SHELL_LENGTH)

        # Y 向打孔必须落在 XZ 工作平面上，所以这里的局部 placement 明确是 `(x, z)`。
        with BuildSketch(Plane.XZ.offset(0)):
            with Locations(*HOLE_CENTERS):
                Circle(HOLE_DIAMETER / 2.0)
        extrude(amount=OUTER_RADIUS + PAD_HEIGHT, both=True, mode=Mode.SUBTRACT)

    return housing.part


def export_demo() -> dict[str, object]:
    # 这个 demo 的讲解重点是 builder-native boolean 和定向 workplane，而不是半壳造型本身。
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
