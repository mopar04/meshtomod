"""Turn cylindrical patches into CAD features: holes, bosses and fillets.

The base solid is rebuilt from the planes alone, as if every hole were filled, every boss cut
off and every fillet sharp. The features are then applied with ordinary CAD operations (fuse a
cylinder, fillet an edge, cut a cylinder), so the STEP file holds the same kind of geometry a
designer would have made, with clean sizes snapped to standard values.
"""

from __future__ import annotations

from dataclasses import dataclass

import cadquery as cq
import numpy as np
from scipy.optimize import minimize_scalar

from .cylinders import FULL_COVERAGE, CurvedPatch, Cylinder, fit_circle_on_axis
from .segment import Geo, Plane, Region
from .tolerance import ToleranceLevel

# A plane is an end of a cylinder if its normal is within this angle of the axis, and a side
# (tangent to a fillet) if within this angle of perpendicular.
END_ANGLE_DEG = 15.0
SIDE_ANGLE_DEG = 15.0
# The two faces a fillet joins must meet at an angle (|cos| below this), not be parallel.
MAX_SIDE_DOT = np.cos(np.radians(20.0))
# Snap a size to a standard value if that moves the surface by at most this share of the
# fit tolerance.
SNAP_SHARE = 0.5
# Cutting and fusing tools overshoot the faces they end on by this much, so no sliver is left.
OVERSHOOT_MM = 1.0

# Common drill sizes (mm) and what they are for.
_METRIC = {
    "M2": (2.4, 2.2, 1.6),
    "M2.5": (2.9, 2.7, 2.05),
    "M3": (3.4, 3.2, 2.5),
    "M4": (4.5, 4.3, 3.3),
    "M5": (5.5, 5.3, 4.2),
    "M6": (6.6, 6.4, 5.0),
    "M8": (9.0, 8.4, 6.8),
    "M10": (11.0, 10.5, 8.5),
    "M12": (13.5, 13.0, 10.2),
    "M16": (17.5, 17.0, 14.0),
    "M20": (22.0, 21.0, 17.5),
}
_INCH = {
    "1/8": 1 / 8,
    "5/32": 5 / 32,
    "3/16": 3 / 16,
    "1/4": 1 / 4,
    "5/16": 5 / 16,
    "3/8": 3 / 8,
    "7/16": 7 / 16,
    "1/2": 1 / 2,
    "5/8": 5 / 8,
    "3/4": 3 / 4,
    "1": 1.0,
}
STANDARD_HOLES: list[tuple[float, str]] = sorted(
    [(d, f"{m} clearance") for m, (d, _, _) in _METRIC.items()]
    + [(d, f"{m} close fit") for m, (_, d, _) in _METRIC.items()]
    + [(d, f"{m} tap drill") for m, (_, _, d) in _METRIC.items()]
    + [(round(v * 25.4, 3), f'{k}"') for k, v in _INCH.items()]
)


@dataclass
class Feature:
    kind: str
    """"hole", "boss", "fillet", or "sharp" for a fillet the accuracy level leaves out."""
    patch: int
    radius: float
    axis: np.ndarray
    start: np.ndarray
    """Axis point where the feature starts: the base face for a boss, the far end of a hole."""
    end: np.ndarray
    """Axis point where it ends: the boss top, or the face a hole is drilled from."""
    planes: tuple[int, ...] = ()
    """For a fillet, the two planes it joins."""
    through: bool = False
    label: str | None = None
    """Standard size it was snapped to, if any."""

    def as_dict(self) -> dict:
        out: dict = {"type": self.kind}
        if self.kind == "fillet":
            out["radius_mm"] = round(self.radius, 3)
        else:
            out["diameter_mm"] = round(2 * self.radius, 3)
            length = float(np.linalg.norm(self.end - self.start))
            if self.kind == "boss":
                out["height_mm"] = round(length, 3)
            elif self.through:
                out["through"] = True
            else:
                out["depth_mm"] = round(length, 3)
        if self.label:
            out["standard"] = self.label
        return out


def plan_features(
    mesh: Geo,
    planes: list[Region],
    patches: list[CurvedPatch],
    shared: dict[tuple[int, int], float],
    level: ToleranceLevel,
) -> tuple[list[Feature], set[int], str | None]:
    """Decide what each patch is. Returns the features, the planes that belong to a feature
    rather than to the base solid (the floor of a blind hole, the top of a boss), and a note
    on any curved areas left to the flat faces around them because they fit no feature."""
    tol = level.fit_tolerance_mm
    by_id = {r.id: r for r in planes}
    cos_end = np.cos(np.radians(END_ANGLE_DEG))
    sin_side = np.sin(np.radians(SIDE_ANGLE_DEG))
    directions = _directions(planes)
    features: list[Feature] = []
    caps: set[int] = set()
    skipped: list[str] = []

    for patch in patches:
        cyl = patch.cylinder
        nbrs = [by_id[n] for n in patch.neighbours if n in by_id]
        ends = [r for r in nbrs if abs(r.plane.normal @ cyl.axis) >= cos_end]
        sides = [r for r in nbrs if abs(r.plane.normal @ cyl.axis) <= sin_side]
        pts = mesh.vertices[np.unique(mesh.faces[patch.faces])]

        if patch.coverage >= FULL_COVERAGE:
            cyl = _snap_axis(cyl, directions, level, pts)
            cap_ends = [r for r in ends if r.neighbours <= {patch.id}]
            open_ends = [r for r in ends if r not in cap_ends]
            kind = "boss" if patch.convex else "hole"
            if kind == "boss" and (len(cap_ends) != 1 or len(open_ends) != 1):
                skipped.append(f"a round boss \u00d8{2 * cyl.radius:.1f} mm without a flat top")
                continue
            if kind == "hole" and len(cap_ends) > 1:
                skipped.append("a closed round cavity")
                continue
            radius, label = _snap_radius(cyl.radius, tol, kind)
            if kind == "boss":
                start = _axis_hit(cyl, open_ends[0].plane)
                end = _axis_hit(cyl, cap_ends[0].plane)
                caps.add(cap_ends[0].id)
                features.append(
                    Feature("boss", patch.id, radius, cyl.axis, start, end, label=label)
                )
                continue
            # Hole: from its floor (or the far face) out to the face it was drilled from.
            t = (pts - cyl.point) @ cyl.axis
            lo, hi = cyl.point + t.min() * cyl.axis, cyl.point + t.max() * cyl.axis
            hits = [(_axis_hit(cyl, r.plane), r) for r in ends]
            through = not cap_ends
            if cap_ends:
                caps.add(cap_ends[0].id)
                start = _axis_hit(cyl, cap_ends[0].plane)
                others = [p for p, r in hits if r is not cap_ends[0]]
                far = lo if np.linalg.norm(lo - start) > np.linalg.norm(hi - start) else hi
                end = others[0] if others else far
            else:
                start = hits[0][0] if hits else lo
                end = hits[1][0] if len(hits) > 1 else (hi if hits else hi)
                if len(hits) == 1:
                    far = lo if np.linalg.norm(lo - start) > np.linalg.norm(hi - start) else hi
                    end, start = start, far
            features.append(
                Feature(
                    "hole", patch.id, radius, cyl.axis, start, end, through=through, label=label
                )
            )
            continue

        # Part of the way round: a fillet between the two planes it is tangent to.
        if len(sides) < 2:
            skipped.append(f"a curved surface (radius about {cyl.radius:.1f} mm)")
            continue
        sides.sort(key=lambda r: -shared.get((min(r.id, patch.id), max(r.id, patch.id)), 0.0))
        a = sides[0]
        crossing = [r for r in sides[1:] if abs(r.plane.normal @ a.plane.normal) < MAX_SIDE_DOT]
        if not crossing:
            skipped.append(f"a curved surface (radius about {cyl.radius:.1f} mm)")
            continue
        b = crossing[0]
        radius = _fillet_radius(a.plane, b.plane, patch.convex, pts, cyl.radius)
        radius, label = _snap_radius(radius, tol, "fillet")
        # Below the level's smallest kept fillet, the edge is simply made sharp.
        kind = "fillet"
        if not level.keep_fillets or radius < level.min_fillet_radius_mm:
            kind = "sharp"
        axis = np.cross(a.plane.normal, b.plane.normal)
        axis /= np.linalg.norm(axis)
        features.append(
            Feature(kind, patch.id, radius, axis, pts.mean(0), pts.mean(0), (a.id, b.id))
        )
    return features, caps, _skipped_note(skipped)


def apply_features(
    base: cq.Solid, features: list[Feature], planes: list[Region]
) -> tuple[cq.Solid, str | None]:
    """Base solid, then bosses, then fillets, then holes: the order a designer would use.

    A feature that OpenCASCADE cannot apply (a fillet too big for its edge, say) is left out
    and noted, rather than failing the whole part."""
    by_id = {r.id: r for r in planes}
    solid: cq.Shape = base
    failed: list[str] = []

    def attempt(f: Feature, op) -> None:
        nonlocal solid
        try:
            result = op(solid)
            parts = result.Solids()
            if len(parts) == 1 and parts[0].isValid():
                solid = parts[0]
                return
        except Exception:  # OpenCASCADE raises a variety of errors here
            pass
        failed.append(f.kind)

    for f in features:
        if f.kind == "boss":
            rod = _rod(f.start - OVERSHOOT_MM * _unit(f.end - f.start), f.end, f.radius)
            attempt(f, lambda s, rod=rod: s.fuse(rod).clean())
    for f in features:
        if f.kind == "fillet":
            a, b = by_id[f.planes[0]].plane, by_id[f.planes[1]].plane
            attempt(f, lambda s, a=a, b=b, r=f.radius: s.fillet(r, _edges_on(s, a, b)))
    for f in features:
        if f.kind == "hole":
            d = _unit(f.end - f.start)
            start = f.start - OVERSHOOT_MM * d if f.through else f.start
            rod = _rod(start, f.end + OVERSHOOT_MM * d, f.radius)
            attempt(f, lambda s, rod=rod: s.cut(rod).clean())
    note = None
    if failed:
        note = f"Could not apply {len(failed)} feature(s) ({', '.join(failed)}); left out."
    return solid, note


# ---------------------------------------------------------------------------------------------


def _skipped_note(skipped: list[str]) -> str | None:
    if not skipped:
        return None
    listed = "; ".join(sorted(set(skipped)))
    return (
        f"{len(skipped)} curved area{'s' if len(skipped) > 1 else ''} could not be made into a "
        f"hole, boss or fillet yet and {'were' if len(skipped) > 1 else 'was'} left to the flat "
        f"faces around {'them' if len(skipped) > 1 else 'it'} ({listed})."
    )


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


def _rod(start: np.ndarray, end: np.ndarray, radius: float) -> cq.Solid:
    length = float(np.linalg.norm(end - start))
    return cq.Solid.makeCylinder(
        radius, length, cq.Vector(*start), cq.Vector(*((end - start) / length))
    )


def _axis_hit(cyl: Cylinder, plane: Plane) -> np.ndarray:
    t = (plane.offset - plane.normal @ cyl.point) / (plane.normal @ cyl.axis)
    return cyl.point + t * cyl.axis


def _directions(planes: list[Region]) -> np.ndarray:
    normals = np.array([r.plane.normal for r in planes])
    return normals if len(normals) else np.zeros((0, 3))


def _snap_axis(
    cyl: Cylinder, directions: np.ndarray, level: ToleranceLevel, pts: np.ndarray
) -> Cylinder:
    """Square a hole or boss up with the part when it is nearly square already."""
    if not len(directions):
        return cyl
    dots = np.abs(directions @ cyl.axis)
    best = int(np.argmax(dots))
    if dots[best] < np.cos(np.radians(level.angle_snap_deg)):
        return cyl
    axis = directions[best] * np.sign(directions[best] @ cyl.axis)
    return fit_circle_on_axis(pts, axis, cyl)


def _snap_radius(radius: float, tol: float, kind: str) -> tuple[float, str | None]:
    budget = SNAP_SHARE * tol
    if kind == "hole":
        d = 2 * radius
        named = min(STANDARD_HOLES, key=lambda s: abs(s[0] - d))
        if abs(named[0] - d) / 2 <= budget:
            plain = round(d * 2) / 2
            if abs(plain - d) < abs(named[0] - d) - 1e-9:
                return plain / 2, None
            return named[0] / 2, named[1]
        return _round_size(d, budget * 2) / 2, None
    if kind == "boss":
        return _round_size(2 * radius, budget * 2) / 2, None
    return _round_size(radius, budget), None


def _round_size(value: float, budget: float) -> float:
    for step in (1.0, 0.5, 0.1):
        snapped = round(value / step) * step
        if abs(snapped - value) <= budget:
            return snapped
    return round(value, 2)


def _fillet_radius(a: Plane, b: Plane, convex: bool, pts: np.ndarray, guess: float) -> float:
    """Radius of the fillet that rounds the edge between planes a and b, fitted to the scan.

    The model is the real cross-section: plane a up to the tangent line, the arc, then plane b.
    Points the cylinder took from either plane near the tangent lines then fit the model as
    well, instead of flattening the arc and inflating the radius.
    """
    axis = _unit(np.cross(a.normal, b.normal))
    sign = -1.0 if convex else 1.0
    # A point on the edge line: on both planes, closest to the patch centroid.
    m = np.vstack([a.normal, b.normal, axis])
    edge = np.linalg.solve(m, [a.offset, b.offset, axis @ pts.mean(0)])

    def centre(r: float) -> np.ndarray:
        return np.linalg.solve(m, [a.offset + sign * r, b.offset + sign * r, axis @ edge])

    def cost(r: float) -> float:
        c = centre(r)
        rel = pts - c
        rel -= np.outer(rel @ axis, axis)
        ta, tb = -sign * a.normal, -sign * b.normal  # centre to the tangent points
        turn = np.cross(ta, tb) @ axis
        inside = (np.cross(ta, rel) @ axis * turn >= 0) & (np.cross(rel, tb) @ axis * turn >= 0)
        d_arc = np.abs(np.linalg.norm(rel, axis=1) - r)
        d_planes = np.minimum(np.abs(a.distance(pts)), np.abs(b.distance(pts)))
        d = np.where(inside, d_arc, d_planes)
        return float(np.mean(d**2))

    res = minimize_scalar(cost, bounds=(0.05 * guess, 3.0 * guess), method="bounded")
    return float(res.x)


def _edges_on(solid: cq.Shape, a: Plane, b: Plane) -> list[cq.Edge]:
    size = solid.BoundingBox().DiagonalLength
    eps = 1e-6 * size + 1e-7
    out = []
    for e in solid.Edges():
        pts = np.array([e.startPoint().toTuple(), e.endPoint().toTuple()])
        if np.abs(a.distance(pts)).max() <= eps and np.abs(b.distance(pts)).max() <= eps:
            out.append(e)
    return out
