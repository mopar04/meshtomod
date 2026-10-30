import functools
import io
import json
import zipfile

import cadquery as cq
import numpy as np
import pytest
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib

from meshtomod import fixtures as fx
from meshtomod.convert import convert
from meshtomod.history import _trim_op, build_history, replay
from meshtomod.history_files import featurescript_literal, parameters, write_history_zips
from meshtomod.orient import Z_TO_Y, place, transform_shape
from meshtomod.step_io import face_type_counts, read_step, write_step
from meshtomod.tolerance import LEVELS


def noisy(make, sigma: float = 0.05, seed: int = 3):
    return fx.rotated(fx.add_noise(fx.subdivided(make(), 1.0), sigma, seed=seed), seed)


@functools.cache
def converted(name: str):
    """Each part converted once for every test here (they only read the result)."""
    make = {
        "holed_plate": fx.holed_plate,
        "standoff_plate": lambda: noisy(fx.standoff_plate),
        "bracket": lambda: noisy(fx.bracket),
        "turned_part": fx.turned_part,
        "surfaces_plate": fx.surfaces_plate,
    }[name]
    return convert(make(), LEVELS["functional"])


def same_solid(a: cq.Shape, b: cq.Shape) -> None:
    assert b.isValid()
    assert face_type_counts(b) == face_type_counts(a)
    assert abs(b.Volume() - a.Volume()) < 1e-4 * a.Volume()
    assert np.allclose(exact_box(a), exact_box(b), atol=1e-3)


def exact_box(shape: cq.Shape) -> list[float]:
    """The tight bounding box: OpenCASCADE's quick one grows around curved faces."""
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape.wrapped, box, False, False)
    return list(box.Get())


# (steps after the first, first step)
PARTS = {
    "holed_plate": (["extrude:cut"] * 4, "base"),
    "standoff_plate": (["extrude:join", "extrude:cut"], "base"),
    "bracket": (["fillet:", "extrude:cut", "extrude:cut"], "base"),
    "turned_part": (["extrude:cut"], "revolve"),
    "surfaces_plate": (["revolve:join"] * 3 + ["revolve:cut"] * 3, "base"),
}


@pytest.mark.parametrize("name", PARTS)
def test_history_rebuilds_the_model(name):
    steps, first = PARTS[name]
    result = converted(name)
    history, base = build_history(result)

    ops = history["ops"]
    assert ops[0]["op"] == first
    assert [f"{o['op']}:{o.get('mode', '')}" for o in ops[1:]] == steps
    assert (base is not None) == (first == "base")
    solid, failed = replay(history, base)
    assert failed == []
    same_solid(result.solid, solid)


def test_holes_are_sketched_on_their_face_with_their_own_size():
    result = converted("holed_plate")
    history, _ = build_history(result)
    holes = [o for o in history["ops"] if o["op"] == "extrude"]
    assert [o["id"] for o in holes] == ["hole_1", "hole_2", "hole_3", "hole_4"]
    for o in holes:
        assert o["through"] and o["from_face"]
        assert o["sketch"]["circle"]["diameter"] == pytest.approx(6.6)
        # Each centre is on the plate's top or bottom face (the plate is 8 mm, centred on 0).
        assert abs(o["plane"]["origin"][2]) == pytest.approx(4.0, abs=0.01)
    keys = [k for k, _, _ in parameters(history)]
    assert keys == ["hole_1_diameter", "hole_2_diameter", "hole_3_diameter", "hole_4_diameter"]


def test_changing_a_size_changes_only_that_feature():
    result = converted("holed_plate")
    history, base = build_history(result)
    before, _ = replay(history, base)
    history["ops"][1]["sketch"]["circle"]["diameter"] = 10.0
    after, failed = replay(history, base)
    assert failed == []
    grown = np.pi / 4 * (10.0**2 - 6.6**2) * 8.0
    assert before.Volume() - after.Volume() == pytest.approx(grown, rel=1e-3)


def test_history_follows_the_placement():
    result = converted("standoff_plate")
    placement = place(result.solid, None, "corner", "y")
    history, base = build_history(result, matrix=placement.export)
    solid, failed = replay(history, base)
    assert failed == []
    same_solid(placement.export_solid, solid)


def test_round_body_and_trim_steps_replay():
    history = {
        "ops": [
            {
                "op": "extrude",
                "name": "Round body",
                "id": "round_body",
                "mode": "new",
                "plane": {"origin": [0, 0, -2], "x_dir": [1, 0, 0], "normal": [0, 0, 1]},
                "sketch": {"circle": {"center": [0, 0], "diameter": 20}},
                "distance": 32,
            },
            {
                "op": "extrude",
                "name": "Trim flush",
                "id": "trim_flush",
                "mode": "cut",
                "plane": {"origin": [0, 0, 0], "x_dir": [1, 0, 0], "normal": [0, 0, -1]},
                "sketch": {
                    "rect": {"center": [0, 0], "width": 60, "height": 60, "corner_radius": 0}
                },
                "distance": 10,
            },
            {
                "op": "fillet",
                "name": "Top fillet",
                "id": "top_fillet",
                "radius": 2,
                "face": {"point": [0, 0, 30], "normal": [0, 0, 1]},
            },
        ]
    }
    solid, failed = replay(history)
    assert failed == []
    bb = solid.BoundingBox()
    assert bb.zmin == pytest.approx(0.0) and bb.zmax == pytest.approx(30.0)
    assert face_type_counts(solid) == {"PLANE": 2, "CYLINDER": 1, "TORUS": 1}


def test_a_step_that_fails_is_left_out():
    history = {
        "ops": [
            {
                "op": "extrude",
                "name": "Block",
                "id": "block",
                "mode": "new",
                "plane": {"origin": [0, 0, 0], "x_dir": [1, 0, 0], "normal": [0, 0, 1]},
                "sketch": {
                    "rect": {"center": [0, 0], "width": 20, "height": 20, "corner_radius": 3}
                },
                "distance": 5,
            },
            {
                "op": "fillet",
                "name": "Fillet 1",
                "id": "fillet_1",
                "radius": 1,
                "edges": [
                    [{"normal": [1, 0, 0], "offset": 50}, {"normal": [0, 1, 0], "offset": 50}]
                ],
            },
        ]
    }
    solid, failed = replay(history)
    assert failed == ["Fillet 1"]
    assert solid.Volume() == pytest.approx((400 - (4 - np.pi) * 9) * 5, rel=1e-6)


def test_zips_hold_a_script_for_each_tool(tmp_path):
    result = converted("standoff_plate")
    history, base = build_history(result)
    write_step(result.solid, tmp_path / "model.step")
    base_path = write_step(base, tmp_path / "base.step")
    names = write_history_zips(tmp_path, history, "standoff plate", base_path)
    assert names == {
        "fusion": "history-fusion.zip",
        "onshape": "history-onshape.zip",
        "freecad": "history-freecad.zip",
        "all": "history-all.zip",
    }

    def files(name: str) -> dict[str, bytes]:
        with zipfile.ZipFile(io.BytesIO((tmp_path / name).read_bytes())) as z:
            return {n: z.read(n) for n in z.namelist()}

    fusion = files("history-fusion.zip")
    assert sorted(fusion) == [
        "HOW_TO_OPEN.txt",
        "standoff_plate/base.step",
        "standoff_plate/standoff_plate.manifest",
        "standoff_plate/standoff_plate.py",
    ]
    freecad = files("history-freecad.zip")
    assert sorted(freecad) == ["HOW_TO_OPEN.txt", "base.step", "standoff_plate.FCMacro"]
    for script in (fusion["standoff_plate/standoff_plate.py"], freecad["standoff_plate.FCMacro"]):
        text = script.decode()
        compile(text, "script", "exec")
        assert "__HISTORY__" not in text and "__NAME__" not in text
        # The steps travel inside the script, exactly.
        data = text.split("HISTORY = json.loads(", 1)[1].split(")\n", 1)[0]
        assert json.loads(json.loads(data)) == history

    onshape = files("history-onshape.zip")["standoff_plate.fs"].decode()
    assert "__HISTORY__" not in onshape and "__PARAMETERS__" not in onshape
    assert "definition.part is Query;" in onshape
    assert "isLength(definition.boss_1_diameter, { (millimeter) : [0.001, 12" in onshape
    assert "isLength(definition.hole_1_diameter" in onshape
    assert read_step(tmp_path / "base.step").isValid()
    everything = files("history-all.zip")
    assert "standoff_plate.step" in everything and "freecad/standoff_plate.FCMacro" in everything


def test_featurescript_literal():
    text = featurescript_literal({"a": [1, 2.5, 1e-9, -0.0], "b": True, "c": 'say "hi"'})
    assert text == '{ "a" : [1, 2.5, 0.000000001, 0], "b" : true, "c" : "say \\"hi\\"" }'


@pytest.mark.parametrize("up", ["z", "y"])
def test_trim_flush_step_cuts_off_what_was_trimmed(up):
    block = cq.Workplane("XY").box(30, 20, 10, centered=(True, True, False)).val()
    bump = cq.Workplane("XY").workplane(offset=-0.5).circle(3).extrude(0.6).val()
    part = block.fuse(bump).clean()
    if up == "y":  # the STEP's frame for Y up: Z turned onto Y
        part, block = (transform_shape(s, Z_TO_Y) for s in (part, block))
    history = {"ops": [{"op": "base", "name": "Base", "file": "base.step"}, _trim_op(block, up)]}
    solid, failed = replay(history, part)
    assert failed == []
    same_solid(block, solid)
