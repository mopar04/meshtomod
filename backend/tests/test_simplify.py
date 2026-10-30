import pytest

from meshtomod import fixtures as fx
from meshtomod.convert import convert
from meshtomod.quality import estimate_noise
from meshtomod.simplify import SIMPLIFIED, cap, simplify, simplify_for_level
from meshtomod.tolerance import LEVELS


def dense_scan(make, sigma: float = 0.03, edge: float = 0.3, seed: int = 3):
    """Much denser than the part needs, noisy and turned: like a scanner's raw export."""
    return fx.rotated(fx.add_noise(fx.subdivided(make(), edge), sigma, seed=seed), seed)


def test_small_scans_are_left_alone():
    mesh = fx.l_bracket()
    for level in LEVELS.values():
        out, done = simplify(mesh, level)
        assert out is mesh and done is None


def test_levels_keep_more_triangles_as_they_tighten():
    rough, functional, precise = (LEVELS[k] for k in ("rough", "functional", "precise"))
    assert rough.simplify_above_faces < functional.simplify_above_faces
    assert functional.simplify_above_faces < precise.simplify_above_faces


def test_turned_part_keeps_its_4mm_cross_hole():
    """Zac's turned part: simplified to a tenth, its Ø4 cross hole and every turned surface
    come back as drawn, and the model still matches the full scan."""
    scan = dense_scan(fx.turned_part)
    level = LEVELS["functional"].with_overrides(simplify_above_faces=len(scan.faces) // 10)
    light, done = simplify(scan, level)
    assert len(light.faces) <= 1.05 * len(scan.faces) // 10
    assert not done.softened and done.error_mm < level.fit_tolerance_mm / 2
    # Smoothing doesn't hide the scanner's noise from the accuracy checks.
    assert estimate_noise(light) == pytest.approx(estimate_noise(scan), rel=0.3)

    result = convert(light, level)
    holes = [f for f in result.report()["features"] if f["type"] == "hole"]
    assert [h["diameter_mm"] for h in holes] == [4.0]
    full = convert(scan, level)
    assert result.report()["face_types"] == full.report()["face_types"]
    assert result.solid.Volume() == pytest.approx(full.solid.Volume(), rel=0.002)
    assert result.fit.within_tolerance_pct > 99.5


def test_holes_survive_on_every_level():
    scan = dense_scan(fx.holed_plate, edge=0.4)
    for key, level in LEVELS.items():
        light, done = simplify(scan, level.with_overrides(simplify_above_faces=60_000))
        assert done is not None and not done.softened, key
        report = convert(light, LEVELS["functional"]).report()
        holes = sorted(f["diameter_mm"] for f in report["features"] if f["type"] == "hole")
        assert holes == [6.6] * 4, key


def test_a_simplification_that_loses_detail_is_redone_with_more():
    scan = dense_scan(fx.turned_part)
    precise = LEVELS["precise"]
    # 2,000 triangles can't hold the turned part to Precise: the check fails and retries
    # keep more, up to the limit, where it is kept and flagged.
    light, done = simplify(scan, precise, target=2_000, limit=4_000)
    assert done.softened and len(light.faces) > 3_000
    assert "may be softened" in done.note()
    # With room to grow it finds a count that passes.
    light, done = simplify(scan, precise, target=2_000)
    assert not done.softened and len(light.faces) > 2_000


def test_a_scan_that_only_passes_whole_is_kept_whole():
    scan = dense_scan(fx.turned_part)
    out, done = simplify(scan, LEVELS["precise"], target=2_000, limit=len(scan.faces) + 1)
    assert done is None or not done.softened
    if done is None:
        assert out is scan


def test_cap_then_level_reports_from_the_upload():
    scan = dense_scan(fx.holed_plate, edge=0.4)
    n = len(scan.faces)
    scan.metadata["loose_pieces"] = 2
    capped = cap(scan) if n <= LEVELS["precise"].simplify_above_faces else scan
    assert capped is scan  # under the cap: untouched
    # As cap would leave it after a big upload:
    first, earlier = simplify(scan, LEVELS["precise"], target=n // 2)
    first.metadata[SIMPLIFIED] = earlier
    level = LEVELS["rough"].with_overrides(simplify_above_faces=n // 8)
    light, done = simplify_for_level(first, level)
    assert done.from_faces == n and done.to_faces == len(light.faces)
    assert done.error_mm >= earlier.error_mm
    assert SIMPLIFIED not in light.metadata
    assert light.metadata["loose_pieces"] == 2
    assert f"Simplified from {n:,} to {len(light.faces):,} triangles" in done.note()
