"""Thin-walled parts (covers): rebuilt from the outside of the wall and its thickness. The test
part is a cover like the Max4 toolhead cover, built in CadQuery, so the truth is exact: walls
1.8 mm, three vent slots, a U notch, two 6 mm standoffs with 2.5 mm screw holes. As in the real
scan, the scanner has skinned the screw holes over with a slight dome."""

import cadquery as cq
import numpy as np
import pytest

from meshtomod import fixtures as fx
from meshtomod.convert import convert, repair, working_mesh
from meshtomod.recipe import build_recipe
from meshtomod.segment import Geo
from meshtomod.shell import SHELL_FACES, find_shell
from meshtomod.tolerance import LEVELS

LEVEL = LEVELS["functional"]


@pytest.fixture(scope="module", params=["clean", "noisy"])
def cover(request):
    mesh = fx.cover()
    if request.param == "noisy":
        mesh = fx.add_wavy_noise(mesh, 0.05)
    return convert(mesh, LEVEL)


def inside(shape: cq.Shape, *point: float) -> bool:
    return shape.isInside(cq.Vector(*point))


def test_cover_is_one_valid_shell_close_to_the_scan(cover):
    report = cover.report()
    shell = report["features"][0]
    assert shell["type"] == "shell"
    assert shell["thickness_mm"] == pytest.approx(1.8, abs=0.05)
    assert cover.solid.isValid()
    assert len(cover.solid.Solids()) == 1
    assert report["fit"]["within_tolerance_pct"] >= 95
    assert not cover.partial
    assert report["size_mm"] == pytest.approx([80, 50, 30], abs=0.3)
    # The truth's screw holes stop 3 mm short of the top; the rebuilt ones run to the wall.
    truth = fx.cover_cad().val().Volume()
    assert cover.solid.Volume() == pytest.approx(truth, rel=0.03)


def test_cover_keeps_its_openings(cover):
    solid = cover.solid
    mid_wall = 15 - 0.9
    # Wall where there is wall...
    assert inside(solid, -20, 0, mid_wall)
    assert inside(solid, 0, -25 + 0.9, 0)
    # ...and none in the vent slots, the U notch or the open underneath.
    for y in (-8, 0, 8):
        assert not inside(solid, 10, y, mid_wall)
    assert not inside(solid, 40 - 0.9, 0, -15 + 3)
    assert not inside(solid, 0, 0, -15 + 0.5)
    assert cover.report()["features"][0]["openings"] == 3


def test_standoffs_are_bosses_with_screw_holes(cover):
    features = [f.as_dict() for f in cover.features]
    bosses = [f for f in features if f["type"] == "boss"]
    holes = [f for f in features if f["type"] == "hole"]
    assert [b["diameter_mm"] for b in bosses] == [6.0, 6.0]
    assert [b["height_mm"] for b in bosses] == pytest.approx([15, 15], abs=0.2)
    # The scan skinned the holes over, so they get the usual size for the screw (M3) and say so.
    assert [h["diameter_mm"] for h in holes] == [2.5, 2.5]
    assert any("M3 screw" in n for n in cover.notes)
    for y in (-15, 15):
        assert inside(cover.solid, -30 + 2.2, y, 5)  # the post's wall
        assert not inside(cover.solid, -30, y, 5)  # its hole
        assert inside(cover.solid, -30, y, 15 - 0.9)  # the top above it is closed


def test_history_starts_from_the_walls(cover):
    assert cover.base is not None and cover.base.isValid()
    assert cover.base.Volume() < cover.solid.Volume()
    steps = build_recipe(cover)["steps"]
    assert steps[0]["type"] == "shell"


@pytest.mark.parametrize(
    "part",
    [
        lambda: fx.box(),
        lambda: fx.subdivided(fx.holed_plate(), 0.8),
        lambda: fx.subdivided(fx.frame(), 0.8),
        lambda: fx.subdivided(fx.l_bracket(), 0.8),
        lambda: fx.subdivided(fx.from_cad(cq.Workplane("XY").box(60, 40, 2)), 0.8),
    ],
    ids=["box", "plate", "frame", "bracket", "thin plate"],
)
def test_solid_parts_are_not_shells(part):
    assert find_shell(Geo.of(working_mesh(repair(part()), SHELL_FACES))) is None


def test_wall_thickness_is_measured_across_the_wall():
    mesh = fx.subdivided(
        fx.from_cad(cq.Workplane("XY").box(60, 40, 25).faces("<Z").shell(-2.5)), 0.8
    )
    found = find_shell(Geo.of(mesh))
    assert found is not None
    assert found.thickness_mm == pytest.approx(2.5, abs=0.05)
    assert np.isclose(found.paired_share, 1.0, atol=0.25)
