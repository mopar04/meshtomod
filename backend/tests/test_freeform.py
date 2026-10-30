"""Freeform surfaces: a curved top, a mound and a scoop, each rebuilt as one smooth B-spline
face joined to the flat faces around it."""

import numpy as np
import pytest

from meshtomod import fixtures as fx
from meshtomod.cells import _sample_cell
from meshtomod.convert import convert
from meshtomod.freeform import (
    Frame,
    _contains,
    _crosses,
    _mean_normal,
    _offset,
    fit_height,
    fit_surface,
)
from meshtomod.step_io import face_type_counts
from meshtomod.tolerance import LEVELS


def _heights(fn, x, y) -> np.ndarray:
    xx, yy = np.meshgrid(np.linspace(*x, 801), np.linspace(*y, 801))
    return fn(xx, yy)


# As drawn: the mound's top above the 5 mm plate, the scoop's bottom below the 20 mm top.
MOUND_HEIGHT = float(_heights(fx._pillow, (-30, 22), (-20, 20)).max() - 5.0)
SCOOP_DEPTH = float(20.0 - _heights(fx._dish, (-22, 26), (-14, 18)).min())

PARTS = {
    "freeform_top": (
        fx.freeform_top,
        fx.freeform_top_cad,
        {"PLANE": 5, "BSPLINE": 1},
        "surface",
        None,
    ),
    "freeform_bump_plate": (
        fx.freeform_bump_plate,
        fx.freeform_bump_plate_cad,
        {"PLANE": 6, "BSPLINE": 1},
        "bump",
        ("height_mm", MOUND_HEIGHT),
    ),
    "freeform_recess_block": (
        fx.freeform_recess_block,
        fx.freeform_recess_block_cad,
        {"PLANE": 6, "BSPLINE": 1},
        "recess",
        ("depth_mm", SCOOP_DEPTH),
    ),
}


def scanned(make, sigma: float, seed: int = 3):
    """Dense and noisy, turned to an arbitrary angle: like a scan of the printed part."""
    return fx.rotated(fx.add_noise(fx.subdivided(make(), 0.5), sigma, seed=seed), seed)


def check(result, truth, faces, form, size):
    report = result.report()
    assert result.solid.isValid()
    assert face_type_counts(result.solid) == faces
    (feature,) = report["features"]
    assert feature["type"] == "freeform" and feature["form"] == form
    if size is not None:
        key, value = size
        assert feature[key] == pytest.approx(value, abs=0.1)
    cad = truth().val()
    # Mean surface offset (volume error / area): the surface is the drawn one, not a sag or
    # a bulge of it.
    assert abs(result.solid.Volume() - cad.Volume()) / cad.Area() < 0.005
    assert report["fit"]["within_tolerance_pct"] > 99.5


@pytest.mark.parametrize("name", PARTS)
def test_design_mesh_rebuilds_as_drawn(name):
    make, truth, faces, form, size = PARTS[name]
    check(convert(make(), LEVELS["functional"]), truth, faces, form, size)


@pytest.mark.parametrize("name", PARTS)
@pytest.mark.parametrize("sigma", [0.02, 0.05])
def test_noisy_scan_rebuilds_as_drawn(name, sigma):
    make, truth, faces, form, size = PARTS[name]
    check(convert(scanned(make, sigma), LEVELS["functional"]), truth, faces, form, size)


def _frame() -> Frame:
    return Frame(np.zeros(3), np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0]))


def _bowl(u, v):
    return 0.02 * u**2 - 0.01 * u * v + 0.03 * v**2 + 0.2 * u + 1.0


def test_fit_recovers_a_smooth_height_field():
    rng = np.random.default_rng(0)
    uv = rng.uniform([-20, -15], [20, 15], (4000, 2))
    local = np.c_[uv, _bowl(*uv.T)]
    surface = fit_height(_frame(), local, (-20, 20, -15, 15), (4, 3))
    check_uv = rng.uniform([-18, -13], [18, 13], (500, 2))
    assert surface.height(check_uv) == pytest.approx(_bowl(*check_uv.T), abs=1e-3)


def test_exact_surface_matches_the_fitted_heights():
    # The poles sit at the Greville points, so the CAD surface's (u, v) are the frame's: the
    # B-spline written to the STEP file is exactly w = S(u, v).
    rng = np.random.default_rng(1)
    uv = rng.uniform([-20, -15], [20, 15], (4000, 2))
    tilted = Frame(
        np.array([3.0, -2.0, 7.0]),
        np.array([0.8, 0.6, 0.0]),
        np.array([-0.48, 0.64, 0.6]),
        np.array([0.36, -0.48, 0.8]),
    )
    surface = fit_height(tilted, np.c_[uv, _bowl(*uv.T)], (-20, 20, -15, 15), (4, 3))
    geom = surface.geom()
    for u, v in rng.uniform([-19, -14], [19, 14], (20, 2)):
        p = geom.Value(u, v)
        local = tilted.local(np.array([[p.X(), p.Y(), p.Z()]]))[0]
        assert local == pytest.approx([u, v, surface.height(np.array([[u, v]]))[0]], abs=1e-6)


def test_few_spans_for_a_smooth_noisy_patch():
    # Noise is not followed: a gentle bowl with 0.05 mm noise gets a handful of spans.
    rng = np.random.default_rng(2)
    uv = rng.uniform([-20, -15], [20, 15], (20000, 2))
    pts = np.c_[uv, _bowl(*uv.T) + rng.normal(0, 0.05, len(uv))]
    fit = fit_surface(_frame(), pts, 0.3, 1.0, rng)
    assert fit is not None and max(fit.spans) <= 4
    assert fit.within > 0.99


def test_polygon_offset_and_checks():
    t = np.linspace(0, 2 * np.pi, 200, endpoint=False) + 0.01
    circle = np.c_[np.cos(t), np.sin(t)] * 10
    for ring in (circle, circle[::-1]):
        grown = _offset(ring, 2.0)
        assert np.linalg.norm(grown, axis=1) == pytest.approx(12.0, abs=1e-3)
    assert not _crosses(circle)
    eight = np.c_[np.sin(t), np.sin(t) * np.cos(t)] * 10
    assert _crosses(eight)
    inside = _contains(circle, np.array([[0.0, 0.0], [9.0, 0.0], [11.0, 0.0], [0.0, -12.0]]))
    assert inside.tolist() == [True, True, False, False]


def test_cell_samples_fill_a_thin_slanted_slab():
    # A slab 0.4 mm thick cut at a slant fills about 1% of its bounding box; its samples must
    # still all be inside it, or it is judged by one point (this left walls 0.3 mm out).
    corners = np.array([[x, y, z] for x in (0, 80) for y in (0, 0.4) for z in (0, 19)], float)
    turn = np.linalg.qr(np.random.default_rng(3).normal(size=(3, 3)))[0]
    pts = _sample_cell(corners @ turn.T, np.random.default_rng(0)) @ turn
    assert len(pts) == 64
    assert (pts >= -1e-9).all() and (pts <= [80, 0.4, 19] + np.array(1e-9)).all()
    assert np.ptp(pts[:, 0]) > 40 and np.ptp(pts[:, 2]) > 9


def test_no_viewing_side_for_faces_that_face_every_way():
    # A hollow standoff's wall (or both sides of a thin wall) has normals that cancel out.
    # It used to divide by zero and crash the conversion; now it is just not a freeform area.
    import trimesh

    tube = trimesh.creation.annulus(1.6, 3.5, 18.0, sections=64)
    assert _mean_normal(tube, np.arange(len(tube.faces))) is None
    top = np.flatnonzero(tube.face_normals[:, 2] > 0.99)
    assert np.allclose(_mean_normal(tube, top), [0, 0, 1])
