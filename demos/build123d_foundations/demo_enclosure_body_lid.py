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
            title="Shelled enclosure body",
            narrative=(
                "Stage the cavity as a PRIVATE part and subtract it only after the outer host dimensions are stable."
            ),
            talking_points=[
                "Mode.PRIVATE avoids mutating the host while staging cavity geometry.",
                "The body keeps the opening-face semantics explicit instead of relying on selector-heavy shell edits.",
                "This mirrors the runtime preference for explicit inner-solid subtraction on simple shelled boxes.",
            ],
        ),
        export_artifact(
            "demo_03_enclosure_lid",
            build_lid(),
            title="Lip-fit lid with countersunk fasteners",
            narrative=(
                "Build the lid plate and lip as separate solids, then add countersunk holes from the top frame."
            ),
            talking_points=[
                "The lip is an explicit ring, not an implicit shell side effect.",
                "Fastener placement stays readable because placement and subtraction are decoupled.",
                "This is the clean Build123d pattern behind the external enclosure experiment work.",
            ],
        ),
    ]


if __name__ == "__main__":
    for artifact in export_demo():
        print(f"Wrote {artifact['step_path']}")
        print(artifact["narrative"])
