import numpy as np
import pytest
from fastapi.testclient import TestClient

from meshtomod import fixtures
from meshtomod.api import app
from meshtomod.convert import convert
from meshtomod.orient import (
    TurnAxis,
    anchor_offset,
    axis_rotation,
    check_options,
    distinct_axes,
    place,
    resting_faces,
    rotation_between,
    trim_flush,
)
from meshtomod.step_io import face_type_counts, read_step
from meshtomod.tolerance import LEVELS

client = TestClient(app)


def _rotated_noisy(mesh, seed=2):
    return fixtures.add_noise(fixtures.subdivided(fixtures.rotated(mesh, seed), 1.5), 0.03)


def _bottom_face(shape, axis=2):
    """The planar face lowest along the axis, with its outward normal."""
    planes = [f for f in shape.Faces() if f.geomType() == "PLANE"]
    face = min(planes, key=lambda f: f.Center().toTuple()[axis])
    return face, np.array(face.normalAt().toTuple())


def test_rotation_between_handles_opposite_vectors():
    for a in ([0, 0, 1.0], [0, 0, -1.0], [1.0, 2.0, 3.0]):
        r = rotation_between(np.array(a), np.array([0, 0, -1.0]))
        assert r @ (np.array(a) / np.linalg.norm(a)) == pytest.approx([0, 0, -1], abs=1e-12)
        assert np.linalg.det(r) == pytest.approx(1.0)


def test_resting_faces_of_a_tilted_plate_are_its_big_sides_first():
    plate = fixtures.holed_plate()  # 60 x 40 x 8
    tilted = fixtures.rotated(plate, seed=5)
    faces = resting_faces(tilted)
    assert [round(f.area_mm2) for f in faces[:2]] == [2400, 2400]
    for f in faces:
        # Each rotation lays its face straight down and squares the part to X and Y.
        assert f.rotation @ f.normal == pytest.approx([0, 0, -1], abs=1e-9)
    laid = tilted.copy()
    laid.apply_transform(np.block([[faces[0].rotation, np.zeros((3, 1))], [0, 0, 0, 1]]))
    assert sorted(laid.extents) == pytest.approx([8, 40, 60], abs=1e-6)
    assert laid.extents[0] == pytest.approx(60, abs=1e-6)  # long side along X


def test_noisy_scan_face_is_one_candidate():
    faces = resting_faces(_rotated_noisy(fixtures.holed_plate()))
    assert len(faces) >= 6  # two sides, four ends; noise doesn't split them
    assert faces[0].area_mm2 > 2000 and faces[1].area_mm2 > 2000
    assert faces[2].area_mm2 < 600


def test_unstable_faces_are_not_offered():
    # An L-bracket can't rest on the top of its upright: the centre of mass overhangs it.
    bracket = fixtures.l_bracket(base=(40.0, 20.0, 5.0), upright_height=30.0)
    normals = [f.normal for f in resting_faces(bracket)]
    tops = [n for n in normals if n @ [0, 0, 1] > 0.99]
    assert not tops  # the hull's top facet slopes from the wall top to the base's far edge


def test_anchor_offsets():
    bounds = np.array([[1.0, 2.0, 3.0], [5.0, 8.0, 13.0]])
    assert anchor_offset(bounds, "keep", "z") == pytest.approx([0, 0, 0])
    assert anchor_offset(bounds, "center", "z") == pytest.approx([-3, -5, -8])
    assert anchor_offset(bounds, "bottom", "z") == pytest.approx([-3, -5, -3])
    assert anchor_offset(bounds, "bottom", "y") == pytest.approx([-3, -2, -8])
    assert anchor_offset(bounds, "corner", "y") == pytest.approx([-1, -2, -3])
    # The axis through (4, 6, 9) put on the origin, the bottom on the ground.
    axis = np.array([4.0, 6.0, 9.0])
    assert anchor_offset(bounds, "axis", "z", axis) == pytest.approx([-4, -6, -3])
    assert anchor_offset(bounds, "axis", "y", axis) == pytest.approx([-4, -2, -9])


def _convert(mesh, tmp_path, face=0, **fields):
    files = {"file": ("part.stl", mesh.export(file_type="stl"))}
    up = client.post("/api/meshes", files=files)
    assert up.status_code == 200, up.text
    faces = up.json()["lay_flat"]
    res = client.post(
        "/api/convert",
        files=files,
        data={"lay_flat": ",".join(map(str, faces[face]["normal"])), **fields},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    path = tmp_path / "part.step"
    path.write_bytes(client.get(body["downloads"]["step"]).content)
    return body, read_step(path)


@pytest.mark.parametrize("noise", [0.0, 0.03])
def test_lay_flat_and_bottom_centre_on_xy(tmp_path, noise):
    part = fixtures.rotated(fixtures.holed_plate(), seed=4)
    if noise:
        part = _rotated_noisy(fixtures.holed_plate(), seed=4)
    body, shape = _convert(part, tmp_path, origin="bottom", up="z")
    assert shape.isValid()
    assert face_type_counts(shape) == {"PLANE": 6, "CYLINDER": 4}
    bb = shape.BoundingBox()
    # Lying on its big face, squared to the axes, bottom centre on the origin: exactly.
    assert [bb.xlen, bb.ylen, bb.zlen] == pytest.approx([60, 40, 8], abs=0.3)
    assert bb.zmin == pytest.approx(0, abs=1e-6)
    assert (bb.xmin + bb.xmax) / 2 == pytest.approx(0, abs=1e-6)
    assert (bb.ymin + bb.ymax) / 2 == pytest.approx(0, abs=1e-6)
    _, normal = _bottom_face(shape)
    assert normal == pytest.approx([0, 0, -1], abs=1e-9)
    side = max(
        (f for f in shape.Faces() if f.geomType() == "PLANE" and abs(f.normalAt().z) < 0.5),
        key=lambda f: f.Area(),
    )
    assert max(abs(v) for v in side.normalAt().toTuple()) == pytest.approx(1, abs=1e-9)
    assert body["report"]["size_mm"] == pytest.approx([60, 40, 8], abs=0.3)
    assert body["report"]["fit"]["within_tolerance_pct"] > 99


def test_y_up_puts_the_base_on_xz(tmp_path):
    part = fixtures.rotated(fixtures.boss_plate(), seed=7)  # 60 x 40 x 6 plate, 10 mm boss
    body, shape = _convert(part, tmp_path, origin="corner", up="y")
    assert shape.isValid()
    bb = shape.BoundingBox()
    assert [bb.xmin, bb.ymin, bb.zmin] == pytest.approx([0, 0, 0], abs=1e-6)
    assert bb.ylen == pytest.approx(16, abs=0.3)  # height is along Y
    _, normal = _bottom_face(shape, axis=1)
    assert normal == pytest.approx([0, -1, 0], abs=1e-9)
    # The viewer gets the same placement with Z up: the boss sticks up along Z.
    assert body["report"]["size_mm"][2] == pytest.approx(16, abs=0.3)
    view = np.array(body["report"]["placement"]["matrix"])
    assert np.linalg.det(view[:3, :3]) == pytest.approx(1)


def test_keep_without_lay_flat_changes_nothing():
    data = fixtures.l_bracket().export(file_type="stl")
    res = client.post("/api/convert", files={"file": ("b.stl", data)})
    assert res.status_code == 200
    assert res.json()["report"]["placement"]["matrix"] == np.eye(4).tolist()


def test_bad_placement_options_are_a_422():
    data = fixtures.box().export(file_type="stl")
    for fields in ({"origin": "middle"}, {"up": "x"}, {"lay_flat": "1,2"}, {"lay_flat": "0,0,0"}):
        res = client.post("/api/convert", files={"file": ("b.stl", data)}, data=fields)
        assert res.status_code == 422, fields


def _plate_with_bump(height=0.8):
    import cadquery as cq

    plate = cq.Workplane("XY").box(60, 40, 8).translate((0, 0, 4))
    bump = cq.Workplane("XY").box(10, 10, height).translate((24, 14, -height / 2))
    return plate.union(bump).val()


def test_trim_flush_cuts_a_bump_off_the_base():
    trimmed, depth, note = trim_flush(_plate_with_bump(0.8), 1.0)
    assert note is None and depth == pytest.approx(0.8)
    assert trimmed.isValid()
    assert trimmed.BoundingBox().zmin == pytest.approx(0, abs=1e-9)
    assert trimmed.Volume() == pytest.approx(60 * 40 * 8, rel=1e-9)
    assert face_type_counts(trimmed) == {"PLANE": 6}  # the bump's scar merges into the base


def test_trim_flush_leaves_more_than_the_allowance():
    solid = _plate_with_bump(2.0)
    trimmed, depth, note = trim_flush(solid, 0.5)
    assert trimmed is solid and depth == 0
    assert "2.00 mm below" in note


def test_trim_flush_through_the_api(tmp_path):
    part = fixtures.rotated(fixtures.from_cad(_plate_with_bump(0.8)), seed=3)
    # The face with the bump on it, which the hull tilts about 1 degree (2341 mm2), after the
    # clean top (2400 mm2).
    body, shape = _convert(part, tmp_path, face=1, origin="bottom", trim_flush="1.0")
    assert shape.isValid()
    assert shape.BoundingBox().zlen == pytest.approx(8, abs=0.1)
    assert shape.BoundingBox().zmin == pytest.approx(0, abs=1e-6)
    assert body["report"]["placement"]["trimmed_mm"] == pytest.approx(0.8, abs=0.1)


def _axis(direction, point=(0.0, 0.0, 0.0), kind="hole", diameter=5.0):
    return TurnAxis(np.array(point, float), np.array(direction, float), kind, diameter)


def test_one_axis_per_line():
    a = _axis([0, 0, 1])
    assert a.same_line(_axis([0, 0, -1], (0, 0.05, 7)))  # same line, either way along it
    assert not a.same_line(_axis([0, 0, 1], (0, 0.5, 0)))  # parallel, 0.5 mm over
    assert not a.same_line(_axis([0, 0.1, 1]))  # crossing at 6 degrees
    lines = distinct_axes([a, _axis([0, 0, -1], kind="boss"), _axis([1, 0, 0])])
    assert [x.kind for x in lines] == ["hole", "hole"]


def test_axis_origin_needs_an_axis():
    with pytest.raises(ValueError):
        check_options("axis", "z")
    check_options("axis", "z", axis=_axis([0, 0, 1]))


def _stepped_round():
    """A Ø30 x 10 base under a Ø10 x 20 pin: it stands on the base."""
    import cadquery as cq

    base = cq.Workplane("XY").circle(15).extrude(10)
    return base.union(cq.Workplane("XY").workplane(offset=10).circle(5).extrude(20)).val()


@pytest.mark.parametrize("direction", [[0, 0, 1], [0, 0, -1]])
def test_stands_on_the_broad_end_whichever_way_the_axis_points(direction):
    turn = axis_rotation(_stepped_round(), _axis(direction))
    assert turn @ [0, 0, 1] == pytest.approx([0, 0, 1], abs=1e-12)


def test_no_flat_end_keeps_the_axis_pointing_up():
    import cadquery as cq

    rod = cq.Workplane("XY").circle(5).extrude(30).val()  # both ends alike: a tie
    turn = axis_rotation(rod, _axis([0.6, 0, 0.8]))
    assert turn @ [0.6, 0, 0.8] == pytest.approx([0, 0, 1], abs=1e-12)
    turn = axis_rotation(rod, _axis([0.6, 0, -0.8]))
    assert turn @ [-0.6, 0, 0.8] == pytest.approx([0, 0, 1], abs=1e-12)


TURNED = {}


def _turned(noise):
    if noise not in TURNED:
        part = fixtures.rotated(fixtures.turned_part(), seed=3)
        if noise:
            part = fixtures.add_noise(fixtures.subdivided(part, 1.0), noise, seed=3)
        TURNED[noise] = convert(part, LEVELS["functional"])
    return TURNED[noise]


def _vertical(placement, axis, frame, up=(0.0, 0.0, 1.0)):
    """The axis in that frame (export or view): its direction, and where it crosses the
    ground plane square to up."""
    m = placement.solid_view if frame == "view" else placement.export
    d = m[:3, :3] @ (axis.direction / np.linalg.norm(axis.direction))
    p = (m @ np.r_[axis.point, 1.0])[:3]
    up = np.array(up)
    return d, p - (p @ up) * up


@pytest.mark.parametrize("noise", [0.0, 0.02])
def test_turned_part_stands_on_its_axis_through_the_origin(noise):
    # The lathe part (Ø30 base, ball on top, 44 tall) scanned at an odd angle.
    result = _turned(noise)
    axis = result.axes()[0]
    assert axis.kind == "turned" and axis.diameter_mm == pytest.approx(30, abs=0.1)
    assert result.report()["axes"][0]["type"] == "turned"
    placement = place(result.solid, None, "axis", "z", axis=axis)
    shape = placement.export_solid
    assert shape.isValid()
    bb = shape.BoundingBox()
    assert [bb.xmin, bb.xmax, bb.ymin, bb.ymax] == pytest.approx([-15, 15, -15, 15], abs=0.1)
    assert [bb.zmin, bb.zmax] == pytest.approx([0, 44], abs=0.1)
    assert bb.zmin == pytest.approx(0, abs=1e-9)
    # The axis is exactly vertical and exactly through the origin.
    direction, ground = _vertical(placement, axis, "export")
    assert abs(direction[2]) == pytest.approx(1, abs=1e-12)
    assert ground == pytest.approx([0, 0, 0], abs=1e-9)
    # Standing on its base, not its ball.
    face, normal = _bottom_face(shape)
    assert normal == pytest.approx([0, 0, -1], abs=1e-12)
    assert face.Area() == pytest.approx(np.pi * 14**2, rel=0.02)
    assert placement.as_dict()["axis"]["type"] == "turned"


def test_turned_part_on_its_axis_with_y_up():
    result = _turned(0.0)
    axis = result.axes()[0]
    placement = place(result.solid, None, "axis", "y", axis=axis)
    bb = placement.export_solid.BoundingBox()
    assert [bb.ymin, bb.ymax] == pytest.approx([0, 44], abs=0.1)
    direction, ground = _vertical(placement, axis, "export", up=(0.0, 1.0, 0.0))
    assert abs(direction[1]) == pytest.approx(1, abs=1e-12)
    assert ground == pytest.approx([0, 0, 0], abs=1e-9)
    # The viewer stays Z up: there the axis is vertical along Z.
    direction, ground = _vertical(placement, axis, "view")
    assert abs(direction[2]) == pytest.approx(1, abs=1e-12)
    assert ground == pytest.approx([0, 0, 0], abs=1e-9)


def test_plate_stands_on_a_feature_axis_square_to_x():
    # The surfaces plate (70 x 50 x 6) with a tapered boss (8 mm tall) and other turned
    # features, scanned at an odd angle; stood on the boss's axis it lies flat, long side on X.
    result = convert(fixtures.rotated(fixtures.surfaces_plate(), seed=5), LEVELS["functional"])
    kinds = [a.kind for a in result.axes()]
    assert sorted(kinds) == ["countersink", "countersink", "dome", "revolved_boss", "tapered_boss"]
    axis = result.axes()[kinds.index("tapered_boss")]
    placement = place(result.solid, None, "axis", "z", axis=axis)
    bb = placement.export_solid.BoundingBox()
    assert [bb.xlen, bb.ylen, bb.zlen] == pytest.approx([70, 50, 14], abs=0.1)
    assert bb.zmin == pytest.approx(0, abs=1e-9)
    _, ground = _vertical(placement, axis, "export")
    assert ground == pytest.approx([0, 0, 0], abs=1e-9)
