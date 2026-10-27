"""MeshToMod test prints for M2.5 to M5, designed to be printed in one batch.

Each part has known dimensions (FEATURES, written to <name>.json beside the STL and STEP).
All of them print with no supports on a 0.4 mm nozzle, and no opening is deeper than it is
wide, so a turntable scanner can see into each one.

    backend/.venv/bin/python tools/batch_prints.py out_dir

Parts, and what each one is for:
- turned_part: a lathe-style part with steps, a chamfer, a groove, a cone, a ball end and a
  cross hole (cones, spheres, and the turned part asked for earlier). Stands upright.
- surfaces_plate: countersunk holes, a dome, a tapered boss and a ring with a full round on
  top (cones, spheres and tori on a flat part).
- freeform_top: a smooth lofted top on a flat base, like a mouse shell (freeform surfaces,
  and merging a top and bottom scan of a curved part).
- wave_skin: a 2 mm wall bent into an S curve, standing up (Thicken skin: scan one face).
- peg_and_base: a base with a socket and a separate post whose peg fits it (Combine scans:
  scan each piece, combine, compare to the assembled design).
- size_gauge: metric and inch holes in steps, and a staircase of known heights (hole size
  snapping, the Precise level, inch input).
"""

import json
import math
import sys
from pathlib import Path

import cadquery as cq
import trimesh

INCH = 25.4

PARTS = {}


def part(fn):
    PARTS[fn.__name__] = fn
    return fn


def note(features, name, kind, where, **dims):
    features.append({"name": name, "kind": kind, "at_mm": where, **dims})


@part
def turned_part(f):
    """Revolved about Z, standing on its biggest end. 30 mm across, 44 mm tall."""
    # (radius, z) profile, bottom to top, then closed down the axis.
    pts = [
        (0, 0), (14, 0), (15, 1),           # Ø30 base with a 1 mm chamfer at the bottom
        (15, 8), (10, 8),                    # step down to Ø20
        (10, 14), (8, 14), (8, 15), (10, 17),  # groove to Ø16, upper flank 45° so it prints
        (10, 24),                            # Ø20 shaft
        (6, 32),                             # cone to Ø12 over 8 mm
        (6, 38),                             # Ø12 neck
    ]  # fmt: skip
    prof = cq.Workplane("XZ").polyline(pts).lineTo(0, 38).close()
    body = prof.revolve(360, (0, 0, 0), (0, 1, 0))
    ball = cq.Workplane("XY").workplane(offset=38).sphere(6.0)
    body = body.union(
        ball.intersect(cq.Workplane("XY").workplane(offset=38).circle(7).extrude(7))
    )
    cross = (
        cq.Workplane("YZ").workplane(offset=-20).center(0, 20).circle(2.0).extrude(40)
    )
    body = body.cut(cross)
    note(f, "base", "cylinder", (0, 0), diameter=30.0, height=8.0, bottom_chamfer=1.0)
    note(f, "shaft", "cylinder", (0, 0), diameter=20.0, z=[8, 24])
    note(f, "groove", "cylinder", (0, 0), diameter=16.0, z=[14, 15],
         flank="45 degree cone from z 15 to 17")  # fmt: skip
    note(
        f,
        "cone",
        "cone",
        (0, 0),
        diameters=[20.0, 12.0],
        z=[24, 32],
        half_angle_deg=26.57,
    )
    note(f, "neck", "cylinder", (0, 0), diameter=12.0, z=[32, 38])
    note(f, "ball end", "sphere", (0, 0), radius=6.0, centre_z=38.0)
    note(f, "cross hole (along X)", "hole", (0, 0), diameter=4.0, z=20.0, through=True)
    return body


@part
def surfaces_plate(f):
    """70 x 50 x 6 plate with curved features other than cylinders."""
    T = 6.0
    p = cq.Workplane("XY").box(70, 50, T, centered=(True, True, False))
    # 90 degree countersinks for M3 and M5 flat heads.
    p = (
        p.faces(">Z").workplane()
        .pushPoints([(-27, 15)]).cskHole(3.4, 6.5, 90)
        .pushPoints([(-27, -12)]).cskHole(5.5, 10.4, 90)
    )  # fmt: skip
    note(
        f,
        "countersunk hole, M3",
        "countersink",
        (-27, 15),
        diameter=3.4,
        head=6.5,
        angle=90,
    )
    note(f, "countersunk hole, M5", "countersink", (-27, -12), diameter=5.5, head=10.4,
         angle=90)  # fmt: skip
    # Spherical dome: a cap of a 20 mm radius sphere, 6 mm high, 30.2 mm across.
    dome = cq.Workplane("XY").workplane(offset=T - 14).center(-2, 0).sphere(20.0)
    dome = dome.intersect(
        cq.Workplane("XY").workplane(offset=T).center(-2, 0).circle(16).extrude(7)
    )
    p = p.union(dome)
    note(f, "dome", "sphere", (-2, 0), radius=20.0, height=6.0)
    # Tapered boss: Ø16 at the plate to Ø10 at the top, 8 mm tall.
    cone = cq.Solid.makeCone(8.0, 5.0, 8.0, cq.Vector(24, 13, T))
    p = p.union(cq.Workplane().add(cone))
    note(f, "tapered boss", "cone", (24, 13), diameters=[16.0, 10.0], height=8.0)
    # Ring: Ø20 x 5 tall with a Ø10 bore through, and a 2 mm full round on both top edges.
    ring = (
        cq.Workplane("XY").workplane(offset=T).center(24, -12)
        .circle(10).circle(5).extrude(5).faces(">Z").edges().fillet(2.0)
    )  # fmt: skip
    p = p.union(ring)
    p = p.cut(cq.Workplane("XY").center(24, -12).circle(5).extrude(20))
    note(f, "ring with rounded top", "torus", (24, -12), outer=20.0, bore=10.0, height=5.0,
         round_radius=2.0)  # fmt: skip
    return p


@part
def freeform_top(f):
    """80 x 50 base, flat underneath, with a smooth top rising to 18 mm (like a mouse)."""
    # Cross-sections across X at several Y; each is flat on the bottom with a spline top.
    sections = []
    for y, (edge, mid, peak_x) in zip(
        (-25, -12.5, 0, 12.5, 25),
        ((3, 6, 5), (4, 13, 8), (5, 18, 10), (4, 14, 8), (3, 7, 5)),
        strict=True,
    ):
        w = (
            cq.Workplane("XZ", origin=(0, y, 0))
            .moveTo(-40, 0).lineTo(40, 0).lineTo(40, edge)
            .spline([(peak_x, mid), (-40, edge)], includeCurrent=True)
            .close()
        )  # fmt: skip
        sections.append(w.val())
    body = cq.Solid.makeLoft(sections, False)
    note(f, "base", "plane", (0, 0), size=[80, 50])
    note(f, "top", "freeform", (10, 0), peak_height=18.0)
    return cq.Workplane().add(body)


@part
def wave_skin(f):
    """A 2 mm wall bent into an S, 40 mm tall, standing on its edge."""
    # Centre line y = 12 sin(pi x / 35), offset 1 mm to each side along its normal.
    xs = [-35 + 70 * i / 40 for i in range(41)]
    sides = ([], [])
    for x in xs:
        y = 12 * math.sin(math.pi * x / 35)
        slope = 12 * math.pi / 35 * math.cos(math.pi * x / 35)
        n = math.hypot(1, slope)
        for side, sign in zip(sides, (1, -1), strict=True):
            side.append((x - sign * slope / n, y + sign / n))
    outline = (
        cq.Workplane("XY").moveTo(*sides[0][0]).spline(sides[0][1:], includeCurrent=True)
        .lineTo(*sides[1][-1]).spline(sides[1][-2::-1], includeCurrent=True).close()
    )  # fmt: skip
    wall = outline.extrude(40.0)
    note(f, "skin", "thin curved wall", (0, 0), thickness=2.0, height=40.0, length_x=70.0,
         amplitude=12.0)  # fmt: skip
    return wall


@part
def peg_and_base(f):
    """Two pieces, printed apart. The assembled design is written as peg_and_base_assembled."""
    base = cq.Workplane("XY").box(50, 30, 8, centered=(True, True, False))
    base = base.edges("|Z").fillet(3.0)
    base = base.faces(">Z").workplane().center(10, 0).hole(8.6, 6.0)
    post = (
        cq.Workplane("XY")
        .circle(8.0)
        .extrude(20)
        .faces(">Z")
        .workplane()
        .circle(4.0)
        .extrude(6)
    )
    post = post.faces(">Z").edges().chamfer(0.5)
    # Printed: the post stands on its Ø16 end beside the base.
    printed = base.union(post.translate((-10 - 30, 0, 0)))
    note(f, "base", "planes", (0, 0), size=[50, 30, 8], corner_radius=3.0)
    note(f, "socket", "hole", (10, 0), diameter=8.6, depth=6.0)
    note(f, "post (separate piece)", "cylinder", (-40, 0), diameter=16.0, height=20.0)
    note(f, "peg", "cylinder", (-40, 0), diameter=8.0, length=6.0, end_chamfer=0.5)
    assembled = base.union(
        post.rotate((0, 0, 0), (1, 0, 0), 180).translate((10, 0, 8 + 20))
    )
    note(f, "assembled: post upside down, peg in the socket, post face on the base top",
         "assembly", (10, 0), height=28.0)  # fmt: skip
    return printed, assembled


@part
def size_gauge(f):
    """100 x 40 plate, 4 mm thick, with two rows of through holes and a staircase."""
    p = cq.Workplane("XY").box(100, 40, 4, centered=(True, True, False))
    metric = [2, 3, 4, 5, 6, 7, 8, 9, 10]
    xs = [-44 + i * 8.5 + sum(metric[:i]) * 0.5 for i in range(len(metric))]
    p = p.faces(">Z").workplane()
    for x, d in zip(xs, metric, strict=True):
        p = p.pushPoints([(x, 10)]).hole(d)
    note(f, "metric holes", "holes", (0, 10), diameters=metric)
    inch = [1 / 8, 3 / 16, 1 / 4, 5 / 16, 3 / 8, 1 / 2]
    xs = [-42 + i * 10 + sum(inch[:i]) * INCH * 0.5 for i in range(len(inch))]
    for x, d in zip(xs, inch, strict=True):
        p = p.pushPoints([(x, -6)]).hole(d * INCH)
    note(f, "inch holes", "holes", (0, -6), diameters_in=["1/8", "3/16", "1/4", "5/16", "3/8",
         "1/2"], diameters_mm=[round(d * INCH, 3) for d in inch])  # fmt: skip
    # Staircase along the -Y edge: steps 1, 2, 4 and 8 mm above the plate, 12 mm wide each.
    for i, h in enumerate([1, 2, 4, 8]):
        step = (
            cq.Workplane("XY")
            .workplane(offset=4)
            .center(26 + i * 6, -15.5)
            .rect(6, 9)
            .extrude(h)
        )
        p = p.union(step)
    note(f, "steps", "planes", (35, -15.5), heights_above_plate=[1, 2, 4, 8], width=6)
    return p


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, build in PARTS.items():
        features = []
        made = build(features)
        shapes = {name: made} if not isinstance(made, tuple) else {
            name: made[0], f"{name}_assembled": made[1]}  # fmt: skip
        for stem, shape in shapes.items():
            solid = shape.val() if isinstance(shape, cq.Workplane) else shape
            assert solid.isValid(), stem
            cq.exporters.export(shape, str(out / f"meshtomod_{stem}.step"))
            stl = out / f"meshtomod_{stem}.stl"
            cq.exporters.export(shape, str(stl), tolerance=0.01, angularTolerance=0.05)
            # The exporter leaves a zero-area sliver at a sphere's pole; drop it.
            mesh = trimesh.load(stl)
            mesh.merge_vertices()
            mesh.update_faces(mesh.nondegenerate_faces())
            mesh.remove_unreferenced_vertices()
            assert mesh.is_watertight, stem
            mesh.export(stl)
            bb = solid.BoundingBox()
            print(f"{stem}: {bb.xlen:.1f} x {bb.ylen:.1f} x {bb.zlen:.1f} mm, "
                  f"{solid.Volume() / 1000:.1f} cm3, solids {len(solid.Solids())}")  # fmt: skip
        (out / f"meshtomod_{name}.json").write_text(json.dumps(features, indent=2))


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "."))
