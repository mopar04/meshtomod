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

from .cylinders import FULL_COVERAGE, CurvedPatch, Cylinder, fit_circle_on_axis
from .features import CHAMFER_MIN_WIDTH, Feature, _snap_radius
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
# A line is seeded only from faces this close to parallel, so a narrow flat between two rounds
# is found as a flat before an arc can run across it.
LINE_SEED_DEG = 3.0
ARC_NORMAL_DEG = 12.0
# Share of a piece's area that must agree with it like that.
NORMAL_AGREE = 0.9
# An arc bigger than this share of the profile's size is a line that bowed.
MAX_ARC_SHARE = 2.0
# Normals that turn steadily by more than this along a line mean it is really an arc.
LINE_TURN_DEG = 5.0
TURN_SLICES = 6
# A piece grows only while it fits its faces about as well as it fit its seed.
GROW_MISFIT = 1.5
# Two pieces merge only if one fits them both about as well as the pieces they were made
# from fitted on their own.
MERGE_MISFIT = 1.2
# Edge faces move to the neighbouring piece they face at least this much more closely,
# a row at a time, for at most this many rows.
SETTLE_GAIN_DEG = 1.0
SETTLE_ROUNDS = 50
SETTLE_REFITS = 8
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
# A big shape's profile is found in a wedge of about this many faces (at least MIN_WEDGE_DEG
# wide), the most typical of WEDGE_TRIES; faces then go to a piece within this many tolerances
# of the stretch the wedge found it on.
WEDGE_FACES = 4000
MIN_WEDGE_DEG = 15.0
WEDGE_TRIES = 12
SPAN_MARGIN_TOLS = 3.0
# Below this noise (mm) a mesh is a clean export and its own face normals are used.
CLEAN_NOISE_MM = 0.005
PROFILE_SMOOTHING_ROUNDS = 4
PROFILE_SMOOTHING_KEEP_DEG = 30.0
AXIS_SMOOTHING_ROUNDS = 12
AXIS_SMOOTHING_KEEP_DEG = 60.0
# A turned part may border areas sunk into it that it doesn't explain, like the mouth of a
# cross hole (a scanner sees only part of the way into a small hole): together at most this
# share of its area, and lying this many tolerances inside it.
POCKET_SHARE = 0.1
POCKET_TOLS = 3.0
# A cross hole's walls fit their cylinder within this many tolerances at this percentile
# (only the mouth is seen, and a printed hole is rarely round).
CROSS_HOLE_TOLS = 2.0
CROSS_HOLE_PERCENTILE = 90
# The far mouth of a cross hole is closed (a blind hole) when the scan covers this share of
# where it would be.
FAR_MOUTH_COVER = 0.5
# A profile piece smaller than this share of the profile's area, and than this many smallest
# features squared, is a scrap the outline can go on without.
SCRAP_SHARE = 0.02
SCRAP_FEATURES = 4.0
# A scan with an open end (a part that stood on the turntable, its base never seen) is closed
# there by a flat face square to the axis when its open edge runs this far round the axis.
OPEN_END_COVERAGE = 0.5


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
        # Face centres too: a CAD export's flat disc has vertices only on its rim, which all
        # fold onto one point.
        return np.concatenate([f.qv[np.unique(f.tri[idx])], f.q[idx]])

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

    def bends(idx: np.ndarray) -> bool:
        # Within the tolerance an arc also fits a narrow flat and the edges of the rounds
        # beside it; where a line fits too, it is a line.
        line = fit_line(vertex_points(idx), f.n[idx])
        return line is None or not fits(line, idx)

    def neighbourhood(seed: int, kind: type) -> np.ndarray:
        """Unowned faces around seed that a piece of this kind could hold: for a line, faces
        facing the seed's way; for an arc, faces turning gently from one to the next. On a
        coarse mesh a few long triangles reach from the middle of a face to its edge, so a
        plain ring of neighbours would take in the next face too."""
        limit = LINE_SEED_DEG if kind is Line2 else ARC_NORMAL_DEG
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
        holds on every face it took, and only as well as on the seed: within the tolerance a
        bigger arc can run on over a narrow flat and the round beyond it, but its normals
        can't follow them."""
        idx = seed
        limit = GROW_MISFIT * _misfit(f, piece, seed) + np.radians(1.0)

        def good(p: Piece | None, at: np.ndarray) -> bool:
            return p is not None and fits(p, at) and _misfit(f, p, at) <= limit

        for _ in range(GROW_ROUNDS):
            on = (np.abs(piece.distance(f.qv)) <= tol)[f.tri].all(axis=1)
            on &= np.abs(piece.distance(f.q)) <= tol
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
            if not good(refit, grown):
                if not good(piece, grown):
                    break
                refit = piece
            piece, idx = refit, grown
        return piece, idx

    # Lines first, over the whole profile, then arcs over what is left: an arc fitted to a
    # neighbourhood that spans a corner rounds the corner off and would grow along both sides.
    # Lines are seeded from the faces whose neighbours turn least; arcs from the faces whose
    # neighbours all turn most steeply, so a small round is found before a bigger, flatter
    # arc can run from a narrow flat beside it onto its edges (a face on the line between the
    # two has some neighbours that turn and some that don't).
    spread = np.zeros(m)
    bend = np.zeros(m)
    for i in range(m):
        nbrs = rows.indices[rows.indptr[i] : rows.indptr[i + 1]]
        if len(nbrs):
            dots = np.clip(f.n[nbrs] @ f.n[i], -1.0, 1.0)
            spread[i] = 1.0 - float(np.min(dots))
            gaps = np.linalg.norm(f.q[nbrs] - f.q[i], axis=1)
            bend[i] = float(np.min(np.arccos(dots) / np.maximum(gaps, 1e-9)))
    # As short as the smallest chamfer the features step makes, so a turned chamfer survives.
    # But within the tolerance a short line also fits a band of a ball, so short lines are
    # only looked for after the arcs: long lines, then arcs, then what is left over (a chamfer,
    # a narrow flat between two rounds).
    min_len = CHAMFER_MIN_WIDTH * level.min_feature_mm
    flattest = np.argsort(spread, kind="stable")
    steepest = np.argsort(-bend, kind="stable")
    passes = ((Line2, level.min_feature_mm), (Arc2, level.min_feature_mm), (Line2, min_len))
    for kind, shortest in passes:
        for seed in flattest if kind is Line2 else steepest:
            if owner[seed] != -1:
                continue
            hood = neighbourhood(int(seed), kind)
            if len(hood) < 3:
                continue
            piece = first_fit(hood, kind)
            if piece is None:
                continue
            piece, idx = grow(piece, hood)
            if not len(idx) or _length(f, piece, idx) < shortest - tol:
                continue
            if isinstance(piece, Arc2) and not bends(idx):
                continue
            owner[idx] = len(pieces)
            pieces.append(piece)

    owner = _drop_short(f, owner, pieces, min_len - tol, level.min_feature_mm - tol)
    owner, pieces = _merge_pieces(f, owner, pieces, fits, fit_like, tol)
    # Settling and refitting go hand in hand: an arc that gives up a round's edge refits
    # flatter, and gives up the next row.
    for _ in range(SETTLE_REFITS):
        settled = _settle(f, owner, pieces, tol)
        if (settled == owner).all():
            break
        owner = settled
        for k, piece in enumerate(pieces):
            idx = np.flatnonzero(owner == k)
            if len(idx) >= 3:
                refit = fit_like(piece, idx)
                if refit is not None and fits(refit, idx):
                    pieces[k] = refit
    # A piece that gave its edges up may now be plainly a line (a narrow flat that an arc
    # had taken together with the edges of the rounds beside it), not needed at all, or too
    # short to keep.
    for k, piece in enumerate(pieces):
        idx = np.flatnonzero(owner == k)
        if not isinstance(piece, Arc2) or len(idx) < 3:
            continue
        line = fit_line(vertex_points(idx), f.n[idx])
        if line is not None and fits(line, idx):
            pieces[k] = line
            continue
        # A narrow flat may still hold the edges of the rounds beside it, and pass for a
        # gentle arc: the faces that face one line's way make a flat, and the rest lie on the
        # rounds next to it.
        core = _flat_core(f, idx, vertex_points, fits)
        if core is not None and _length(f, core[0], core[1]) < min_len - tol:
            core = None
        rest = idx if core is None else np.setdiff1d(idx, core[1])
        if core is not None and _on_rounds(f, owner, pieces, k, rest, tol):
            pieces[k] = core[0]
            owner[rest] = -1
        # A blurred sharp edge (or a round so slight it can't be told from one): the pieces
        # either side of it, run on to their corner, already lie on its faces.
        elif _explained(f, owner, pieces, k, tol):
            owner[idx] = -1
        # Too short to be a round, but with a flat in it.
        elif core is not None and _length(f, piece, idx) < level.min_feature_mm - tol:
            pieces[k] = core[0]
            owner[rest] = -1
    owner = _drop_short(f, owner, pieces, min_len - tol, level.min_feature_mm - tol)
    owner = _flood(f, owner)
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


def _length(f: _Folded, piece: Piece, idx: np.ndarray) -> float:
    """How far along the profile a piece's faces reach."""
    pts = np.concatenate([f.qv[np.unique(f.tri[idx])], f.q[idx]])
    t = _param(piece, pts)
    return float(np.ptp(t)) * (1.0 if isinstance(piece, Line2) else piece.radius)


def _drop_short(
    f: _Folded, owner: np.ndarray, pieces: list[Piece], min_line: float, min_arc: float
) -> np.ndarray:
    """Pieces shorter than the smallest feature give their faces up: a line as short as a
    chamfer, an arc as short as the smallest feature (a narrower band that turns is the blur
    of a sharp edge in a noisy scan, not a round)."""
    owner = owner.copy()
    for k, piece in enumerate(pieces):
        idx = np.flatnonzero(owner == k)
        shortest = min_line if isinstance(piece, Line2) else min_arc
        if len(idx) and _length(f, piece, idx) < shortest:
            owner[idx] = -1
    return owner


def _flat_core(f: _Folded, idx: np.ndarray, vertex_points, fits) -> tuple[Line2, np.ndarray] | None:
    """The line and faces of the flat middle of a stretch of faces, if it has one: the faces
    that face one line's way, more strictly until the line fits them (the edges of rounds
    either side face nearly its way too)."""
    for limit in (LINE_NORMAL_DEG / 2, LINE_NORMAL_DEG / 4, LINE_NORMAL_DEG / 8):
        core = idx
        for _ in range(3):
            line = fit_line(vertex_points(core), f.n[core])
            flat = f.n[idx] @ line.normal >= np.cos(np.radians(limit))
            if flat.sum() < 3:
                break
            core = idx[flat]
        if len(core) >= 3 and fits(line, core):
            return line, core
    return None


def _neighbours(f: _Folded, owner: np.ndarray, k: int) -> set[int]:
    coo = f.adjacency.tocoo()
    return set(owner[coo.col[owner[coo.row] == k]].tolist()) - {k, -1}


def _explained(f: _Folded, owner: np.ndarray, pieces: list[Piece], k: int, tol: float) -> bool:
    """Whether nearly all (by area) of piece k's faces lie, corners and all, within the
    tolerance of the pieces beside it. Two are needed: one piece alone that fits them would
    have been merged with it."""
    others = [pieces[j] for j in _neighbours(f, owner, k)]
    if len(others) < 2:
        return False
    idx = np.flatnonzero(owner == k)
    corners = f.qv[f.tri[idx]].reshape(-1, 2)
    off = np.min([np.abs(p.distance(corners)) for p in others], axis=0)
    near = off.reshape(len(idx), -1).max(axis=1) <= tol
    return bool(np.average(near, weights=f.area[idx]) >= NORMAL_AGREE)


def _on_rounds(
    f: _Folded, owner: np.ndarray, pieces: list[Piece], k: int, faces: np.ndarray, tol: float
) -> bool:
    """Whether the faces (nearly all, by area: some are noise) lie on the tighter rounds of the
    same hand that border piece k."""
    piece = pieces[k]
    rounds = [
        pieces[j]
        for j in _neighbours(f, owner, k)
        if isinstance(pieces[j], Arc2)
        and pieces[j].convex == piece.convex
        and pieces[j].radius < piece.radius / 2
    ]
    if not rounds:
        return False
    if not len(faces):
        return True
    near = np.zeros(len(faces), bool)
    for arc in rounds:
        dots = (arc.normal_at(f.q[faces]) * f.n[faces]).sum(axis=1)
        near |= (np.abs(arc.distance(f.q[faces])) <= tol) & (
            dots >= np.cos(np.radians(ARC_NORMAL_DEG))
        )
    return bool(np.average(near, weights=f.area[faces]) >= NORMAL_AGREE)


def _flood(f: _Folded, owner: np.ndarray) -> np.ndarray:
    """Faces no piece holds go to the piece they touch most, flooded out over the mesh."""
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
    owner = owner.copy()
    # Each piece's squared misfit times area, summed over the pieces it was made from: a
    # merge is judged against how well the originals fitted, so merges can't ratchet a fit
    # worse a step at a time.
    apart = {
        k: float(f.area[owner == k].sum()) * _misfit(f, p, np.flatnonzero(owner == k)) ** 2
        for k, p in enumerate(pieces)
        if (owner == k).any()
    }
    # Neighbours across faces no piece holds (a seam, a scrap) still count as neighbours.
    flooded = _flood(f, owner)
    changed = True
    while changed:
        changed = False
        coo = f.adjacency.tocoo()
        la, lb = flooded[coo.row], flooded[coo.col]
        pairs = {(min(a, b), max(a, b)) for a, b in zip(la, lb, strict=True) if a != b and a >= 0}
        live = sorted(set(owner[owner >= 0].tolist()))
        # The stretch a piece covers runs to its faces' corners: on a coarse mesh one row of
        # long triangles has its centres well inside the faces' reach.
        box = {
            k: np.percentile(f.qv[f.tri[owner == k]].reshape(-1, 2), [0, 100], axis=0) for k in live
        }
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
            # As good a fit as the originals apart, over all their faces: a corner rounded
            # off by one arc is not. (Weighted by area, so a scrap's noisy fit licenses
            # nothing.)
            worst = np.sqrt((apart[a] + apart[b]) / max(float(f.area[idx].sum()), 1e-12))
            for like in (line_kind, arc_kind):
                merged = fit_like(like, idx)
                if (
                    merged is not None
                    and fits(merged, idx)
                    and _misfit(f, merged, idx) <= MERGE_MISFIT * worst + np.radians(1.0)
                ):
                    pieces[a] = merged
                    owner[owner == b] = a
                    apart[a] += apart.pop(b)
                    flooded[flooded == b] = a
                    tried = {t for t in tried if a not in t and b not in t}
                    changed = True
                    break
            if changed:
                break
    return owner, pieces


def _settle(f: _Folded, owner: np.ndarray, pieces: list[Piece], tol: float) -> np.ndarray:
    """Faces at the edge of a piece move to the neighbouring piece they lie on and face more
    closely: within the tolerance one piece can take the edges of the next (an arc over a
    narrow flat takes the tops of the rounds beside it), and the order the pieces were found
    in decides which, not the fit."""
    owner = owner.copy()
    coo = f.adjacency.tocoo()
    rows, cols = coo.row, coo.col
    on = {}
    angle = {}
    for k, piece in enumerate(pieces):
        near = (np.abs(piece.distance(f.qv)) <= tol)[f.tri].all(axis=1)
        on[k] = near & (np.abs(piece.distance(f.q)) <= tol)
        dots = np.clip((piece.normal_at(f.q) * f.n).sum(axis=1), -1.0, 1.0)
        angle[k] = np.arccos(dots)
    for _ in range(SETTLE_ROUNDS):
        moved = False
        current = np.full(len(owner), np.inf)
        held = owner >= 0
        for k in range(len(pieces)):
            here = held & (owner == k)
            current[here] = angle[k][here]
        best_angle, best = current.copy(), owner.copy()
        edge = (owner[rows] != owner[cols]) & (owner[cols] >= 0) & (owner[rows] >= 0)
        for k in range(len(pieces)):
            faces = np.unique(rows[edge & (owner[cols] == k)])
            if not len(faces):
                continue
            ok = on[k][faces] & (angle[k][faces] < best_angle[faces] - np.radians(SETTLE_GAIN_DEG))
            faces = faces[ok]
            best_angle[faces], best[faces] = angle[k][faces], k
        changed = best != owner
        if changed.any():
            owner, moved = best, True
        if not moved:
            break
    return owner


def _misfit(f: _Folded, piece: Piece, idx: np.ndarray) -> float:
    """Area-weighted RMS angle (radians) between the faces' normals and the piece's."""
    dots = np.clip((piece.normal_at(f.q[idx]) * f.n[idx]).sum(axis=1), -1.0, 1.0)
    return float(np.sqrt(np.average(np.arccos(dots) ** 2, weights=f.area[idx])))


def _same(a: Piece, b: Piece, tol: float) -> bool:
    """Two pieces on one line or one circle."""
    if isinstance(a, Line2) and isinstance(b, Line2):
        return (
            a.normal @ b.normal > np.cos(np.radians(LINE_NORMAL_DEG))
            and abs(a.offset - b.offset) <= 2 * tol
        )
    if isinstance(a, Arc2) and isinstance(b, Arc2):
        return (
            a.convex == b.convex
            and abs(a.radius - b.radius) <= 2 * tol
            and float(np.linalg.norm(a.centre - b.centre)) <= 2 * tol
        )
    return False


def fit_turned(
    mesh: Geo,
    faces: np.ndarray,
    axis: Axis,
    normals: np.ndarray,
    level: ToleranceLevel,
    typical: bool = False,
) -> tuple[_Folded, list[ProfilePart], np.ndarray]:
    """The profile of a turned shape: folded faces, the parts, and each face's part.

    Every slice through the axis shows the same profile, so on a big shape the pieces are
    found in one wedge of it (the one the scan covers best), then every face goes to the
    piece it lies on, within that piece's stretch of the profile, and each piece is refitted
    on all of its faces. typical fits in the most typical wedge instead of the fullest: a
    wedge through the mouth of a cross hole is the fullest, and the hole's walls fit no piece
    of the profile. That keeps the fitting time down without losing the averaging over
    the whole scan.
    """
    folded = fold(mesh, faces, axis, normals)
    m = len(folded.faces)
    if m <= WEDGE_FACES:
        parts, labels = fit_profile(folded, level)
        return folded, parts, labels
    centres = mesh.vertices[mesh.faces[folded.faces]].mean(axis=1)
    angle = _angles(axis, centres)
    width = max(2 * np.pi * WEDGE_FACES / m, np.radians(MIN_WEDGE_DEG))
    starts = np.linspace(0.0, 2 * np.pi, WEDGE_TRIES, endpoint=False)
    within = [((angle - a) % (2 * np.pi)) < width for a in starts]
    covered = np.array([float(folded.area[w].sum()) for w in within])
    if typical:
        best = within[int(np.argmin(np.abs(covered - np.median(covered))))]
    else:
        best = within[int(np.argmax(covered))]
    wedge = fold(mesh, folded.faces[best], axis, normals)
    wedge_parts, _ = fit_profile(wedge, level)
    if not wedge_parts:
        return folded, [], np.full(m, -1)

    tol = level.fit_tolerance_mm
    size = float(np.linalg.norm(np.ptp(folded.q, axis=0)))
    pts = np.concatenate([folded.qv[folded.tri], folded.q[:, None]], axis=1)  # (m, 4, 2)
    cost = np.full((len(wedge_parts), m), np.inf)
    for k, part in enumerate(wedge_parts):
        piece = part.piece
        local = np.searchsorted(wedge.faces, part.faces)
        ref = None if isinstance(piece, Line2) else (wedge.q[local] - piece.centre).mean(axis=0)
        span = _param(
            piece, np.concatenate([wedge.q[local], wedge.qv[wedge.tri[local]].reshape(-1, 2)]), ref
        )
        lo, hi = span.min(), span.max()
        t = _param(piece, folded.q, ref)
        margin = SPAN_MARGIN_TOLS * tol
        if not isinstance(piece, Line2):
            margin /= max(piece.radius, 1e-9)  # an angle round the arc
        dist = np.abs(piece.distance(pts.reshape(-1, 2))).reshape(m, 4).max(axis=1)
        limit = LINE_NORMAL_DEG if isinstance(piece, Line2) else ARC_NORMAL_DEG
        facing = (piece.normal_at(folded.q) * folded.n).sum(axis=1) >= np.cos(np.radians(limit))
        ok = (dist <= tol) & facing & (t >= lo - margin) & (t <= hi + margin)
        cost[k, ok] = dist[ok]
    # A flat end square to the axis that the wedge holds too few faces of to fit (a scan's
    # open end closed with a few long triangles) is still a piece.
    # If the wedge has a few faces of it, they are the same piece: it only held the faces
    # within their short stretch.
    for flat in _missed_flats(folded, np.isfinite(cost).any(axis=0), level):
        dist = np.abs(flat.piece.distance(pts.reshape(-1, 2))).reshape(m, 4).max(axis=1)
        facing = folded.n @ flat.piece.normal >= np.cos(np.radians(LINE_NORMAL_DEG))
        ok = (dist <= tol) & facing
        same = [
            k
            for k, part in enumerate(wedge_parts)
            if isinstance(part.piece, Line2)
            and part.piece.normal @ flat.piece.normal > np.cos(np.radians(LINE_NORMAL_DEG))
            and abs(part.piece.offset - flat.piece.offset) <= 2 * tol
        ]
        if same:
            cost[same[0], ok] = np.minimum(cost[same[0], ok], dist[ok])
            continue
        row = np.full(m, np.inf)
        row[ok] = dist[ok]
        wedge_parts.append(flat)
        cost = np.vstack([cost, row])
    fitted = np.where(np.isfinite(cost.min(axis=0)), cost.argmin(axis=0), -1)
    labels = _flood(folded, fitted)
    parts = []
    renumber = np.full(len(wedge_parts) + 1, -1)
    for k, part in enumerate(wedge_parts):
        idx = np.flatnonzero(labels == k)
        if not len(idx):
            continue
        piece = part.piece
        # Refitted on the faces that lay on it, not the ones flooded in (a seam, a corner).
        on = np.flatnonzero(fitted == k)
        vpts = np.concatenate([folded.qv[np.unique(folded.tri[on])], folded.q[on]])
        if isinstance(piece, Line2):
            refit = fit_line(vpts, folded.n[on])
        else:
            refit = fit_arc(folded.q[on], folded.n[on], vpts, folded.area[on])
        if (
            refit is None
            or float(np.percentile(np.abs(refit.distance(vpts)), FIT_PERCENTILE)) > tol
            or (isinstance(refit, Arc2) and refit.radius > MAX_ARC_SHARE * size)
        ):
            refit = piece
        renumber[k] = len(parts)
        parts.append(ProfilePart(refit, folded.faces[idx], float(folded.area[idx].sum())))
    labels = renumber[labels]
    coo = folded.adjacency.tocoo()
    la, lb = labels[coo.row], labels[coo.col]
    for a, b in zip(la[la != lb], lb[la != lb], strict=True):
        if a >= 0 and b >= 0:
            parts[a].neighbours.add(int(b))
    return folded, parts, labels


# ---------------------------------------------------------------------------------------------
# Axis
# ---------------------------------------------------------------------------------------------


def _missed_flats(f: _Folded, held: np.ndarray, level: ToleranceLevel) -> list[ProfilePart]:
    """Flat faces square to the axis at either end of the shape among the faces no piece
    holds, one piece for each end. Only the ends: inside the shape, faces like these are
    the floor of a cross hole."""
    tol = level.fit_tolerance_mm
    square = ~held & (np.abs(f.n[:, 0]) >= np.cos(np.radians(LINE_NORMAL_DEG)))
    lo, hi = float(f.qv[:, 0].min()), float(f.qv[:, 0].max())
    out = []
    for sign, end in ((1.0, hi), (-1.0, lo)):
        idx = np.flatnonzero(square & (np.sign(f.n[:, 0]) == sign))
        if not len(idx):
            continue
        idx = idx[np.argsort(f.q[idx, 0])]
        breaks = np.flatnonzero(np.diff(f.q[idx, 0]) > tol) + 1
        for run in np.split(idx, breaks):
            if f.area[run].sum() < level.min_feature_mm**2:
                continue
            if abs(float(np.median(f.q[run, 0])) - end) > 2 * tol:
                continue
            z = float(np.average(f.q[run, 0], weights=f.area[run]))
            piece = Line2(np.array([sign, 0.0]), sign * z)
            out.append(ProfilePart(piece, f.faces[run], float(f.area[run].sum())))
    return out


def profile_normals(mesh: Geo, noise_mm: float) -> np.ndarray:
    """Face normals for folding: raw on a clean mesh (a CAD export, where smoothing would only
    blur its long triangles across corners), lightly smoothed on a scan."""
    if noise_mm < CLEAN_NOISE_MM:
        return mesh.face_normals
    return smoothed_normals(mesh, PROFILE_SMOOTHING_ROUNDS, PROFILE_SMOOTHING_KEEP_DEG)


def axis_normals(mesh: Geo, noise_mm: float) -> np.ndarray:
    """Face normals for finding axes and testing that faces turn about them: smoothed much
    harder on a scan. Blurring two surfaces turned about one axis into each other leaves the
    normals in the plane through the axis, so only scanner noise is lost, and a twist test
    on lightly smoothed normals fails on noise alone."""
    if noise_mm < CLEAN_NOISE_MM:
        return mesh.face_normals
    return smoothed_normals(mesh, AXIS_SMOOTHING_ROUNDS, AXIS_SMOOTHING_KEEP_DEG)


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


def _angles(axis: Axis, points: np.ndarray) -> np.ndarray:
    """Angle of each point round the axis, from 0 to 2 pi."""
    u = np.cross(axis.direction, [1.0, 0.0, 0.0])
    if np.linalg.norm(u) < 0.1:
        u = np.cross(axis.direction, [0.0, 1.0, 0.0])
    u /= np.linalg.norm(u)
    v = np.cross(axis.direction, u)
    rel = points - axis.point
    return np.arctan2(rel @ v, rel @ u) % (2 * np.pi)


def coverage(axis: Axis, points: np.ndarray) -> float:
    """Share of the way round the axis the points cover, in 10-degree sectors."""
    far = axis.to_profile(points)[:, 1] > 1e-6
    ang = _angles(axis, points[far])
    sectors = np.unique((ang / (2 * np.pi) * COVERAGE_SECTORS).astype(int) % COVERAGE_SECTORS)
    return len(sectors) / COVERAGE_SECTORS


# ---------------------------------------------------------------------------------------------
# Grouping: which labels (planes and patches) are one turned shape, and what it stands on
# ---------------------------------------------------------------------------------------------


@dataclass
class _Group:
    axis: Axis
    labels: set[int]
    """Planes and patches that lie on the shape, caps (flat faces square to the axis that are
    only bordered by the shape) included."""
    junk: set[int]
    """Scraps along its edge too small to be anything; they go with the shape."""
    bases: list[Region]
    """Flat faces square to the axis that the shape stands on (none for a turned part)."""
    pockets: set[int] = field(default_factory=set)
    """Small areas sunk into a turned part that aren't turned about its axis: the mouth of a
    cross hole, cut afterwards."""


def find_groups(
    mesh: Geo,
    planes: list[Region],
    patches: list[CurvedPatch],
    labels: np.ndarray,
    seeds: set[int],
    cuttable: set[int],
    normals: np.ndarray,
    level: ToleranceLevel,
) -> list[_Group]:
    """Shapes turned about an axis, grown from the curved areas nothing else explained.

    seeds are the labels of those areas; cuttable the labels of holes and slots that are cut
    after the shape is built, so the shape may border them (a cross hole through a shaft).
    Each connected set of seeds gets an axis: square to one of the part's flat faces if that
    fits (a dome or countersink stands square on its face), or from the normals alone. It then
    takes in every neighbouring label whose normals pass through that axis, and the flat faces
    square to it that only border the shape (the top of a boss, the step of a shaft). The
    other flat faces square to it are what it stands on.
    """
    centres = mesh.vertices[mesh.faces].mean(axis=1)
    area = mesh.area_faces
    faces_of: dict[int, np.ndarray] = {}
    order = np.argsort(labels, kind="stable")
    ids, starts = np.unique(labels[order], return_index=True)
    for i, lab in enumerate(ids):
        end = starts[i + 1] if i + 1 < len(ids) else len(order)
        faces_of[int(lab)] = order[starts[i] : end]
    plane_by_id = {r.id: r for r in planes}
    la, lb = labels[mesh.face_adjacency[:, 0]], labels[mesh.face_adjacency[:, 1]]
    touching: dict[int, set[int]] = {}
    for a, b in {(int(a), int(b)) for a, b in zip(la, lb, strict=True) if a != b}:
        touching.setdefault(a, set()).add(b)
        touching.setdefault(b, set()).add(a)
    cos_square = np.cos(np.radians(level.angle_snap_deg))
    sin_twist = np.sin(np.radians(MAX_TWIST_DEG))
    junk_area = level.min_feature_mm**2

    # Directions to try: square to the part's flat faces, biggest first.
    flat = sorted(
        (r for r in planes if r.id not in seeds and r.area >= junk_area), key=lambda r: -r.area
    )
    directions: list[np.ndarray] = []
    for r in flat:
        if all(abs(r.plane.normal @ d) < cos_square for d in directions):
            directions.append(r.plane.normal)

    def straight(faces: np.ndarray, axis: Axis) -> float:
        """Share of the faces' area whose normals pass through the axis."""
        ok = twist(axis, centres[faces], normals[faces]) <= sin_twist
        return float(area[faces][ok].sum() / max(area[faces].sum(), 1e-12))

    def square(lab: int, axis: Axis) -> bool:
        r = plane_by_id.get(lab)
        return r is not None and abs(r.plane.normal @ axis.direction) >= cos_square

    def level_with(a: int, b: int) -> bool:
        """Two flat faces on one plane."""
        ra, rb = plane_by_id.get(a), plane_by_id.get(b)
        if ra is None or rb is None:
            return False
        dot = float(ra.plane.normal @ rb.plane.normal)
        return (
            dot >= cos_square and abs(ra.plane.offset - rb.plane.offset) <= level.fit_tolerance_mm
        )

    def best_axis(faces: np.ndarray) -> Axis | None:
        # Square to a flat face if that fits: noise tilts a free fit a little, and a tilt
        # just over the angle snap would leave the shape standing on nothing.
        for d in directions:
            axis = axis_through(d, centres[faces], normals[faces], area[faces])
            if axis is not None and straight(faces, axis) >= 1.0 - TWIST_OUTLIERS:
                return axis
        free = axis_from_normals(centres[faces], normals[faces], area[faces])
        if free is not None and straight(faces, free) >= 1.0 - TWIST_OUTLIERS:
            return free
        return None

    def fits_axis(lab: int, axis: Axis, taken: set[int]) -> bool:
        return (
            lab >= 0
            and lab not in taken
            and straight(faces_of[lab], axis) >= (1.0 - TWIST_OUTLIERS)
        )

    def scrap(lab: int) -> bool:
        return lab >= 0 and lab not in cuttable and area[faces_of[lab]].sum() < junk_area

    def sunk(pockets: set[int], members: set[int], axis: Axis) -> bool:
        """Small areas sunk into the shape (the mouth of a cross hole): most of their area
        well inside the shape's outside at the same height."""
        pf = np.concatenate([faces_of[p] for p in pockets])
        mf = np.concatenate([faces_of[m] for m in members])
        if area[pf].sum() > POCKET_SHARE * area[mf].sum():
            return False
        member_axis = axis_through(axis.direction, centres[mf], normals[mf], area[mf])
        if member_axis is None:
            return False
        zm, rm = member_axis.to_profile(centres[mf]).T
        zp, rp = member_axis.to_profile(centres[pf]).T
        order = np.argsort(zm)
        zm, rm = zm[order], rm[order]
        reach = max(level.min_feature_mm, 4 * level.fit_tolerance_mm)
        lo = np.searchsorted(zm, zp - reach)
        hi = np.searchsorted(zm, zp + reach)
        outside = np.array(
            [rm[a:b].max() if b > a else -np.inf for a, b in zip(lo, hi, strict=True)]
        )
        deep = rp < outside - POCKET_TOLS * level.fit_tolerance_mm
        return bool(area[pf][deep].sum() >= 0.5 * area[pf].sum())

    # Seeds that touch, by label.
    seed_list = sorted(seeds & set(faces_of))
    index = {s: i for i, s in enumerate(seed_list)}
    pairs = [(index[a], index[b]) for a in seed_list for b in touching.get(a, ()) if b in index]
    n = len(seed_list)
    graph = csr_matrix(
        (np.ones(len(pairs), bool), ([p[0] for p in pairs], [p[1] for p in pairs])), shape=(n, n)
    )
    _, comp = connected_components(graph, directed=False)
    components = [{seed_list[i] for i in np.flatnonzero(comp == c)} for c in np.unique(comp)]
    components.sort(key=lambda c: -sum(area[faces_of[s]].sum() for s in c))

    groups: list[_Group] = []
    taken: set[int] = set()
    for comp_labels in components:
        if comp_labels & taken:
            continue
        axis = best_axis(np.concatenate([faces_of[s] for s in comp_labels]))
        if axis is None:
            continue
        members = set(comp_labels)
        junk: set[int] = set()
        square_planes: set[int] = set()
        refused: set[int] = set()

        grew = True
        while grew:
            grew = False
            for lab in sorted(set().union(*(touching.get(m, set()) for m in members))):
                if lab in members or lab in junk or lab in square_planes or lab in refused:
                    continue
                if square(lab, axis):
                    # A flat face square to the axis is a cap (the top of a boss, the step of a
                    # shaft) if beyond it there is only more of the shape; otherwise the shape
                    # stands on it. (Noise can split a narrow cap in two: the other half is
                    # still the cap.)
                    beyond = touching.get(lab, set()) - members - junk - cuttable
                    if all(
                        level_with(b, lab)
                        or (not square(b, axis) and (fits_axis(b, axis, taken) or scrap(b)))
                        for b in beyond
                    ):
                        members.add(lab)
                        grew = True
                    else:
                        square_planes.add(lab)
                elif fits_axis(lab, axis, taken):
                    members.add(lab)
                    grew = True
                elif scrap(lab):
                    junk.add(lab)
                else:
                    refused.add(lab)
        pockets = refused - cuttable
        if pockets and (square_planes or not sunk(pockets, members, axis)):
            continue  # it runs into something that isn't turned about this axis
        bases = [plane_by_id[i] for i in sorted(square_planes)]
        if len(bases) > 2 or (
            len(bases) == 2 and bases[0].plane.normal @ bases[1].plane.normal > 0
        ):
            continue  # stands on steps or ledges; not handled
        member_faces = np.concatenate([faces_of[m] for m in members])
        # Refit the axis on everything, square to the faces it stands on if any.
        direction = bases[0].plane.normal if bases else axis.direction
        if not bases:
            caps_n = [plane_by_id[m].plane.normal for m in members if m in plane_by_id]
            caps_n = [c for c in caps_n if abs(c @ axis.direction) >= cos_square]
            if caps_n:
                direction = caps_n[0]
        refit = axis_through(
            direction, centres[member_faces], normals[member_faces], area[member_faces]
        )
        if refit is None:
            continue
        if coverage(refit, centres[member_faces]) < FULL_COVERAGE:
            continue
        groups.append(_Group(refit, members, junk, bases, pockets))
        taken |= members | junk | pockets
    return groups


# ---------------------------------------------------------------------------------------------
# Outline: the profile's pieces in order, snapped to clean sizes, with exact corners
# ---------------------------------------------------------------------------------------------

# Half-angles (degrees from the axis) a cone snaps to: every 15 degrees, and the 82, 100 and
# 118 degree countersinks and drill point.
STANDARD_HALF_ANGLES = (15.0, 30.0, 41.0, 45.0, 50.0, 59.0, 60.0, 75.0)
# An end of the outline is on the axis or a base within this many tolerances (plus an edge).
REACH_TOLS = 3.0
# Arcs are drawn through a middle point; lines and arcs are sampled this finely to check
# that the outline doesn't cross itself.
ARC_SAMPLES = 24


@dataclass
class _End:
    kind: str
    """"axis" or "base"; "open" while the outline is put together, for an end of the scan
    that is open all round (closed with a flat face, ending on the axis)."""
    near: np.ndarray
    """(z, r) of the scan where the outline ends."""
    base: Region | None = None
    z: float = 0.0
    """For a base, its height along the axis."""


@dataclass
class Outline:
    pieces: list[Piece]
    points: list[np.ndarray]
    """Corners: piece i runs from points[i] to points[i + 1] (cyclically when closed)."""
    mids: list[np.ndarray | None]
    """For an arc, a point on it between its corners."""
    closed: bool
    ends: tuple[_End, _End] | None
    names: list[str | None]
    """Standard size a piece was snapped to (a clearance hole)."""
    capped: int = 0
    """Open ends of the scan closed by a flat face (an unscanned base)."""


def _param(piece: Piece, q: np.ndarray, ref: np.ndarray | None = None) -> np.ndarray:
    """Position along a piece: distance along a line, angle round an arc (from ref)."""
    if isinstance(piece, Line2):
        return q @ piece.direction
    d = q - piece.centre
    ref = d.mean(axis=0) if ref is None else ref
    return np.arctan2(ref[0] * d[:, 1] - ref[1] * d[:, 0], d @ ref)


def _order(
    parts: list[ProfilePart], weights: dict[tuple[int, int], int], skip: frozenset = frozenset()
) -> tuple[list[int], bool]:
    """The parts in order along the outline, and whether it closes on itself. Each part keeps
    its two strongest links (by shared mesh edges), so a stray face or two touching a part
    further along doesn't branch the outline. Empty if it still isn't a single line or loop.
    Parts in skip are left out."""
    links: dict[int, list[tuple[int, int]]] = {i: [] for i in range(len(parts)) if i not in skip}
    for (a, b), w in weights.items():
        if a in skip or b in skip:
            continue
        links[a].append((w, b))
        links[b].append((w, a))
    top = {i: {b for _, b in sorted(ls, reverse=True)[:2]} for i, ls in links.items()}
    nbrs = {i: {b for b in top[i] if i in top[b]} for i in top}
    if any(len(v) > 2 for v in nbrs.values()):
        return [], False
    ends = [i for i, v in nbrs.items() if len(v) < 2]
    start = ends[0] if ends else min(links)
    order, prev = [start], None
    while True:
        nxt = [b for b in nbrs[order[-1]] if b != prev and b not in order]
        if not nxt:
            break
        prev = order[-1]
        order.append(nxt[0])
    if len(order) != len(links):
        return [], False
    closed = not ends and len(links) > 2
    if not closed and len(ends) != 2 and len(links) > 1:
        return [], False
    return order, closed


def _snap_value(value: float, budget: float) -> float:
    for step in (1.0, 0.5, 0.1):
        snapped = round(value / step) * step
        if abs(snapped - value) <= budget:
            return snapped
    return value


def _intersections(a: Piece, b: Piece) -> list[np.ndarray]:
    """Where two pieces (as whole lines and circles) cross; for two that only nearly touch,
    the point where they come closest."""
    if isinstance(a, Arc2) and isinstance(b, Line2):
        a, b = b, a
    if isinstance(a, Line2) and isinstance(b, Line2):
        m = np.array([a.normal, b.normal])
        if abs(np.linalg.det(m)) < 1e-9:
            return []
        return [np.linalg.solve(m, [a.offset, b.offset])]
    if isinstance(a, Line2):
        foot = b.centre - a.distance(b.centre[None])[0] * a.normal
        h2 = b.radius**2 - float(np.sum((foot - b.centre) ** 2))
        if h2 <= 0:
            return [foot]
        h = np.sqrt(h2)
        return [foot + h * a.direction, foot - h * a.direction]
    d = b.centre - a.centre
    dist = float(np.linalg.norm(d))
    if dist < 1e-12:
        return []
    u = d / dist
    x = (dist**2 + a.radius**2 - b.radius**2) / (2 * dist)
    h2 = a.radius**2 - x**2
    base = a.centre + x * u
    if h2 <= 0:
        return [base]
    perp = np.array([-u[1], u[0]]) * np.sqrt(h2)
    return [base + perp, base - perp]


def _nearest(points: list[np.ndarray], hint: np.ndarray) -> np.ndarray | None:
    if not points:
        return None
    return min(points, key=lambda p: float(np.linalg.norm(p - hint)))


def _end_point(piece: Piece, end: _End) -> np.ndarray | None:
    if end.kind == "axis":
        stop = Line2(np.array([0.0, 1.0]), 0.0)
    else:
        stop = Line2(np.array([1.0, 0.0]), end.z)
    return _nearest(_intersections(piece, stop), end.near)


def _inner_radius(mesh: Geo, faces: np.ndarray, axis: Axis) -> float:
    """How close a flat ring of faces square to the axis comes to it: 0 when a triangle
    covers the axis, else the nearest point of the triangles' edges."""
    rel = mesh.vertices[mesh.faces[faces]] - axis.point
    rel = rel - (rel @ axis.direction)[..., None] * axis.direction  # onto the plane
    a, b, c = rel[:, 0], rel[:, 1], rel[:, 2]
    # The axis is inside a triangle when it is on the same side of all three edges.
    n = np.cross(b - a, c - a)
    sides = np.stack(
        [(np.cross(q - p, -p) * n).sum(axis=1) for p, q in ((a, b), (b, c), (c, a))], axis=1
    )
    if ((sides >= 0).all(axis=1) | (sides <= 0).all(axis=1)).any():
        return 0.0
    best = np.inf
    for p, q in ((a, b), (b, c), (c, a)):
        d = q - p
        t = np.clip(-(p * d).sum(axis=1) / np.maximum((d * d).sum(axis=1), 1e-24), 0.0, 1.0)
        best = min(best, float(np.linalg.norm(p + t[:, None] * d, axis=1).min()))
    return best


def build_outline(
    parts: list[ProfilePart],
    f: _Folded,
    labels: np.ndarray,
    mesh: Geo,
    bases: list[Region],
    axis: Axis,
    level: ToleranceLevel,
) -> Outline | None:
    """Put the pieces in order, find how each end finishes (on the axis, or on a face the
    shape stands on), snap sizes, and place the corners where the snapped pieces meet."""
    tol = level.fit_tolerance_mm
    # Links between parts, and the scan's corner between each pair: the vertices they share.
    local = np.full(len(mesh.faces), -1)
    local[f.faces] = labels
    fa, fb = local[mesh.face_adjacency[:, 0]], local[mesh.face_adjacency[:, 1]]
    across = (fa >= 0) & (fb >= 0) & (fa != fb)
    weights: dict[tuple[int, int], int] = {}
    corner_verts: dict[tuple[int, int], list[int]] = {}
    for a, b, edge in zip(fa[across], fb[across], mesh.face_adjacency_edges[across], strict=True):
        key = (int(min(a, b)), int(max(a, b)))
        weights[key] = weights.get(key, 0) + 1
        corner_verts.setdefault(key, []).extend(edge.tolist())
    order, closed = _order(parts, weights)
    if not order:
        # A scrap fitted on its own in a corner (a few faces of a scan's rounded inside
        # corner) can link three pieces: the outline goes on without it.
        scrap = min(
            SCRAP_SHARE * sum(p.area for p in parts), SCRAP_FEATURES * level.min_feature_mm**2
        )
        small = frozenset(i for i, p in enumerate(parts) if p.area < scrap)
        if small:
            order, closed = _order(parts, weights, small)
    if not order:
        return None
    pieces = [parts[i].piece for i in order]
    n = len(pieces)
    pts_of = [
        np.concatenate(
            [
                f.qv[np.unique(mesh.faces[parts[i].faces])],
                f.q[np.searchsorted(f.faces, parts[i].faces)],
            ]
        )
        for i in order
    ]
    hints = []
    for k in range(n - 1 + closed):
        a, b = order[k], order[(k + 1) % n]
        hints.append(f.qv[np.unique(corner_verts[(min(a, b), max(a, b))])].mean(axis=0))

    ends = None
    if not closed:
        tris = mesh.vertices[mesh.faces[f.faces]]
        shortest = np.linalg.norm(tris - np.roll(tris, 1, axis=1), axis=2).min(axis=1)
        reach = REACH_TOLS * tol + float(np.median(shortest))
        rim = _open_edge(mesh)
        rim_q = axis.to_profile(rim)
        z_span = (float(f.qv[:, 0].min()), float(f.qv[:, 0].max()))

        def open_end(far: np.ndarray) -> bool:
            """The scan is open at this end of the shape, all round it: the base it stood
            on was never scanned."""
            if min(abs(far[0] - z) for z in z_span) > reach:
                return False
            near = (np.abs(rim_q[:, 0] - far[0]) <= reach) & (rim_q[:, 1] <= far[1] + reach)
            return near.any() and coverage(axis, rim[near]) >= OPEN_END_COVERAGE

        base_z = [
            ((b.plane.offset * b.plane.normal - axis.point) @ axis.direction, b) for b in bases
        ]

        def end_at(k: int, toward: np.ndarray | None, first: bool) -> _End | None:
            t = _param(pieces[k], pts_of[k])
            if toward is None:
                far = pts_of[k][np.argmin(t)] if first else pts_of[k][np.argmax(t)]
            else:
                t_hint = _param(
                    pieces[k],
                    toward[None],
                    ref=None
                    if isinstance(pieces[k], Line2)
                    else (pts_of[k] - pieces[k].centre).mean(axis=0),
                )[0]
                far = pts_of[k][np.argmax(np.abs(t - t_hint))]
            piece = pieces[k]
            if isinstance(piece, Line2) and piece.kind() == "plane" and toward is not None:
                # A flat face's inner edge, from its triangles: a CAD export's disc has no
                # vertices inside its rim, but one of its triangles covers the axis.
                outer = float(pts_of[k][:, 1].max())
                if toward[1] > 0.5 * outer:
                    inner = _inner_radius(mesh, parts[order[k]].faces, axis)
                    far = np.array([far[0], min(inner, float(far[1]))])
            if far[1] <= reach:
                return _End("axis", far)
            near_bases = [(abs(far[0] - z), z, b) for z, b in base_z if abs(far[0] - z) <= reach]
            if near_bases:
                _, z, b = min(near_bases, key=lambda x: x[0])
                return _End("base", far, b, float(z))
            if not bases and open_end(far):
                return _End("open", far)
            return None

        first = end_at(0, hints[0] if n > 1 else None, True)
        last = end_at(n - 1, hints[-1] if n > 1 else None, False)
        if first is None or last is None:
            return None
        ends = (first, last)

    # Heights are measured from the face the shape stands on, or from its biggest flat face.
    if bases:
        z0 = float((bases[0].plane.offset * bases[0].plane.normal - axis.point) @ axis.direction)
    else:
        flats = [
            (parts[i].area, p)
            for i, p in zip(order, pieces, strict=True)
            if isinstance(p, Line2) and p.kind() == "plane"
        ]
        z0 = (
            float(
                max(flats, key=lambda x: x[0])[1].offset
                * np.sign(max(flats, key=lambda x: x[0])[1].normal[0])
            )
            if flats
            else 0.0
        )
    capped = 0
    if ends is not None and any(e.kind == "open" for e in ends):
        # Close each open end with a flat face square to the axis, out to the axis.
        middle = float(np.median(f.qv[:, 0]))
        closed_ends = []
        for k, end in enumerate(ends):
            if end.kind != "open":
                closed_ends.append(end)
                continue
            normal = np.array([1.0 if end.near[0] > middle else -1.0, 0.0])
            flat = Line2(normal, float(normal @ end.near))
            span = np.stack([np.full(8, end.near[0]), np.linspace(0.0, end.near[1], 8)], axis=1)
            at = 0 if k == 0 else len(pieces)
            pieces.insert(at, flat)
            pts_of.insert(at, span)
            hints.insert(at if k == 0 else len(hints), end.near)
            closed_ends.append(_End("axis", np.array([end.near[0], 0.0])))
            capped += 1
        ends = (closed_ends[0], closed_ends[1])
        n = len(pieces)
    pieces, names = _snap_pieces(pieces, pts_of, hints, closed, ends, z0, level)
    if pieces is None:
        return None

    points: list[np.ndarray] = []
    if not closed:
        p = _end_point(pieces[0], ends[0])
        if p is None:
            return None
        points.append(p)
    for k in range(n - 1 + closed):
        p = _nearest(_intersections(pieces[k], pieces[(k + 1) % n]), hints[k])
        if p is None:
            return None
        points.append(p)
    if closed:
        points = [points[-1], *points[:-1]]
    else:
        p = _end_point(pieces[-1], ends[1])
        if p is None:
            return None
        points.append(p)
    mids: list[np.ndarray | None] = []
    for k, piece in enumerate(pieces):
        a, b = points[k], points[(k + 1) % len(points)]
        if isinstance(piece, Line2):
            mids.append(None)
            continue
        mids.append(_arc_middle(piece, a, b, pts_of[k]))
    outline = Outline(pieces, points, mids, closed, ends, names, capped)
    return outline if _simple(outline, tol) else None


def _open_edge(mesh: Geo) -> np.ndarray:
    """The vertices on the scan's open edges: those of edges only one triangle has."""
    edges = np.sort(mesh.faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
    unique, count = np.unique(edges, axis=0, return_counts=True)
    return mesh.vertices[np.unique(unique[count == 1])]


def _arc_middle(arc: Arc2, a: np.ndarray, b: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """The point halfway round the arc from a to b, going the way the scan does."""
    ta = np.arctan2(*(a - arc.centre)[::-1])
    tb = np.arctan2(*(b - arc.centre)[::-1])
    tm = np.arctan2(*np.median(pts - arc.centre, axis=0)[::-1])
    sweep = (tb - ta) % (2 * np.pi)  # going anticlockwise from a
    if not (tm - ta) % (2 * np.pi) <= sweep:
        sweep -= 2 * np.pi  # the scan is the other way round
    t = ta + sweep / 2
    return arc.centre + arc.radius * np.array([np.cos(t), np.sin(t)])


def _samples(outline: Outline, k: int) -> np.ndarray:
    a = outline.points[k]
    b = outline.points[(k + 1) % len(outline.points)]
    piece = outline.pieces[k]
    if isinstance(piece, Line2):
        return np.array([a, b])
    c = piece.centre
    ta = np.arctan2(*(a - c)[::-1])
    tm = np.arctan2(*(outline.mids[k] - c)[::-1])
    tb = np.arctan2(*(b - c)[::-1])
    half = (tm - ta + np.pi) % (2 * np.pi) - np.pi
    rest = (tb - tm + np.pi) % (2 * np.pi) - np.pi
    t = np.concatenate(
        [ta + np.linspace(0, half, ARC_SAMPLES // 2), tm + np.linspace(0, rest, ARC_SAMPLES // 2)]
    )
    return c + piece.radius * np.stack([np.cos(t), np.sin(t)], axis=1)


def _simple(outline: Outline, tol: float) -> bool:
    """No piece is (nearly) empty or crosses another, and the outline stays off the far side
    of the axis."""
    count = len(outline.pieces)
    polys = [_samples(outline, k) for k in range(count)]
    for k, poly in enumerate(polys):
        if np.linalg.norm(poly[-1] - poly[0]) < 1e-3 * tol and isinstance(outline.pieces[k], Line2):
            return False
        if poly[:, 1].min() < -1e-6:
            return False
    for i in range(count):
        for j in range(i + 1, count):
            adjacent = j == i + 1 or (outline.closed and i == 0 and j == count - 1)
            if _crosses(polys[i], polys[j], skip_shared=adjacent):
                return False
    return True


def _crosses(p: np.ndarray, q: np.ndarray, skip_shared: bool) -> bool:
    for i in range(len(p) - 1):
        for j in range(len(q) - 1):
            if skip_shared and (
                np.allclose(p[i + 1], q[j], atol=1e-9) or np.allclose(p[i], q[j + 1], atol=1e-9)
            ):
                continue
            if _segments_cross(p[i], p[i + 1], q[j], q[j + 1]):
                return True
    return False


def _segments_cross(a, b, c, d) -> bool:
    def side(p, q, r) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    d1, d2 = side(c, d, a), side(c, d, b)
    d3, d4 = side(a, b, c), side(a, b, d)
    return d1 * d2 < -1e-12 and d3 * d4 < -1e-12


def _on_axis(pts: np.ndarray, arc: Arc2, radius: float | None = None) -> tuple[float, float]:
    """Centre height and radius of the circle centred on the axis that best fits pts, or just
    the centre height for a given radius."""
    if radius is None:

        def residuals(p: np.ndarray) -> np.ndarray:
            return np.hypot(pts[:, 0] - p[0], pts[:, 1]) - p[1]

        p = least_squares(residuals, [arc.centre[0], arc.radius], method="lm").x
        return float(p[0]), abs(float(p[1]))

    def at(p: np.ndarray) -> np.ndarray:
        return np.hypot(pts[:, 0] - p[0], pts[:, 1]) - radius

    return float(least_squares(at, [arc.centre[0]], method="lm").x[0]), radius


def _snap_pieces(
    pieces: list[Piece],
    pts_of: list[np.ndarray],
    hints: list[np.ndarray],
    closed: bool,
    ends: tuple[_End, _End] | None,
    z0: float,
    level: ToleranceLevel,
) -> tuple[list[Piece] | None, list[str | None]]:
    """Clean sizes, the way the part was drawn: heights and diameters to round values, cone
    angles to standard ones, and rounds exactly tangent to what they round off. A size only
    snaps if that moves the surface by at most SNAP_SHARE of the tolerance, and no snapped
    piece may leave the scan by more than the tolerance."""
    tol = level.fit_tolerance_mm
    budget = SNAP_SHARE * tol
    n = len(pieces)
    out: list[Piece] = list(pieces)
    names: list[str | None] = [None] * n

    def neighbour(k: int, step: int) -> int | None:
        j = k + step
        if closed:
            return j % n
        return j if 0 <= j < n else None

    def corner_hint(k: int, step: int) -> np.ndarray:
        return hints[k - 1] if step < 0 else hints[k]

    def still_fits(piece: Piece, k: int) -> bool:
        return float(np.percentile(np.abs(piece.distance(pts_of[k])), FIT_PERCENTILE)) <= tol

    # Flat faces and cylinders first: they set the corners everything else hangs off.
    for k, piece in enumerate(pieces):
        if not isinstance(piece, Line2) or piece.kind() == "cone":
            continue
        if piece.kind() == "plane":
            s = float(np.sign(piece.normal[0]))
            z = z0 + _snap_value(piece.offset * s - z0, budget)
            snapped = Line2(np.array([s, 0.0]), s * z)
        else:
            s = float(np.sign(piece.normal[1]))
            radius = piece.offset * s
            # Same rules as holes and bosses, so a countersink's shank comes out as a hole.
            radius, names[k] = _snap_radius(radius, tol, "hole" if s < 0 else "boss")
            snapped = Line2(np.array([0.0, s]), s * radius)
        if still_fits(snapped, k):
            out[k] = snapped

    # Cones: a standard angle through a snapped end, or both ends snapped.
    for k, piece in enumerate(pieces):
        if not isinstance(piece, Line2) or piece.kind() != "cone":
            continue
        # A diameter on a flat face is how a countersink or chamfer is drawn, so those
        # anchors come before a height up a cylinder.
        anchored: list[tuple[int, np.ndarray]] = []
        for step in (-1, 1):
            j = neighbour(k, step)
            if j is not None:
                other = out[j]
                if not isinstance(other, Line2) or other.kind() == "cone":
                    continue
                p = _nearest(_intersections(piece, other), corner_hint(k, step))
                if p is None:
                    continue
                flat = other.kind() == "plane"
                if flat:  # slides along the flat face: snap its diameter
                    p = np.array([p[0], _snap_value(2 * p[1], 2 * budget) / 2])
                else:  # slides along the cylinder: snap its height
                    p = np.array([z0 + _snap_value(p[0] - z0, budget), p[1]])
            else:
                end = ends[0] if step < 0 else ends[1]
                p = _end_point(piece, end)
                if p is None:
                    continue
                flat = end.kind == "base"
                if flat:
                    p = np.array([p[0], _snap_value(2 * p[1], 2 * budget) / 2])
                else:
                    p = np.array([z0 + _snap_value(p[0] - z0, budget), 0.0])
            anchored.append((0 if flat else 1, p))
        anchors = [p for _, p in sorted(anchored, key=lambda x: x[0])]
        d = piece.direction
        half = np.degrees(np.arctan2(abs(d[1]), abs(d[0])))
        best = min(STANDARD_HALF_ANGLES, key=lambda a: abs(a - half))
        candidates: list[Piece] = []
        if anchors and abs(best - half) <= level.angle_snap_deg:
            t = np.radians(best)
            dz, dr = np.sign(d[0]) * np.cos(t), np.sign(d[1]) * np.sin(t)
            normal = np.array([dr, -dz])
            if normal @ piece.normal < 0:
                normal = -normal
            candidates += [Line2(normal, float(normal @ a)) for a in anchors]
        if len(anchors) == 2 and np.linalg.norm(anchors[1] - anchors[0]) > tol:
            along = (anchors[1] - anchors[0]) / np.linalg.norm(anchors[1] - anchors[0])
            normal = np.array([along[1], -along[0]])
            if normal @ piece.normal < 0:
                normal = -normal
            candidates.append(Line2(normal, float(normal @ anchors[0])))
        for c in candidates:
            if still_fits(c, k):
                out[k] = c
                break

    # Arcs: round radius; a sphere's centre on the axis; tangent to the lines they round off.
    for k, piece in enumerate(pieces):
        if not isinstance(piece, Arc2):
            continue
        radius = _snap_value(piece.radius, budget)
        centre = piece.centre.copy()
        # An arc that ends on the axis is a ball: off it, it would leave a point or a dimple.
        on_axis = ends is not None and any(
            neighbour(k, step) is None and ends[step > 0].kind == "axis" for step in (-1, 1)
        )
        if abs(centre[1]) <= tol or on_axis:
            centre[1] = 0.0
        tangent: list[Line2] = []
        for step in (-1, 1):
            j = neighbour(k, step)
            if j is None or not isinstance(out[j], Line2):
                continue
            line = out[j]
            p = corner_hint(k, step)
            arc_n = piece.normal_at(p[None])[0]
            if arc_n @ line.normal >= np.cos(np.radians(TANGENT_DEG)):
                tangent.append(line)
        sign = -1.0 if piece.convex else 1.0  # which side of the line the centre is on
        if len(tangent) == 2 and centre[1] != 0.0:
            m = np.array([tangent[0].normal, tangent[1].normal])
            if abs(np.linalg.det(m)) > 1e-6:
                rhs = [tangent[0].offset + sign * radius, tangent[1].offset + sign * radius]
                centre = np.linalg.solve(m, rhs)
        elif tangent and centre[1] == 0.0:
            # A ball on a neck: its radius is the neck's.
            centre[0] = z0 + _snap_value(centre[0] - z0, budget)
            radius = abs(float(tangent[0].distance(centre[None])[0]))
        elif tangent:
            line = tangent[0]
            centre = centre + (line.offset + sign * radius - line.normal @ centre) * line.normal
        elif centre[1] == 0.0:
            # A ball: refitted with its centre held on the axis (a free fit lets noise pull it
            # off, and the radius with it), then the radius rounded and the centre refitted
            # for that radius.
            radius = _snap_value(_on_axis(pts_of[k], piece)[1], budget)
            centre[0] = _on_axis(pts_of[k], piece, radius)[0]
            centre[0] = z0 + _snap_value(centre[0] - z0, budget)
        else:
            centre[0] = z0 + _snap_value(centre[0] - z0, budget)
            centre[1] = _snap_value(centre[1], budget)
        snapped = Arc2(centre, radius, piece.convex)
        if still_fits(snapped, k):
            out[k] = snapped
    return out, names


# ---------------------------------------------------------------------------------------------
# Solids
# ---------------------------------------------------------------------------------------------


def _point_in(poly: np.ndarray, p: np.ndarray) -> bool:
    inside = False
    for a, b in zip(poly, np.roll(poly, -1, axis=0), strict=True):
        if (a[1] > p[1]) != (b[1] > p[1]):
            x = a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if x > p[0]:
                inside = not inside
    return inside


class _Plane3:
    """The profile plane in 3D: (z, r) to a point."""

    def __init__(self, axis: Axis):
        a = axis.direction
        u = np.cross(a, [1.0, 0.0, 0.0])
        if np.linalg.norm(u) < 0.1:
            u = np.cross(a, [0.0, 1.0, 0.0])
        self.a, self.u, self.c = a, u / np.linalg.norm(u), axis.point

    def __call__(self, q: np.ndarray) -> cq.Vector:
        return cq.Vector(*(self.c + q[0] * self.a + q[1] * self.u))


def _edges(segments: list[tuple], at: _Plane3) -> list[cq.Edge]:
    edges = []
    for seg in segments:
        if len(seg) == 2:
            if np.linalg.norm(seg[1] - seg[0]) > 1e-9:
                edges.append(cq.Edge.makeLine(at(seg[0]), at(seg[1])))
        else:
            edges.append(cq.Edge.makeThreePointArc(at(seg[0]), at(seg[1]), at(seg[2])))
    return edges


def _revolve(face: cq.Shape, axis: Axis) -> cq.Shape | None:
    solids = []
    for fc in face.Faces():
        if fc.Area() < 1e-9:
            continue
        solids.append(
            cq.Solid.revolve(
                fc, 360.0, cq.Vector(*axis.point), cq.Vector(*(axis.point + axis.direction))
            )
        )
    if not solids:
        return None
    out = solids[0]
    for s in solids[1:]:
        out = out.fuse(s)
    return out.clean()


def outline_profiles(
    outline: Outline, axis: Axis, bases: list[Region]
) -> tuple[cq.Shape | None, cq.Shape | None]:
    """What to add to the part and what to cut from it, as flat profiles in the plane through
    the axis, to be revolved about it: (add, cut). A turned part (no base) is all add. They
    are closed loops of lines and arcs, so CAD can sketch them and revolve them again.

    The outline, carried on along the faces it stands on, splits a box round the shape (in
    the profile plane, from the axis outwards) into material and air. Revolving the material
    gives the shape; where it stands on the part, only what lies beyond the part's faces is
    added, and only the air within them is cut. So one rule covers a dome, a countersink, and
    a ring with its bore going on through the plate.
    """
    at = _Plane3(axis)
    pts = outline.points
    segs: list[tuple] = []
    for k in range(len(outline.pieces)):
        a, b = pts[k], pts[(k + 1) % len(pts)]
        segs.append((a, b) if outline.mids[k] is None else (a, outline.mids[k], b))
    sampled = np.concatenate([_samples(outline, k) for k in range(len(outline.pieces))])
    # Material side: just inside the longest piece, at its middle.
    lengths = [
        float(np.linalg.norm(np.ptp(_samples(outline, k), axis=0)))
        for k in range(len(outline.pieces))
    ]
    k = int(np.argmax(lengths))
    mid = _samples(outline, k)
    mid = mid[len(mid) // 2] if len(mid) > 2 else mid.mean(axis=0)
    eps = 1e-4 * float(np.linalg.norm(np.ptp(sampled, axis=0)))
    probe = mid - eps * outline.pieces[k].normal_at(mid[None])[0]

    if outline.closed:
        if not _point_in(sampled, probe):
            return None, None  # a hollow inside the part; not handled
        return cq.Face.makeFromWires(cq.Wire.assembleEdges(_edges(segs, at))), None

    z_lo, z_hi = sampled[:, 0].min() - OVERSHOOT_MM, sampled[:, 0].max() + OVERSHOOT_MM
    big_r = sampled[:, 1].max() + OVERSHOOT_MM
    # Carry each end that stands on a face out along it to the box's side.
    start, end = pts[0], pts[-1]
    if outline.ends[0].kind == "base":
        segs.insert(0, (np.array([start[0], big_r]), start))
        start = np.array([start[0], big_r])
    if outline.ends[1].kind == "base":
        segs.append((end, np.array([end[0], big_r])))
        end = np.array([end[0], big_r])
    path = np.concatenate([[start], sampled, [end]])
    corners = [np.array(c) for c in ((z_lo, 0.0), (z_lo, big_r), (z_hi, big_r), (z_hi, 0.0))]

    def around(p: np.ndarray) -> float:
        """Position on the box's boundary, going round from (z_lo, 0)."""
        if abs(p[1]) < 1e-9:
            return 3.0 + (z_hi - p[0]) / (z_hi - z_lo)
        return 1.0 + (p[0] - z_lo) / (z_hi - z_lo)

    def region(forward: bool) -> tuple[list[tuple], np.ndarray]:
        """The outline, then back to its start round the box one way or the other."""
        t_end, t_start = around(end), around(start)
        if forward:
            between = [
                (c, i) for i, c in enumerate(corners) if 0 < (i - t_end) % 4 < (t_start - t_end) % 4
            ]
            between.sort(key=lambda ci: (ci[1] - t_end) % 4)
        else:
            between = [
                (c, i) for i, c in enumerate(corners) if 0 < (t_end - i) % 4 < (t_end - t_start) % 4
            ]
            between.sort(key=lambda ci: (t_end - ci[1]) % 4)
        walk = [end, *[c for c, _ in between], start]
        back = [(walk[i], walk[i + 1]) for i in range(len(walk) - 1)]
        return segs + back, np.concatenate([path, np.array(walk[1:-1]).reshape(-1, 2)])

    one, one_poly = region(True)
    other, _ = region(False)
    material, air = (one, other) if _point_in(one_poly, probe) else (other, one)
    m_face = cq.Face.makeFromWires(cq.Wire.assembleEdges(_edges(material, at)))
    if not bases:
        return m_face, None
    a_face = cq.Face.makeFromWires(cq.Wire.assembleEdges(_edges(air, at)))
    # The slab of the part between the faces the shape stands on.
    h_lo, h_hi = z_lo, z_hi
    for b in bases:
        z = float((b.plane.offset * b.plane.normal - axis.point) @ axis.direction)
        if b.plane.normal @ axis.direction > 0:
            h_hi = min(h_hi, z)
        else:
            h_lo = max(h_lo, z)
    if h_hi <= h_lo:
        return None, None
    slab = [np.array(p) for p in ((h_lo, 0.0), (h_lo, big_r), (h_hi, big_r), (h_hi, 0.0))]
    s_face = cq.Face.makeFromWires(
        cq.Wire.assembleEdges(_edges([(slab[i], slab[(i + 1) % 4]) for i in range(4)], at))
    )
    add, cut = m_face.cut(s_face), a_face.intersect(s_face)
    return (
        add if any(f.Area() > 1e-9 for f in add.Faces()) else None,
        cut if any(f.Area() > 1e-9 for f in cut.Faces()) else None,
    )


# ---------------------------------------------------------------------------------------------
# The whole step
# ---------------------------------------------------------------------------------------------


@dataclass
class Revolved:
    """A shape turned about an axis: the whole part, or a feature on one of its faces."""

    kind: str
    """"turned" for a whole turned part; "countersink", "dome", "tapered_boss",
    "revolved_boss" or "revolved_cut" for a feature on a face."""
    axis: Axis
    outline: Outline
    add: cq.Shape | None
    cut: cq.Shape | None
    labels: set[int]
    """Planes and patches it replaces."""
    faces: np.ndarray
    """Mesh faces it covers."""
    inside: np.ndarray
    """Per face: within the part's faces it stands on (so solid in the base before the cut)."""
    dims: dict = field(default_factory=dict)
    add_profile: cq.Shape | None = None
    cut_profile: cq.Shape | None = None
    """The flat faces revolved into add and cut, in a plane through the axis: what an
    editable history sketches and revolves."""
    holes: list[Feature] = field(default_factory=list)
    """Holes drilled across a turned part, cut after it like any other hole."""

    def as_dict(self) -> dict:
        return {"type": self.kind, **self.dims}

    def on_surface(self, points: np.ndarray, tol: float) -> np.ndarray:
        """Which points lie on the shape's surface, within tol."""
        q = self.axis.to_profile(points)
        out = np.zeros(len(q), bool)
        path = _outline_path(self.outline)
        for i in range(0, len(q), 2000):
            out[i : i + 2000] = _gap(q[i : i + 2000], path) <= tol
        return out


# A tapered boss's top is the part of its outline within this share of its height of the top.
TOP_SHARE = 0.02


def describe(
    outline: Outline, bases: list[Region], axis: Axis, has_add: bool, has_cut: bool
) -> tuple[str, dict]:
    kinds = [p.kind() for p in outline.pieces]
    sampled = np.concatenate([_samples(outline, k) for k in range(len(outline.pieces))])
    diameter = round(2 * float(sampled[:, 1].max()), 3)
    surfaces = {
        k: kinds.count(k)
        for k in ("cone", "sphere", "torus", "cylinder", "plane")
        if kinds.count(k)
    }
    if not bases:
        height = round(float(np.ptp(sampled[:, 0])), 3)
        return "turned", {"diameter_mm": diameter, "height_mm": height, "surfaces": surfaces}

    def height_over(base: Region) -> np.ndarray:
        z_b = float((base.plane.offset * base.plane.normal - axis.point) @ axis.direction)
        return (sampled[:, 0] - z_b) * float(np.sign(base.plane.normal @ axis.direction))

    # Heights are taken from the face an added shape stands on (a ring on a plate's top,
    # not its bottom, which only the bore reaches).
    above = max((height_over(b) for b in bases), key=lambda h: float(h.max()))
    if has_add and not has_cut:
        height = round(float(above.max()), 3)
        if "sphere" in kinds and len(set(kinds) - {"plane"}) == 1:
            arc = outline.pieces[kinds.index("sphere")]
            return "dome", {
                "radius_mm": round(arc.radius, 3),
                "height_mm": height,
                "diameter_mm": diameter,
            }
        if kinds.count("cone") == 1 and set(kinds) <= {"cone", "plane"}:
            # The top's width: noise can leave its flat a hair off square to the axis.
            top = float(sampled[above >= (1 - TOP_SHARE) * above.max(), 1].max())
            return "tapered_boss", {
                "diameters_mm": [diameter, round(2 * top, 3)],
                "height_mm": height,
            }
        return "revolved_boss", {"diameter_mm": diameter, "height_mm": height, "surfaces": surfaces}
    depth = round(float(-above.min()), 3)
    cyl = [
        k
        for k, p in enumerate(outline.pieces)
        if isinstance(p, Line2) and p.kind() == "cylinder" and p.normal[1] < 0
    ]
    if (
        has_cut
        and not has_add
        and "cone" in kinds
        and cyl
        and set(kinds) <= {"cone", "cylinder", "plane"}
    ):
        at = min(cyl, key=lambda k: abs(outline.pieces[k].offset))
        shank = outline.pieces[at]
        cone = outline.pieces[kinds.index("cone")]
        half = np.degrees(np.arctan2(abs(cone.direction[1]), abs(cone.direction[0])))
        out_d = {
            "diameter_mm": round(2 * abs(shank.offset), 3),
            "head_mm": diameter,
            "angle_deg": round(float(2 * half), 2),
        }
        if len(bases) == 2:
            out_d["through"] = True
        else:
            out_d["depth_mm"] = depth
        name = outline.names[at]
        if name:
            out_d["standard"] = name
        return "countersink", out_d
    dims = {"diameter_mm": diameter, "surfaces": surfaces}
    if has_add:
        dims["height_mm"] = round(float(above.max()), 3)
        return "revolved_boss", dims
    dims["depth_mm"] = depth
    return "revolved_cut", dims


def off_bases(
    mesh: Geo, faces: np.ndarray, bases: list[Region], normals: np.ndarray, tol: float
) -> np.ndarray:
    """The faces that are not part of a base: in a noisy scan the crease where a shape meets
    the face it stands on is blurred, and the faces of the crease that lie on the base and face
    its way would otherwise make a sliver of a flat (or a shallow cone) at the shape's foot."""
    keep = np.ones(len(faces), bool)
    corners = mesh.vertices[mesh.faces[faces]]
    for b in bases:
        n, d = b.plane.normal, b.plane.offset
        on = np.abs(corners @ n - d).max(axis=1) <= tol
        on &= normals[faces] @ n >= np.cos(np.radians(LINE_NORMAL_DEG))
        keep &= ~on
    return faces[keep]


def find_revolved(
    mesh: Geo,
    planes: list[Region],
    patches: list[CurvedPatch],
    labels: np.ndarray,
    seeds: set[int],
    cuttable: set[int],
    noise_mm: float,
    level: ToleranceLevel,
) -> list[Revolved]:
    """Cones, spheres and tori: every shape turned about an axis that has at least one of
    them. Shapes made only of cylinders and flat faces are left to the hole, boss and fillet
    rules, which name them better."""
    normals = profile_normals(mesh, noise_mm)
    found = []
    grouping = axis_normals(mesh, noise_mm)
    for group in find_groups(mesh, planes, patches, labels, seeds, cuttable, grouping, level):
        member = np.flatnonzero(np.isin(labels, list(group.labels)))
        member = off_bases(mesh, member, group.bases, normals, level.fit_tolerance_mm)
        outline = None
        # The fullest wedge first; if its pieces don't make an outline, the most typical one.
        for typical in (False, True) if len(member) > WEDGE_FACES else (False,):
            folded, parts, part_of = fit_turned(mesh, member, group.axis, normals, level, typical)
            if not parts or not {p.piece.kind() for p in parts} & {"cone", "sphere", "torus"}:
                continue
            outline = build_outline(parts, folded, part_of, mesh, group.bases, group.axis, level)
            if outline is not None:
                break
        if outline is None:
            continue
        try:
            profiles = outline_profiles(outline, group.axis, group.bases)
            add, cut = (_revolve(f, group.axis) if f is not None else None for f in profiles)
        except Exception:  # OpenCASCADE raises a variety of errors on a bad outline
            continue
        if add is None and cut is None:
            continue
        kind, dims = describe(outline, group.bases, group.axis, add is not None, cut is not None)
        every = group.labels | group.junk | group.pockets
        faces = np.flatnonzero(np.isin(labels, list(every)))
        z = Axis.to_profile(group.axis, mesh.vertices[mesh.faces[faces]].mean(axis=1))[:, 0]
        inside = np.ones(len(faces), bool)
        for b in group.bases:
            z_b = float((b.plane.offset * b.plane.normal - group.axis.point) @ group.axis.direction)
            inside &= (z - z_b) * np.sign(b.plane.normal @ group.axis.direction) <= 0
        holes = []
        if not group.bases:
            pocket = np.flatnonzero(np.isin(labels, list(group.pockets)))
            holes = cross_holes(
                mesh, np.concatenate([member, pocket]), outline, group.axis, normals, level
            )
        found.append(
            Revolved(
                kind,
                group.axis,
                outline,
                add,
                cut,
                every,
                faces,
                inside,
                dims,
                *profiles,
                holes=holes,
            )
        )
    return found


def _outline_path(outline: Outline) -> np.ndarray:
    """The outline as a closed polyline in (z, r)."""
    path = np.concatenate([_samples(outline, k)[:-1] for k in range(len(outline.pieces))])
    return np.concatenate([path, outline.points[-1][None]])


def _gap(q: np.ndarray, path: np.ndarray) -> np.ndarray:
    """How far each (z, r) point is from a closed polyline."""
    a, b = path, np.roll(path, -1, axis=0)
    d = b - a
    t = np.clip(
        ((q[:, None] - a[None]) * d[None]).sum(axis=2) / np.maximum((d * d).sum(axis=1), 1e-12),
        0.0,
        1.0,
    )
    return np.linalg.norm(q[:, None] - (a[None] + t[..., None] * d[None]), axis=2).min(axis=1)


def _sunk_faces(q: np.ndarray, outline: Outline, depth: float) -> np.ndarray:
    """Which (z, r) points lie inside a turned part's outline, deeper than depth below it."""
    path = _outline_path(outline)
    a, b = path, np.roll(path, -1, axis=0)
    # Inside: crossings of a ray along +z (the outline closes along the axis).
    crosses = (a[None, :, 1] > q[:, None, 1]) != (b[None, :, 1] > q[:, None, 1])
    with np.errstate(divide="ignore", invalid="ignore"):
        z = (
            a[None, :, 0]
            + (q[:, None, 1] - a[None, :, 1]) * (b - a)[None, :, 0] / (b - a)[None, :, 1]
        )
    inside = (crosses & (z > q[:, None, 0])).sum(axis=1) % 2 == 1
    return inside & (_gap(q, path) > depth)


def cross_holes(
    mesh: Geo,
    faces: np.ndarray,
    outline: Outline,
    axis: Axis,
    normals: np.ndarray,
    level: ToleranceLevel,
) -> list[Feature]:
    """Holes drilled across a turned part, square to its axis: the areas sunk into it that the
    outline doesn't explain, each fitted as a cylinder. A scanner sees only the mouth of a
    small hole, so the walls are fitted on part of the way round; both mouths open in the scan
    (or the far one unscanned) make it a hole through, else it stops where the scan does."""
    tol = level.fit_tolerance_mm
    centres = mesh.vertices[mesh.faces[faces]].mean(axis=1)
    sunk = faces[_sunk_faces(axis.to_profile(centres), outline, POCKET_TOLS * tol)]
    if not len(sunk):
        return []
    # Pieces of sunk area, by shared edges.
    pos = np.full(len(mesh.faces), -1)
    pos[sunk] = np.arange(len(sunk))
    fa, fb = pos[mesh.face_adjacency[:, 0]], pos[mesh.face_adjacency[:, 1]]
    keep = (fa >= 0) & (fb >= 0)
    graph = csr_matrix(
        (np.ones(int(keep.sum()), bool), (fa[keep], fb[keep])), shape=(len(sunk), len(sunk))
    )
    _, comp = connected_components(graph, directed=False)
    u = np.cross(axis.direction, [1.0, 0.0, 0.0])
    if np.linalg.norm(u) < 0.1:
        u = np.cross(axis.direction, [0.0, 1.0, 0.0])
    u /= np.linalg.norm(u)
    v = np.cross(axis.direction, u)
    sampled = np.concatenate([_samples(outline, k) for k in range(len(outline.pieces))])

    def centre_of(idx: np.ndarray) -> np.ndarray:
        return mesh.vertices[mesh.faces[idx]].mean(axis=1)

    fitted: list[tuple[Cylinder, np.ndarray]] = []
    for c in np.unique(comp):
        idx = sunk[comp == c]
        if mesh.area_faces[idx].sum() < level.min_feature_mm**2:
            continue
        # The walls face across the hole, never along it: its direction is the one square to
        # the axis that the normals are furthest from.
        n = normals[idx]
        w = mesh.area_faces[idx]
        nu, nv = n @ u, n @ v
        scatter = np.array([[w @ (nu * nu), w @ (nu * nv)], [w @ (nu * nv), w @ (nv * nv)]])
        _, vecs = np.linalg.eigh(scatter)
        d = vecs[0, 0] * u + vecs[1, 0] * v
        pts = mesh.vertices[np.unique(mesh.faces[idx])]
        middle = centre_of(idx).mean(axis=0)
        start = Cylinder(
            axis.point + ((middle - axis.point) @ axis.direction) * axis.direction, d, 1.0
        )
        start.radius = float(np.median(np.abs(start.distance(pts) + 1.0)))
        cyl = fit_circle_on_axis(pts, d, start)
        # Drilled through the axis when it passes that close.
        rel = cyl.point - axis.point
        off = rel - (rel @ axis.direction) * axis.direction
        off -= (off @ d) * d
        if np.linalg.norm(off) <= CROSS_HOLE_TOLS * tol:
            cyl.point = cyl.point - off
            cyl.radius = float(np.median(np.abs(cyl.distance(pts) + cyl.radius)))
        misfit = np.percentile(np.abs(cyl.distance(pts)), CROSS_HOLE_PERCENTILE)
        hollow = float(w @ (cyl.radial(centre_of(idx)) * n).sum(axis=1)) < 0
        if (
            misfit > CROSS_HOLE_TOLS * tol
            or not hollow
            or not level.min_feature_mm / 2 <= cyl.radius <= sampled[:, 1].max()
        ):
            continue
        fitted.append((cyl, idx))

    # Two mouths of one hole: the same line and size.
    holes: list[list[tuple[Cylinder, np.ndarray]]] = []
    for cyl, idx in fitted:
        for hole in holes:
            other = hole[0][0]
            rel = cyl.point - other.point
            apart = np.linalg.norm(rel - (rel @ other.axis) * other.axis)
            if (
                abs(cyl.axis @ other.axis) >= np.cos(np.radians(level.angle_snap_deg))
                and apart <= CROSS_HOLE_TOLS * tol
                and abs(cyl.radius - other.radius) <= CROSS_HOLE_TOLS * tol
            ):
                hole.append((cyl, idx))
                break
        else:
            holes.append([(cyl, idx)])

    out = []
    every = mesh.vertices[mesh.faces[faces]].mean(axis=1)
    for k, hole in enumerate(holes):
        idx = np.concatenate([i for _, i in hole])
        pts = mesh.vertices[np.unique(mesh.faces[idx])]
        cyl = hole[0][0]
        d = cyl.axis
        # Where the hole's line is nearest the axis, and how far out the part's outside is
        # at the hole's height.
        rel = cyl.point - axis.point
        h = float(rel @ axis.direction)
        aside = rel - h * axis.direction
        aside -= (aside @ d) * d
        p0 = axis.point + h * axis.direction + aside
        e = float(np.linalg.norm(aside))
        band = np.abs(sampled[:, 0] - h) <= cyl.radius
        outer = float(sampled[band, 1].max()) if band.any() else float(sampled[:, 1].max())
        reach = float(np.sqrt(max(outer**2 - e**2, 0.0)))
        t = (pts - p0) @ d
        side = 1.0 if float(np.median(t)) > 0 else -1.0
        sides = {float(np.sign(np.median((centre_of(i) - p0) @ d))) for _, i in hole}
        through = len(sides) == 2
        if not through:
            # The far mouth: open unless the scan covers where it would be.
            tf = (every - p0) @ d
            across = np.linalg.norm((every - p0) - np.outer(tf, d), axis=1)
            mouth = (tf * side < 0) & (np.abs(np.abs(tf) - reach) <= cyl.radius)
            mouth &= across <= cyl.radius
            covered = float(mesh.area_faces[faces[mouth]].sum())
            through = covered < FAR_MOUTH_COVER * np.pi * cyl.radius**2
        radius, label = _snap_radius(cyl.radius, tol, "hole")
        end = p0 + side * reach * d
        start = p0 - side * reach * d if through else p0 + side * float((t * side).min()) * d
        out.append(Feature("hole", -1 - k, radius, d, start, end, through=through, label=label))
    return out
