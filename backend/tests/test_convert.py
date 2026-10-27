import pytest

from meshtomod import fixtures as fx
from meshtomod.convert import UnsupportedGeometry, convert
from meshtomod.quality import estimate_noise
from meshtomod.step_io import face_type_counts, read_step, write_step
from meshtomod.tolerance import LEVELS

PARTS = {
    "box": (fx.box, 6),
    "l_bracket": (fx.l_bracket, 8),
    "stepped_block": (fx.stepped_block, 10),
    "frame": (fx.frame, 10),
}


def scanned(make, sigma: float, seed: int = 3):
    """Dense, noisy, arbitrarily oriented: like a real scan of the part."""
    return fx.rotated(fx.add_noise(fx.subdivided(make(), 2.0), sigma, seed=seed), seed)


@pytest.mark.parametrize("name", PARTS)
def test_clean_mesh_rebuilds_exactly(name):
    make, faces = PARTS[name]
    truth = make()
    result = convert(truth, LEVELS["functional"])
    assert result.solid.isValid()
    assert face_type_counts(result.solid) == {"PLANE": faces}
    assert result.solid.Volume() == pytest.approx(truth.volume, rel=1e-6)
    assert result.fit.within_tolerance_pct == 100.0


@pytest.mark.parametrize("name", PARTS)
@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("sigma", [0.02, 0.1, 0.3])
def test_noisy_scan_recovers_true_faces(name, level, sigma):
    make, faces = PARTS[name]
    truth = make()
    result = convert(scanned(make, sigma), LEVELS[level])

    assert result.solid.isValid()
    assert face_type_counts(result.solid) == {"PLANE": faces}
    # Mean surface offset (volume error / area) stays far below the noise.
    offset = abs(result.solid.Volume() - truth.volume) / truth.area
    assert offset < max(0.02, sigma / 5)


def test_noise_estimate_tracks_true_noise():
    for sigma in (0.02, 0.05, 0.1, 0.3):
        assert estimate_noise(scanned(fx.l_bracket, sigma)) == pytest.approx(sigma, rel=0.15)
    assert estimate_noise(fx.box()) == pytest.approx(0.0, abs=1e-6)


def test_rough_scan_widens_tolerance_and_warns():
    result = convert(scanned(fx.frame, 0.1), LEVELS["precise"])
    q = result.scan
    assert q.suggested_level == "rough"
    assert q.effective_tolerance_mm > LEVELS["precise"].fit_tolerance_mm
    assert "too rough for Precise" in q.warning
    # Scored against what the user asked for, so the number is honest.
    assert result.fit.within_tolerance_pct < 90


def test_clean_scan_has_no_warning():
    result = convert(scanned(fx.l_bracket, 0.02), LEVELS["functional"])
    assert result.scan.warning is None
    assert result.fit.within_tolerance_pct > 99


def test_freeform_parts_are_refused_not_faceted():
    with pytest.raises(UnsupportedGeometry, match="curved"):
        convert(fx.ball(), LEVELS["functional"])


def test_round_parts_become_cylinders():
    result = convert(fx.cylinder(), LEVELS["functional"])
    assert face_type_counts(result.solid) == {"PLANE": 2, "CYLINDER": 1}
    (body,) = result.report()["features"]
    assert body["type"] == "body" and body["diameter_mm"] == pytest.approx(20.0, abs=0.1)

    result = convert(fx.plate_with_hole(), LEVELS["functional"])
    assert face_type_counts(result.solid) == {"PLANE": 6, "CYLINDER": 1}
    assert [f["type"] for f in result.report()["features"]] == ["hole"]


def test_step_export_of_converted_part(tmp_path):
    result = convert(scanned(fx.stepped_block, 0.05), LEVELS["functional"])
    shape = read_step(write_step(result.solid, tmp_path / "steps.step"))
    assert shape.isValid()
    assert face_type_counts(shape) == {"PLANE": 10}
    assert shape.Volume() == pytest.approx(result.solid.Volume(), rel=1e-6)
