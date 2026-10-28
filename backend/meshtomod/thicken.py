"""Thicken skin: a one-sided scan (an open surface, not a closed part) made into a solid wall.

Scanning a big curved panel, or a part only one side of which is reachable, gives a skin: a
sheet of triangles with a free border all round. There is no inside to fill, so the pipeline
for closed parts does not apply. Instead the sheet is treated like a surface in CAD that the
designer thickens:

1. Fit the sheet with one smooth B-spline surface, w = S(u, v), seen along its average normal
   (the same height-field fit as freeform areas use). It must be one smooth sheet that is
   seen once from that side.
2. Outline: the scan's border, seen along w, is ragged (torn triangles, noise). It is
   smoothed over the smallest feature size (wobbles smaller than that are noise, as
   everywhere else), and pulled in where it runs over gaps between the border's teeth, so the
   edge stays on scanned surface. Sharp corners of the border round off by about that size.
3. Thicken: the second surface is S moved along its normal by the thickness, fitted as a
   B-spline too. The side wall is ruled along the surface normal at the border, square to
   the skin, as CAD's thicken makes it. The solid is the slab between the two surfaces, cut
   by that side wall.
4. Finish the rim: square, a chamfer of a third of the thickness, or a full round (a fillet
   of half the thickness on both edges, so the rim is a half circle).

Direction: "in" keeps the scanned surface as the outside face and adds material behind it,
"out" adds it on the scanned side (the scan becomes the inside face), "centred" puts the
scan in the middle of the wall. Which side is the outside comes from the triangles' winding:
scanners point it at the scanner.
"""

from __future__ import annotations

from dataclasses import dataclass

import cadquery as cq
import numpy as np
import trimesh
from OCP.BRep import BRep_Tool
from OCP.BRepAlgoAPI import BRepAlgoAPI_Splitter
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.BRepTopAdaptor import BRepTopAdaptor_FClass2d
from OCP.Geom import Geom_BSplineCurve
from OCP.GeomFill import GeomFill
from OCP.gp import gp_Pnt, gp_Pnt2d
from OCP.TColgp import TColgp_Array1OfPnt
from OCP.TColStd import TColStd_Array1OfInteger, TColStd_Array1OfReal
from OCP.TopAbs import TopAbs_IN
from OCP.TopTools import TopTools_ListOfShape
from scipy.ndimage import gaussian_filter1d
from scipy.spatial import cKDTree

from .brep import UnsupportedGeometry
from .freeform import (
    Frame,
    HeightSurface,
    _contains,
    _crosses,
    _offset,
    _vec,
    fit_height,
    fit_surface,
    frame_for,
)
from .segment import Geo, smoothed_normals
from .tolerance import ToleranceLevel

DEFAULT_THICKNESS_MM = 3.0
DIRECTIONS = ("in", "out", "centred")
EDGES = ("square", "chamfer", "round")
# Chamfer legs, as a share of the thickness.
CHAMFER_SHARE = 1 / 3
# A full round is two fillets of half the thickness meeting in the middle of the rim. Exactly
# half leaves nothing of the side wall between them, which the fillet can't build, so it
# keeps this share of the thickness as a sliver of wall.
ROUND_SHARE = 0.49

# The scan is a skin when its biggest free border, seen along the average normal, encloses at
# least this share of the area the sheet covers seen that way. A closed part with a few holes
# in the scan has small borders.
BORDER_AREA_SHARE = 0.8
# And when no more of it than this faces away from that side (it folds back on itself, or
# it is a closed part missing a face: a box without its bottom has walls facing both ways).
BACKFACING_SHARE = 0.05
# The outline is pulled in until no more than (100 - this) percent of it runs over gaps in the
# scan by more than the tolerance.
TRIM_PERCENTILE = 90
# Grid points for measuring the surface's area.
AREA_SAMPLES = 40_000
# The border is resampled at this share of the smoothing length before smoothing.
BORDER_STEP_SHARE = 0.25
# Spans of the outline curve, in smoothing lengths.
CURVE_SPAN_SMOOTHINGS = 2.0
# How far past the outline the surfaces are fitted and built, in thicknesses (and at least
# MARGIN_FEATURES smallest features), so the thickened side wall stays on them.
MARGIN_THICKNESSES = 2.0
MARGIN_FEATURES = 3.0
# Offset surface fit: grid samples per side, and the most it may be off the true offset (as a
# share of the tolerance) before more spans are tried or the wall is refused.
OFFSET_GRID = 160
OFFSET_TOL_SHARE = 0.05
OFFSET_SPAN_FACTORS = (2, 4, 8)
# The skin is the whole part, so its fit looks two counts of spans further before settling
# (see fit_surface).
SPAN_LOOKAHEAD = 2


@dataclass(frozen=True)
class ThickenOptions:
    thickness_mm: float = DEFAULT_THICKNESS_MM
    direction: str = "in"
    edge: str = "square"

    def __post_init__(self) -> None:
        if not self.thickness_mm > 0:
            raise ValueError("Wall thickness must be more than 0 mm.")
        if self.direction not in DIRECTIONS:
            raise ValueError(f"Direction must be one of {', '.join(DIRECTIONS)}.")
        if self.edge not in EDGES:
            raise ValueError(f"Edge finish must be one of {', '.join(EDGES)}.")

    @property
    def span(self) -> tuple[float, float]:
        """Where the wall's two faces are, along the surface normal from the scan."""
        t = self.thickness_mm
        return {"in": (-t, 0.0), "out": (0.0, t), "centred": (-t / 2, t / 2)}[self.direction]


@dataclass
class Skin:
    surface: HeightSurface
    outline: np.ndarray
    """The smoothed border in the surface's (u, v), counter-clockwise: (n, 2)."""
    options: ThickenOptions
    area_mm2: float
    """Of the scanned surface inside the outline."""
    trimmed_mm: float
    """How far the outline was pulled in from the ragged border."""
    smoothing_mm: float
    """The length the border was smoothed over."""

    def as_dict(self) -> dict:
        return {
            "type": "skin",
            "thickness_mm": round(self.options.thickness_mm, 3),
            "direction": self.options.direction,
            "edge": self.options.edge,
            "area_mm2": round(self.area_mm2, 1),
        }


def border_loops(mesh: trimesh.Trimesh) -> list[np.ndarray]:
    """Closed loops of free edges (used by one triangle), as vertex indices in the order the
    triangles' winding runs along them."""
    directed = np.stack([mesh.faces, np.roll(mesh.faces, -1, axis=1)], axis=2).reshape(-1, 2)
    _, inverse, counts = np.unique(
        np.sort(directed, axis=1), axis=0, return_inverse=True, return_counts=True
    )
    free = directed[counts[inverse.ravel()] == 1]
    following: dict[int, list[int]] = {}
    for a, b in free.tolist():
        following.setdefault(a, []).append(b)
    loops = []
    for start in list(following):
        while following.get(start):
            loop = [start]
            here = following[start].pop()
            while here != start and following.get(here):
                loop.append(here)
                here = following[here].pop()
            if here == start and len(loop) >= 3:
                loops.append(np.array(loop))
    return loops


def _area(ring: np.ndarray) -> float:
    """Signed area of a closed polygon: positive counter-clockwise."""
    x, y = ring[:, 0], ring[:, 1]
    return 0.5 * float((x * np.roll(y, -1) - np.roll(x, -1) * y).sum())


def find_skin(
    mesh: trimesh.Trimesh,
    level: ToleranceLevel,
    options: ThickenOptions,
    rng: np.random.Generator,
) -> Skin | None:
    """The scan as a skin to thicken, or None if it is not one smooth open sheet (then it goes
    through the pipeline for closed parts)."""
    loops = border_loops(mesh)
    if not loops:
        return None
    total = (mesh.face_normals * mesh.area_faces[:, None]).sum(axis=0)
    if np.linalg.norm(total) < 1e-9:
        return None
    w = total / np.linalg.norm(total)
    geo = Geo.of(mesh)
    seen = float((mesh.area_faces * np.abs(mesh.face_normals @ w)).sum())
    # Smoothed normals, or scan noise tips a few triangles of a steep stretch over.
    away = float(mesh.area_faces[smoothed_normals(geo) @ w < 0].sum())
    if away > BACKFACING_SHARE * float(mesh.area_faces.sum()):
        return None
    frame = frame_for(geo, np.arange(len(mesh.faces)), w)
    rings = [frame.local(mesh.vertices[loop])[:, :2] for loop in loops]
    border = max(rings, key=lambda r: abs(_area(r)))
    if abs(_area(border)) < BORDER_AREA_SHARE * seen:
        return None

    tol = level.fit_tolerance_mm
    margin = max(MARGIN_THICKNESSES * options.thickness_mm, MARGIN_FEATURES * level.min_feature_mm)
    fit = fit_surface(frame, mesh.vertices, tol, margin, rng, lookahead=SPAN_LOOKAHEAD)
    if fit is None:
        return None
    smoothing = level.min_feature_mm
    outline, trimmed = smooth_outline(border, smoothing, tol)
    area = surface_area(fit.surface, outline)
    return Skin(fit.surface, outline, options, area, trimmed, smoothing)


def smooth_outline(border: np.ndarray, smoothing: float, tol: float) -> tuple[np.ndarray, float]:
    """The ragged border as a smooth counter-clockwise loop: resampled evenly, smoothed over
    the smoothing length, and pulled in where it passes over gaps in the scan (between the
    teeth of the border): far enough that most of it lies on scanned surface, give or take
    the tolerance. Returns the loop and how far it was pulled in."""
    if _area(border) < 0:
        border = border[::-1]
    closed = np.r_[border, border[:1]]
    at = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(closed, axis=0), axis=1))]
    step = BORDER_STEP_SHARE * smoothing
    s = np.arange(0.0, at[-1], step)
    ring = np.c_[np.interp(s, at, closed[:, 0]), np.interp(s, at, closed[:, 1])]
    # Smoothing shrinks a curved outline (by about smoothing^2 / 2 radius at each bend);
    # smoothing what it took off and adding that back undoes most of it while still
    # removing the wobbles.
    blur = gaussian_filter1d(ring, smoothing / step, axis=0, mode="wrap")
    smooth = blur + gaussian_filter1d(ring - blur, smoothing / step, axis=0, mode="wrap")
    # How far each point of the smooth line is outside the scan (negative inside it).
    gap, _ = cKDTree(ring).query(smooth)
    outside = np.where(_contains(ring, smooth), -gap, gap)
    trim = max(0.0, float(np.percentile(outside, TRIM_PERCENTILE)) - tol)
    if trim > 0:
        smooth = _offset(smooth, -trim)
    if _crosses(smooth[:: max(1, len(smooth) // 400)]) or _area(smooth) <= 0:
        raise UnsupportedGeometry(
            "The scan's border is too ragged or narrow to trace a clean outline around it."
        )
    return smooth, trim


def surface_area(surface: HeightSurface, outline: np.ndarray) -> float:
    """Area of the surface inside the outline (a sum over a fine grid in (u, v))."""
    lo, hi = outline.min(axis=0), outline.max(axis=0)
    step = float(np.sqrt(np.prod(hi - lo) / AREA_SAMPLES))
    grid = (
        np.stack(
            np.meshgrid(np.arange(lo[0], hi[0], step), np.arange(lo[1], hi[1], step)), -1
        ).reshape(-1, 2)
        + step / 2
    )
    inside = grid[_contains(outline, grid)]
    slope = surface.slope(inside)
    return float(np.sqrt(1 + (slope**2).sum(axis=1)).sum() * step**2)


def offset_surface(
    surface: HeightSurface, distance: float, tol: float, outline: np.ndarray
) -> HeightSurface:
    """The surface moved along its normal by distance (positive along +w), fitted as a
    B-spline in the same frame. It has to follow the true offset closely within the outline
    (and the wall's reach past it); where the surface curves too tightly for the distance
    the offset folds over itself, no smooth surface follows it, and the wall is refused."""
    u0, u1, v0, v1 = surface.bounds
    grid = np.stack(
        np.meshgrid(np.linspace(u0, u1, OFFSET_GRID), np.linspace(v0, v1, OFFSET_GRID)), -1
    ).reshape(-1, 2)
    moved = np.c_[grid, surface.height(grid)] + distance * surface.normals(grid)
    keep = (moved[:, 0] >= u0) & (moved[:, 0] <= u1) & (moved[:, 1] >= v0) & (moved[:, 1] <= v1)
    moved = moved[keep]
    near = _contains(_offset(outline, 2 * abs(distance)), moved[:, :2])
    nu, nv = len(surface.uknots) - 7, len(surface.vknots) - 7
    for factor in OFFSET_SPAN_FACTORS:
        out = fit_height(surface.frame, moved, surface.bounds, (factor * nu, factor * nv))
        off = np.abs(out.height(moved[near, :2]) - moved[near, 2])
        if off.max() <= OFFSET_TOL_SHARE * tol:
            return out
    raise UnsupportedGeometry(
        f"The surface curves too tightly to make a {abs(distance):g} mm wall on that side. "
        "Try a thinner wall or the other direction."
    )


def _loop_curve(points: np.ndarray, spans: int) -> Geom_BSplineCurve:
    """A closed (periodic) cubic B-spline through evenly spaced points round a loop, by least
    squares with uniform knots: points[i] sits at parameter i / len(points)."""
    n = len(points)
    t = np.arange(n) / n * spans
    k = np.floor(t).astype(int)
    s = t - k
    weights = np.stack(
        [
            (1 - s) ** 3 / 6,
            (3 * s**3 - 6 * s**2 + 4) / 6,
            (-3 * s**3 + 3 * s**2 + 3 * s + 1) / 6,
            s**3 / 6,
        ],
        axis=1,
    )
    a = np.zeros((n, spans))
    for j in range(4):
        np.add.at(a, (np.arange(n), (k + j) % spans), weights[:, j])
    # Pole i is the first of the four on span i, as in OpenCASCADE's periodic curves.
    poles, *_ = np.linalg.lstsq(a, points, rcond=None)
    array = TColgp_Array1OfPnt(1, spans)
    for i, p in enumerate(poles):
        array.SetValue(i + 1, gp_Pnt(*map(float, p)))
    knots = TColStd_Array1OfReal(1, spans + 1)
    mults = TColStd_Array1OfInteger(1, spans + 1)
    for i in range(spans + 1):
        knots.SetValue(i + 1, i / spans)
        mults.SetValue(i + 1, 1)
    return Geom_BSplineCurve(array, knots, mults, 3, True)


def _split(shape, tool) -> list[cq.Shape]:
    splitter = BRepAlgoAPI_Splitter()
    arguments, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
    arguments.Append(shape)
    tools.Append(tool)
    splitter.SetArguments(arguments)
    splitter.SetTools(tools)
    splitter.Build()
    if not splitter.IsDone():
        return []
    return cq.Shape.cast(splitter.Shape()).Solids()


def _inside(solid: cq.Shape, point: np.ndarray) -> bool:
    check = BRepClass3d_SolidClassifier(solid.wrapped, gp_Pnt(*map(float, point)), 1e-6)
    return check.State() == TopAbs_IN


def _interior_point(outline: np.ndarray) -> np.ndarray:
    """A point well inside the outline: the middle of the widest chord across it through its
    centre line."""
    centre = outline.mean(axis=0)
    if _contains(outline, centre[None])[0]:
        return centre
    # A crescent: walk in from the outline along its inward normal.
    inward = _offset(outline, -1e-3)
    step = (inward - outline) / 1e-3
    for depth in np.geomspace(0.1, 10.0, 12):
        pts = outline + depth * step
        ok = _contains(outline, pts)
        if ok.any():
            return pts[np.flatnonzero(ok)[0]]
    return centre


def thicken(skin: Skin, tol: float) -> tuple[cq.Shape, cq.Face]:
    """The wall as a solid, and the scanned surface trimmed to the outline (to score the fit
    against: for a centred wall it is not one of the solid's faces)."""
    surface, outline, options = skin.surface, skin.outline, skin.options
    frame: Frame = surface.frame
    lo, hi = options.span
    upper = surface if hi == 0 else offset_surface(surface, hi, tol, outline)
    lower = surface if lo == 0 else offset_surface(surface, lo, tol, outline)

    # The slab between the two surfaces, over the whole area they were built on.
    u0, u1, v0, v1 = surface.bounds
    corners = np.array([[u0, v0], [u1, v0], [u0, v1], [u1, v1]])
    heights = np.r_[surface.height(corners), surface.height(outline)]
    reach = float(np.ptp(heights)) + 4 * options.thickness_mm + max(u1 - u0, v1 - v0)
    top = BRepPrimAPI_MakePrism(upper.face().wrapped, _vec(-frame.w * reach)).Shape()
    bottom = BRepPrimAPI_MakePrism(lower.face().wrapped, _vec(frame.w * reach)).Shape()
    slab = cq.Shape.cast(top).intersect(cq.Shape.cast(bottom))

    # The side wall: lines along the surface normal through each point of the outline,
    # running a little past both faces so it cuts clean through the slab.
    normals = surface.normals(outline)
    on = np.c_[outline, surface.height(outline)]
    past = options.thickness_mm
    length = float(np.linalg.norm(np.diff(np.r_[outline, outline[:1]], axis=0), axis=1).sum())
    spans = max(8, int(round(length / (CURVE_SPAN_SMOOTHINGS * skin.smoothing_mm))))
    a = _loop_curve(frame.world(on + (hi + past) * normals), spans)
    b = _loop_curve(frame.world(on + (lo - past) * normals), spans)
    side = BRepBuilderAPI_MakeFace(GeomFill.Surface_s(a, b), 1e-6).Face()

    middle = _interior_point(outline)
    point = np.r_[middle, surface.height(middle[None])[0]]
    normal = surface.normals(middle[None])[0]
    probe = frame.world((point + 0.5 * (lo + hi) * normal)[None])[0]
    solid = next((p for p in _split(slab.wrapped, side) if _inside(p, probe)), None)
    if solid is None or not solid.isValid():
        raise UnsupportedGeometry("Could not build a valid wall from this skin.")
    sheet = _trimmed(surface, side, outline)
    return _finish(solid, options), sheet


def _trimmed(surface: HeightSurface, side, outline: np.ndarray) -> cq.Face:
    """The surface's face cut to the outline by the side wall: the piece holding a point well
    inside the outline (the surface's parameters are the frame's u and v)."""
    splitter = BRepAlgoAPI_Splitter()
    arguments, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
    arguments.Append(surface.face().wrapped)
    tools.Append(side)
    splitter.SetArguments(arguments)
    splitter.SetTools(tools)
    splitter.Build()
    u, v = map(float, _interior_point(outline))
    for face in cq.Shape.cast(splitter.Shape()).Faces():
        if BRepTopAdaptor_FClass2d(face.wrapped, 1e-6).Perform(gp_Pnt2d(u, v)) == TopAbs_IN:
            return face
    raise UnsupportedGeometry("Could not trim the scanned surface to its outline.")


def _rim_edges(solid: cq.Shape) -> list[cq.Edge]:
    """Where the side wall meets the two faces: every edge but the side wall's seam (the
    line where its closed surface meets itself)."""
    seams = {
        e.hashCode()
        for f in solid.Faces()
        for e in f.Edges()
        if BRep_Tool.IsClosed_s(e.wrapped, f.wrapped)
    }
    return [e for e in solid.Edges() if e.hashCode() not in seams]


def _finish(solid: cq.Shape, options: ThickenOptions) -> cq.Shape:
    if options.edge == "square":
        return solid
    edges = _rim_edges(solid)
    t = options.thickness_mm
    try:
        if options.edge == "chamfer":
            out = solid.chamfer(CHAMFER_SHARE * t, None, edges)
        else:
            out = solid.fillet(ROUND_SHARE * t, edges)
    except Exception:  # OpenCASCADE raises plain StdFail errors
        out = None
    if out is None or not out.isValid():
        raise UnsupportedGeometry(
            f"Could not put a {options.edge} edge on this wall; try a square edge."
        )
    return out
