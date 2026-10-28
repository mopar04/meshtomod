import cadquery as cq
import pytest

from meshtomod.step_io import face_type_counts, read_step, write_step


def test_step_round_trip_keeps_analytic_faces(tmp_path):
    """Toolchain check: a filleted plate with a hole survives STEP export as real CAD faces."""
    part = cq.Workplane("XY").box(60, 40, 8).edges("|Z").fillet(5).faces(">Z").workplane().hole(12)
    path = write_step(part, tmp_path / "plate.step")
    shape = read_step(path)

    assert shape.isValid()
    assert shape.Volume() == pytest.approx(part.val().Volume(), rel=1e-6)
    counts = face_type_counts(shape)
    assert counts.get("PLANE", 0) >= 6
    assert counts.get("CYLINDER", 0) >= 5  # 4 fillets + the hole
    assert "BSPLINE" not in counts
