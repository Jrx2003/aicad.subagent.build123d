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
        title="局部坐标 countersink 板",
        narrative="先把角点坐标映射到居中宿主坐标系，再用 `Locations` 放置重复 cutter。",
        talking_points=[
            "显式坐标重映射让面局部阵列更稳定、更可解释。",
            "`Locations` 去掉了重复 placement 的样板代码。",
            "这和成功的 L2_172 运行时路径是同一种模式。",
        ],
    )


if __name__ == "__main__":
    artifact = export_demo()
    print(f"已写出 {artifact['step_path']}")
    print(artifact["narrative"])
