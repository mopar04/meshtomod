"""Surfaces of revolution: cones, spheres and tori, and the turned parts made of them.

Every face of a turned part, and of a countersink, dome or tapered boss, is swept by turning
one flat outline (the profile) about an axis. Seen in that outline's plane, the faces become
2D pieces: a plane square to the axis is a straight line across it, a cylinder a line along
it, a cone a slanted line, a sphere an arc centred on the axis and a torus an arc centred off
it. So instead of fitting each kind of surface in 3D, the scan is folded into (z, r), height
along the axis and distance from it, and fitted with lines and arcs. The outline is then
snapped to clean sizes and revolved, which is also how a designer would draw it.

The axis comes from the surface normals: on any surface of revolution the line along each
normal passes through the axis.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cadquery as cq
import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components

from .cylinders import FULL_COVERAGE, CurvedPatch
from .segment import Geo, Region, smoothed_normals
from .tolerance import ToleranceLevel

# A face belongs to a surface of revolution when its normal leans out of the plane through
# the axis by no more than this.
MAX_TWIST_DEG = 12.0
# Share of a group's area allowed to lean further (noise, a seam, a sliver of the next face).
TWIST_OUTLIERS = 0.1
# A face joins a profile piece only if its normal is within this angle of the piece's normal
# there. Tighter than the planar regions' 25 degrees: within the tolerance a line can follow
# a small ball or a fillet a long way, but its normal can't.
LINE_NORMAL_DEG = 8.0
ARC_NORMAL_DEG = 12.0
# Share of a piece's area that must agree with it like that.
NORMAL_AGREE = 0.9
# An arc bigger than this share of the profile's size is a line that bowed.
MAX_ARC_SHARE = 2.0
# Normals that turn steadily by more than this along a line mean it is really an arc.
LINE_TURN_DEG = 5.0
TURN_SLICES = 6
# Two pieces merge only if one fits them both about as well as each fits on its own.
MERGE_MISFIT = 1.5
# Fit residual at this percentile must be within tolerance.
FIT_PERCENTILE = 98
GROW_ROUNDS = 4
# Faces in the neighbourhood a piece is seeded from.
SEED_FACES = 24
MIN_FIT_POINTS = 6
# Lines within this angle of square to the axis or along it are planes and cylinders.
SQUARE_DEG = 2.0
# Two pieces meet tangentially (a fillet, a ball on a neck) below this angle between them.
TANGENT_DEG = 12.0
# Snap a size to a round value if that moves the surface by at most this share of the
# fit tolerance (as features.SNAP_SHARE).
SNAP_SHARE = 0.5
# Revolved tools overshoot the face they stand on by this much, so no sliver is left.
OVERSHOOT_MM = 1.0
COVERAGE_SECTORS = 36
# Below this noise (mm) a mesh is a clean export and its own face normals are used.
CLEAN_NOISE_MM = 0.005
PROFILE_SMOOTHING_ROUNDS = 4
PROFILE_SMOOTHING_KEEP_DEG = 30.0


@dataclass
class Axis:
    point: np.ndarray
    direction: np.ndarray

    def to_profile(self, points: np.ndarray) -> np.ndarray:
        """(z, r) of each point: height along the axis and distance from it."""
        rel = points - self.point
        z = rel @ self.direction
        r = np.linalg.norm(rel - np.outer(z, self.direction), axis=1)
        return np.stack([z, r], axis=1)

    def radial(self, points: np.ndarray) -> np.ndarray:
        rel = points - self.point
        rel = rel - np.outer(rel @ self.direction, self.direction)
        return rel / np.maximum(np.linalg.norm(rel, axis=1, keepdims=True), 1e-12)


# ---------------------------------------------------------------------------------------------
# 2D pieces of a profile
# ---------------------------------------------------------------------------------------------


@dataclass
class Line2:
    """Points q with normal . q == offset. The normal points out of the material."""

    normal: np.ndarray
    offset: float

    def distance(self, q: np.ndarray) -> np.ndarray:
        return q @ self.normal - self.offset

    def normal_at(self, q: np.ndarray) -> np.ndarray:
        return np.tile(self.normal, (len(q), 1))

    @property
    def direction(self) -> np.ndarray:
        return np.array([-self.normal[1], self.normal[0]])

    def kind(self) -> str:
        nz, nr = self.normal
        if abs(nr) <= np.sin(np.radians(SQUARE_DEG)):
            return "plane"
        if abs(nz) <= np.sin(np.radians(SQUARE_DEG)):
            return "cylinder"
        return "cone"


@dataclass
class Arc2:
    """Circle about centre. convex: the material is inside it (a ball, a rounded edge)."""

    centre: np.ndarray
    radius: float
    convex: bool

    def distance(self, q: np.ndarray) -> np.ndarray:
        return np.linalg.norm(q - self.centre, axis=1) - self.radius

    def normal_at(self, q: np.ndarray) -> np.ndarray:
        out = q - self.centre
        out /= np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-12)
        return out if self.convex else -out

    def kind(self) -> str:
        return "sphere" if abs(self.centre[1]) < 1e-9 else "torus"


Piece = Line2 | Arc2


def fit_line(q: np.ndarray, normals: np.ndarray, weights: np.ndarray | None = None) -> Line2:
    """Least-squares line through q, its normal agreeing with the given normals."""
    w = np.ones(len(q)) if weights is None else weights
    centre = (q * w[:, None]).sum(axis=0) / w.sum()
    _, _, vt = np.linalg.svd((q - centre) * np.sqrt(w)[:, None], full_matrices=False)
    n = vt[-1] / np.linalg.norm(vt[-1])
    if n @ normals.mean(axis=0) < 0:
        n = -n
    return Line2(n, float(n @ centre))


def fit_arc(
    q: np.ndarray, normals: np.ndarray, points: np.ndarray, weights: np.ndarray | None = None
) -> Arc2 | None:
    """Circle through the faces at q with normals, refined on the points (vertices).

    Each face sits one radius from the centre along its normal, which pins the centre and
    radius down linearly: centre + s * normal = q, with s the radius, negative for a hollow.
    """
    w = np.ones(len(q)) if weights is None else weights
    a = np.zeros((2 * len(q), 3))
    a[0::2, 0] = 1.0
    a[1::2, 1] = 1.0
    a[:, 2] = normals.ravel()
    sw = np.repeat(np.sqrt(w), 2)
    sol, *_ = np.linalg.lstsq(a * sw[:, None], q.ravel() * sw, rcond=None)
    centre, s = sol[:2], float(sol[2])
    if not np.isfinite(s) or abs(s) < 1e-6:
        return None
    if len(points) >= MIN_FIT_POINTS:

        def residuals(p: np.ndarray) -> np.ndarray:
            return np.linalg.norm(points - p[:2], axis=1) - p[2]

        p = least_squares(residuals, [*centre, abs(s)], method="lm").x
        centre, radius = p[:2], abs(float(p[2]))
    else:
        radius = abs(s)
    return Arc2(np.asarray(centre, dtype=float), radius, s > 0)


# ---------------------------------------------------------------------------------------------
# Profile fitting
# ---------------------------------------------------------------------------------------------


@dataclass
class ProfilePart:
    piece: Piece
    faces: np.ndarray
    area: float
    neighbours: set[int] = field(default_factory=set)


@dataclass
class _Folded:
    """Member faces folded into the profile plane."""

    faces: np.ndarray
    """Mesh face ids, sorted."""
    q: np.ndarray
    """(z, r) of each face's centre."""
    n: np.ndarray
    """Unit 2D normal of each face, from the smoothed normals."""
    area: np.ndarray
    tri: np.ndarray
    """Per face, its three vertices as indices into qv."""
    qv: np.ndarray
    """(z, r) of every mesh vertex."""
    adjacency: csr_matrix
    """Between member faces, by local index."""


def fold(mesh: Geo, faces: np.ndarray, axis: Axis, normals: np.ndarray) -> _Folded:
    faces = np.sort(faces)
    centres = mesh.vertices[mesh.faces[faces]].mean(axis=1)
    q = axis.to_profile(centres)
    rho = axis.radial(centres)
    nn = normals[faces]
    n2 = np.stack([nn @ axis.direction, (nn * rho).sum(axis=1)], axis=1)
    n2 /= np.maximum(np.linalg.norm(n2, axis=1, keepdims=True), 1e-12)
    local = np.full(len(mesh.faces), -1)
    local[faces] = np.arange(len(faces))
    a, b = local[mesh.face_adjacency[:, 0]], local[mesh.face_adjacency[:, 1]]
    keep = (a >= 0) & (b >= 0)
    m = len(faces)
    adjacency = csr_matrix((np.ones(keep.sum(), bool), (a[keep], b[keep])), shape=(m, m))
    adjacency = adjacency + adjacency.T
    return _Folded(
        faces,
        q,
        n2,
        mesh.area_faces[faces],
        mesh.faces[faces],
        axis.to_profile(mesh.vertices),
        adjacency.tocsr(),
    )


def fit_profile(f: _Folded, level: ToleranceLevel) -> tuple[list[ProfilePart], np.ndarray]:
    """Lines and arcs that cover the folded faces, grown over the mesh like the planar regions.

    A seed's neighbourhood is fitted with a line if one fits (a gentle arc that fits a line is
    a line), else with an arc; the piece then grows over connected faces that lie on it and
    face its way, refitting as it goes. Pieces too small to be a feature are dropped and their
    faces given to the piece they touch most. Returns the parts and each folded face's part
    (-1 for none).
    """
    tol = level.fit_tolerance_mm
    m = len(f.faces)
    size = float(np.linalg.norm(np.ptp(f.q, axis=0)))
    owner = np.full(m, -1)
    pieces: list[Piece] = []
    rows = f.adjacency

    def vertex_points(idx: np.ndarray) -> np.ndarray:
        return f.qv[np.unique(f.tri[idx])]

    def facing(piece: Piece, idx: np.ndarray | slice) -> np.ndarray:
        limit = LINE_NORMAL_DEG if isinstance(piece, Line2) else ARC_NORMAL_DEG
        return (piece.normal_at(f.q[idx]) * f.n[idx]).sum(axis=1) >= np.cos(np.radians(limit))

    def fits(piece: Piece, idx: np.ndarray) -> bool:
        if isinstance(piece, Arc2) and piece.radius > MAX_ARC_SHARE * size:
            return False  # a bowed line, not a ball or a round
        pts = vertex_points(idx)
        if np.percentile(np.abs(piece.distance(pts)), FIT_PERCENTILE) > tol:
            return False
        if np.average(facing(piece, idx), weights=f.area[idx]) < NORMAL_AGREE:
            return False
        return not isinstance(piece, Line2) or _turn(piece, f.q[idx], f.n[idx], f.area[idx]) <= (
            np.radians(LINE_TURN_DEG)
        )

    def fit_like(piece: Piece, idx: np.ndarray) -> Piece | None:
        if isinstance(piece, Line2):
            return fit_line(vertex_points(idx), f.n[idx])
        return fit_arc(f.q[idx], f.n[idx], vertex_points(idx), f.area[idx])

    def first_fit(idx: np.ndarray, kind: type) -> Piece | None:
        if kind is Line2:
            piece = fit_line(vertex_points(idx), f.n[idx])
        else:
            piece = fit_arc(f.q[idx], f.n[idx], vertex_points(idx), f.area[idx])
        return piece if piece is not None and fits(piece, idx) else None

    def neighbourhood(seed: int, kind: type) -> np.ndarray:
        """Unowned faces around seed that a piece of this kind could hold: for a line, faces
        facing the seed's way; for an arc, faces turning gently from one to the next. On a
        coarse mesh a few long triangles reach from the middle of a face to its edge, so a
        plain ring of neighbours would take in the next face too."""
        limit = LINE_NORMAL_DEG if kind is Line2 else ARC_NORMAL_DEG
        cos_limit = np.cos(np.radians(limit))
        found, frontier = {seed}, [seed]
        while frontier and len(found) < SEED_FACES:
            nxt = []
            for i in frontier:
                ref = f.n[seed] if kind is Line2 else f.n[i]
                for j in rows.indices[rows.indptr[i] : rows.indptr[i + 1]]:
                    if owner[j] == -1 and j not in found and f.n[j] @ ref >= cos_limit:
                        found.add(int(j))
                        nxt.append(int(j))
            frontier = nxt
        return np.array(sorted(found))

    def grow(piece: Piece, seed: np.ndarray) -> tuple[Piece, np.ndarray]:
        """The piece grown from seed, and the faces it took; it only ever keeps a fit that
        holds on every face it took."""
        idx = seed
        for _ in range(GROW_ROUNDS):
            on = (np.abs(piece.distance(f.qv)) <= tol)[f.tri].all(axis=1)
            ok = np.flatnonzero(on & facing(piece, slice(None)) & (owner == -1))
            if not len(ok):
                return piece, np.array([], dtype=int)
            _, comp = connected_components(rows[ok][:, ok], directed=False)
            pos = np.searchsorted(ok, seed)
            hit = pos < len(ok)
            hit[hit] = ok[pos[hit]] == seed[hit]
            if not hit.any():
                return piece, np.array([], dtype=int)
            grown = ok[np.isin(comp, comp[pos[hit]])]
            refit = fit_like(piece, grown)
            if refit is None or not fits(refit, grown):
                if not fits(piece, grown):
                    break
                refit = piece
            piece, idx = refit, grown
        return piece, idx

    # Lines first, over the whole profile, then arcs over what is left: an arc fitted to a
    # neighbourhood that spans a corner rounds the corner off and would grow along both sides.
    # Seeds go from the faces whose neighbours turn least.
    spread = np.zeros(m)
    for i in range(m):
        nbrs = rows.indices[rows.indptr[i] : rows.indptr[i + 1]]
        if len(nbrs):
            spread[i] = 1.0 - float(np.min(f.n[nbrs] @ f.n[i]))
    min_area = level.min_feature_mm**2
    order = np.argsort(spread, kind="stable")
    for kind in (Line2, Arc2):
        for seed in order:
            if owner[seed] != -1:
                continue
            hood = neighbourhood(int(seed), kind)
            if len(hood) < 3:
                continue
            piece = first_fit(hood, kind)
            if piece is None:
                continue
            piece, idx = grow(piece, hood)
            if not len(idx) or f.area[idx].sum() < 0.25 * min_area:
                continue
            owner[idx] = len(pieces)
            pieces.append(piece)

    owner = _drop_small(f, owner, len(pieces), min_area)
    owner, pieces = _merge_pieces(f, owner, pieces, fits, fit_like, tol)
    parts = []
    for k, piece in enumerate(pieces):
        idx = np.flatnonzero(owner == k)
        if len(idx):
            parts.append(ProfilePart(piece, f.faces[idx], float(f.area[idx].sum())))
    # Each face's owner, by the parts' new numbering, for their neighbours.
    renumber = np.full(len(pieces) + 1, -1)
    kept = [k for k in range(len(pieces)) if (owner == k).any()]
    renumber[kept] = np.arange(len(kept))
    labels = renumber[owner]
    coo = f.adjacency.tocoo()
    la, lb = labels[coo.row], labels[coo.col]
    for a, b in zip(la[la != lb], lb[la != lb], strict=True):
        if a >= 0 and b >= 0:
            parts[a].neighbours.add(int(b))
    return parts, labels


def _turn(line: Line2, q: np.ndarray, n: np.ndarray, w: np.ndarray) -> float:
    """How far (radians) the normals turn steadily from one end of the line to the other.

    Measured on the faces that face the line's way, in slices along it, so stray faces (the
    rim of a cross hole, a seam) and noise average out while a steady turn does not."""
    s = q @ line.direction
    angle = np.arctan2(n @ line.direction, n @ line.normal)
    keep = np.abs(angle) <= np.radians(LINE_NORMAL_DEG)
    if keep.sum() < TURN_SLICES:
        return 0.0
    s, angle, w = s[keep], angle[keep], w[keep]
    lo, hi = np.percentile(s, [2, 98])
    if hi - lo < 1e-9:
        return 0.0
    k = np.clip(((s - lo) / (hi - lo) * TURN_SLICES).astype(int), 0, TURN_SLICES - 1)
    centres, means = [], []
    for i in range(TURN_SLICES):
        here = k == i
        if w[here].sum() > 0:
            centres.append(np.average(s[here], weights=w[here]))
            means.append(np.average(angle[here], weights=w[here]))
    if len(centres) < 2:
        return 0.0
    slope = np.polyfit(centres, means, 1)[0]
    return abs(float(slope)) * (hi - lo)


def _drop_small(f: _Folded, owner: np.ndarray, count: int, min_area: float) -> np.ndarray:
    """Pieces under the smallest feature, and faces no piece took, go to the piece they touch:
    flooded out over the mesh from the pieces that stay."""
    areas = np.bincount(owner[owner >= 0], weights=f.area[owner >= 0], minlength=count)
    owner = np.where((owner >= 0) & (areas[np.maximum(owner, 0)] >= min_area), owner, -1)
    rows = f.adjacency
    while (owner == -1).any():
        free = np.flatnonzero(owner == -1)
        changed = False
        claim = owner.copy()
        for i in free:
            nbrs = rows.indices[rows.indptr[i] : rows.indptr[i + 1]]
            taken = owner[nbrs][owner[nbrs] >= 0]
            if len(taken):
                claim[i] = np.bincount(taken).argmax()
                changed = True
        owner = claim
        if not changed:
            break  # an island with no piece at all; left out
    return owner


def _merge_pieces(f: _Folded, owner: np.ndarray, pieces: list[Piece], fits, fit_like, tol: float):
    """Pieces that one line or arc fits become one: noise can split a cone, a gentle sphere
    can be taken as a run of short lines, each flat enough on its own, and a cross hole
    splits a shaft into two pieces that don't touch. Neighbours are tried, and pieces that
    lie on the same line or circle over the same stretch of it (not two steps of a shaft
    that happen to share a diameter)."""
    line_kind, arc_kind = Line2(np.array([0.0, 1.0]), 0.0), Arc2(np.zeros(2), 1.0, True)
    tried: set[tuple[int, int]] = set()
    changed = True
    while changed:
        changed = False
        coo = f.adjacency.tocoo()
        la, lb = owner[coo.row], owner[coo.col]
        pairs = {(min(a, b), max(a, b)) for a, b in zip(la, lb, strict=True) if a != b and a >= 0}
        live = sorted(set(owner[owner >= 0].tolist()))
        box = {k: np.percentile(f.q[owner == k], [0, 100], axis=0) for k in live}
        pairs |= {
            (a, b)
            for i, a in enumerate(live)
            for b in live[i + 1 :]
            if _same(pieces[a], pieces[b], tol)
            and (box[a][0] <= box[b][1] + tol).all()
            and (box[b][0] <= box[a][1] + tol).all()
        }
        for a, b in sorted(pairs - tried):
            tried.add((a, b))
            ia, ib = np.flatnonzero(owner == a), np.flatnonzero(owner == b)
            idx = np.union1d(ia, ib)
            # As good a fit as the two apart: a corner rounded off by one arc is not.
            worst = max(_misfit(f, pieces[a], ia), _misfit(f, pieces[b], ib))
            for like in (line_kind, arc_kind):
                merged = fit_like(like, idx)
                if (
                    merged is not None
                    and fits(merged, idx)
                    and _misfit(f, merged, idx) <= MERGE_MISFIT * worst + np.radians(1.0)
                ):
                    pieces[a] = merged
                    owner[owner == b] = a
                    tried = {t for t in tried if a not in t and b not in t}
                    changed = True
                    break
            if changed:
                break
    return owner, pieces


def _misfit(f: _Folded, piece: Piece, idx: np.ndarray) -> float:
    """Area-weighted RMS angle (radians) between the faces' normals and the piece's."""
    dots = np.clip((piece.normal_at(f.q[idx]) * f.n[idx]).sum(axis=1), -1.0, 1.0)
    return float(np.sqrt(np.average(np.arccos(dots) ** 2, weights=f.area[idx])))


def _same(a: Piece, b: Piece, tol: float) -> bool:
    """Two pieces on one line or one circle."""
    if isinstance(a, Line2) and isinstance(b, Line2):
        return a.normal @ b.normal > np.cos(np.radians(LINE_NORMAL_DEG)) and abs(
            a.offset - b.offset
        ) <= 2 * tol
    if isinstance(a, Arc2) and isinstance(b, Arc2):
        return (
            a.convex == b.convex
            and abs(a.radius - b.radius) <= 2 * tol
            and float(np.linalg.norm(a.centre - b.centre)) <= 2 * tol
        )
    return False


# ---------------------------------------------------------------------------------------------
# Axis
# ---------------------------------------------------------------------------------------------


def profile_normals(mesh: Geo, noise_mm: float) -> np.ndarray:
    """Face normals for folding: raw on a clean mesh (a CAD export, where smoothing would only
    blur its long triangles across corners), lightly smoothed on a scan."""
    if noise_mm < CLEAN_NOISE_MM:
        return mesh.face_normals
    return smoothed_normals(mesh, PROFILE_SMOOTHING_ROUNDS, PROFILE_SMOOTHING_KEEP_DEG)


def axis_through(
    direction: np.ndarray, points: np.ndarray, normals: np.ndarray, weights: np.ndarray
) -> Axis | None:
    """Axis with the given direction that the normal lines pass closest to.

    A normal line (p, n) meets the axis (c, a) when (p - c) . (n x a) = 0, which is linear in
    c. Normals along the axis (the face of a step) say nothing about where it is."""
    a = direction / np.linalg.norm(direction)
    k = np.cross(normals, a)
    s = np.linalg.norm(k, axis=1)
    use = s > np.sin(np.radians(10.0))
    if use.sum() < MIN_FIT_POINTS:
        return None
    k = k[use] / s[use, None]
    w = np.sqrt(weights[use] * s[use] ** 2)
    rhs = (k * points[use]).sum(axis=1)
    c, *_ = np.linalg.lstsq(k * w[:, None], rhs * w, rcond=None)
    c = c - (c @ a) * a
    return Axis(c, a)


def axis_from_normals(points: np.ndarray, normals: np.ndarray, weights: np.ndarray) -> Axis | None:
    """The line every normal line meets, with no hint of its direction.

    In Pluecker coordinates the axis is (a, m) with m = c x a, and a normal line (n, p x n)
    meets it when (p x n) . a + n . m = 0: linear in (a, m). The smallest eigenvector of the
    weighted normal equations, with m eliminated, gives a (Pottmann's line-complex method,
    restricted to rotations)."""
    centre = np.average(points, axis=0, weights=weights)
    p = points - centre
    v = np.hstack([np.cross(p, normals), normals]) * np.sqrt(weights)[:, None]
    big = v.T @ v
    m11, m12, m22 = big[:3, :3], big[:3, 3:], big[3:, 3:]
    reduced = m11 - m12 @ np.linalg.pinv(m22 + 1e-9 * np.trace(m22) * np.eye(3)) @ m12.T
    _, vecs = np.linalg.eigh(reduced)
    axis = axis_through(vecs[:, 0], p, normals, weights)
    if axis is None:
        return None
    return Axis(axis.point + centre - (centre @ axis.direction) * axis.direction, axis.direction)


def twist(axis: Axis, points: np.ndarray, normals: np.ndarray) -> np.ndarray:
    """|sin| of how far each normal leans out of the plane through the axis."""
    tangent = np.cross(axis.direction, axis.radial(points))
    return np.abs((normals * tangent).sum(axis=1))


def coverage(axis: Axis, points: np.ndarray) -> float:
    """Share of the way round the axis the points cover, in 10-degree sectors."""
    u = np.cross(axis.direction, [1.0, 0.0, 0.0])
    if np.linalg.norm(u) < 0.1:
        u = np.cross(axis.direction, [0.0, 1.0, 0.0])
    u /= np.linalg.norm(u)
    v = np.cross(axis.direction, u)
    rel = points - axis.point
    far = np.linalg.norm(rel - np.outer(rel @ axis.direction, axis.direction), axis=1) > 1e-6
    ang = np.arctan2(rel[far] @ v, rel[far] @ u)
    sectors = np.unique(((ang + np.pi) / (2 * np.pi) * COVERAGE_SECTORS).astype(int))
    return len(sectors) / COVERAGE_SECTORS
