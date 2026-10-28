"""Thicken skin: one-sided scans (an open sheet, not a closed part) made into solid walls. Each
is checked against OpenCASCADE's own thicken of the exact face the sheet was made from."""

import cadquery as cq
import numpy as np
import pytest
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape

from meshtomod import fixtures as fx
from meshtomod.convert import UnsupportedGeometry, convert
from meshtomod.step_io import face_type_counts
from meshtomod.thicken import (
    ThickenOptions,
    _area,
    _loop_curve,
    border_loops,
    find_skin,
    smooth_outline,
)
from meshtomod.tolerance import LEVELS

LEVEL = LEVELS["functional"]

SKINS = {
    # A scan is dense: the wave's CAD mesh has one long triangle edge down each end.
    "wave": (lambda: fx.subdivided(fx.wave_skin(), 0.5), fx.wave_skin_face, 2.0),
    "cap": (fx.cap_skin, fx.cap_skin_face, 3.0),
}


def truth(face: cq.Face, t: float, direction: str) -> cq.Shape:
    if direction == "in":
        return face.thicken(-t)
    if direction == "out":
        return face.thicken(t)
    moved = BRepOffsetAPI_MakeOffsetShape()
    moved.PerformByJoin(face.wrapped, t / 2, 1e-6)
    return cq.Face(cq.Shape.cast(moved.Shape()).Faces()[0].wrapped).thicken(-t)


def volume(shape: cq.Shape) -> float:
    # OpenCASCADE's own volume integration is off by a few percent on these solids; a fine
    # triangulation is not.
    return fx.from_cad(shape, tolerance=0.005, angular=0.05).volume


def scanned(make, sigma: float, seed: int = 3):
    """Dense, noisy, with a torn border up to 1.5 mm deep, and turned to an arbitrary angle."""
    return fx.rotated(fx.add_noise(fx.ragged(fx.subdivided(make(), 0.5), 1.5), sigma, seed), seed)


def check(result, thickness: float, direction: str, edge: str = "square"):
    report = result.report()
    assert result.solid.isValid()
    (feature,) = report["features"]
    assert feature == {
        "type": "skin",
        "thickness_mm": thickness,
        "direction": direction,
        "edge": edge,
        "area_mm2": pytest.approx(result.skin.area_mm2, abs=0.1),
    }
    assert report["fit"]["within_tolerance_pct"] > 99.5
    return report


@pytest.mark.parametrize(
    "name,direction", [("wave", "in"), ("cap", "in"), ("cap", "out"), ("cap", "centred")]
)
def test_design_skin_thickens_as_cad_would(name, direction):
    make, face, t = SKINS[name]
    result = convert(make(), LEVEL, ThickenOptions(t, direction))
    report = check(result, t, direction)
    # Two B-spline faces and a side wall square to them.
    assert face_type_counts(result.solid) == {"BSPLINE": 3}
    # Mean offset from the exact wall (volume error / area): the outline's corners round off
    # by the smoothing, nothing else.
    exact = face()
    assert abs(report["volume_mm3"] - volume(truth(exact, t, direction))) / exact.Area() < 0.01


@pytest.mark.parametrize("name,direction,sigma", [("wave", "in", 0.05), ("cap", "out", 0.05)])
def test_noisy_ragged_scan_thickens_to_an_even_wall(name, direction, sigma):
    make, face, t = SKINS[name]
    result = convert(scanned(make, sigma), LEVEL, ThickenOptions(t, direction))
    report = check(result, t, direction)
    assert face_type_counts(result.solid) == {"BSPLINE": 3}
    # As thick as asked everywhere: volume over area, against the same for the drawn wall
    # (over t on a curve: a wall outside a dome holds more than one inside it).
    area = result.skin.area_mm2
    exact = face()
    drawn = volume(truth(exact, t, direction)) / exact.Area()
    assert report["volume_mm3"] / area == pytest.approx(drawn, rel=0.02)
    # The outline keeps to the scanned surface: inside the drawn face, and not cut in much
    # past the torn band.
    rim = sum(e.Length() for e in exact.Edges())
    assert exact.Area() - 2.0 * rim < area < exact.Area()
    # Its free border is its edge, not a gap in the scan.
    assert "gaps" not in (report["scan_quality"]["warning"] or "")


def test_default_is_a_3mm_wall_behind_the_scan_with_a_note():
    result = convert(fx.cap_skin(), LEVEL)
    check(result, 3.0, "in")
    (note,) = result.notes
    assert "3 mm wall behind the scanned surface" in note and "other side" in note


@pytest.mark.parametrize(
    "edge,expected", [("chamfer", (1 / 3) ** 2), ("round", 0.49**2 * (2 - np.pi / 2))]
)
def test_edge_finish(edge, expected):
    # Material taken off the rim, per mm of rim, over thickness squared: a chamfer of t/3
    # takes two triangles of (t/3)^2 / 2; a round of r = 0.49 t takes 2 r^2 (1 - pi/4).
    _, face, t = SKINS["cap"]
    square = convert(fx.cap_skin(), LEVEL, ThickenOptions(t)).report()["volume_mm3"]
    result = convert(fx.cap_skin(), LEVEL, ThickenOptions(t, "in", edge))
    report = check(result, t, "in", edge)
    assert face_type_counts(result.solid) == {"BSPLINE": 5}
    a, b = fx.CAP_AXES
    rim = np.pi * (3 * (a + b) - np.sqrt((3 * a + b) * (a + 3 * b)))
    removed = (square - report["volume_mm3"]) / (rim * t**2)
    assert removed == pytest.approx(expected, rel=0.1)


def test_too_thick_for_the_curve_is_refused():
    # The dome curves at a radius of 20 mm across its top: a 25 mm wall inside it would fold.
    with pytest.raises(UnsupportedGeometry, match="curves too tightly"):
        convert(fx.cap_skin(), LEVEL, ThickenOptions(25.0))


def test_closed_part_and_open_box_are_not_skins():
    rng = np.random.default_rng(0)
    assert find_skin(fx.subdivided(fx.box(), 2.0), LEVEL, ThickenOptions(), rng) is None
    # A box scanned without its bottom: the walls face both ways round, so it goes through the
    # pipeline for closed parts (which closes the gap).
    box = fx.subdivided(fx.box(), 2.0)
    box.update_faces(box.face_normals[:, 2] > -0.5)
    assert find_skin(box, LEVEL, ThickenOptions(), rng) is None


def test_options_are_checked():
    with pytest.raises(ValueError):
        ThickenOptions(0.0)
    with pytest.raises(ValueError):
        ThickenOptions(2.0, "sideways")
    with pytest.raises(ValueError):
        ThickenOptions(2.0, "in", "bevel")
    assert ThickenOptions(4.0, "centred").span == (-2.0, 2.0)


def test_border_runs_counter_clockwise_round_a_sheet_facing_up():
    disc = fx.cap_skin()
    (loop,) = border_loops(disc)
    assert _area(disc.vertices[loop][:, :2]) > 0


def test_ragged_border_is_smoothed_and_pulled_in():
    rng = np.random.default_rng(1)
    t = np.linspace(0, 2 * np.pi, 2000, endpoint=False)
    # A 30 mm circle whose edge is torn in by up to 1.5 mm, in teeth about 2 mm apart.
    teeth = 1.5 * (rng.random(len(t) // 10).repeat(10) ** 2)
    border = np.c_[np.cos(t), np.sin(t)] * (30 - teeth)[:, None]
    outline, trim = smooth_outline(border, 2.0, 0.3)
    r = np.linalg.norm(outline, axis=1)
    assert trim > 0
    assert r.max() < 30
    # The teeth are gone: what wobble is left is slow and small.
    assert np.std(r) < 0.5 * np.std(np.linalg.norm(border, axis=1))
    # Clockwise in, counter-clockwise out.
    assert _area(smooth_outline(border[::-1], 2.0, 0.3)[0]) > 0


def test_loop_curve_follows_the_points():
    t = np.linspace(0, 2 * np.pi, 300, endpoint=False)
    pts = np.c_[np.cos(t) * 10, np.sin(t) * 5, np.sin(2 * t)]
    curve = _loop_curve(pts, 24)
    got = np.array([curve.Value(i / 300).Coord() for i in range(300)])
    assert got == pytest.approx(pts, abs=1e-3)
