"""Thin-walled parts (covers, enclosures, bins): rebuilt from one side of the wall and its
thickness.

A moulded cover is a skin about 1.5 to 3 mm thick, with vents, notches and an open side. The
flat-face rebuild can't hold walls that thin (the solid it traces from both sides of the wall
comes apart), and a few millimetres of sag in a wide panel reads as curved. A designer
models such a part as its outside faces plus a wall thickness, so this does the same:

1. Pair each triangle with the one behind it across the wall (facing the other way, a few
   millimetres off). Where most of the scan pairs up, it is a shell, and the median gap is
   its wall thickness.
2. The outside of the wall is the side of each pair that faces away from the part's middle.
   Only that side is fitted: it is the better scanned one and the one the part is drawn to.
3. Each flat panel of the outside gets an outline. Where the panel meets another panel, the
   outline runs along their intersection (on the other panel's outside for a convex fold,
   its inside for a concave one, so the corner fills). Everywhere else (the open end, a
   notch, a vent) it follows the scan's own edge, simplified. Pinholes are left out.
4. Each outline is pushed in by the wall thickness, and the panels are fused into one solid.

Posts standing off the inside of the wall (screw standoffs) are not part of it. They are
found afterwards in what of the scan the walls leave unexplained, and added as a boss with a
hole: see standoffs().
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cadquery as cq
import numpy as np
import shapely
import shapely.geometry
import trimesh
from scipy.spatial import cKDTree

from .brep import merge_coplanar, snap_planes
from .cylinders import _basis, _fit_circle
from .features import Feature, _snap_radius
from .quality import distances
from .segment import (
    Geo,
    Plane,
    Region,
    face_labels,
    plane_offset,
    segment_planes,
    update_deviation,
)
from .tolerance import ToleranceLevel

# Pieces of wall under this share of the biggest, standing apart from it, are specks.
SPECK_SHARE = 0.02
# Shells are fitted on a working mesh this dense: a 1.5 mm wall needs triangles well under its
# thickness for each one to find its partner across it.
SHELL_FACES = 250_000

# Pairing: a triangle's partner lies straight behind it (within this of the line through its
# centre along its normal), facing the other way (normals at least this opposed), between
# these distances.
PAIR_LATERAL_MM = 0.45
PAIR_OPPOSED = -0.7
PAIR_MIN_MM = 0.3
PAIR_MAX_MM = 4.0
PAIR_STEP_MM = 0.1
# It is a shell when at least this share of the surface pairs up, the walls are at most this
# thick (and this share of the part's size), and both sides of the walls were scanned (each
# side at least this share of what paired).
MIN_PAIRED_SHARE = 0.6
MAX_WALL_MM = 3.5
MAX_WALL_SHARE = 0.05
MIN_SIDE_SHARE = 0.3
# The outside is fitted with at least this tolerance: wide moulded panels sag and bow by more
# than a precise level allows, and are still flat by design.
MIN_PANEL_TOL_MM = 0.5
# Side labels are smoothed over neighbouring triangles this many times, but not across folds
# sharper than this (the edge of the wall, where the outside turns into the rim).
SIDE_ROUNDS = 6
SIDE_KEEP_DEG = 50.0
# Holes in a panel smaller than this (scan pinholes) are filled.
PINHOLE_MM2 = 2.0
# Notches and spikes in a panel's outline narrower than twice this are scan noise.
SMOOTH_MM = 0.4
# Outlines are simplified to this share of the fit tolerance.
SIMPLIFY_SHARE = 0.6
# Pieces of panel smaller than this are left out.
MIN_PANEL_MM2 = 4.0
# A hole is checked for surface across it at points this far in from its edge, about this
# many of them; it is a gap in the scan, not an opening, when this share lie within this of
# scan surface lying along the panel (its normal within the facing limit of the panel's).
HOLE_EDGE_MM = 0.6
HOLE_SAMPLES = 60
HOLE_WALL_SHARE = 0.5
HOLE_WALL_MM = 0.4
HOLE_FACING = 0.8
# Surface across a hole counts from this far proud of the panel, looked for in steps of this.
HOLE_PROUD_MM = 1.0
HOLE_DEPTH_STEP_MM = 0.2
# Regions within this angle and a tolerance of the same plane are one panel.
PARALLEL_DEG = 3.0
# Regions under this area lying within this many tolerances of a bigger neighbour's plane
# are part of it.
FACET_MM2 = 40.0
FACET_TOLS = 2.0
# The inside of a panel's wall: inner faces within this angle of facing straight back,
# within this of a wall's thickness behind it; each is grown by this to close the cracks
# between triangles.
BACK_DEG = 25.0
BACK_BAND_MM = 0.6
BACK_GROW_MM = 0.05
# A stretch of free edge shorter than this between two stretches against the same panel is a
# gap in the scan, not an edge.
GAP_BRIDGE_MM = 3.0
# Panels meeting at less than this angle are not intersected (the line would run far off).
MIN_FOLD_DEG = 12.0
# Standoffs. Scan left unexplained by the walls is what lies further than this many fit
# tolerances (and at least this far) from them; a piece of it smaller than this is ignored.
POST_FAR_TOLS = 2.0
POST_FAR_MM = 0.6
POST_MIN_MM2 = 20.0
# A post's faces point every way around its axis: on the weakest of three directions their
# normals share at most this much, on the middle one at least this much.
POST_AXIS_SHARE = 0.15
POST_ROUND_SHARE = 0.25
# Its axis is within this of square to the wall it stands on, its sides go this far round
# (in 10 degree steps), and it is between these diameters and at least this tall.
POST_AXIS_DEG = 10.0
POST_ARC_DEG = 270
POST_MIN_DIAMETER_MM = 2.5
POST_MAX_DIAMETER_MM = 15.0
POST_MIN_HEIGHT_MM = 2.0
# Sides fit their circle to within this many fit tolerances (RMS).
POST_ROUND_TOLS = 1.0
# The end of a post is open (a hole) when its middle, inside this share of its radius, is
# under this share covered; and the scanner closed it over when the middle (its highest
# tenth) stands this far proud of the rim. A closed-over hole gets the usual pilot size for
# the screw a post that thick takes: (largest post diameter, screw, pilot hole) in mm.
POST_BORE_SHARE = 0.4
POST_OPEN_COVER = 0.3
POST_DOME_MM = 0.3
SCREW_POSTS = [(4.5, "M2", 1.7), (5.5, "M2.5", 2.1), (7.0, "M3", 2.5), (9.0, "M4", 3.4)]
SCREW_POST_LARGEST = (11.0, "M5", 4.2)


@dataclass
class ShellScan:
    thickness_mm: float
    outer: np.ndarray
    """Faces of the working mesh on the outside of the wall."""
    inner: np.ndarray
    """Faces on the inside of the wall (paired with the outside)."""
    paired_share: float


@dataclass
class Panel:
    region: Region
    outline: np.ndarray
    """Outer loop in 3D, on the panel's outside plane."""
    holes: list[np.ndarray] = field(default_factory=list)


@dataclass
class ShellInfo:
    thickness_mm: float
    panels: int
    vents: int
    """Openings cut through panels (slots, windows)."""

    def as_dict(self) -> dict:
        return {
            "type": "shell",
            "thickness_mm": self.thickness_mm,
            "panels": self.panels,
            "openings": self.vents,
        }


@dataclass
class ShellBuild:
    walls: cq.Shape
    """The panels fused into one solid: the base of the editable history."""
    features: list[Feature]
    """Standoffs: a boss each, and their holes."""
    info: ShellInfo
    notes: list[str]


THICKNESS_NOTE = (
    "Rebuilt as a thin-walled shell: its outside faces, {:.2f} mm thick (the wall measured "
    "across the scan)."
)
SEALED_NOTE = (
    "The scan closed over the end of {} standoff{}, as scanners do with narrow holes. Each "
    "got a {:.1f} mm hole, the usual size for an {} screw in plastic; change it in the "
    "editable history if yours differs."
)
LEFT_OUT_NOTE = "{} small piece{} of wall could not be built and {} left out."


def build_shell(mesh: trimesh.Trimesh, level: ToleranceLevel) -> ShellBuild | None:
    """The part as a shell, if it is one: None when the scan isn't a thin-walled part, or
    its panels don't make a valid solid."""
    work = Geo.of(mesh)
    found = find_shell(work)
    if found is None:
        return None
    t = found.thickness_mm
    skin, _ = outer_skin(work, found)
    regions = panel_regions(skin, level, t)
    tol = max(level.fit_tolerance_mm, MIN_PANEL_TOL_MM)
    panels = panel_outlines(skin, regions, t, tol, work, found.inner)
    solids = [s for s in (panel_solid(p, t) for p in panels) if s is not None]
    if not solids:
        return None
    try:
        walls = fuse_panels(solids, tol)
    except Exception:  # OpenCASCADE gives up on the fuse in several ways
        return None
    # Specks of panel standing apart from the walls (the end of a post, flat enough to read
    # as a panel) are left out; the posts are found afterwards in what the walls miss.
    pieces = sorted(walls.Solids(), key=lambda p: -p.Volume())
    if len(pieces) > 1 and pieces[1].Volume() < SPECK_SHARE * pieces[0].Volume():
        walls = pieces[0]
        pieces = pieces[:1]
    if not walls.isValid() or len(pieces) != 1:
        return None
    posts = standoffs(work, walls, panels, t, tol)
    notes = [THICKNESS_NOTE.format(t)]
    sealed = [p for p in posts if p.bore_sealed]
    for screw in sorted({p.screw for p in sealed}):
        same = [p for p in sealed if p.screw == screw]
        many = "s" if len(same) > 1 else ""
        notes.append(SEALED_NOTE.format(len(same), many, 2 * same[0].bore, screw))
    if len(solids) < len(panels):
        n = len(panels) - len(solids)
        notes.append(LEFT_OUT_NOTE.format(n, "s" if n > 1 else "", "were" if n > 1 else "was"))
    info = ShellInfo(t, len(panels), sum(len(p.holes) for p in panels))
    return ShellBuild(walls, post_features(posts, level.fit_tolerance_mm), info, notes)


# ---------------------------------------------------------------------------------------------
# Finding the wall
# ---------------------------------------------------------------------------------------------


def wall_partners(work: Geo) -> tuple[np.ndarray, np.ndarray]:
    """For each face, the face straight behind it across the wall (or -1) and the gap."""
    centres = work.vertices[work.faces].mean(axis=1)
    normals = work.face_normals
    tree = cKDTree(centres)
    partner = np.full(len(centres), -1)
    for s in np.arange(PAIR_MIN_MM, PAIR_MAX_MM + 1e-9, PAIR_STEP_MM):
        todo = np.flatnonzero(partner < 0)
        if not len(todo):
            break
        # The bound matters: inside a solid part most probes are far from every face, and an
        # unbounded nearest search over the whole tree took minutes.
        d, g = tree.query(
            centres[todo] - s * normals[todo], k=1, distance_upper_bound=PAIR_LATERAL_MM, workers=-1
        )
        near = np.flatnonzero(d < PAIR_LATERAL_MM)
        ok = near[(normals[g[near]] * normals[todo[near]]).sum(axis=1) < PAIR_OPPOSED]
        partner[todo[ok]] = g[ok]
    gap = np.full(len(centres), np.nan)
    has = partner >= 0
    gap[has] = ((centres[has] - centres[partner[has]]) * normals[has]).sum(axis=1)
    return partner, gap


def find_shell(work: Geo) -> ShellScan | None:
    """The scan's wall, if it is a thin-walled part; None otherwise."""
    partner, gap = wall_partners(work)
    area = work.area_faces
    paired = (partner >= 0) & (gap > 0)
    total = area.sum()
    share = float(area[paired].sum() / total)
    if share < MIN_PAIRED_SHARE:
        return None
    thickness = float(_weighted_median(gap[paired], area[paired]))
    size = float(np.linalg.norm(np.ptp(work.vertices, axis=0)))
    if thickness > MAX_WALL_MM or thickness > MAX_WALL_SHARE * size:
        return None
    centres = work.vertices[work.faces].mean(axis=1)
    middle = (centres * area[:, None]).sum(axis=0) / total
    away = ((centres - middle) * work.face_normals).sum(axis=1) > 0
    side = np.where(paired, np.where(away, 1.0, -1.0), 0.0)
    # Each pair is one wall: its two faces are on opposite sides whatever the middle says.
    # Where a fold puts a pair's faces on the same side of the middle (a ledge), the face with
    # the longer way out keeps it.
    side = _smoothed(work, side)
    outer, inner = side > 0.2, side < -0.2
    out_area, in_area = area[outer & paired].sum(), area[inner & paired].sum()
    if min(out_area, in_area) < MIN_SIDE_SHARE * area[paired].sum():
        return None  # both faces of a plate face away from its middle: not a shell
    return ShellScan(round(thickness, 2), np.flatnonzero(outer), np.flatnonzero(inner), share)


def _smoothed(work: Geo, side: np.ndarray) -> np.ndarray:
    """Side labels averaged over neighbours (area weighted) without crossing sharp folds, so
    unpaired faces on a panel join its side and a panel's stray flips are outvoted."""
    pairs = work.face_adjacency
    n = work.face_normals
    keep = (n[pairs[:, 0]] * n[pairs[:, 1]]).sum(axis=1) > np.cos(np.radians(SIDE_KEEP_DEG))
    pairs = pairs[keep]
    area = work.area_faces
    value = side.copy()
    for _ in range(SIDE_ROUNDS):
        acc = value * area
        weight = area.copy()
        np.add.at(acc, pairs[:, 0], value[pairs[:, 1]] * area[pairs[:, 1]])
        np.add.at(acc, pairs[:, 1], value[pairs[:, 0]] * area[pairs[:, 0]])
        np.add.at(weight, pairs[:, 0], area[pairs[:, 1]])
        np.add.at(weight, pairs[:, 1], area[pairs[:, 0]])
        value = acc / weight
        # Paired faces keep their own side; only the unpaired ones are filled in.
        value = np.where(side != 0, 0.5 * (value + side), value)
    return value


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values)
    cum = np.cumsum(weights[order])
    return float(values[order][np.searchsorted(cum, 0.5 * cum[-1])])


# ---------------------------------------------------------------------------------------------
# Panels and their outlines
# ---------------------------------------------------------------------------------------------


def outer_skin(work: Geo, shell: ShellScan) -> tuple[Geo, np.ndarray]:
    """The outside of the wall as a mesh of its own (sharing the working mesh's vertices),
    with the working-mesh face id of each of its faces."""
    faces = shell.outer
    mesh = trimesh.Trimesh(work.vertices, work.faces[faces], process=False)
    # Specks of outside (a flipped patch on a rim) are dropped.
    labels = trimesh.graph.connected_component_labels(mesh.face_adjacency, len(mesh.faces))
    areas = np.bincount(labels, weights=mesh.area_faces)
    keep = areas[labels] >= 0.002 * areas.max()
    mesh = trimesh.Trimesh(work.vertices, work.faces[faces[keep]], process=False)
    return Geo.of(mesh), faces[keep]


def panel_regions(skin: Geo, level: ToleranceLevel, thickness: float) -> list[Region]:
    """The flat panels of the outside. Nearly parallel or square panels are made exactly so,
    and pieces of one plane (the bars of a vent grille, two halves of a panel split by a
    sag) become one panel, so a vent is a hole in its panel."""
    level = level.with_overrides(fit_tolerance_mm=max(level.fit_tolerance_mm, MIN_PANEL_TOL_MM))
    regions = segment_planes(skin, level)
    snap_planes(skin, regions, level)
    regions = merge_coplanar(skin, regions, level)
    regions = _merge_parallel(skin, regions, level.fit_tolerance_mm)
    return _absorb_facets(skin, regions, level.fit_tolerance_mm, thickness)


def _merge_parallel(skin: Geo, regions: list[Region], tol: float) -> list[Region]:
    """Pieces of one plane become one region even where they don't touch (a panel cut up by
    its vents, or split by noise along a seam). A region in pieces still gives a panel per
    piece, so pieces that only happen to share a plane are not joined."""
    parent = list(range(len(regions)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    same = np.cos(np.radians(PARALLEL_DEG))
    order = sorted(range(len(regions)), key=lambda i: -regions[i].area)
    for k, i in enumerate(order):
        a = regions[i].plane
        for j in order[k + 1 :]:
            b = regions[j].plane
            if a.normal @ b.normal > same and abs(a.offset - b.offset) <= tol:
                parent[find(j)] = find(i)
    groups: dict[int, list[Region]] = {}
    for i, r in enumerate(regions):
        groups.setdefault(find(i), []).append(r)
    if len(groups) == len(regions):
        return regions
    merged = []
    for new_id, group in enumerate(groups.values()):
        faces = np.concatenate([g.faces for g in group])
        normal = max(group, key=lambda g: g.area).plane.normal
        plane = Plane(normal, plane_offset(skin, faces, normal))
        merged.append(Region(new_id, faces, plane, float(skin.area_faces[faces].sum())))
    labels = face_labels(merged, len(skin.faces))
    pairs = skin.face_adjacency
    a, b = labels[pairs[:, 0]], labels[pairs[:, 1]]
    cross = (a != b) & (a >= 0) & (b >= 0)
    for x, y in zip(a[cross], b[cross], strict=True):
        merged[x].neighbours.add(int(y))
        merged[y].neighbours.add(int(x))
    update_deviation(skin, merged)
    return merged


def _absorb_facets(skin: Geo, regions: list[Region], tol: float, thickness: float) -> list[Region]:
    """Small regions lying on a neighbouring panel's plane join it: a scuffed patch, or the
    bars of a grille flush with its panel, each tilted by noise into a plane of its own. Ones
    lying a little below it (the bars of a recessed grille) are made parallel to it, at their
    depth."""
    by_id = {r.id: r for r in regions}
    for r in sorted(regions, key=lambda r: r.area):
        if r.area >= FACET_MM2 or r.id not in by_id:
            continue
        pts = skin.vertices[np.unique(skin.faces[r.faces])]
        for nb in sorted(r.neighbours, key=lambda i: -by_id[i].area if i in by_id else 0):
            q = by_id.get(nb)
            if q is None or q.area <= r.area:
                continue
            d = q.plane.distance(pts)
            lo, mid, hi = np.percentile(d, [10, 50, 90])
            if -thickness <= lo and hi <= FACET_TOLS * tol and mid < -tol:
                r.plane = Plane(q.plane.normal.copy(), q.plane.offset + float(mid))
                break
            if np.percentile(np.abs(d), 90) <= FACET_TOLS * tol:
                q.faces = np.concatenate([q.faces, r.faces])
                q.area += r.area
                q.neighbours |= r.neighbours - {q.id}
                for o in by_id.values():
                    if r.id in o.neighbours:
                        o.neighbours.discard(r.id)
                        if o.id != q.id:
                            o.neighbours.add(q.id)
                del by_id[r.id]
                break
    return list(by_id.values())


class _Edges:
    """Directed edges of a mesh and the face across each."""

    def __init__(self, faces: np.ndarray, vertex_count: int):
        self.directed = np.stack([faces, np.roll(faces, -1, axis=1)], axis=2).reshape(-1, 2)
        self.face = np.repeat(np.arange(len(faces)), 3)
        key = self.directed[:, 0] * vertex_count + self.directed[:, 1]
        rev = self.directed[:, 1] * vertex_count + self.directed[:, 0]
        order = np.argsort(key)
        pos = np.searchsorted(key[order], rev)
        pos = np.clip(pos, 0, len(key) - 1)
        hit = key[order][pos] == rev
        self.across = np.where(hit, self.face[order][pos], -1)


def panel_outlines(
    skin: Geo,
    regions: list[Region],
    thickness: float,
    tol: float,
    scan: Geo,
    inner: np.ndarray,
) -> list[Panel]:
    """Each region's outline: along its neighbours' planes where it meets them, along the
    scan's edge elsewhere. The panel's material is where either side of its wall was
    scanned: the inside (inner, faces of scan) fills in where the outside went unscanned,
    like the narrow bars of a grille. A hole with the inside of the wall across it anyway is
    a gap in the scan, not an opening, and is filled."""
    centres = scan.vertices[scan.faces].mean(axis=1)
    tree = cKDTree(centres)
    labels = np.full(len(skin.faces), -1)
    for r in regions:
        labels[r.faces] = r.id
    by_id = {r.id: r for r in regions}
    edges = _Edges(skin.faces, len(skin.vertices))
    panels = []
    for r in regions:
        loops = _boundary_loops(skin, edges, labels, r)
        frame = _Frame(r.plane)
        built = []
        for verts, tags in loops:
            pts = frame.to2d(skin.vertices[verts])
            seg_len = np.linalg.norm(np.roll(pts, -1, axis=0) - pts, axis=1)
            tags = _clean_tags(tags, seg_len, r, by_id)
            poly = _outline(pts, tags, r, by_id, frame, thickness, tol)
            if poly is not None and len(poly) >= 3:
                built.append(poly)
        backs = _inside_footprint(scan, inner, centres, r.plane, frame, thickness)
        for shape in _clean_outline(built, tol, backs):
            outer = np.asarray(shape.exterior.coords)[:-1]
            holes = [
                np.asarray(h.coords)[:-1]
                for h in shape.interiors
                if not _wall_across(shapely.Polygon(h), frame, thickness, tree, scan.face_normals)
            ]
            panels.append(Panel(r, frame.to3d(outer), [frame.to3d(h) for h in holes]))
    return panels


def _wall_across(
    hole: shapely.Polygon, frame: _Frame, thickness: float, tree: cKDTree, normals: np.ndarray
) -> bool:
    """Whether the scan has surface across most of a hole, lying along the panel anywhere
    from a little proud of it to a wall's thickness behind it: the inside of the wall, a
    debossed logo's floor, a dimple. Then the hole is a gap in the outside's scan, not an
    opening. (The sides of a narrow slot are square to the panel and don't count. Where
    another panel covers the hole, that panel fills it.)"""
    inner = hole.buffer(-HOLE_EDGE_MM)
    if inner.is_empty:
        inner = hole
    x0, y0, x1, y1 = inner.bounds
    step = max(np.sqrt(inner.area / HOLE_SAMPLES), 0.3)
    gx, gy = np.meshgrid(np.arange(x0, x1 + step, step), np.arange(y0, y1 + step, step))
    pts = np.c_[gx.ravel(), gy.ravel()]
    pts = pts[shapely.contains_xy(inner, pts[:, 0], pts[:, 1])]
    if not len(pts):
        pts = np.array(inner.representative_point().coords)
    p3 = frame.to3d(pts)
    near = np.zeros(len(pts), bool)
    for depth in np.arange(HOLE_PROUD_MM, -(thickness + BACK_BAND_MM), -HOLE_DEPTH_STEP_MM):
        d, i = tree.query(p3 + depth * frame.n)
        near |= (d <= HOLE_WALL_MM) & (np.abs(normals[i] @ frame.n) > HOLE_FACING)
    return bool(near.mean() >= HOLE_WALL_SHARE)


def _inside_footprint(
    scan: Geo,
    inner: np.ndarray,
    centres: np.ndarray,
    plane: Plane,
    frame: _Frame,
    thickness: float,
) -> shapely.Geometry | None:
    """The inside of this panel's wall, as scanned, flattened onto the panel: the inner faces
    facing the other way a wall's thickness behind it."""
    d = plane.distance(centres[inner])
    facing = scan.face_normals[inner] @ plane.normal < -np.cos(np.radians(BACK_DEG))
    faces = inner[facing & (np.abs(d + thickness) <= BACK_BAND_MM)]
    if not len(faces):
        return None
    tris = frame.to2d(scan.vertices[scan.faces[faces]].reshape(-1, 3)).reshape(-1, 3, 2)
    polys = shapely.polygons(np.concatenate([tris, tris[:, :1]], axis=1))
    polys = polys[shapely.area(polys) > 1e-9]
    return shapely.union_all(shapely.buffer(polys, BACK_GROW_MM, join_style="mitre"))


def _clean_outline(
    loops: list[np.ndarray], tol: float, backs: shapely.Geometry | None = None
) -> list[shapely.Polygon]:
    """The traced loops as clean polygons: outlines (anticlockwise) less holes (clockwise),
    plus what backs shows of the wall within the outlines,
    self-crossings undone, the scan's ragged edge smoothed (notches and spikes under
    SMOOTH_MM closed and cut), pinholes filled, and the edge simplified to the tolerance.
    Straight edges and square corners come through the smoothing unchanged."""
    outlines, holes = [], []
    for loop in loops:
        poly = shapely.make_valid(shapely.Polygon(loop))
        (outlines if _area(loop) > 0 else holes).append(poly)
    if not outlines:
        return []
    shape = shapely.union_all(outlines)
    if holes:
        shape = shape.difference(shapely.union_all(holes))
    if backs is not None:
        # Only within the panel's outline: the inside fills holes and gaps, not beyond.
        hull = shapely.union_all(
            [
                shapely.Polygon(p.exterior)
                for p in shapely.get_parts(shape)
                if p.geom_type == "Polygon"
            ]
        )
        shape = shape.union(backs.intersection(hull))
    mitre = {"join_style": "mitre", "mitre_limit": 4.0}
    shape = shape.buffer(SMOOTH_MM, **mitre).buffer(-2 * SMOOTH_MM, **mitre)
    shape = shape.buffer(SMOOTH_MM, **mitre).simplify(SIMPLIFY_SHARE * tol)
    out = []
    for part in shapely.get_parts(shape):
        if part.geom_type != "Polygon" or part.area < MIN_PANEL_MM2:
            continue
        keep = [h for h in part.interiors if shapely.Polygon(h).area >= PINHOLE_MM2]
        part = shapely.Polygon(part.exterior, keep)
        if part.is_valid:
            out.append(shapely.geometry.polygon.orient(part))
    return out


class _Frame:
    def __init__(self, plane: Plane):
        n = plane.normal
        u = np.cross(n, [1.0, 0.0, 0.0] if abs(n[0]) < 0.9 else [0.0, 1.0, 0.0])
        self.u = u / np.linalg.norm(u)
        self.v = np.cross(n, self.u)
        self.n = n
        self.origin = n * plane.offset

    def to2d(self, p: np.ndarray) -> np.ndarray:
        q = p - self.origin
        return np.c_[q @ self.u, q @ self.v]

    def to3d(self, q: np.ndarray) -> np.ndarray:
        return self.origin + q[:, :1] * self.u + q[:, 1:] * self.v


def _boundary_loops(skin: Geo, edges: _Edges, labels: np.ndarray, r: Region):
    """The region's boundary as loops of vertex ids, each with the label across each edge
    (-1 where nothing is: the scan's edge, or a part of it that isn't the outside)."""
    mine = np.zeros(len(labels), bool)
    mine[r.faces] = True
    on = mine[edges.face]
    across = edges.across
    out = on & ((across < 0) | ~mine[np.maximum(across, 0)])
    ids = np.flatnonzero(out)
    if not len(ids):
        return []
    tag = np.where(across[ids] >= 0, labels[np.maximum(across[ids], 0)], -1)
    starts = edges.directed[ids, 0]
    ends = edges.directed[ids, 1]
    outgoing: dict[int, list[int]] = {}
    for k, a in enumerate(starts.tolist()):
        outgoing.setdefault(a, []).append(k)
    frame = _Frame(r.plane)
    used = np.zeros(len(ids), bool)
    loops = []
    for k0 in range(len(ids)):
        if used[k0]:
            continue
        verts, tags = [], []
        k = k0
        while not used[k]:
            used[k] = True
            verts.append(int(starts[k]))
            tags.append(int(tag[k]))
            choices = [j for j in outgoing.get(int(ends[k]), []) if not used[j]]
            if not choices:
                break
            if len(choices) > 1:
                # A pinch: keep the region on the left, taking the sharpest left turn.
                a, b = skin.vertices[starts[k]], skin.vertices[ends[k]]
                d_in = frame.to2d(np.array([b]))[0] - frame.to2d(np.array([a]))[0]
                best, best_turn = choices[0], -np.inf
                for j in choices:
                    d_out = frame.to2d(skin.vertices[[ends[j]]])[0] - frame.to2d(np.array([b]))[0]
                    turn = np.arctan2(d_in[0] * d_out[1] - d_in[1] * d_out[0], d_in @ d_out)
                    if turn > best_turn:
                        best, best_turn = j, turn
                k = best
            else:
                k = choices[0]
        if len(verts) >= 3 and int(ends[k]) == verts[0]:
            loops.append((np.array(verts), np.array(tags)))
    return loops


def _runs(tags: np.ndarray) -> list[tuple[int, int, int]]:
    """Runs of equal tags round a closed loop as (start, length, tag), the first starting at a
    change of tag (or the whole loop as one run)."""
    n = len(tags)
    change = np.flatnonzero(tags != np.roll(tags, 1))
    if not len(change):
        return [(0, n, int(tags[0]))]
    runs = []
    for i, s in enumerate(change):
        e = change[(i + 1) % len(change)]
        length = (e - s) % n or n
        runs.append((int(s), int(length), int(tags[s])))
    return runs


def _clean_tags(
    tags: np.ndarray, seg_len: np.ndarray, r: Region, by_id: dict[int, Region]
) -> np.ndarray:
    """Tags that can be intersected: a neighbour nearly parallel to the panel, or one too
    small to be a panel, counts as free edge; short free gaps between stretches against the
    same neighbour are bridged; short stretches against a neighbour inside free edge are free
    edge (a speck of the neighbour's faces on a rim)."""
    tags = tags.copy()
    cos_fold = np.cos(np.radians(MIN_FOLD_DEG))
    for t in np.unique(tags):
        if t >= 0 and (t not in by_id or abs(by_id[t].plane.normal @ r.plane.normal) > cos_fold):
            tags[tags == t] = -1
    n = len(tags)
    for _ in range(3):
        runs = _runs(tags)
        if len(runs) < 3:
            break
        changed = False
        for i, (s, length, t) in enumerate(runs):
            prev_t, next_t = runs[i - 1][2], runs[(i + 1) % len(runs)][2]
            idx = (s + np.arange(length)) % n
            run_len = seg_len[idx].sum()
            if t == -1 and prev_t == next_t and prev_t >= 0 and run_len < GAP_BRIDGE_MM:
                tags[idx] = prev_t
                changed = True
            elif t >= 0 and prev_t == next_t == -1 and run_len < GAP_BRIDGE_MM:
                tags[idx] = -1
                changed = True
            elif t >= 0 and prev_t == next_t and prev_t != t and run_len < 0.5 * GAP_BRIDGE_MM:
                tags[idx] = prev_t
                changed = True
        if not changed:
            break
    return tags


def _outline(
    pts: np.ndarray,
    tags: np.ndarray,
    r: Region,
    by_id: dict[int, Region],
    frame: _Frame,
    thickness: float,
    tol: float,
) -> np.ndarray | None:
    """The loop as a polygon in the panel's 2D frame."""
    n = len(pts)
    runs = _runs(tags)
    if len(runs) == 1 and runs[0][2] >= 0:
        return None  # a loop all against one neighbour: a sliver, not an outline
    pieces = []
    for s, length, t in runs:
        idx = (s + np.arange(length + 1)) % n  # the run's points, and the next run's first
        run_pts = pts[idx]
        if t < 0:
            pieces.append(("free", _simplify(run_pts, tol)))
        else:
            q = by_id[t].plane
            # Into the panel from the edge, in its plane.
            line = _plane_line(frame, q, 0.0)
            if line is None:
                pieces.append(("free", _simplify(run_pts, tol)))
                continue
            p0, d = line
            # The panel lies to the left of its boundary as it runs.
            run_dir = run_pts[-1] - run_pts[0]
            if np.linalg.norm(run_dir) < 1e-6:
                run_dir = d
            normal2d = np.array([-run_dir[1], run_dir[0]])
            into = frame.u * normal2d[0] + frame.v * normal2d[1]
            convex = q.normal @ into < 0
            if not convex:
                line = _plane_line(frame, q, thickness)
            pieces.append(("line", line, run_pts))
    out = []
    m = len(pieces)
    for i in range(m):
        a, b = pieces[i - 1], pieces[i]
        corner = _corner(a, b)
        out.append(corner)
        if b[0] == "free":
            out.extend(b[1][1:-1])
    poly = np.array(out)
    # Corners that ran far off (nearly parallel neighbours) are not trusted.
    span = np.ptp(pts, axis=0).max()
    if not np.isfinite(poly).all() or np.ptp(poly, axis=0).max() > 1.5 * span + 5:
        return _simplify(np.vstack([pts, pts[:1]]), tol)[:-1]
    return poly


def _plane_line(frame: _Frame, q: Plane, inset: float) -> tuple[np.ndarray, np.ndarray] | None:
    """Where the other plane (moved inset along its own inward side) crosses this panel's
    plane, as a 2D point and unit direction."""
    n1, n2 = frame.n, q.normal
    d3 = np.cross(n1, n2)
    s = np.linalg.norm(d3)
    if s < np.sin(np.radians(MIN_FOLD_DEG)):
        return None
    d3 /= s
    o1 = frame.n @ frame.origin
    o2 = q.offset - inset
    # Point on both planes closest to the origin.
    a = np.array([n1, n2, d3])
    p3 = np.linalg.solve(a, np.array([o1, o2, 0.0]))
    p = frame.to2d(p3[None])[0]
    d = np.array([d3 @ frame.u, d3 @ frame.v])
    return p, d / np.linalg.norm(d)


def _corner(a, b) -> np.ndarray:
    """Where piece a ends and piece b starts."""
    if a[0] == "line" and b[0] == "line":
        hit = _cross_lines(a[1], b[1])
        if hit is not None:
            return hit
        return _project(b[2][0], b[1])
    if a[0] == "line":
        free = b[1]
        return _meet(a[1], free[0], free[min(1, len(free) - 1)], free)
    if b[0] == "line":
        free = a[1]
        return _meet(b[1], free[-1], free[max(-2, -len(free))], free)
    return b[1][0]


def _meet(line, end: np.ndarray, towards: np.ndarray, free: np.ndarray) -> np.ndarray:
    """Where a free edge, run on from its end, reaches the line; or the end projected onto the
    line when the free edge runs along it."""
    d = end - towards
    if np.linalg.norm(d) > 1e-9:
        hit = _cross_lines((end, d / np.linalg.norm(d)), line, min_sin=0.3)
        if hit is not None and np.linalg.norm(hit - end) < 5.0:
            return hit
    return _project(end, line)


def _cross_lines(l1, l2, min_sin: float = 0.1) -> np.ndarray | None:
    (p1, d1), (p2, d2) = l1, l2
    cross = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(cross) < min_sin:
        return None
    t = ((p2 - p1)[0] * d2[1] - (p2 - p1)[1] * d2[0]) / cross
    return p1 + t * d1


def _project(p: np.ndarray, line) -> np.ndarray:
    p0, d = line
    return p0 + ((p - p0) @ d) * d


def _simplify(pts: np.ndarray, tol: float) -> np.ndarray:
    """Douglas-Peucker on an open polyline, keeping both ends."""
    if len(pts) <= 2:
        return pts
    keep = np.zeros(len(pts), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = pts[i], pts[j]
        ab = b - a
        L = np.linalg.norm(ab)
        seg = pts[i + 1 : j]
        if L < 1e-12:
            d = np.linalg.norm(seg - a, axis=1)
        else:
            d = np.abs((seg[:, 0] - a[0]) * ab[1] - (seg[:, 1] - a[1]) * ab[0]) / L
        k = int(np.argmax(d))
        if d[k] > tol:
            m = i + 1 + k
            keep[m] = True
            stack += [(i, m), (m, j)]
    return pts[keep]


def _area(poly: np.ndarray) -> float:
    x, y = poly[:, 0], poly[:, 1]
    return 0.5 * float(x @ np.roll(y, -1) - y @ np.roll(x, -1))


def _inside2d(point: np.ndarray, polygon: np.ndarray) -> bool:
    x, y = point
    a, b = polygon, np.roll(polygon, -1, axis=0)
    crosses = (a[:, 1] > y) != (b[:, 1] > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        at = a[:, 0] + (y - a[:, 1]) * (b[:, 0] - a[:, 0]) / (b[:, 1] - a[:, 1])
    return bool(np.count_nonzero(crosses & (x < at)) % 2)


# ---------------------------------------------------------------------------------------------
# Standoffs
# ---------------------------------------------------------------------------------------------


@dataclass
class Post:
    """A round post standing on the inside of a panel."""

    panel: Panel
    axis: np.ndarray
    """Unit vector from the wall towards the post's end."""
    base: np.ndarray
    """Axis point on the inside of the wall."""
    top: np.ndarray
    """Axis point at the post's end."""
    radius: float
    bore: float | None
    """Radius of the hole in its end, if it has one."""
    bore_sealed: bool = False
    """The scanner closed the hole over, so its size is the usual one for the post."""
    screw: str | None = None


def standoffs(
    scan: Geo, model: cq.Shape, panels: list[Panel], thickness: float, tol: float
) -> list[Post]:
    """Round posts on the inside of the walls (screw standoffs, locating pins).

    They are found in what the walls leave unexplained: scan further than a couple of
    tolerances from the panels, in pieces whose faces point every way around one direction
    square to a panel. The sides give the post's diameter; its end says whether it has a
    hole. Moulded screw posts are hollow, but a scanner rarely sees down a narrow bore and
    often skins the opening over with a slight dome; that dome is read as the hole."""
    near = distances(scan.vertices, model, tol)
    far = (near[scan.faces] > _far_mm(tol)).all(axis=1)
    pairs = scan.face_adjacency
    pairs = pairs[far[pairs[:, 0]] & far[pairs[:, 1]]]
    labels = trimesh.graph.connected_component_labels(pairs, len(scan.faces))
    labels = np.where(far, labels, -1)
    centres = scan.vertices[scan.faces].mean(axis=1)
    found = []
    for label in np.unique(labels[labels >= 0]):
        faces = np.flatnonzero(labels == label)
        if scan.area_faces[faces].sum() < POST_MIN_MM2:
            continue
        post = _post(scan, centres, faces, panels, thickness, tol)
        if post is not None:
            found.append(post)
    return found


def _post(
    scan: Geo,
    centres: np.ndarray,
    faces: np.ndarray,
    panels: list[Panel],
    thickness: float,
    tol: float,
) -> Post | None:
    normals, area = scan.face_normals[faces], scan.area_faces[faces]
    share, vecs = np.linalg.eigh((normals * area[:, None]).T @ normals)
    share = share / share.sum()
    if share[0] > POST_AXIS_SHARE or share[1] < POST_ROUND_SHARE:
        return None
    guess = vecs[:, 0]
    square = np.cos(np.radians(POST_AXIS_DEG))
    stands = [p for p in panels if abs(p.region.plane.normal @ guess) >= square]
    if not stands:
        return None
    # The wall it stands on: the post runs inwards from it, square to it, at least partly
    # over the panel, and the post's sides start within a wall's thickness of its inside.
    best = None
    for p in stands:
        axis = -p.region.plane.normal
        fit = _round_sides(centres[faces], normals, axis)
        if fit is None:
            return None
        centre, radius, sides = fit
        t = centres[faces] @ axis
        outside = -p.region.plane.offset - centre @ axis
        frame = _Frame(p.region.plane)
        hit = frame.to2d((centre + outside * axis)[None])[0]
        inside = outside + thickness
        gap = abs(float(t[sides].min()) - inside)
        footprint = shapely.Polygon(frame.to2d(p.outline), [frame.to2d(h) for h in p.holes])
        if (
            footprint.distance(shapely.Point(hit)) <= radius
            and gap <= thickness + _far_mm(tol)
            and t[sides].max() > inside
            and (best is None or gap < best[0])
        ):
            best = (gap, p, axis, centre, radius, sides, inside)
    if best is None:
        return None
    _, panel, axis, centre, radius, sides, base_t = best
    u, v = _basis(axis)
    xy = np.c_[centres[faces] @ u, centres[faces] @ v] - [centre @ u, centre @ v]
    ring = xy[sides]
    arc = 10 * len(np.unique(np.floor(np.degrees(np.arctan2(ring[:, 1], ring[:, 0])) / 10)))
    rms = float(np.sqrt(np.mean((np.linalg.norm(ring, axis=1) - radius) ** 2)))
    if (
        arc < POST_ARC_DEG
        or rms > POST_ROUND_TOLS * tol
        or not POST_MIN_DIAMETER_MM <= 2 * radius <= POST_MAX_DIAMETER_MM
    ):
        return None
    # The end: the faces across the axis at the far end. Its rim gives the height.
    t = centres[faces] @ axis
    caps = (normals @ axis > 0.8) & (t > t[sides].max() - 2 * radius)
    r_cap = np.linalg.norm(xy[caps], axis=1)
    rim = caps.copy()
    rim[caps] = (r_cap > 0.6 * radius) & (r_cap < radius)
    top_t = float(np.median(t[rim])) if rim.any() else float(t[sides].max())
    if top_t - base_t < POST_MIN_HEIGHT_MM:
        return None
    post = Post(panel, axis, centre + base_t * axis, centre + top_t * axis, radius, None)
    _read_end(post, xy[caps], t[caps], area[caps], top_t)
    return post


def _round_sides(
    centres: np.ndarray, normals: np.ndarray, axis: np.ndarray
) -> tuple[np.ndarray, float, np.ndarray] | None:
    """The circle the post's sides lie on, seen down the axis: its centre (on the axis line
    through the origin's plane), radius, and which faces are the sides."""
    u, v = _basis(axis)
    xy = np.c_[centres @ u, centres @ v]
    sides = np.abs(normals @ axis) < 0.3
    if sides.sum() < 10:
        return None
    cx, cy, radius = _fit_circle(xy[sides])
    for _ in range(3):
        out = xy - [cx, cy]
        dist = np.maximum(np.linalg.norm(out, axis=1), 1e-9)
        facing = ((normals @ u) * out[:, 0] + (normals @ v) * out[:, 1]) / dist
        sides = (np.abs(normals @ axis) < 0.3) & (facing > 0.7)
        if sides.sum() < 10:
            return None
        cx, cy, radius = _fit_circle(xy[sides])
    return cx * u + cy * v, radius, sides


def _far_mm(tol: float) -> float:
    return max(POST_FAR_TOLS * tol, POST_FAR_MM)


def _read_end(post: Post, xy: np.ndarray, t: np.ndarray, area: np.ndarray, top_t: float) -> None:
    """Whether the post's end has a hole in it, and how big."""
    r = np.linalg.norm(xy, axis=1)
    middle = r < POST_BORE_SHARE * post.radius
    covered = area[middle].sum() / (np.pi * (POST_BORE_SHARE * post.radius) ** 2)
    if covered < POST_OPEN_COVER:
        ring = r[(r > POST_BORE_SHARE * post.radius * 0.5) & (r < post.radius)]
        if len(ring):
            post.bore = float(np.percentile(ring, 5))
        return
    if np.percentile(t[middle], 90) - top_t >= POST_DOME_MM:
        largest, screw, pilot = next(
            (s for s in SCREW_POSTS if 2 * post.radius <= s[0]), SCREW_POST_LARGEST
        )
        post.bore, post.bore_sealed, post.screw = pilot / 2, True, screw


def post_features(posts: list[Post], tol: float) -> list[Feature]:
    """Each post as a boss, and its hole, drilled from the post's end to the wall."""
    # Posts that measure within a tolerance of each other are one size, as designed.
    sizes = {}
    order = sorted(posts, key=lambda p: p.radius)
    group = order[:1]
    for post in order[1:] + [None]:
        if post is not None and 2 * (post.radius - group[0].radius) <= tol:
            group.append(post)
            continue
        mean = float(np.mean([p.radius for p in group]))
        sizes.update({id(p): mean for p in group})
        group = [post]
    features = []
    for post in posts:
        radius, _ = _snap_radius(sizes[id(post)], tol, "boss")
        features.append(Feature("boss", -1, radius, post.axis, post.base, post.top))
        if post.bore:
            bore, label = (
                (post.bore, None) if post.bore_sealed else _snap_radius(post.bore, tol, "hole")
            )
            features.append(Feature("hole", -1, bore, -post.axis, post.base, post.top, label=label))
    return features


# ---------------------------------------------------------------------------------------------
# The solid
# ---------------------------------------------------------------------------------------------


def panel_solid(panel: Panel, thickness: float) -> cq.Solid | None:
    """The panel's outline pushed in by the wall thickness."""

    def wire(points: np.ndarray) -> cq.Wire:
        return cq.Wire.makePolygon([cq.Vector(*p) for p in points], close=True)

    inward = -panel.region.plane.normal * thickness
    try:
        solid = cq.Solid.extrudeLinear(
            wire(panel.outline), [wire(h) for h in panel.holes], cq.Vector(*inward)
        )
    except Exception:  # OpenCASCADE refuses a self-crossing outline in several ways
        try:
            solid = cq.Solid.extrudeLinear(wire(panel.outline), [], cq.Vector(*inward))
        except Exception:
            return None
    if not solid.isValid() or solid.Volume() <= 0:
        return None
    return solid


def fuse_panels(solids: list[cq.Solid], tol: float) -> cq.Shape:
    if len(solids) == 1:
        return solids[0]
    # No fuzzy tolerance: the panels overlap at their folds, and a fuzzy fuse of many
    # overlapping prisms comes back broken.
    return solids[0].fuse(*solids[1:]).clean()
