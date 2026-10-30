"""Merging a top and a bottom scan of one part, with no design to line them up against."""

import numpy as np
import pytest
import trimesh

from meshtomod import fixtures as fx
from meshtomod.convert import convert
from meshtomod.merge import MergeError, merge_scans
from meshtomod.step_io import face_type_counts
from meshtomod.tolerance import LEVELS


def scan(side: trimesh.Trimesh) -> trimesh.Trimesh:
    return fx.add_noise(fx.subdivided(side, 1.0), 0.03, seed=2)


def flipped(mesh: trimesh.Trimesh, seed: int) -> tuple[trimesh.Trimesh, np.ndarray]:
    """The bottom scan as the scanner returns it: turned over and moved."""
    move = trimesh.transformations.random_rotation_matrix(np.random.default_rng(seed).random(3))
    move[:3, 3] = [40.0, -25.0, 10.0]
    out = mesh.copy()
    out.apply_transform(move)
    return out, move


@pytest.mark.parametrize("make", [fx.boss_plate, fx.bracket, fx.blind_hole_block])
def test_bottom_scan_lands_where_it_belongs(make):
    part = make()
    top, bottom = (scan(s) for s in fx.sides(part))
    moved, _ = flipped(bottom, seed=5)
    result = merge_scans(top, moved)
    # Placed, the bottom scan lies on the part. (Not necessarily point for point where it
    # started: a symmetric part turned half round looks the same from below.)
    placed = trimesh.transform_points(moved.vertices, result.transform)
    _, d, _ = trimesh.proximity.closest_point(part, placed)
    assert d.mean() < 0.1
    assert np.percentile(d, 99) < 0.3
    assert result.air_pct < 2


def test_merged_scans_convert_like_the_whole_part():
    top, bottom = (scan(s) for s in fx.sides(fx.boss_plate()))
    moved, _ = flipped(bottom, seed=7)
    merged = merge_scans(top, moved).mesh
    report = convert(merged, LEVELS["functional"]).report()
    assert report["fit"]["within_tolerance_pct"] > 99
    assert [f["type"] for f in report["features"]] == ["boss"]


def test_scans_that_do_not_overlap_are_refused():
    # Two balls of very different sizes: no pose puts one surface on the other.
    small, big = fx.ball(radius=10.0, squash=1.0), fx.ball(radius=40.0, squash=1.0)
    with pytest.raises(MergeError):
        merge_scans(small, big)


def test_merged_turned_part_keeps_its_scanned_base():
    # Seen from below, a turned part comes out in two pieces (the base, and the underside of
    # the step), and once placed the base piece is apart from the top scan. Both pieces are
    # kept and the base is stitched on, so the base is the scanned one, not closed flat.
    top, bottom = (
        fx.add_noise(fx.subdivided(s, 0.5), 0.03, seed=2) for s in fx.sides(fx.turned_part())
    )
    moved, _ = flipped(bottom, seed=5)
    merged = merge_scans(top, moved).mesh
    pieces = sorted(merged.split(only_watertight=False), key=lambda p: -p.area)
    assert pieces[0].area > 0.9 * merged.area
    result = convert(merged, LEVELS["functional"])
    report = result.report()
    assert result.solid.isValid()
    assert face_type_counts(result.solid) == {"PLANE": 3, "CONE": 3, "CYLINDER": 6, "SPHERE": 1}
    turned, hole = sorted(report["features"], key=lambda f: f["type"], reverse=True)
    assert turned["type"] == "turned" and turned["diameter_mm"] == 30.0
    assert turned["height_mm"] == 44.0
    assert hole["type"] == "hole" and hole["through"]
    assert hole["diameter_mm"] == pytest.approx(4.0, abs=0.05)
    assert report["fit"]["within_tolerance_pct"] > 99.5
    assert not any("closed flat" in n for n in report["notes"])
