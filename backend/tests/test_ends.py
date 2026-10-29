import numpy as np
import pytest

from meshtomod import fixtures as fx
from meshtomod.convert import CLOSED_NOTE, convert, repair
from meshtomod.ends import boundary_loops, close_flat_ends
from meshtomod.step_io import face_type_counts
from meshtomod.tolerance import LEVELS

TOL = 0.1


def opened(make, sigma: float = 0.0, seed: int = 3):
    mesh = fx.unscanned_base(fx.subdivided(make(), 1.0))
    return repair(fx.rotated(fx.add_noise(mesh, sigma, seed=seed), seed) if sigma else mesh)


@pytest.mark.parametrize("sigma", [0.0, 0.05])
def test_an_unscanned_base_is_closed_flat(sigma):
    mesh = opened(fx.box, sigma)
    assert not mesh.is_watertight

    closed, added = close_flat_ends(mesh, TOL)
    assert added > 0
    assert closed.is_watertight and closed.is_winding_consistent
    assert closed.volume == pytest.approx(40 * 30 * 20, rel=2e-3)
    # The scan's own points are only moved onto the plane, never further than the noise.
    assert np.abs(closed.vertices[: len(mesh.vertices)] - mesh.vertices).max() < 4 * sigma + 1e-9


def test_a_tube_end_closes_as_a_ring():
    mesh = opened(fx.tape_roll)
    assert len(boundary_loops(mesh)) == 2

    closed, _ = close_flat_ends(mesh, TOL)
    assert closed.is_watertight
    # The bore stays open: a ring, not a disc over it.
    assert closed.volume == pytest.approx(fx.tape_roll_cad().val().Volume(), rel=2e-3)


def test_a_gap_that_is_not_flat_is_left_open():
    mesh = repair(fx.subdivided(fx.box(), 1.0))
    # A bite out of one corner: its rim runs over three faces.
    corner = np.array([20.0, 15.0, 10.0])
    mesh.update_faces(np.linalg.norm(mesh.triangles_center - corner, axis=1) > 6)
    mesh.remove_unreferenced_vertices()

    closed, added = close_flat_ends(mesh, TOL)
    assert added == 0 and closed is mesh


def test_a_closed_scan_is_untouched():
    mesh = repair(fx.box())
    closed, added = close_flat_ends(mesh, TOL)
    assert added == 0 and closed is mesh


OPEN_PARTS = {
    "tape_roll": (fx.tape_roll, fx.tape_roll_cad, {"PLANE": 2, "CYLINDER": 2}),
    "holed_plate": (fx.holed_plate, fx.holed_plate_cad, {"PLANE": 6, "CYLINDER": 4}),
}


@pytest.mark.parametrize("name", OPEN_PARTS)
def test_open_scans_convert_exactly(name):
    make, truth, faces = OPEN_PARTS[name]
    cad = truth().val()
    result = convert(opened(make, 0.02), LEVELS["functional"])
    report = result.report()

    assert result.solid.isValid()
    assert face_type_counts(result.solid) == faces
    assert abs(result.solid.Volume() - cad.Volume()) / cad.Area() < 0.02
    assert report["fit"]["within_tolerance_pct"] > 99
    assert CLOSED_NOTE in report["notes"]
