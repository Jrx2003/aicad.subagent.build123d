from __future__ import annotations

from build123d import (
    Align,
    Box,
    BuildPart,
    Cone,
    Cylinder,
    Locations,
    Mode,
    Pos,
    add,
)

from common import export_artifact

BODY_X = 80.0
BODY_Y = 50.0
BODY_Z = 35.0
WALL = 3.0
LID_THICKNESS = 3.0
LIP_HEIGHT = 8.0
FASTENER_POINTS = [
    (-24.0, -14.0, LID_THICKNESS),
    (24.0, -14.0, LID_THICKNESS),
    (24.0, 14.0, LID_THICKNESS),
    (-24.0, 14.0, LID_THICKNESS),
]


def build_body():
    with BuildPart() as body:
        Box(BODY_X, BODY_Y, BODY_Z, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with BuildPart(mode=Mode.PRIVATE) as cavity:
            Box(
                BODY_X - 2.0 * WALL,
                BODY_Y - 2.0 * WALL,
                BODY_Z - WALL,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
            )
        add(cavity.part.locate(Pos(0, 0, WALL)), mode=Mode.SUBTRACT)
    return body.part


def build_lid():
    with BuildPart() as lid:
        Box(
            BODY_X, BODY_Y, LID_THICKNESS, align=(Align.CENTER, Align.CENTER, Align.MIN)
        )
        with BuildPart(mode=Mode.PRIVATE) as lip:
            Box(
                BODY_X - 2.0 * WALL,
                BODY_Y - 2.0 * WALL,
                LIP_HEIGHT,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
            )
            Box(
                BODY_X - 2.0 * WALL - 4.0,
                BODY_Y - 2.0 * WALL - 4.0,
                LIP_HEIGHT,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
                mode=Mode.SUBTRACT,
            )
        add(lip.part.locate(Pos(0, 0, -LIP_HEIGHT)), mode=Mode.ADD)

        with Locations(*FASTENER_POINTS):
            Cylinder(
                2.0,
                LID_THICKNESS + 2.0,
                align=(Align.CENTER, Align.CENTER, Align.MAX),
                mode=Mode.SUBTRACT,
            )
            Cone(
                4.5,
                2.0,
                2.5,
                align=(Align.CENTER, Align.CENTER, Align.MAX),
                mode=Mode.SUBTRACT,
            )

    return lid.part


def export_demo() -> list[dict[str, object]]:
    return [
        export_artifact(
            "demo_03_enclosure_body",
            build_body(),
            title="壳体本体",
            narrative="先把 cavity 作为 `Mode.PRIVATE` 的暂存零件构建出来，等外部宿主尺寸稳定后再统一减掉。",
            talking_points=[
                "`Mode.PRIVATE` 可以避免在暂存 cavity 时提前污染宿主。",
                "本体继续保留 opening-face 语义，而不是依赖 selector 很重的 shell 编辑。",
                "这和运行时对简单 shelled box 优先使用显式 inner-solid subtraction 的偏好一致。",
            ],
        ),
        export_artifact(
            "demo_03_enclosure_lid",
            build_lid(),
            title="lip-fit 盖子与沉头紧固孔",
            narrative="把 lid plate 和 lip 作为两个显式实体组织，再从顶部坐标系切出沉头紧固孔。",
            talking_points=[
                "lip 是一个显式的环形结构，而不是隐式 shell 副作用。",
                "紧固孔之所以更易读，是因为 placement 和 subtraction 被明确拆开了。",
                "这更接近外部 enclosure 实验背后真正需要的 Build123d 建模模式。",
            ],
        ),
    ]


if __name__ == "__main__":
    for artifact in export_demo():
        print(f"已写出 {artifact['step_path']}")
        print(artifact["narrative"])
