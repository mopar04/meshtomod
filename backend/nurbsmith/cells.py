"""Build the base solid from blocks cut by the fitted planes.

The exact rebuild in brep.py traces which planes meet along each region's border and joins
their intersection points into faces. That gives perfect corners when the scan is complete,
but a real scan has gaps, warped faces and ragged rims, and then the traced borders do not
agree and the faces will not close.

This is the fallback. Start from a block around the scan and cut it with each fitted plane,
only near where that plane's region actually is, so a small face does not slice the whole
part. Every block is convex, so it is always a valid solid. Keep the blocks that are mostly
inside the scan and fuse them. The result has the same planes and the same corners, and it
closes over gaps by construction.
"""

from __future__ import annotations

import cadquery as cq
import numpy as np
import trimesh
from scipy.spatial import ConvexHull, QhullError

from .brep import UnsupportedGeometry, _sew_solid
from .segment import Geo, Region

# Points sampled in each block to decide whether it is inside the scan.
SAMPLES_PER_CELL = 64
# A plane cuts a block only if its region comes within this many tolerances of the block.
REACH_TOLS = 3.0


def cell_solid(mesh: Geo, planes: list[Region], tol: float, min_feature: float) -> cq.Solid:
    pts = mesh.vertices
    margin = 0.05 * float(np.linalg.norm(np.ptp(pts, axis=0))) + tol
    lo, hi = pts.min(axis=0) - margin, pts.max(axis=0) + margin
    cells = [
        np.array(
            [[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
        )
    ]
    eps = 1e-6 * float(np.linalg.norm(hi - lo))
    rng = np.random.default_rng(0)
    reach = max(REACH_TOLS * tol, min_feature)

    for r in sorted(planes, key=lambda r: -r.area):
        support = pts[np.unique(mesh.faces[r.faces])]
        if len(support) > 2000:
            support = support[rng.choice(len(support), 2000, replace=False)]
        out = []
        for cell in cells:
            d = r.plane.distance(cell)
            if d.min() >= -eps or d.max() <= eps or not _near(cell, support, reach):
                out.append(cell)
                continue
            for side in (-1.0, 1.0):
                piece = _clip(cell, r.plane.normal * side, r.plane.offset * side)
                if piece is not None:
                    out.append(piece)
        cells = out

    inside = _inside_cells(mesh, cells, rng)
    if not inside:
        raise UnsupportedGeometry("Could not find the inside of the scan.")
    solids = [s for s in (_hull_solid(c) for c in inside) if s is not None]
    fused = (solids[0].fuse(*solids[1:]) if len(solids) > 1 else solids[0]).clean()
    parts = fused.Solids()
    if not parts:
        raise UnsupportedGeometry("Rebuilt solid is not valid.")
    solid = max(parts, key=lambda s: s.Volume())
    if not solid.isValid():
        raise UnsupportedGeometry("Rebuilt solid is not valid.")
    return solid


def _near(cell: np.ndarray, support: np.ndarray, reach: float) -> bool:
    """Does any support point lie within reach of the (convex) cell?"""
    lo, hi = cell.min(axis=0) - reach, cell.max(axis=0) + reach
    cand = support[((support >= lo) & (support <= hi)).all(axis=1)]
    if not len(cand):
        return False
    try:
        eq = ConvexHull(cell).equations
    except QhullError:
        return True
    return bool(((cand @ eq[:, :3].T + eq[:, 3]) <= reach).all(axis=1).any())


def _clip(cell: np.ndarray, normal: np.ndarray, offset: float) -> np.ndarray | None:
    """The part of a convex cell (given by its corners) where normal . x <= offset."""
    try:
        hull = ConvexHull(cell)
    except QhullError:
        return None
    d = cell @ normal - offset
    keep = list(cell[d <= 0])
    edges = {tuple(sorted((s[i], s[(i + 1) % 3]))) for s in hull.simplices for i in range(3)}
    for a, b in edges:
        if (d[a] < 0 < d[b]) or (d[b] < 0 < d[a]):
            t = d[a] / (d[a] - d[b])
            keep.append(cell[a] + t * (cell[b] - cell[a]))
    if len(keep) < 4:
        return None
    piece = np.array(keep)
    # Merge near-duplicate corners and keep only true hull corners, so repeated cutting does
    # not pile up points that make slivers of hull triangles.
    grid = 1e-6 * float(np.linalg.norm(np.ptp(cell, axis=0)))
    _, first = np.unique(np.round(piece / grid), axis=0, return_index=True)
    piece = piece[np.sort(first)]
    try:
        hull = ConvexHull(piece)
    except QhullError:
        return None
    if hull.volume < 1e-9 * max(np.ptp(cell, axis=0).prod(), 1e-12):
        return None
    return piece[hull.vertices]


def _inside_cells(mesh: Geo, cells: list[np.ndarray], rng: np.random.Generator) -> list:
    surface = trimesh.Trimesh(mesh.vertices, mesh.faces, process=False)
    samples, owner = [], []
    for i, cell in enumerate(cells):
        hull = ConvexHull(cell)
        lo, hi = cell.min(axis=0), cell.max(axis=0)
        cand = lo + rng.random((SAMPLES_PER_CELL * 4, 3)) * (hi - lo)
        ok = ((cand @ hull.equations[:, :3].T + hull.equations[:, 3]) <= 0).all(axis=1)
        cand = cand[ok][:SAMPLES_PER_CELL]
        if not len(cand):
            cand = cell.mean(axis=0)[None]
        samples.append(cand)
        owner.append(np.full(len(cand), i))
    samples_arr, owner_arr = np.concatenate(samples), np.concatenate(owner)
    closest, _, tri = trimesh.proximity.closest_point(surface, samples_arr)
    # Inside if the nearest bit of surface faces away from the point.
    side = ((samples_arr - closest) * mesh.face_normals[tri]).sum(axis=1) < 0
    share = np.bincount(owner_arr, weights=side, minlength=len(cells)) / np.bincount(
        owner_arr, minlength=len(cells)
    )
    return [c for c, s in zip(cells, share, strict=True) if s > 0.5]


def _hull_solid(cell: np.ndarray) -> cq.Solid | None:
    """A convex cell as a solid, from its hull triangles (outward wound). The fuse at the end
    merges coplanar triangles back into single faces."""
    hull = ConvexHull(cell)
    size = float(np.linalg.norm(np.ptp(cell, axis=0)))
    faces = []
    for simplex, eq in zip(hull.simplices, hull.equations, strict=True):
        tri = cell[simplex]
        if np.cross(tri[1] - tri[0], tri[2] - tri[0]) @ eq[:3] < 0:
            tri = tri[::-1]
        faces.append(
            cq.Face.makeFromWires(cq.Wire.makePolygon([tuple(x) for x in tri], close=True))
        )
    try:
        return _sew_solid(faces, 1e-6 * size)
    except UnsupportedGeometry:
        return None  # a sliver too thin to matter
