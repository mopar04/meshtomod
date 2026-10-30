"""Simplifying dense scans before they are converted.

Scanner exports run to millions of triangles, far more than a part needs at any accuracy
level, and every stage that works on the full scan (repair, noise estimate, deviation map)
pays for each one in time and memory. So a scan over its level's limit
(ToleranceLevel.simplify_above_faces) is simplified to that many triangles first.

Quadric decimation (the same library the pipeline already uses for its working mesh) removes
triangles where the surface is flat and keeps them where it bends, so edges, holes and fillets
hold their shape. Every simplification is checked: 99.9% of the original scan's points must
stay within half the level's fit tolerance of the simplified surface, plus three times the
scan's own noise (smoothing noise away is not a loss), and no more than a few points may be
twice that far (a filled-in hole on a big part is too small a share to show in the 99.9%).
If the check fails, it is redone keeping more triangles. A scan over CONVERT_MAX_FACES can't
be kept as it is, because the server can't convert that much: the closest it gets is kept and
the note says details may be softened.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import fast_simplification
import numpy as np
import trimesh
from scipy.spatial import cKDTree

from .quality import estimate_noise
from .tolerance import PRECISE, ToleranceLevel

# The most triangles the server converts: a 2.2 million triangle scan peaked at 3 GB.
CONVERT_MAX_FACES = 2_500_000
# Each retry after a failed check keeps this many times more triangles.
GROWTH = 1.6
# Points of the original scan measured against the simplified one.
CHECK_POINTS = 100_000
CHECK_CHUNK = 20_000
# Triangles each point is measured against: those with the nearest centres.
CANDIDATES = 12
# Share of checked points that must be within the allowance (stray spikes may be further).
CHECK_QUANTILE = 0.999
# A lost feature (a hole filled in) is a cluster of points far off, too few to move the
# 99.9th percentile on a big part; a stray spike flattened away is one or two.
FAR_ALLOWANCES = 2
MAX_FAR_POINTS = 5
# Gentle passes: at the library's default aggressiveness a few collapses cut across corners
# (see convert.working_mesh).
AGGRESSION = 4
PASSES = 6

SIMPLIFIED = "simplified"  # mesh.metadata key


@dataclass(frozen=True)
class Simplified:
    from_faces: int
    to_faces: int
    error_mm: float
    """99.9% of the original scan's points are within this of the simplified surface."""
    allowed_mm: float
    """What the check allowed: half the fit tolerance plus three times the scan noise."""
    softened: bool = False
    """The check failed even at CONVERT_MAX_FACES: some detail may be softened."""

    def as_dict(self) -> dict:
        return asdict(self)

    def note(self) -> str:
        text = (
            f"Simplified from {self.from_faces:,} to {self.to_faces:,} triangles before "
            f"converting; 99.9% of the scan stays within {self.error_mm:.3f} mm of it."
        )
        if self.softened:
            text += (
                " Some spots moved more than this accuracy level allows "
                f"({self.allowed_mm:.3f} mm), so small details may be softened. A smaller "
                "scan, or the area you need cut out of it, keeps them."
            )
        return text

    def then(self, later: Simplified) -> Simplified:
        """This step followed by a later one: from the first count to the last, errors added
        (an upper bound)."""
        return Simplified(
            self.from_faces,
            later.to_faces,
            round(self.error_mm + later.error_mm, 4),
            max(self.allowed_mm, later.allowed_mm),
            self.softened or later.softened,
        )


def simplify(
    mesh: trimesh.Trimesh,
    tol: ToleranceLevel,
    target: int | None = None,
    limit: int = CONVERT_MAX_FACES,
) -> tuple[trimesh.Trimesh, Simplified | None]:
    """The mesh simplified for tol (to target triangles, the level's own by default), and what
    was done; the mesh itself and None when it is small enough already, or when only keeping
    it whole passes the check and it is under limit."""
    n = len(mesh.faces)
    target = min(target or tol.simplify_above_faces, limit)
    if n <= target:
        return mesh, None
    while True:
        light = _decimate(mesh, target)
        dist = _distances(mesh, light)
        error = float(np.quantile(dist, CHECK_QUANTILE))
        allowed = tol.fit_tolerance_mm / 2 + 3 * estimate_noise(light)
        lost = int((dist > FAR_ALLOWANCES * allowed).sum())
        if error <= allowed and lost <= MAX_FAR_POINTS or target >= limit:
            break
        target = min(int(target * GROWTH), limit)
        if target >= n:
            return mesh, None
    light.metadata.update(mesh.metadata)
    passed = error <= allowed and lost <= MAX_FAR_POINTS
    return light, Simplified(n, len(light.faces), round(error, 4), round(allowed, 4), not passed)


def cap(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Bring a scan down to the most any level keeps, at the strictest level's check, as soon
    as it is loaded: before a merge, and before anything else holds a copy. What was done is
    kept in mesh.metadata for simplify_for_level to report."""
    light, done = simplify(mesh, PRECISE)
    if done is not None:
        light.metadata[SIMPLIFIED] = done
    return light


def simplify_for_level(mesh: trimesh.Trimesh, tol: ToleranceLevel):
    """The mesh simplified for tol, and everything done to it since it was loaded (or None)."""
    earlier = mesh.metadata.pop(SIMPLIFIED, None)
    light, done = simplify(mesh, tol)
    if earlier is not None:
        done = earlier if done is None else earlier.then(done)
    return light, done


def _decimate(mesh: trimesh.Trimesh, target: int) -> trimesh.Trimesh:
    verts, faces = mesh.vertices, mesh.faces
    for _ in range(PASSES):
        verts, faces = fast_simplification.simplify(
            verts, faces, target_count=target, agg=AGGRESSION
        )
        if len(faces) <= 1.05 * target:
            break
    # A face and its mirror image: a collapsed sliver. Drop both.
    _, inverse, counts = np.unique(
        np.sort(faces, axis=1), axis=0, return_inverse=True, return_counts=True
    )
    faces = faces[counts[inverse.ravel()] == 1]
    light = trimesh.Trimesh(verts, faces)
    light.remove_unreferenced_vertices()
    # Any small gap this leaves is filled by convert.repair, as on any open scan.
    return light


def _distances(original: trimesh.Trimesh, light: trimesh.Trimesh) -> np.ndarray:
    """How far the original scan's points (a fixed sample of them) are from the simplified
    surface.

    Each point is measured to the nearest of the CANDIDATES triangles whose centres are
    closest to it. trimesh's exact search builds a tree of every triangle, which takes longer
    than the simplifying; missing the true nearest triangle can only overstate the distance,
    so the check never passes a mesh it shouldn't."""
    points = np.asarray(original.vertices)
    if len(points) > CHECK_POINTS:
        points = points[np.random.default_rng(0).choice(len(points), CHECK_POINTS, replace=False)]
    triangles = light.triangles
    _, near = cKDTree(triangles.mean(axis=1)).query(points, k=min(CANDIDATES, len(triangles)))
    near = near.reshape(len(points), -1)
    dist = np.empty(len(points))
    for i in range(0, len(points), CHECK_CHUNK):
        rows = near[i : i + CHECK_CHUNK]
        pts = np.repeat(points[i : i + CHECK_CHUNK], rows.shape[1], axis=0)
        closest = trimesh.triangles.closest_point(triangles[rows.ravel()], pts)
        d = np.linalg.norm(closest - pts, axis=1).reshape(rows.shape)
        dist[i : i + CHECK_CHUNK] = d.min(axis=1)
    return dist
