import pytest

from meshtomod import fixtures
from meshtomod.mesh_io import MeshLoadError, load_mesh, summarize


@pytest.mark.parametrize("fmt", ["stl", "obj", "ply", "3mf"])
def test_round_trip_formats(fmt):
    data = fixtures.box().export(file_type=fmt)
    if isinstance(data, str):
        data = data.encode()
    mesh = load_mesh(data, f"part.{fmt}")
    summary = summarize(mesh)
    assert summary.watertight
    assert summary.extents == pytest.approx([40.0, 30.0, 20.0], abs=1e-4)
    assert summary.volume == pytest.approx(40 * 30 * 20, rel=1e-6)


def test_noise_keeps_shape_roughly():
    noisy = fixtures.add_noise(fixtures.box(), sigma_mm=0.05)
    assert summarize(noisy).extents == pytest.approx([40.0, 30.0, 20.0], abs=0.5)


def test_plate_with_hole_is_watertight():
    assert fixtures.plate_with_hole().is_watertight


def test_rejects_unknown_format():
    with pytest.raises(MeshLoadError):
        load_mesh(b"solid x", "part.dwg")


def test_rejects_garbage():
    with pytest.raises(MeshLoadError):
        load_mesh(b"not a mesh", "part.stl")
