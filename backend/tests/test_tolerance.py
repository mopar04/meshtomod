import pytest

from nurbsmith.tolerance import DEFAULT_LEVEL, LEVELS, get_level


def test_levels_tighten_monotonically():
    rough, functional, precise = (LEVELS[k] for k in ("rough", "functional", "precise"))
    assert rough.fit_tolerance_mm > functional.fit_tolerance_mm > precise.fit_tolerance_mm
    assert rough.min_feature_mm > functional.min_feature_mm > precise.min_feature_mm


def test_default_is_functional():
    assert DEFAULT_LEVEL.key == "functional"


def test_overrides_do_not_mutate_preset():
    custom = get_level("functional").with_overrides(fit_tolerance_mm=0.2)
    assert custom.fit_tolerance_mm == 0.2
    assert get_level("functional").fit_tolerance_mm == 0.3


def test_unknown_level():
    with pytest.raises(ValueError):
        get_level("ultra")
