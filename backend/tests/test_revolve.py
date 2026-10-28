"""Cones, spheres and tori: turned parts and shapes turned about an axis on a plate."""

import numpy as np
import pytest

from meshtomod import fixtures as fx
from meshtomod.convert import convert
from meshtomod.revolve import Arc2, _on_axis, _snap_value, fit_arc, fit_line
from meshtomod.step_io import face_type_counts
from meshtomod.tolerance import LEVELS

TURNED_FACES = {"PLANE": 3, "CONE": 3, "CYLINDER": 6, "SPHERE": 1}
TURNED_FEATURES = [
    {
        "type": "turned",
        "diameter_mm": 30.0,
        "height_mm": 44.0,
        "surfaces": {"cone": 3, "sphere": 1, "cylinder": 5, "plane": 3},
    },
    {"type": "hole", "diameter_mm": 4.0, "through": True},
]
PLATE_FACES = {"PLANE": 8, "CYLINDER": 4, "CONE": 3, "SPHERE": 1, "TORUS": 2}
PLATE_FEATURES = [
    {"type": "countersink", "diameter_mm": 3.4, "head_mm": 6.5, "angle_deg": 90.0,
     "through": True, "standard": "M3 clearance"},
    # Drawn 10.4; 10.5 is as close and rounder, and well within the tolerance.
    {"type": "countersink", "diameter_mm": 5.5, "head_mm": 10.5, "angle_deg": 90.0,
     "through": True, "standard": "M5 clearance"},
    {"type": "dome", "radius_mm": 20.0, "height_mm": 6.0, "diameter_mm": 28.566},
    {"type": "revolved_boss", "diameter_mm": 20.0, "height_mm": 5.0,
     "surfaces": {"torus": 2, "cylinder": 2, "plane": 1}},
    {"type": "tapered_boss", "diameters_mm": [16.0, 10.0], "height_mm": 8.0},
]  # fmt: skip
PARTS = {
    "turned_part": (fx.turned_part, fx.turned_part_cad, TURNED_FACES, TURNED_FEATURES),
    "surfaces_plate": (fx.surfaces_plate, fx.surfaces_plate_cad, PLATE_FACES, PLATE_FEATURES),
}


def scanned(make, sigma: float, seed: int = 3):
    """Dense and noisy, turned to an arbitrary angle: like a scan of the printed part."""
    return fx.rotated(fx.add_noise(fx.subdivided(make(), 0.5), sigma, seed=seed), seed)


def ordered(features: list[dict]) -> list[dict]:
    return sorted(features, key=lambda f: (f["type"], str(sorted(f.items()))))


def check(result, truth, faces, features):
    report = result.report()
    assert result.solid.isValid()
    assert face_type_counts(result.solid) == faces
    assert ordered(report["features"]) == ordered(features)
    cad = truth().val()
    # Mean surface offset (volume error / area): the shapes are the drawn ones.
    assert abs(result.solid.Volume() - cad.Volume()) / cad.Area() < 0.005
    assert report["fit"]["within_tolerance_pct"] > 99.5


@pytest.mark.parametrize("name", PARTS)
def test_design_mesh_rebuilds_as_drawn(name):
    make, truth, faces, features = PARTS[name]
    check(convert(make(), LEVELS["functional"]), truth, faces, features)


@pytest.mark.parametrize("name", PARTS)
@pytest.mark.parametrize("sigma", [0.02, 0.05])
def test_noisy_scan_rebuilds_as_drawn(name, sigma):
    make, truth, faces, features = PARTS[name]
    check(convert(scanned(make, sigma), LEVELS["functional"]), truth, faces, features)


def test_precise_level_on_a_fine_scan():
    # A laser scan's noise (0.02 mm) is well inside Precise (0.1 mm), and the narrow flat on
    # top of the ring, which noise splits in two, is still its top.
    _, truth, faces, features = PARTS["surfaces_plate"]
    result = convert(scanned(fx.surfaces_plate, 0.02), LEVELS["precise"])
    check(result, truth, faces, features)


def test_arc_fit_recovers_circle():
    rng = np.random.default_rng(0)
    t = np.linspace(0.2, 1.4, 60)
    q = np.c_[3 + 7 * np.cos(t), -2 + 7 * np.sin(t)]
    normals = np.c_[np.cos(t), np.sin(t)]
    noisy = q + rng.normal(0, 0.01, q.shape)
    arc = fit_arc(noisy, normals, noisy)
    assert arc.convex
    assert arc.radius == pytest.approx(7.0, abs=0.05)
    assert arc.centre == pytest.approx([3.0, -2.0], abs=0.05)
    # Hollow: normals pointing to the centre.
    assert not fit_arc(noisy, -normals, noisy).convex


def test_line_fit_faces_the_normals():
    q = np.c_[np.linspace(0, 5, 20), np.full(20, 4.0)]
    line = fit_line(q, np.tile([0.0, 1.0], (20, 1)))
    assert line.kind() == "cylinder"
    assert line.normal == pytest.approx([0.0, 1.0]) and line.offset == pytest.approx(4.0)
    line = fit_line(q, np.tile([0.0, -1.0], (20, 1)))
    assert line.normal == pytest.approx([0.0, -1.0]) and line.offset == pytest.approx(-4.0)


def test_ball_centre_held_on_axis():
    # A cap of a 20 mm ball centred 8 mm below the origin, sampled off-centre and noisy: the
    # fit with the centre on the axis gets the drawn radius back.
    rng = np.random.default_rng(1)
    a = np.linspace(0.0, 0.75, 200)
    pts = np.c_[-8 + 20 * np.cos(a), 20 * np.sin(a)] + rng.normal(0, 0.03, (200, 2))
    guess = Arc2(np.array([-8.3, -0.1]), 20.3, True)
    z, radius = _on_axis(pts, guess)
    assert radius == pytest.approx(20.0, abs=0.02) and z == pytest.approx(-8.0, abs=0.03)
    z, radius = _on_axis(pts, guess, 20.0)
    assert radius == 20.0 and z == pytest.approx(-8.0, abs=0.01)


def test_snap_prefers_round_values_within_budget():
    assert _snap_value(19.93, 0.15) == 20.0
    assert _snap_value(10.42, 0.15) == 10.5
    assert _snap_value(10.37, 0.05) == 10.4
    assert _snap_value(10.37, 0.001) == 10.37
