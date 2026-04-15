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
# 四个紧固孔都以 lid 顶面坐标系为基准，便于和 body/lid 生命周期一起讲。
FASTENER_POINTS = [
    (-24.0, -14.0, LID_THICKNESS),
    (24.0, -14.0, LID_THICKNESS),
    (24.0, 14.0, LID_THICKNESS),
    (-24.0, 14.0, LID_THICKNESS),
]


def build_body():
    with BuildPart() as body:
        # 先把外部宿主稳定下来，body 采用显式 inner-solid subtraction，而不是依赖隐式 shell 副作用。
        Box(BODY_X, BODY_Y, BODY_Z, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with BuildPart(mode=Mode.PRIVATE) as cavity:
            # cavity 先作为暂存几何存在，等到外部 host 完整后再统一减掉。
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
        # lid plate 先单独成立，再把 lip 作为第二层显式几何并入。
        Box(
            BODY_X, BODY_Y, LID_THICKNESS, align=(Align.CENTER, Align.CENTER, Align.MIN)
        )
        with BuildPart(mode=Mode.PRIVATE) as lip:
            # 这里不是调用一个抽象的“lip-fit”黑盒，而是把 lip 环形结构明确做出来。
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

        # 紧固孔和沉头锥体共享同一组显式 placement，便于解释“坐标”与“减料”如何拆开。
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
    # 这个 demo 同时导出 body 和 lid，两者分开看比合成一个零件更适合讲容器改造。
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
