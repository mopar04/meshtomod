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

from dataclasses import dataclass

import cadquery as cq
import numpy as np
import trimesh
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from scipy.spatial import ConvexHull, QhullError

from .brep import UnsupportedGeometry, _sew_solid
from .segment import Geo, Region

# Points sampled in each block to decide whether it is inside the scan.
SAMPLES_PER_CELL = 64
# A plane cuts a block only if its region comes within this many tolerances of the block.
REACH_TOLS = 3.0
# Points per closest-point query, and the most blocks worth building.
SAMPLE_CHUNK = 20_000
MAX_CELLS = 20_000
# Separate bodies smaller than this share of the biggest are crumbs and are dropped.
MIN_BODY_SHARE = 0.05
# Blocks fused per round.
FUSE_BATCH = 16
# Sliver blocks that break a fuse are left out, up to this share of the volume.
MAX_DROPPED_SHARE = 1e-3


@dataclass
class Corner:
    """A rounded edge the base solid gets as a sharp corner (the fillet is added after): the
    fillet's faces, the two planes it joins, and whether it rounds an outside corner."""

    faces: np.ndarray
    a: Region
    b: Region
    convex: bool

    def inside(self, points: np.ndarray) -> np.ndarray:
        """Inside the sharp corner: behind both planes for an outside corner, behind either
        for an inside one."""
        da, db = self.a.plane.distance(points) < 0, self.b.plane.distance(points) < 0
        return da & db if self.convex else da | db


def cell_solid(
    mesh: Geo,
    planes: list[Region],
    tol: float,
    min_feature: float,
    corners: list[Corner] | None = None,
    fixed: list[tuple[np.ndarray, bool]] | None = None,
    ignore: np.ndarray | None = None,
) -> cq.Solid | cq.Compound:
    """Corners are the fillets the base solid leaves sharp. Each plane carries on under its
    fillets to the corner, so it cuts there too, and blocks in a fillet's corner count as
    inside or outside by the sharp corner, not by the rounded scan.

    fixed lists faces whose side is already decided: (faces, True) for holes, which are solid
    until they are drilled, and (faces, False) for bosses, which are added after.

    ignore lists faces left out when deciding inside from outside, as if they were not there:
    freeform surfaces, which are cut from or added to the block afterwards."""
    corners = corners or []
    fixed = fixed or []
    blends: dict[int, list[np.ndarray]] = {}
    for c in corners:
        for r in (c.a, c.b):
            blends.setdefault(r.id, []).append(c.faces)
    pts = mesh.vertices
    # Start from the scan's oriented bounding box, only a tolerance bigger: a block that no
    # plane cuts near the outside then cannot carry a spill beyond the part.
    to_box, extents = trimesh.bounds.oriented_bounds(pts)
    half = extents / 2 + tol
    box = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]) * half
    cells = [trimesh.transform_points(box, np.linalg.inv(to_box))]
    eps = 1e-6 * float(np.linalg.norm(2 * half))
    rng = np.random.default_rng(0)
    reach = max(REACH_TOLS * tol, min_feature)

    for r in sorted(planes, key=lambda r: -r.area):
        faces = np.concatenate([r.faces, *blends.get(r.id, [])])
        support = pts[np.unique(mesh.faces[faces])]
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
        if len(cells) > MAX_CELLS:
            raise UnsupportedGeometry("This part has too many separate faces to rebuild yet.")

    inside = _inside_cells(mesh, cells, rng, corners, fixed, ignore)
    if not inside:
        raise UnsupportedGeometry("Could not find the inside of the scan.")
    solids = [s for s in (_hull_solid(c) for c in inside) if s is not None]
    fused = _fuse_all(solids)
    parts = fused.Solids()
    if not parts:
        raise UnsupportedGeometry("Rebuilt solid is not valid.")
    # A scan of an assembly can come apart where a joining piece is not modelled (a threaded
    # post between two plates); keep each sizeable body rather than only the biggest.
    biggest = max(s.Volume() for s in parts)
    bodies = [s for s in parts if s.Volume() >= MIN_BODY_SHARE * biggest]
    solid = bodies[0] if len(bodies) == 1 else cq.Compound.makeCompound(bodies)
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


def _inside_cells(
    mesh: Geo,
    cells: list[np.ndarray],
    rng: np.random.Generator,
    corners: list[Corner],
    fixed: list[tuple[np.ndarray, bool]],
    ignore: np.ndarray | None = None,
) -> list:
    keep = np.ones(len(mesh.faces), bool)
    if ignore is not None:
        keep[ignore] = False
    index = np.flatnonzero(keep)
    surface = trimesh.Trimesh(mesh.vertices, mesh.faces[index], process=False)
    samples, owner = [], []
    for i, cell in enumerate(cells):
        cand = _sample_cell(cell, rng)
        samples.append(cand)
        owner.append(np.full(len(cand), i))
    samples_arr, owner_arr = np.concatenate(samples), np.concatenate(owner)
    # In chunks: closest-point queries on a dense scan take a lot of memory per point.
    side = np.zeros(len(samples_arr), dtype=bool)
    for at in range(0, len(samples_arr), SAMPLE_CHUNK):
        chunk = samples_arr[at : at + SAMPLE_CHUNK]
        closest, _, tri = trimesh.proximity.closest_point(surface, chunk)
        tri = index[tri]
        # Inside if the nearest bit of surface faces away from the point.
        inside = ((chunk - closest) * mesh.face_normals[tri]).sum(axis=1) < 0
        for c in corners:
            near = np.isin(tri, c.faces)
            inside[near] = c.inside(chunk[near])
        for faces, value in fixed:
            inside[np.isin(tri, faces)] = value
        side[at : at + SAMPLE_CHUNK] = inside
    share = np.bincount(owner_arr, weights=side, minlength=len(cells)) / np.bincount(
        owner_arr, minlength=len(cells)
    )
    return [c for c, s in zip(cells, share, strict=True) if s > 0.5]


def _sample_cell(cell: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Points spread evenly through a convex cell. Drawn from the tetrahedra between its
    middle and each hull triangle, by volume, rather than from its bounding box: a thin
    slab cut at a slant fills little of its box, and would get few samples or none."""
    hull = ConvexHull(cell)
    centre = cell.mean(axis=0)
    tri = cell[hull.simplices]
    a, b, c = tri[:, 0] - centre, tri[:, 1] - centre, tri[:, 2] - centre
    volume = np.abs(np.einsum("ij,ij->i", a, np.cross(b, c)))
    if volume.sum() <= 0:
        return centre[None]
    pick = rng.choice(len(tri), SAMPLES_PER_CELL, p=volume / volume.sum())
    # Uniform in a tetrahedron: sorted uniforms give its barycentric weights.
    u = np.sort(rng.random((SAMPLES_PER_CELL, 3)), axis=1)
    w = np.c_[u[:, 0], u[:, 1] - u[:, 0], u[:, 2] - u[:, 1]]
    return centre + w[:, :1] * a[pick] + w[:, 1:2] * b[pick] + w[:, 2:] * c[pick]


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


def _fuse_all(solids: list[cq.Solid]) -> cq.Shape:
    """Fuse the blocks a batch at a time, merging faces after each round. One big fuse of
    thousands of blocks takes many gigabytes; the blocks only touch, never overlap, so the
    glue option applies and small rounds stay light."""
    total = sum(s.Volume() for s in solids)
    dropped: list[float] = []
    shapes: list[cq.Shape] = list(solids)
    while len(shapes) > 1:
        shapes = [
            _fuse_group(group, dropped) if len(group) > 1 else group[0]
            for group in (shapes[i : i + FUSE_BATCH] for i in range(0, len(shapes), FUSE_BATCH))
        ]
    if sum(dropped) > MAX_DROPPED_SHARE * total:
        raise UnsupportedGeometry("Rebuilt solid is not valid.")
    return _unify(shapes[0])


def _fuse_group(group: list[cq.Shape], dropped: list[float]) -> cq.Shape:
    """Glue is fast but trusts the blocks to fit together cleanly. On a scan with gaps a
    sliver block can break the glued fuse, which then comes back empty. Fuse that batch one
    block at a time instead, leaving out any sliver that breaks it (noted in dropped)."""
    fused = group[0].fuse(*group[1:], glue=True)
    if not _empty(fused):
        return _unify(fused)
    fused = None
    for block in sorted(group, key=lambda b: -b.Volume()):
        joined = block if fused is None else fused.fuse(block)
        if _empty(joined):
            dropped.append(block.Volume())
        else:
            fused = joined
    if fused is None:
        raise UnsupportedGeometry("Rebuilt solid is not valid.")
    return _unify(fused)


def _empty(shape: cq.Shape) -> bool:
    return shape.wrapped.IsNull() or not shape.Solids()


def _unify(shape: cq.Shape) -> cq.Shape:
    """Merge the coplanar pieces the blocks leave behind into whole faces."""
    size = shape.BoundingBox().DiagonalLength
    unify = ShapeUpgrade_UnifySameDomain(shape.wrapped, True, True, True)
    unify.SetLinearTolerance(1e-5 * size)
    unify.SetAngularTolerance(1e-5)
    unify.Build()
    return cq.Shape.cast(unify.Shape())
