"""NURBSmith thin-wall test part: an open tray with walls of three thicknesses, a low
partition and a tube open at both ends.

Prints open side up with no supports, about 80 x 60 x 24 mm. Every opening is shallower
than it is wide (the tray is 22 deep over 56, the tube 12 deep over 15), so a turntable
scanner can see the inside walls and the floor. Scan it upright and upside down, then
convert and compare against the STEP this writes (see FEATURES).

    backend/.venv/bin/python tools/thin_wall_part.py out_dir
"""

import json
import sys
from pathlib import Path

import cadquery as cq

L, W, H = 80.0, 60.0, 24.0  # outside of the tray
FLOOR = 2.0
WALL_LEFT, WALL_RIGHT, WALL_SIDES = 1.2, 3.0, 2.0  # -X, +X, and the two Y walls

# name, kind, nominal values (mm), where (x, y from the tray centre)
FEATURES = []


def note(name, kind, where, **dims):
    FEATURES.append({"name": name, "kind": kind, "at_mm": where, **dims})


def build() -> cq.Workplane:
    part = cq.Workplane("XY").box(L, W, H, centered=(True, True, False))
    part = part.edges("|Z").fillet(4.0)
    note("tray outside", "planes", (0, 0), size=[L, W, H], corner_radius=4.0)

    x0, x1 = -L / 2 + WALL_LEFT, L / 2 - WALL_RIGHT
    inside = (
        cq.Workplane("XY").workplane(offset=FLOOR)
        .center((x0 + x1) / 2, 0).rect(x1 - x0, W - 2 * WALL_SIDES).extrude(H)
        .edges("|Z").fillet(2.0)
    )  # fmt: skip
    part = part.cut(inside)
    note("floor", "thin wall", (0, 0), thickness=FLOOR)
    note("left wall (-X)", "thin wall", (-L / 2, 0), thickness=WALL_LEFT, height=H)
    note("right wall (+X)", "thin wall", (L / 2, 0), thickness=WALL_RIGHT, height=H)
    note("side walls (+Y, -Y)", "thin wall", (0, W / 2), thickness=WALL_SIDES, height=H)
    note("open top: the walls' top edges are the rim", "open end", (0, 0), depth=H - FLOOR,
         opening=[x1 - x0, W - 2 * WALL_SIDES])  # fmt: skip

    # Low partition across the tray, joined to both side walls.
    part = part.union(
        cq.Workplane("XY").workplane(offset=FLOOR).center(-12, 0)
        .rect(1.6, W - 2 * WALL_SIDES + 0.2).extrude(14.0)
    )  # fmt: skip
    note("partition", "thin wall", (-12, 0), thickness=1.6, height=14.0)

    # Tube standing on the floor, open at the top and through the floor.
    tube = (
        cq.Workplane("XY")
        .workplane(offset=FLOOR)
        .center(16, 0)
        .circle(10.0)
        .extrude(12.0)
    )
    part = part.union(tube)
    bore = cq.Workplane("XY").center(16, 0).circle(7.5).extrude(H)
    part = part.cut(bore)
    note("tube, open both ends", "thin wall (round)", (16, 0), outer_diameter=20.0,
         inner_diameter=15.0, height_above_floor=12.0)  # fmt: skip

    return part


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    part = build()
    solid = part.val()
    assert solid.isValid()
    cq.exporters.export(part, str(out / "nurbsmith_thin_wall.step"))
    cq.exporters.export(part, str(out / "nurbsmith_thin_wall.stl"), tolerance=0.01,
                        angularTolerance=0.05)  # fmt: skip
    (out / "nurbsmith_thin_wall.json").write_text(json.dumps(FEATURES, indent=2))
    bb = solid.BoundingBox()
    print(
        f"size {bb.xlen:.1f} x {bb.ylen:.1f} x {bb.zlen:.1f} mm, volume {solid.Volume():.0f}"
    )
    print(len(FEATURES), "features")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "."))
