import cadquery as cq
import pytest

from meshtomod import cells
from meshtomod.brep import UnsupportedGeometry


def row(n: int) -> list[cq.Solid]:
    """Unit cubes side by side, as the cell blocks of a bar."""
    return [cq.Solid.makeBox(1, 1, 1, cq.Vector(i, 0, 0)) for i in range(n)]


def breaking(monkeypatch, bad: cq.Shape):
    """Make any fuse that takes in bad come back empty, as OpenCASCADE does with some slivers
    from scans with gaps."""
    for kind in (cq.Shape, cq.Compound):
        monkeypatch.setattr(kind, "fuse", _broken(kind.fuse, bad))


def _broken(fuse, bad):
    def broken(self, *others, **kw):
        if any(s is bad for s in (self, *others)):
            return cq.Compound.makeCompound([])
        return fuse(self, *others, **kw)

    return broken


def test_blocks_fuse_into_one_solid():
    solid = cells._fuse_all(row(40))
    assert len(solid.Solids()) == 1
    assert solid.Volume() == pytest.approx(40)


def test_a_sliver_that_breaks_the_fuse_is_left_out(monkeypatch):
    sliver = cq.Solid.makeBox(1, 1, 1e-3, cq.Vector(0, 0, 1))
    blocks = row(40)
    blocks.insert(3, sliver)
    breaking(monkeypatch, sliver)

    solid = cells._fuse_all(blocks)
    assert len(solid.Solids()) == 1
    assert solid.Volume() == pytest.approx(40)


def test_too_much_left_out_is_refused(monkeypatch):
    blocks = row(40)
    breaking(monkeypatch, blocks[5])
    with pytest.raises(UnsupportedGeometry):
        cells._fuse_all(blocks)
