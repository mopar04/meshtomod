"""Sink and extrude: the user's sketch cut into or added onto the converted solid, square to
the surface under it, with a flat floor or top."""

import cadquery as cq
import numpy as np
import pytest

from meshtomod import fixtures as fx
from meshtomod.brep import UnsupportedGeometry
from meshtomod.sink import Edit, Sketch, apply_edit
from meshtomod.step_io import face_type_counts


def box():
    return cq.Solid.makeBox(40, 30, 20)  # top face at z = 20, centre (20, 15)


def flat_faces(solid, normal):
    """Planar faces facing along normal, by height along it."""
    normal = np.asarray(normal, float)
    out = {}
    for f in solid.Faces():
        if f.geomType() == "PLANE" and np.array(f.normalAt().toTuple()) @ normal > 1 - 1e-9:
            out[round(float(np.array(f.Center().toTuple()) @ normal), 6)] = f
    return out


@pytest.mark.parametrize(
    "sketch,area",
    [
        (Sketch("rectangle", 10, 6), 60.0),
        (Sketch("rectangle", 20, 4, angle_deg=90), 80.0),
        (Sketch("rounded_rectangle", 10, 6, corner_radius_mm=2), 60 - (4 - np.pi) * 4),
        (Sketch("circle", 10), np.pi * 25),
        (Sketch("slot", 20, 6), 14 * 6 + np.pi * 9),
    ],
)
def test_sink_on_a_flat_face(sketch, area):
    part = box()
    result = apply_edit(part, Edit("sink", (20, 15, 20), sketch, 3))
    assert result.solid.isValid()
    assert part.Volume() - result.solid.Volume() == pytest.approx(3 * area, rel=1e-6)
    assert result.normal == pytest.approx([0, 0, 1], abs=1e-12)
    # A flat floor 3 mm down, square to the face.
    floor = flat_faces(result.solid, [0, 0, 1])[17.0]
    assert floor.Area() == pytest.approx(area, rel=1e-6)


def test_sketch_turns_about_the_normal():
    result = apply_edit(box(), Edit("sink", (20, 15, 20), Sketch("rectangle", 20, 4, 0, 90), 3))
    bb = flat_faces(result.solid, [0, 0, 1])[17.0].BoundingBox()
    assert [bb.xlen, bb.ylen] == pytest.approx([4, 20], abs=1e-9)


def test_extrude_on_a_side_face():
    part = box()
    result = apply_edit(part, Edit("extrude", (40, 15, 10), Sketch("circle", 8), 5))
    assert result.solid.isValid()
    assert result.solid.Volume() - part.Volume() == pytest.approx(np.pi * 16 * 5, rel=1e-6)
    assert face_type_counts(result.solid) == {"PLANE": 7, "CYLINDER": 1}
    top = flat_faces(result.solid, [1, 0, 0])[45.0]
    assert top.Center().toTuple() == pytest.approx((45, 15, 10), abs=1e-6)


@pytest.mark.parametrize("mode", ["sink", "extrude"])
def test_on_a_curved_face_the_floor_or_top_is_flat_and_clear(mode):
    # A 10 mm square on a Ø40 cylinder's side: the surface under it drops 0.635 mm at its
    # edges, so the sink's floor is 2 mm below that, and the extrusion's top 2 mm above the
    # crest.
    cylinder = cq.Solid.makeCylinder(20, 40)
    result = apply_edit(cylinder, Edit(mode, (20, 0, 20), Sketch("rectangle", 10, 10), 2))
    assert result.solid.isValid()
    sag = 20 - np.sqrt(20**2 - 5**2)
    assert result.surface_mm == pytest.approx((-sag, 0), abs=0.02)
    assert result.normal == pytest.approx([1, 0, 0], abs=0.003)
    level = 20 - sag - 2 if mode == "sink" else 22
    face = max(
        (
            f
            for f in result.solid.Faces()
            if f.geomType() == "PLANE" and np.array(f.normalAt().toTuple()) @ [1, 0, 0] > 0.999
        ),
        key=lambda f: f.Area(),
    )
    assert face.Area() == pytest.approx(100, rel=1e-3)
    assert face.Center().x == pytest.approx(level, abs=0.03)


def test_extrude_on_a_freeform_top():
    # The mouse-like lofted top: a round pad's top comes out flat however the surface curves.
    part = fx.freeform_top_cad().val()
    bb = part.BoundingBox()
    point = ((bb.xmin + bb.xmax) / 2, (bb.ymin + bb.ymax) / 2, bb.zmax)
    result = apply_edit(part, Edit("extrude", point, Sketch("circle", 12), 1.5))
    assert result.solid.isValid()
    assert result.solid.Volume() > part.Volume()
    low, high = result.surface_mm
    assert high - low > 0.05  # it is curved under the pad
    top = [
        f
        for f in result.solid.Faces()
        if f.geomType() == "PLANE" and np.array(f.normalAt().toTuple()) @ result.normal > 0.9999
    ]
    assert len(top) == 1 and top[0].Area() == pytest.approx(np.pi * 36, rel=1e-3)


@pytest.mark.parametrize("mode,change", [("extrude", -1), ("sink", 1)])
def test_fillet_rounds_the_new_face(mode, change):
    part = box()
    sharp = apply_edit(part, Edit(mode, (20, 15, 20), Sketch("rectangle", 10, 6), 3))
    round_ = apply_edit(part, Edit(mode, (20, 15, 20), Sketch("rectangle", 10, 6), 3, 1.0))
    assert round_.note is None
    assert face_type_counts(round_.solid)["CYLINDER"] == 4
    # A fillet takes material off the top's edges, and fills in the floor's: its cross-section
    # (1 - pi/4) r^2 swept round the rectangle along the section's centroid, which runs
    # r (10 - 3 pi) / (12 - 3 pi) inside the edges (Pappus).
    inset = (10 - 3 * np.pi) / (12 - 3 * np.pi)
    expected = (1 - np.pi / 4) * (2 * (10 - 2 * inset) + 2 * (6 - 2 * inset))
    diff = round_.solid.Volume() - sharp.solid.Volume()
    assert diff * change == pytest.approx(expected, rel=1e-3)


def test_a_fillet_too_big_is_left_sharp_with_a_note():
    result = apply_edit(box(), Edit("extrude", (20, 15, 20), Sketch("rectangle", 10, 6), 3, 4))
    assert result.solid.isValid() and "left sharp" in result.note


def test_overhanging_extrusion_is_noted():
    result = apply_edit(box(), Edit("extrude", (38, 15, 20), Sketch("rectangle", 10, 6), 5))
    assert result.solid.Volume() - box().Volume() == pytest.approx(300, rel=1e-6)
    assert "hangs past the edge" in result.note


def test_a_sink_that_splits_the_part_is_refused():
    plate = cq.Solid.makeBox(40, 30, 2)
    with pytest.raises(UnsupportedGeometry, match="cut the part in two"):
        apply_edit(plate, Edit("sink", (20, 15, 2), Sketch("rectangle", 50, 4), 5))


def test_recipe_record():
    edit = Edit("sink", (20, 15, 20), Sketch("rectangle", 10, 6), 3, 0.5)
    record = apply_edit(box(), edit).as_dict(edit)
    assert record["type"] == "sink" and record["depth_mm"] == 3 and record["fillet_mm"] == 0.5
    assert record["sketch"]["shape"] == "rectangle"
    assert record["plane"] == {"origin": [20, 15, 17], "normal": [0, 0, 1], "x_dir": [1, 0, 0]}


def test_options_are_checked():
    for bad in (
        lambda: Sketch("hexagon", 5, 5),
        lambda: Sketch("rectangle", 0, 5),
        lambda: Sketch("rectangle", 5),
        lambda: Sketch("slot", 5, 6),
        lambda: Sketch("rounded_rectangle", 10, 6, corner_radius_mm=3),
        lambda: Edit("drill", (0, 0, 0), Sketch("circle", 5), 1),
        lambda: Edit("sink", (0, 0, 0), Sketch("circle", 5), 0),
        lambda: Edit("sink", (0, 0, 0), Sketch("circle", 5), 1, -1),
    ):
        with pytest.raises(ValueError):
            bad()
