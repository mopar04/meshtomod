"""Tolerance levels. One user-facing choice sets every threshold in the pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace


@dataclass(frozen=True)
class ToleranceLevel:
    key: str
    label: str
    fit_tolerance_mm: float
    """Max mesh-to-surface distance before a region is split or rejected."""
    min_feature_mm: float
    """Noise filter: regions smaller than this are merged into their neighbours."""
    keep_fillets: bool
    """False turns every fillet into a sharp edge."""
    min_fillet_radius_mm: float
    """Fillets below this radius become sharp edges (when keep_fillets is True)."""
    angle_snap_deg: float
    """Angles within this of 0/90/180 snap to the exact value."""

    def with_overrides(self, **overrides) -> ToleranceLevel:
        return replace(self, **overrides)

    def as_dict(self) -> dict:
        return asdict(self)


# Starting values; tune against real scans (see docs/DECISIONS.md).
ROUGH = ToleranceLevel("rough", "Rough", 1.0, 5.0, False, 0.0, 3.0)
FUNCTIONAL = ToleranceLevel("functional", "Functional", 0.3, 2.0, True, 2.0, 1.5)
PRECISE = ToleranceLevel("precise", "Precise", 0.1, 0.5, True, 0.0, 0.5)

LEVELS: dict[str, ToleranceLevel] = {lvl.key: lvl for lvl in (ROUGH, FUNCTIONAL, PRECISE)}
DEFAULT_LEVEL = FUNCTIONAL


def get_level(key: str) -> ToleranceLevel:
    try:
        return LEVELS[key]
    except KeyError:
        raise ValueError(
            f"Unknown tolerance level {key!r}; expected one of {sorted(LEVELS)}"
        ) from None
