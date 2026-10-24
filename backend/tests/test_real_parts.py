"""Real parts with a known design: meshes exported from CAD, so the answer is exact."""

from pathlib import Path

import pytest
import trimesh

from nurbsmith.convert import convert
from nurbsmith.tolerance import LEVELS

FIXTURES = Path(__file__).parent / "fixtures"


def test_scanner_holder():
    # Zac's scanner holder (Bambu Studio export): an 88 x 87 x 125 mm box with rounded
    # corners, three deep pockets (one round, 32 mm) and slots underneath.
    design = trimesh.load(FIXTURES / "scanner_holder.stl")
    result = convert(design, LEVELS["functional"])
    report = result.report()

    assert result.solid.isValid()
    assert result.solid.Volume() == pytest.approx(design.volume, rel=0.02)
    assert report["fit"]["within_tolerance_pct"] > 93
    holes = [f for f in report["features"] if f["type"] == "hole"]
    assert [h["diameter_mm"] for h in holes] == [32.0]
    radii = [f["radius_mm"] for f in report["features"] if f["type"] == "fillet"]
    assert radii.count(15.0) >= 2  # the outer corners
