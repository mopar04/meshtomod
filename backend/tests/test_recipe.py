"""The feature recipe: each step in designer order, with positions in the STEP's frame."""

import numpy as np
import pytest

from meshtomod import fixtures as fx
from meshtomod.convert import convert
from meshtomod.recipe import RECIPE_VERSION, build_recipe
from meshtomod.sink import Edit, Sketch, apply_edit
from meshtomod.tolerance import LEVELS

CONVERTED = {}


def converted(make):
    if make not in CONVERTED:
        CONVERTED[make] = convert(make(), LEVELS["functional"])
    return CONVERTED[make]


def shift(x=0.0, y=0.0, z=0.0, turn_deg=0.0):
    """A 4x4 matrix: turn about Z, then move."""
    a = np.radians(turn_deg)
    m = np.eye(4)
    m[:2, :2] = [[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]]
    m[:3, 3] = [x, y, z]
    return m


def of_type(recipe, kind):
    return [s for s in recipe["steps"] if s["type"] == kind]


def test_holes_where_they_were_drilled():
    recipe = build_recipe(converted(fx.holed_plate))
    assert recipe["version"] == RECIPE_VERSION and recipe["units"] == "mm"
    holes = of_type(recipe, "hole")
    assert len(holes) == 4
    for hole in holes:
        assert hole["diameter_mm"] == pytest.approx(6.6) and hole["through"]
        assert np.abs(hole["axis"]["direction"]) == pytest.approx([0, 0, 1], abs=1e-3)
        assert hole["replay"] is False
    centres = sorted((round(h["start"][0]), round(h["start"][1])) for h in holes)
    assert centres == [(-20, -10), (-20, 10), (20, -10), (20, 10)]


def test_positions_follow_the_placement():
    # Turned a quarter about Z and moved 100 mm along X: a hole at (20, 10) lands at (90, 20).
    recipe = build_recipe(converted(fx.holed_plate), matrix=shift(x=100, turn_deg=90))
    centres = sorted((round(h["start"][0]), round(h["start"][1])) for h in of_type(recipe, "hole"))
    assert centres == [(90, -20), (90, 20), (110, -20), (110, 20)]
    for hole in of_type(recipe, "hole"):
        assert np.abs(hole["axis"]["direction"]) == pytest.approx([0, 0, 1], abs=1e-3)


def test_fillets_name_the_planes_they_join():
    plain = build_recipe(converted(fx.filleted_block))
    moved = build_recipe(converted(fx.filleted_block), matrix=shift(x=10))
    fillets = of_type(plain, "fillet")
    assert len(fillets) == 4 and all(f["radius_mm"] == pytest.approx(5.0) for f in fillets)
    for fillet, after in zip(fillets, of_type(moved, "fillet"), strict=True):
        assert len(fillet["between"]) == 2
        # The block's walls: ±X at 25 mm, ±Y at 15 mm from the centre.
        offsets = sorted(round(p["offset_mm"]) for p in fillet["between"])
        assert offsets == [15, 25]
        # Moving the part 10 mm along X moves the +X wall out and the -X wall in.
        for before, plane in zip(fillet["between"], after["between"], strict=True):
            assert plane["normal"] == pytest.approx(before["normal"], abs=1e-9)
            expected = before["offset_mm"] + 10 * before["normal"][0]
            assert plane["offset_mm"] == pytest.approx(expected, abs=1e-3)


def test_designer_order():
    # The bracket: the inside fillet before the holes.
    kinds = [s["type"] for s in build_recipe(converted(fx.bracket))["steps"]]
    assert kinds == ["fillet", "hole", "hole"]
    kinds = [s["type"] for s in build_recipe(converted(fx.boss_plate))["steps"]]
    assert kinds == ["boss"]


def test_turned_part_profile():
    step = build_recipe(converted(fx.turned_part))["steps"][0]
    assert step["type"] == "turned" and step["operation"] == "add"
    assert np.abs(step["axis"]["direction"]) == pytest.approx([0, 0, 1], abs=1e-3)
    plane = step["sketch_plane"]
    origin, normal = np.array(plane["origin"]), np.array(plane["normal"])
    ends = []
    for piece in step["profile"]:
        ((kind, points),) = piece.items()
        points = np.array(points)
        assert kind in ("line", "arc") and len(points) == (2 if kind == "line" else 3)
        # Drawn in a plane through the axis.
        assert (points - origin) @ normal == pytest.approx(np.zeros(len(points)), abs=1e-3)
        ends.append((points[0], points[-1]))
    # Each piece starts where the last one ended.
    for (_, end), (start, _) in zip(ends[:-1], ends[1:], strict=True):
        assert start == pytest.approx(end, abs=1e-3)
    if step["closed"]:
        assert ends[0][0] == pytest.approx(ends[-1][1], abs=1e-3)
    # Out to the Ø30 base and up to the top of the ball end.
    points = np.array([p for piece in step["profile"] for p in next(iter(piece.values()))])
    radial = np.linalg.norm(points[:, :2] - origin[:2], axis=1)
    assert radial.max() == pytest.approx(15, abs=0.1)
    assert np.ptp(points[:, 2]) == pytest.approx(44, abs=0.1)


def test_edits_come_last_and_replay():
    result = converted(fx.holed_plate)
    edit = Edit("sink", (0, 0, 4), Sketch("rectangle", 20, 10), 2)
    done = apply_edit(result.solid, edit)
    recipe = build_recipe(result, [(edit, done)], matrix=shift(z=4))
    last = recipe["steps"][-1]
    assert last["type"] == "sink" and last["replay"] is True and last["depth_mm"] == 2
    assert last["plane"]["origin"] == pytest.approx([0, 0, 6], abs=1e-6)
    assert last["plane"]["normal"] == pytest.approx([0, 0, 1], abs=1e-9)
    assert all(not s["replay"] for s in recipe["steps"][:-1])
