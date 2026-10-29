"""Freeform surfaces: smooth curved areas that are not planes, cylinders or shapes turned about
an axis (the top of a mouse shell, a mound on a plate, a thumb scoop), rebuilt as B-spline
surfaces.

By the time this runs, the earlier steps have split such an area into many small tilted flat
regions (facets), and sometimes a few wide "cylinders". Each connected area of those is
gathered up and fitted with one smooth surface:

1. Pick a direction to look along (w). The area must be a height field over the plane across
   it: every point of it seen once, like a landscape seen from above. For a mound on a flat
   face, w is that face's normal; otherwise it is the area's average normal.
2. Fit the height w = S(u, v) as a cubic B-spline (with a light smoothness penalty, a
   P-spline): start with a few large spans and add more until the scan fits within the
   tolerance and more stop helping. Few spans means a smooth surface that is easy to edit in
   CAD and does not follow scan noise.
3. Grow: neighbouring facets and patches that lie on the surface join it, and it is refitted.
4. Rebuild: the surface is extended a little past the area's edge, swept into a solid, and
   added to the part (a mound), cut from it (a scoop), or used to cut the top off a block (a
   whole curved top). The boolean then finds the exact edge where it meets the flat faces.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cadquery as cq
import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Section
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.Geom import Geom_BSplineSurface
from OCP.gp import gp_Pnt, gp_Vec
from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
from OCP.TColgp import TColgp_Array2OfPnt
from OCP.TColStd import TColStd_Array1OfInteger, TColStd_Array1OfReal
from OCP.TopoDS import TopoDS
from OCP.TopTools import TopTools_HSequenceOfShape
from scipy import sparse
from scipy.interpolate import BSpline
from scipy.spatial import ConvexHull

from .segment import Geo, Region
from .tolerance import ToleranceLevel

DEGREE = 3
# A freeform area must be at least this many smallest-feature sizes across, both ways; a
# narrow strip is a fillet or a rounded rim, which the other rules handle (or approximate).
MIN_WIDTH_FEATURES = 4.0
# Every face of the area must face within this angle of w (a height field).
MAX_TILT_DEG = 75.0
# Spans along the longer side tried in turn; the fit stops at the first that fits.
SPAN_STEPS = (2, 3, 4, 6, 8, 12, 16, 24)
# More spans are only worth it while they cut the RMS error to below this share.
NOISE_FLOOR = 0.8
# And not past this RMS error, in tolerances: finer detail than that is not worth the poles.
GOOD_RMS_TOLS = 0.1
# Share of the area's points that must lie within the tolerance of the surface.
FIT_SHARE = 0.99
# Smoothness penalty, relative to the number of points per control point. Only a whisper: it
# carries the surface on straight where there are no points (past the edge, over a hole),
# while the span count keeps it from following noise. Stronger, it flattens real curvature
# (1e-3 left a bowl 0.05 mm shallow).
SMOOTHING = 1e-5
# Points used to fit; more are only used to check.
FIT_POINTS = 30_000
# A neighbour joins if this share of its points lies on the surface and it faces the same way
# (mean normals). A flat neighbour must also fit the surface clearly better than its own plane:
# a facet of the curve, not a flat face it runs into smoothly. Clearly: the drop in mean
# square error is over FACET_SIGMAS times its sampling spread (relative, sqrt(2 / points)),
# and the chord it shows is over FACET_CHORD_TOLS tolerances.
JOIN_SHARE = 0.95
JOIN_ANGLE_DEG = 10.0
FACET_SIGMAS = 3.0
FACET_CHORD_TOLS = 0.02
# A flat neighbour smaller than a freeform area may be (MIN_WIDTH_FEATURES smallest features
# square) joins without that test: on a noisy scan a small facet's chord is lost in the noise,
# and a flat that small can't stand on its own beside the surface anyway.
# Faces of a flat neighbour this many tolerances off its plane (and nearer the surface) are
# peeled off it, unless that is over this share of it (then it is a facet, and joins whole).
PEEL_TOLS = 0.5
PEEL_SHARE = 0.5
# Neighbours worth a refit: within this many tolerances of the carried-on surface (median)
# and facing within this angle of it.
NEAR_TOLS = 3.0
NEAR_ANGLE_DEG = 20.0
GROW_ROUNDS = 6
# A flat neighbour this close to the area's normal, sharing this much of its edge, is the face
# the area sits on (a mound) or is pressed into (a scoop).
LEVEL_DEG = 30.0
LEVEL_EDGE_SHARE = 0.3
SAME_PLANE_DEG = 2.0
SAME_PLANE_TOLS = 4.0
# The surface carries on this far past the area's edge, in triangle sizes, so that the true
# edge, which lies within a triangle or two of the area's border, is found by the boolean.
MARGIN_EDGES = 3.0


@dataclass
class Frame:
    origin: np.ndarray
    u: np.ndarray
    v: np.ndarray
    w: np.ndarray

    @property
    def rotation(self) -> np.ndarray:
        return np.array([self.u, self.v, self.w])

    def local(self, points: np.ndarray) -> np.ndarray:
        return (points - self.origin) @ self.rotation.T

    def world(self, points: np.ndarray) -> np.ndarray:
        return points @ self.rotation + self.origin


@dataclass
class HeightSurface:
    """w = S(u, v) in a frame: a clamped cubic B-spline over a rectangle."""

    frame: Frame
    uknots: np.ndarray
    vknots: np.ndarray
    coef: np.ndarray
    """(number of u basis functions, number of v basis functions): heights of the poles."""

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return self.uknots[0], self.uknots[-1], self.vknots[0], self.vknots[-1]

    def _clamped(self, uv: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        u0, u1, v0, v1 = self.bounds
        return np.clip(uv[:, 0], u0, u1), np.clip(uv[:, 1], v0, v1)

    def _eval(self, uv: np.ndarray, du: int, dv: int) -> np.ndarray:
        u, v = self._clamped(uv)
        rows = _tensor(_rows(u, self.uknots, du), _rows(v, self.vknots, dv), self.coef.shape)
        return rows @ self.coef.ravel()

    def height(self, uv: np.ndarray) -> np.ndarray:
        return self._eval(uv, 0, 0)

    def slope(self, uv: np.ndarray) -> np.ndarray:
        """(dS/du, dS/dv) at each point."""
        return np.c_[self._eval(uv, 1, 0), self._eval(uv, 0, 1)]

    def distance(self, local: np.ndarray) -> np.ndarray:
        """Signed distance of local points from the surface (positive above it), to first
        order: the height difference times the cosine of the slope."""
        uv = local[:, :2]
        g = self.slope(uv)
        return (local[:, 2] - self.height(uv)) / np.sqrt(1 + (g**2).sum(axis=1))

    def normals(self, uv: np.ndarray) -> np.ndarray:
        """Unit normals pointing up (+w), in the local frame."""
        g = self.slope(uv)
        n = np.c_[-g, np.ones(len(g))]
        return n / np.linalg.norm(n, axis=1)[:, None]

    def poles(self) -> np.ndarray:
        """Pole positions in world coordinates: (nu, nv, 3). A B-spline reproduces straight
        lines exactly when its poles sit at the Greville points (the averages of DEGREE
        consecutive inner knots), so placing u and v there makes the surface w = S(u, v)."""
        gu, gv = _greville(self.uknots), _greville(self.vknots)
        uu, vv = np.meshgrid(gu, gv, indexing="ij")
        local = np.stack([uu, vv, self.coef], axis=-1)
        return self.frame.world(local.reshape(-1, 3)).reshape(*self.coef.shape, 3)

    def geom(self) -> Geom_BSplineSurface:
        poles = self.poles()
        array = TColgp_Array2OfPnt(1, poles.shape[0], 1, poles.shape[1])
        for i in range(poles.shape[0]):
            for j in range(poles.shape[1]):
                array.SetValue(i + 1, j + 1, gp_Pnt(*map(float, poles[i, j])))
        uk, um = _occ_knots(self.uknots)
        vk, vm = _occ_knots(self.vknots)
        return Geom_BSplineSurface(array, uk, vk, um, vm, DEGREE, DEGREE)

    def face(self) -> cq.Face:
        u0, u1, v0, v1 = self.bounds
        return cq.Face(BRepBuilderAPI_MakeFace(self.geom(), u0, u1, v0, v1, 1e-6).Face())


def _knots(lo: float, hi: float, spans: int) -> np.ndarray:
    inner = np.linspace(lo, hi, spans + 1)
    return np.r_[[lo] * DEGREE, inner, [hi] * DEGREE]


def _greville(knots: np.ndarray) -> np.ndarray:
    n = len(knots) - DEGREE - 1
    return np.array([knots[i + 1 : i + 1 + DEGREE].mean() for i in range(n)])


def _occ_knots(knots: np.ndarray) -> tuple[TColStd_Array1OfReal, TColStd_Array1OfInteger]:
    values, mults = np.unique(knots, return_counts=True)
    k = TColStd_Array1OfReal(1, len(values))
    m = TColStd_Array1OfInteger(1, len(values))
    for i, (x, c) in enumerate(zip(values, mults, strict=True)):
        k.SetValue(i + 1, float(x))
        m.SetValue(i + 1, int(c))
    return k, m


def _rows(x: np.ndarray, knots: np.ndarray, nu: int) -> tuple[np.ndarray, np.ndarray]:
    """The cubic B-spline basis functions (nu = 0) or their slopes (nu = 1) at x: each point
    touches four consecutive ones, from index start. Returns (start, values (n, 4))."""
    if nu == 0:
        m = BSpline.design_matrix(x, knots, DEGREE)
        cols = m.indices.reshape(-1, DEGREE + 1)
        return cols[:, 0], m.data.reshape(-1, DEGREE + 1)
    # N'(i, p) = p N(i, p-1) / (t[i+p] - t[i]) - p N(i+1, p-1) / (t[i+p+1] - t[i+1]). Kept
    # off the very end, where the lower degree basis has a function no cubic one matches.
    x = np.minimum(x, knots[-1] - 1e-9 * (knots[-1] - knots[0]))
    m = BSpline.design_matrix(x, knots, DEGREE - 1)
    lower = m.data.reshape(-1, DEGREE)
    first = m.indices.reshape(-1, DEGREE)[:, 0]
    start = first - 1
    gap = knots[DEGREE:] - knots[:-DEGREE]
    inv = np.divide(DEGREE, gap, out=np.zeros_like(gap), where=gap > 0)
    padded = np.zeros((len(x), DEGREE + 2))
    padded[:, 1 : DEGREE + 1] = lower
    i = start[:, None] + np.arange(DEGREE + 1)
    vals = (
        padded[:, :-1] * inv[np.clip(i, 0, len(inv) - 1)]
        - padded[:, 1:] * inv[np.clip(i + 1, 0, len(inv) - 1)]
    )
    return start, vals


def _tensor(
    u: tuple[np.ndarray, np.ndarray], v: tuple[np.ndarray, np.ndarray], shape: tuple[int, int]
) -> sparse.csr_matrix:
    """Rows of the tensor-product basis: value_u[i] * value_v[j] at column i * nv + j."""
    (su, vu), (sv, vv) = u, v
    k = np.arange(DEGREE + 1)
    cols = (su[:, None] + k)[:, :, None] * shape[1] + (sv[:, None] + k)[:, None, :]
    vals = vu[:, :, None] * vv[:, None, :]
    n = len(su)
    rows = np.repeat(np.arange(n), (DEGREE + 1) ** 2)
    return sparse.csr_matrix((vals.ravel(), (rows, cols.ravel())), shape=(n, shape[0] * shape[1]))


def _second_differences(n: int) -> sparse.csr_matrix:
    return sparse.diags([1.0, -2.0, 1.0], [0, 1, 2], shape=(max(n - 2, 0), n)).tocsr()


def fit_height(
    frame: Frame,
    local: np.ndarray,
    bounds: tuple[float, float, float, float],
    spans: tuple[int, int],
    weights: np.ndarray | None = None,
) -> HeightSurface:
    """Least-squares cubic B-spline height field with a second-difference penalty on the
    poles, which keeps it smooth and carries it on in a straight line where there is no data
    (past the area's edge, or over a hole in it)."""
    u0, u1, v0, v1 = bounds
    uk, vk = _knots(u0, u1, spans[0]), _knots(v0, v1, spans[1])
    nu, nv = len(uk) - DEGREE - 1, len(vk) - DEGREE - 1
    a = _tensor(_rows(local[:, 0], uk, 0), _rows(local[:, 1], vk, 0), (nu, nv))
    w = np.ones(len(local)) if weights is None else weights
    du = sparse.kron(_second_differences(nu), sparse.identity(nv))
    dv = sparse.kron(sparse.identity(nu), _second_differences(nv))
    penalty = (du.T @ du + dv.T @ dv).toarray()
    lam = SMOOTHING * w.sum() / (nu * nv)
    aw = a.multiply(w[:, None]).tocsr()
    lhs = (a.T @ aw).toarray() + lam * penalty
    rhs = aw.T @ local[:, 2]
    coef = np.linalg.solve(lhs + 1e-12 * np.eye(nu * nv), rhs)
    return HeightSurface(frame, uk, vk, coef.reshape(nu, nv))


@dataclass
class Fit:
    surface: HeightSurface
    within: float
    """Share of the points within the tolerance."""
    rms: float
    """Of the points, leaving out stray ones."""
    spans: tuple[int, int]


def fit_surface(
    frame: Frame,
    points: np.ndarray,
    tol: float,
    margin: float,
    rng: np.random.Generator,
    spans: tuple[int, int] | None = None,
    lookahead: int = 1,
) -> Fit | None:
    """Fit a surface to the points, or None if none fits within tol (FIT_SHARE of them).

    Without spans given, tries more and more and keeps the first that fits once more spans
    stop helping much (the rest is scan noise): enough to follow the shape, no more. That is
    judged over lookahead more steps: a count of spans that happens to put knots where the
    shape bends least can fit worse than one fewer, and the next count well again."""
    local = frame.local(points)
    lo, hi = local[:, :2].min(axis=0) - margin, local[:, :2].max(axis=0) + margin
    bounds = (lo[0], hi[0], lo[1], hi[1])
    size = hi - lo
    sample = local
    if len(local) > FIT_POINTS:
        sample = local[rng.choice(len(local), FIT_POINTS, replace=False)]

    def attempt(spans: tuple[int, int]) -> Fit:
        # Fit, then again without points far off it (a sliver of the next face).
        surface = fit_height(frame, sample, bounds, spans)
        d = surface.distance(sample)
        keep = np.abs(d) <= max(3 * tol, 4 * _mad(d))
        surface = fit_height(frame, sample[keep], bounds, spans)
        d = surface.distance(local)
        within = float((np.abs(d) <= tol).mean())
        # The error that more spans could still bring down: leave out the stray points, or
        # they hide whether the shape itself is followed.
        inliers = d[np.abs(d) <= max(3 * tol, 4 * _mad(d))]
        return Fit(surface, within, float(np.sqrt((inliers**2).mean())), spans)

    if spans is not None:
        fit = attempt(spans)
        return fit if fit.within >= FIT_SHARE else None
    chosen = None
    misses = 0
    for long in SPAN_STEPS:
        ratio = size / size.max()
        fit = attempt(tuple(max(1, int(round(long * r))) for r in ratio))
        if chosen is not None:
            if fit.rms > NOISE_FLOOR * chosen.rms:
                misses += 1
                if misses == lookahead:
                    return chosen
                continue
            chosen, misses = fit, 0
        elif fit.within >= FIT_SHARE:
            chosen = fit
        if chosen is not None and chosen.rms <= GOOD_RMS_TOLS * tol:
            return chosen
    return chosen


def _mad(d: np.ndarray) -> float:
    return float(1.4826 * np.median(np.abs(d - np.median(d))))


@dataclass
class Freeform:
    form: str
    """"surface" (a whole curved side of the part), "bump" (a mound on a flat face) or
    "recess" (a scoop pressed into one)."""
    surface: HeightSurface
    labels: set[int]
    """Ids of the planes and patches it took."""
    faces: np.ndarray
    add: cq.Shape | None
    cut: cq.Shape | None
    area_mm2: float
    dims: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "type": "freeform",
            "form": self.form,
            "area_mm2": round(self.area_mm2, 1),
            **self.dims,
        }


def find_freeform(
    mesh: Geo,
    planes: list[Region],
    patches: list,
    labels: np.ndarray,
    seeds: set[int],
    level: ToleranceLevel,
) -> list[Freeform]:
    """Gather each connected curved area among seeds (the facets and leftover patches), grow
    it over neighbours that lie on it, and fit a surface to it."""
    tol = level.fit_tolerance_mm
    rng = np.random.default_rng(0)
    parts = {r.id: r for r in planes} | {p.id: p for p in patches}
    neighbours = {k: set(x.neighbours) & set(parts) for k, x in parts.items()}
    for k, nbs in list(neighbours.items()):
        for nb in nbs:
            neighbours[nb].add(k)
    faces_of = {k: x.faces for k, x in parts.items()}
    n = len(mesh.faces)
    fa = mesh.face_adjacency
    adjacency = sparse.coo_matrix(
        (np.ones(2 * len(fa)), (np.r_[fa[:, 0], fa[:, 1]], np.r_[fa[:, 1], fa[:, 0]])), shape=(n, n)
    ).tocsr()
    edge = float(
        np.median(
            np.linalg.norm(
                mesh.vertices[mesh.faces[:, 0]] - mesh.vertices[mesh.faces[:, 1]], axis=1
            )
        )
    )
    margin = MARGIN_EDGES * edge + 2 * tol
    groups: list[tuple[set[int], Fit]] = []
    for comp in _components(seeds & set(parts), neighbours):
        taken = set().union(*(g for g, _ in groups))
        if comp <= taken:
            continue
        grown = _grow(
            mesh,
            comp - taken,
            parts,
            neighbours,
            faces_of,
            seeds,
            taken,
            level,
            margin,
            rng,
            adjacency,
        )
        if grown is not None:
            groups.append(grown)
    # Areas that grew up to each other are one surface if one surface fits them both (noise
    # or a band taken for a fillet can split a curved top in two).
    merged = True
    while merged:
        merged = False
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                a, b = groups[i][0], groups[j][0]
                if not (set().union(*(neighbours[k] for k in a)) & b):
                    continue
                others = set().union(*(g for n, (g, _) in enumerate(groups) if n not in (i, j)))
                grown = _grow(
                    mesh,
                    a | b,
                    parts,
                    neighbours,
                    faces_of,
                    seeds,
                    others,
                    level,
                    margin,
                    rng,
                    adjacency,
                )
                if grown is not None:
                    groups = [g for n, g in enumerate(groups) if n not in (i, j)] + [grown]
                    merged = True
                    break
            if merged:
                break
    found = []
    for group, fit in groups:
        made = _build(mesh, group, fit, parts, neighbours, faces_of, labels, level, margin, rng)
        if made is not None:
            found.append(made)
    return found


def _components(ids: set[int], neighbours: dict[int, set[int]]) -> list[set[int]]:
    left, out = set(ids), []
    while left:
        stack, comp = [left.pop()], set()
        while stack:
            k = stack.pop()
            comp.add(k)
            for nb in neighbours[k] & left:
                left.discard(nb)
                stack.append(nb)
        out.append(comp)
    return sorted(out, key=lambda c: -len(c))


def _points(mesh: Geo, faces: np.ndarray) -> np.ndarray:
    return mesh.vertices[np.unique(mesh.faces[faces])]


def _mean_normal(mesh: Geo, faces: np.ndarray) -> np.ndarray:
    n = (mesh.face_normals[faces] * mesh.area_faces[faces, None]).sum(axis=0)
    return n / np.linalg.norm(n)


def frame_for(mesh: Geo, faces: np.ndarray, w: np.ndarray) -> Frame:
    """u and v along the main directions of the area seen along w."""
    pts = _points(mesh, faces)
    origin = pts.mean(axis=0)
    flat = pts - origin
    flat = flat - np.outer(flat @ w, w)
    _, _, vt = np.linalg.svd(flat[:: max(1, len(flat) // 20_000)], full_matrices=False)
    u = vt[0] - (vt[0] @ w) * w
    u /= np.linalg.norm(u)
    return Frame(origin, u, np.cross(w, u), w)


def _tilted(mesh: Geo, faces: np.ndarray, w: np.ndarray) -> bool:
    cos = mesh.face_normals[faces] @ w
    big = mesh.area_faces[faces]
    # Ignore a sliver of steep triangles along the rim (they belong to the wall next door).
    steep = cos < np.cos(np.radians(MAX_TILT_DEG))
    return bool(big[steep].sum() > 0.01 * big.sum())


def _grow(mesh, group, parts, neighbours, faces_of, seeds, taken, level, margin, rng, adjacency):
    """Fit the group, then try each neighbour: it joins if a surface through both still fits
    and it lies on that surface. Repeat until none joins.

    A flat face often reaches a little way into the curve beside it (the curve leaves it
    gently); the faces it took there that lie off its plane and on the surface are peeled off
    and join as a piece of their own, and the flat face stays flat."""
    tol = level.fit_tolerance_mm
    small = (MIN_WIDTH_FEATURES * level.min_feature_mm) ** 2
    group = set(group)
    rejected: set[int] = set()
    fit = None
    for _ in range(GROW_ROUNDS):
        faces = np.concatenate([faces_of[k] for k in group])
        w = _mean_normal(mesh, faces)
        if _tilted(mesh, faces, w):
            return None
        frame = frame_for(mesh, faces, w)
        fit = fit_surface(frame, _points(mesh, faces), tol, margin, rng)
        if fit is None:
            return None
        joined = set()
        member = np.zeros(len(mesh.faces), bool)
        member[faces] = True
        for k in sorted(set().union(*(neighbours[g] for g in group)) - group - taken - rejected):
            if _near(mesh, faces_of[k], fit.surface, tol):
                both = np.concatenate([faces, faces_of[k]])
                pts = _points(mesh, both)
                joint = fit_surface(frame, pts, tol, margin, rng, fit.spans)
                if joint is None:
                    # A big neighbour can stretch the area past what those spans follow.
                    joint = fit_surface(frame, pts, tol, margin, rng)
                curved = (
                    True
                    if k in seeds or not isinstance(parts[k], Region) or parts[k].area < small
                    else parts[k]
                )
                if joint is not None and _lies_on(mesh, faces_of[k], joint.surface, tol, curved):
                    joined.add(k)
                    continue
            rejected.add(k)
            if isinstance(parts[k], Region) and k not in seeds:
                peel = _peel(mesh, adjacency, member, faces_of[k], parts[k], fit.surface, tol)
                area = mesh.area_faces
                if len(peel) and area[peel].sum() < PEEL_SHARE * area[faces_of[k]].sum():
                    piece = min(min(parts), 0) - 1
                    parts[piece] = None
                    faces_of[piece] = peel
                    faces_of[k] = np.setdiff1d(faces_of[k], peel)
                    neighbours[piece] = neighbours[k] | {k}
                    for nb in neighbours[piece]:
                        neighbours[nb].add(piece)
                    joined.add(piece)

        if not joined:
            break
        group |= joined
        rejected.clear()
    faces = np.concatenate([faces_of[k] for k in group])
    size = np.ptp(fit.surface.frame.local(_points(mesh, faces))[:, :2], axis=0)
    if size.min() < MIN_WIDTH_FEATURES * level.min_feature_mm:
        return None
    return group, fit


def _peel(mesh, adjacency, member, faces, region, surface, tol) -> np.ndarray:
    """Faces of a flat region that lie well off its plane and closer to the surface, in pieces
    that touch the group."""
    centres = mesh.vertices[mesh.faces[faces]].mean(axis=1)
    off_plane = np.abs(region.plane.distance(centres))
    off_surface = np.abs(surface.distance(surface.frame.local(centres)))
    cand = faces[(off_plane > PEEL_TOLS * tol) & (off_surface < off_plane)]
    if not len(cand):
        return cand
    # Grow out from the group's faces through the candidates.
    keep = np.zeros(len(mesh.faces), bool)
    pool = np.zeros(len(mesh.faces), bool)
    pool[cand] = True
    front = np.flatnonzero(pool & (adjacency @ member.astype(float) > 0))
    while len(front):
        keep[front] = True
        pool[front] = False
        reached = np.zeros(len(mesh.faces), bool)
        reached[adjacency[front].indices] = True
        front = np.flatnonzero(pool & reached)
    return np.flatnonzero(keep)


def _near(mesh: Geo, faces: np.ndarray, surface: HeightSurface, tol: float) -> bool:
    """Quick check before a refit: about on the carried-on surface, and facing its way."""
    frame = surface.frame
    local = frame.local(_points(mesh, faces))
    if np.median(np.abs(surface.distance(local))) > NEAR_TOLS * tol:
        return False
    return _facing(mesh, faces, surface) <= NEAR_ANGLE_DEG


def _facing(mesh: Geo, faces: np.ndarray, surface: HeightSurface) -> float:
    """Angle between the faces' mean normal and the surface's mean normal under them (both
    weighted by area). Averaged, not compared face by face: a noisy scan's small triangles
    tilt every which way by 10 degrees or more, but the tilts cancel out."""
    frame = surface.frame
    centres = frame.local(mesh.vertices[mesh.faces[faces]].mean(axis=1))
    area = mesh.area_faces[faces, None]
    expected = (surface.normals(centres[:, :2]) * area).sum(axis=0)
    actual = (mesh.face_normals[faces] @ frame.rotation.T * area).sum(axis=0)
    cos = expected @ actual / (np.linalg.norm(expected) * np.linalg.norm(actual) + 1e-300)
    return float(np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))))


def _lies_on(
    mesh: Geo, faces: np.ndarray, surface: HeightSurface, tol: float, curved: bool | Region
) -> bool:
    """Do these faces lie on the surface? curved is True for a facet or patch known to be
    curved, or else the flat region itself, which joins only if it is a facet after all."""
    frame = surface.frame
    local = frame.local(_points(mesh, faces))
    if (np.abs(surface.distance(local)) <= tol).mean() < JOIN_SHARE:
        return False
    if _facing(mesh, faces, surface) > JOIN_ANGLE_DEG:
        return False
    if curved is True:
        return True
    # A flat face the surface runs into smoothly stays flat; a facet of the curve joins. A
    # facet is a chord of the curve, so the surface fits its points clearly better than its
    # own plane does; on a flat face both fit equally well (as well as the scan noise allows).
    # The noise is the same in both, so only the chord shows in the difference.
    pts = _points(mesh, faces)
    plane_ms = float((curved.plane.distance(pts) ** 2).mean())
    surface_ms = float((surface.distance(frame.local(pts)) ** 2).mean())
    chord_ms = plane_ms - surface_ms
    return bool(
        chord_ms > FACET_SIGMAS * np.sqrt(2 / len(pts)) * plane_ms
        and chord_ms > (FACET_CHORD_TOLS * tol) ** 2
    )


def _build(mesh, group, fit, parts, neighbours, faces_of, labels, level, margin, rng):
    tol = level.fit_tolerance_mm
    faces = np.concatenate([faces_of[k] for k in group])
    base = _level_face(mesh, group, faces, fit.surface, parts, neighbours, labels)
    form = "surface"
    surface = fit.surface
    if base is not None:
        w = base.plane.normal
        frame = frame_for(mesh, faces, w)
        if _tilted(mesh, faces, w):
            return None
        # Points on the flat face itself (a strip of it taken along with the curve) would make
        # the surface bend sharply at the edge and ring; leave them to the face.
        points = _points(mesh, faces)
        height = base.plane.distance(points)
        refit = fit_surface(frame, points[np.abs(height) > PEEL_TOLS * tol], tol, margin, rng)
        if refit is None:
            return None
        surface = refit.surface
        form = "bump" if np.median(height) > 0 else "recess"
    try:
        add, cut = _solids(mesh, faces, surface, form, base, tol, margin)
    except Exception:  # OpenCASCADE raises a variety of errors on a bad surface
        return None
    if add is None and cut is None:
        return None
    area = float(mesh.area_faces[faces].sum())
    dims = {}
    if base is not None:
        # How far the surface stands off the face it sits on, over the area.
        local = surface.frame.local(_points(mesh, faces))
        level = float(-base.plane.distance(surface.frame.origin[None])[0])
        off = surface.height(local[:, :2]) - level
        key, value = ("height_mm", off.max()) if form == "bump" else ("depth_mm", -off.min())
        dims[key] = round(float(value), 2)
    return Freeform(form, surface, set(group), faces, add, cut, area, dims)


def _level_face(mesh, group, faces, surface, parts, neighbours, labels) -> Region | None:
    """The flat face the area sits on or is pressed into: neighbouring planes facing about the
    same way as the area, all on one plane (an area can split a face in pieces), that run along
    much of its edge. Returns the biggest of them."""
    w = surface.frame.w
    rim = set().union(*(neighbours[g] for g in group)) - group
    member = np.zeros(len(labels), bool)
    member[faces] = True
    a, b = labels[mesh.face_adjacency[:, 0]], labels[mesh.face_adjacency[:, 1]]
    across = member[mesh.face_adjacency[:, 0]] != member[mesh.face_adjacency[:, 1]]
    other = np.where(member[mesh.face_adjacency[:, 0]], b, a)[across]
    ends = mesh.vertices[mesh.face_adjacency_edges[across]]
    lengths = np.linalg.norm(ends[:, 0] - ends[:, 1], axis=1)
    total = lengths.sum()
    flat = [
        parts[k]
        for k in rim
        if isinstance(parts[k], Region)
        and parts[k].plane.normal @ w > np.cos(np.radians(LEVEL_DEG))
    ]
    best, best_len = None, 0.0
    cos_same = np.cos(np.radians(SAME_PLANE_DEG))
    for r in flat:
        same = [
            x
            for x in flat
            if x.plane.normal @ r.plane.normal > cos_same
            and abs(x.plane.offset - r.plane.offset) <= SAME_PLANE_TOLS * surface_tol(r)
        ]
        shared = float(lengths[np.isin(other, [x.id for x in same])].sum())
        if shared > best_len:
            best, best_len = max(same, key=lambda x: x.area), shared
    if best is None or best_len < LEVEL_EDGE_SHARE * total:
        return None
    return best


def surface_tol(region: Region) -> float:
    return max(region.rms_mm, 0.05)


def _solids(mesh, faces, surface: HeightSurface, form: str, base: Region | None, tol, margin):
    """The solid to add or cut: the surface swept away from the material (or towards it, for
    a mound), kept within a column over the area.

    A mound is added from just below the face it sits on up to the surface, and a scoop is cut
    from the surface up to just above its face, within the loop where the surface meets that
    face. A whole curved side cuts everything above the surface off the base block, within
    the area's outline grown by the margin."""
    frame = surface.frame
    pts_all = frame.local(mesh.vertices)
    w_lo = float(pts_all[:, 2].min()) - 10.0
    w_hi = float(pts_all[:, 2].max()) + 10.0
    span = w_hi - w_lo
    sheet = surface.face()
    up = BRepPrimAPI_MakePrism(sheet.wrapped, _vec(frame.w * 2 * span)).Shape()
    down = BRepPrimAPI_MakePrism(sheet.wrapped, _vec(-frame.w * 2 * span)).Shape()
    if form == "surface":
        outline = _outline(frame, mesh, faces, margin)
        return None, _common(up, _column(frame, outline, w_lo, w_hi))
    level = float(-base.plane.distance(frame.origin[None])[0])  # w of the base face
    centres = frame.local(mesh.vertices[mesh.faces[faces]].mean(axis=1))[:, :2]
    rim = _rim(sheet, surface, level, centres)
    # Grown a little, so the column's walls stand clear of the edge, where the surface has
    # left the face (it dips under it past a mound's edge, and rises over it past a scoop's).
    outline = None if rim is None else _offset(rim, margin)
    if outline is None or _crosses(outline):
        outline = _outline(frame, mesh, faces, margin)
    eps = min(tol, 0.1)
    if form == "bump":
        return _common(down, _column(frame, outline, level - eps, w_hi)), None
    return None, _common(up, _column(frame, outline, w_lo, level + eps))


def _rim(sheet: cq.Face, surface: HeightSurface, level: float, inside: np.ndarray):
    """The loop where the surface meets the plane w = level that holds most of the area's
    points (seen along w), as (n, 2) in (u, v); or None."""
    frame = surface.frame
    u0, u1, v0, v1 = surface.bounds
    corners = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
    plane = _polygon_face(frame, np.array(corners), level)
    section = BRepAlgoAPI_Section(sheet.wrapped, plane, False)
    section.Approximation(True)
    section.Build()
    if not section.IsDone():
        return None
    edges = TopTools_HSequenceOfShape()
    for e in cq.Shape.cast(section.Shape()).Edges():
        edges.Append(e.wrapped)
    wires = TopTools_HSequenceOfShape()
    ShapeAnalysis_FreeBounds.ConnectEdgesToWires_s(edges, 1e-4, False, wires)
    sample = inside[:: max(1, len(inside) // 2000)]
    best, best_count = None, 0
    for i in range(1, wires.Length() + 1):
        wire = TopoDS.Wire_s(wires.Value(i))
        if not BRep_Tool.IsClosed_s(wire):
            continue
        w = cq.Wire(wire)
        pts = np.array([w.positionAt(t).toTuple() for t in np.linspace(0, 1, 400, endpoint=False)])
        ring = frame.local(pts)[:, :2]
        count = int(_contains(ring, sample).sum())
        if count > best_count:
            best, best_count = ring, count
    if best is None or best_count < 0.5 * len(sample):
        return None
    return best


def _offset(ring: np.ndarray, distance: float) -> np.ndarray:
    """A closed polygon moved outward by distance at each point (along its normal there),
    for a densely sampled smooth loop."""
    area = 0.5 * float(
        (ring[:, 0] * np.roll(ring[:, 1], -1) - np.roll(ring[:, 0], -1) * ring[:, 1]).sum()
    )
    tangent = np.roll(ring, -1, axis=0) - np.roll(ring, 1, axis=0)
    normal = np.c_[tangent[:, 1], -tangent[:, 0]]
    normal /= np.linalg.norm(normal, axis=1)[:, None]
    # Counter-clockwise loops have the outside on the right of the direction of travel.
    return ring + np.sign(area) * normal * distance


def _crosses(ring: np.ndarray) -> bool:
    """Does a closed polygon cross itself?"""
    a, b = ring, np.roll(ring, -1, axis=0)

    def side(p, q, r):  # which side of each segment p->q each point r is on, (n, n)
        return (q[:, None, 0] - p[:, None, 0]) * (r[None, :, 1] - p[:, None, 1]) - (
            q[:, None, 1] - p[:, None, 1]
        ) * (r[None, :, 0] - p[:, None, 0])

    s1, s2 = side(a, b, a), side(a, b, b)
    hit = (s1 * s2 < 0) & (s1.T * s2.T < 0)
    n = len(ring)
    near = np.abs(np.subtract.outer(np.arange(n), np.arange(n)))
    hit &= (near > 1) & (near < n - 1)
    return bool(hit.any())


def _contains(ring: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Points inside a closed polygon (ray casting), one edge at a time so a long outline and
    many points need little memory."""
    x, y = pts[:, 0], pts[:, 1]
    inside = np.zeros(len(pts), dtype=bool)
    for (ax, ay), (bx, by) in zip(ring, np.roll(ring, -1, axis=0), strict=True):
        cross = np.flatnonzero((ay > y) != (by > y))
        if len(cross):
            at = ax + (y[cross] - ay) * (bx - ax) / (by - ay)
            inside[cross] ^= x[cross] < at
    return inside


def _vec(v: np.ndarray) -> gp_Vec:
    return gp_Vec(*map(float, v))


def _common(a, b) -> cq.Shape | None:
    op = BRepAlgoAPI_Common(a, b.wrapped if isinstance(b, cq.Shape) else b)
    op.Build()
    if not op.IsDone():
        return None
    shape = cq.Shape.cast(op.Shape())
    solids = shape.Solids()
    if not solids:
        return None
    return solids[0] if len(solids) == 1 else cq.Compound.makeCompound(solids)


def _outline(frame: Frame, mesh: Geo, faces: np.ndarray, margin: float) -> np.ndarray:
    """The area's outline seen along w, grown by the margin: (n, 2) in (u, v)."""
    local = frame.local(_points(mesh, faces))[:, :2]
    hull = ConvexHull(local)
    ring = local[hull.vertices]
    centre = ring.mean(axis=0)
    out = []
    for i in range(len(ring)):
        a, b, c = ring[i - 1], ring[i], ring[(i + 1) % len(ring)]
        n1 = _outward(a, b, centre)
        n2 = _outward(b, c, centre)
        bis = n1 + n2
        bis /= np.linalg.norm(bis)
        out.append(b + bis * margin / max(bis @ n1, 0.2))
    return np.array(out)


def _outward(a: np.ndarray, b: np.ndarray, centre: np.ndarray) -> np.ndarray:
    d = b - a
    n = np.array([d[1], -d[0]]) / np.linalg.norm(d)
    return n if (a - centre) @ n >= 0 else -n


def _polygon_face(frame: Frame, outline: np.ndarray, w: float):
    poly = BRepBuilderAPI_MakePolygon()
    for u, v in outline:
        poly.Add(gp_Pnt(*map(float, frame.world(np.array([[u, v, w]]))[0])))
    poly.Close()
    return BRepBuilderAPI_MakeFace(poly.Wire(), True).Face()


def _column(frame: Frame, outline: np.ndarray, w0: float, w1: float) -> cq.Shape:
    face = _polygon_face(frame, outline, w0)
    return cq.Shape.cast(BRepPrimAPI_MakePrism(face, _vec(frame.w * (w1 - w0))).Shape())
