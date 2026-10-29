"""Sink and extrude: the user's own edits on the converted solid.

The user picks a point on the model and a sketch (rectangle, rounded rectangle, circle or
slot) with its size. The sketch plane sits square to the surface under the sketch (its
area-weighted average normal, found by casting rays through the footprint), and the sketch is
then:
- sunk: cut into the part, with a flat floor depth_mm below the lowest point of the surface
  under the sketch, so the whole floor is flat and at least that deep; or
- extruded: added to the part, with a flat top height_mm above the highest point under the
  sketch, so the whole top is flat.

An optional fillet rounds the floor's or the top's edges. Each edit comes back with a record
of its sketch plane, sketch and distances (`Edit.as_dict`), which the feature recipe keeps so
the step can be replayed as an ordinary sketch and extrude in CAD.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import cadquery as cq
import numpy as np
import trimesh

from .brep import UnsupportedGeometry

SHAPES = ("rectangle", "rounded_rectangle", "circle", "slot")
MODES = ("sink", "extrude")
# Rays through the footprint: about this many across its area, plus its outline.
FOOTPRINT_SAMPLES = 400
OUTLINE_SAMPLES = 120
# Tessellation of the solid for the rays, in mm.
RAY_MESH_TOL = 0.02
# On a curved surface an extrusion starts this far inside it (below its lowest point) so it
# fuses into the part rather than touching it along an edge.
EMBED_MM = 0.5
# Surface under the sketch flatter than this (mm, low to high) is a flat face.
FLAT_MM = 1e-4
# Faces within this angle of the sketch normal are the floor or top an edit made.
FLAT_DEG = 0.5


@dataclass(frozen=True)
class Sketch:
    shape: str
    """rectangle, rounded_rectangle, circle or slot."""
    width_mm: float
    """Overall size along the sketch's x: a circle's diameter, a slot's length end to end."""
    height_mm: float = 0.0
    """Overall size along its y: a slot's width (its ends' diameter). Not used by a circle."""
    corner_radius_mm: float = 0.0
    """A rounded rectangle's corners."""
    angle_deg: float = 0.0
    """Turn about the surface normal, from the sketch's x along the part's X (or Y where the
    surface faces along X)."""

    def __post_init__(self) -> None:
        if self.shape not in SHAPES:
            raise ValueError(f"Unknown sketch shape {self.shape!r}; expected one of {SHAPES}")
        if not self.width_mm > 0:
            raise ValueError("The sketch needs a size above 0 mm")
        if self.shape != "circle" and not self.height_mm > 0:
            raise ValueError("The sketch needs a width and a height above 0 mm")
        if self.shape == "slot" and self.height_mm >= self.width_mm:
            raise ValueError("A slot must be longer than it is wide")
        if self.shape == "rounded_rectangle" and not (
            0 < self.corner_radius_mm < min(self.width_mm, self.height_mm) / 2
        ):
            raise ValueError("Corner radius must be above 0 and under half the shorter side")

    def contains(self, xy: np.ndarray) -> np.ndarray:
        """Which local (x, y) points are inside the sketch."""
        x, y = np.abs(xy[:, 0]), np.abs(xy[:, 1])
        w, h = self.width_mm / 2, self.height_mm / 2
        if self.shape == "circle":
            return np.hypot(x, y) <= w
        if self.shape == "slot":
            r = h
            return np.hypot(np.maximum(x - (w - r), 0.0), y) <= r
        if self.shape == "rounded_rectangle":
            r = self.corner_radius_mm
            dx, dy = np.maximum(x - (w - r), 0.0), np.maximum(y - (h - r), 0.0)
            return (x <= w) & (y <= h) & (np.hypot(dx, dy) <= r)
        return (x <= w) & (y <= h)

    def outline(self, n: int) -> np.ndarray:
        """n points on the sketch's edge."""
        t = np.linspace(0, 2 * np.pi, n, endpoint=False)
        w = self.width_mm / 2
        h = w if self.shape == "circle" else self.height_mm / 2
        # Points on the enclosing ellipse pushed out until they leave the shape, then back.
        direction = np.c_[np.cos(t), np.sin(t)]
        lo, hi = np.zeros(n), np.full(n, float(np.hypot(w, h)) * 1.01)
        for _ in range(40):
            mid = (lo + hi) / 2
            inside = self.contains(direction * mid[:, None])
            lo, hi = np.where(inside, mid, lo), np.where(inside, hi, mid)
        return direction * lo[:, None]

    def wire(self, plane: cq.Plane) -> cq.Workplane:
        wp = cq.Workplane(plane)
        if self.shape == "circle":
            return wp.circle(self.width_mm / 2)
        if self.shape == "slot":
            return wp.slot2D(self.width_mm, self.height_mm)
        if self.shape == "rounded_rectangle":
            sketch = cq.Sketch().rect(self.width_mm, self.height_mm)
            return wp.placeSketch(sketch.vertices().fillet(self.corner_radius_mm))
        return wp.rect(self.width_mm, self.height_mm)


@dataclass(frozen=True)
class Edit:
    """One sink or extrude, in the frame of the converted solid."""

    mode: str
    point: tuple[float, float, float]
    """A point on (or near) the surface: the sketch's centre is put there."""
    sketch: Sketch
    distance_mm: float
    """A sink's depth below the lowest surface point under the sketch, or an extrusion's
    height above the highest."""
    fillet_mm: float = 0.0

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"Unknown edit {self.mode!r}; expected one of {MODES}")
        if not 0 < self.distance_mm <= 1000:
            raise ValueError("Depth or height must be above 0 mm")
        if self.fillet_mm < 0:
            raise ValueError("Fillet must be 0 mm or more")


@dataclass(frozen=True)
class EditResult:
    solid: cq.Shape
    origin: np.ndarray
    """Sketch plane origin: on the floor of a sink, at the start of an extrusion."""
    normal: np.ndarray
    """Sketch plane normal, out of the part."""
    x_dir: np.ndarray
    length_mm: float
    """How far the sketch is extruded from the plane: up along the normal for an extrusion,
    the cutter's full length for a sink."""
    surface_mm: tuple[float, float]
    """Lowest and highest surface point under the sketch, along the normal from the point."""
    note: str | None = None

    def as_dict(self, edit: Edit) -> dict:
        return {
            "type": edit.mode,
            "sketch": asdict(edit.sketch),
            "plane": {
                "origin": np.round(self.origin, 4).tolist(),
                "normal": np.round(self.normal, 6).tolist(),
                "x_dir": np.round(self.x_dir, 6).tolist(),
            },
            ("depth_mm" if edit.mode == "sink" else "height_mm"): edit.distance_mm,
            "length_mm": round(self.length_mm, 4),
            "fillet_mm": edit.fillet_mm,
        }


def _frame(normal: np.ndarray, angle_deg: float) -> tuple[np.ndarray, np.ndarray]:
    """Sketch x and y on the plane square to normal: x along the part's X projected onto the
    plane (Y where the plane faces along X), turned by angle_deg about the normal."""
    ref = np.array([1.0, 0.0, 0.0]) if abs(normal[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    x = ref - (ref @ normal) * normal
    x /= np.linalg.norm(x)
    y = np.cross(normal, x)
    a = np.radians(angle_deg)
    return np.cos(a) * x + np.sin(a) * y, -np.sin(a) * x + np.cos(a) * y


def _footprint(sketch: Sketch) -> np.ndarray:
    w = sketch.width_mm / 2
    h = w if sketch.shape == "circle" else sketch.height_mm / 2
    # A grid over the bounding box; a circle fills pi/4 of it.
    k = int(np.ceil(np.sqrt(FOOTPRINT_SAMPLES * 4 / np.pi)))
    gx, gy = np.meshgrid(np.linspace(-w, w, k), np.linspace(-h, h, k))
    grid = np.c_[gx.ravel(), gy.ravel()]
    return np.r_[grid[sketch.contains(grid)], sketch.outline(OUTLINE_SAMPLES)]


def _surface_under(
    mesh: trimesh.Trimesh, centre: np.ndarray, normal: np.ndarray, sketch: Sketch
) -> tuple[np.ndarray, np.ndarray, int]:
    """Where rays down the normal through the footprint first meet the part: the hit points,
    the area-weighted mean normal of the triangles hit, and how many rays missed."""
    x, y = _frame(normal, sketch.angle_deg)
    local = _footprint(sketch)
    reach = float(np.linalg.norm(mesh.extents)) + 1.0
    origins = centre + local[:, :1] * x + local[:, 1:] * y + reach * normal
    dirs = np.tile(-normal, (len(origins), 1))
    locs, rays, tris = mesh.ray.intersects_location(origins, dirs, multiple_hits=True)
    hits, faces = [], []
    for i in range(len(origins)):
        mine = np.flatnonzero(rays == i)
        if len(mine):
            first = mine[np.argmax(locs[mine] @ normal)]
            hits.append(locs[first])
            faces.append(tris[first])
    if not hits:
        raise UnsupportedGeometry("The sketch isn't over the part at that point.")
    faces = np.array(faces)
    mean = (mesh.face_normals[faces] * mesh.area_faces[faces, None]).sum(axis=0)
    return np.array(hits), mean / np.linalg.norm(mean), len(origins) - len(hits)


def apply_edit(solid: cq.Shape, edit: Edit) -> EditResult:
    """The solid with the sink or extrusion made. Raises UnsupportedGeometry when the edit
    misses the part or would split it in two."""
    verts, tris = solid.tessellate(RAY_MESH_TOL, angularTolerance=0.1)
    mesh = trimesh.Trimesh(np.array([v.toTuple() for v in verts]), np.array(tris))
    closest, _, tri = trimesh.proximity.closest_point(mesh, np.array([edit.point], float))
    centre, normal = closest[0], mesh.face_normals[tri[0]]
    # Twice: the normal at the point, then the mean under the footprint it gives.
    for _ in range(2):
        hits, normal, missed = _surface_under(mesh, centre, normal, edit.sketch)
    along = (hits - centre) @ normal
    low, high = float(along.min()), float(along.max())
    x_dir, _ = _frame(normal, edit.sketch.angle_deg)
    diagonal = float(np.linalg.norm(mesh.extents))

    if edit.mode == "sink":
        start = low - edit.distance_mm
        length = high - start + 1.0 + 0.01 * diagonal
    else:
        # On a flat face it starts on the face, so nothing hangs below it past an edge.
        start = low - (EMBED_MM if high - low > FLAT_MM else 0.0)
        length = high + edit.distance_mm - start
    origin = centre + start * normal
    plane = cq.Plane(cq.Vector(*origin), cq.Vector(*x_dir), cq.Vector(*normal))
    tool = edit.sketch.wire(plane).extrude(length).val()

    bodies = len(solid.Solids())
    out = (solid.cut(tool) if edit.mode == "sink" else solid.fuse(tool)).clean()
    parts = out.Solids()
    if len(parts) != bodies:
        raise UnsupportedGeometry(
            "That sink would cut the part in two; make it shallower or smaller."
            if edit.mode == "sink"
            else "That extrusion doesn't join the part; move it onto the surface."
        )
    if not all(p.isValid() for p in parts):
        raise UnsupportedGeometry("That edit gave an invalid solid here; try another spot.")
    result = parts[0] if bodies == 1 else cq.Compound.makeCompound(parts)

    note = None
    if missed and edit.mode == "extrude":
        note = "Part of the extrusion hangs past the edge of the part."
    if edit.fillet_mm > 0:
        level = start if edit.mode == "sink" else start + length
        filleted = _fillet(result, origin + (level - start) * normal, normal, edit.fillet_mm)
        if filleted is None:
            note = " ".join(
                filter(None, [note, f"The {edit.fillet_mm:g} mm fillet didn't fit; left sharp."])
            )
        else:
            result = filleted
    return EditResult(result, origin, normal, x_dir, length, (low, high), note)


def _fillet(solid: cq.Shape, at: np.ndarray, normal: np.ndarray, radius: float):
    """Round the edges of the flat face the edit made: the floor or the top, the plane square
    to the normal through at."""
    cos_flat = np.cos(np.radians(FLAT_DEG))
    faces = []
    for f in solid.Faces():
        if f.geomType() != "PLANE":
            continue
        n = np.array(f.normalAt().toTuple())
        c = np.array(f.Center().toTuple())
        if abs(n @ normal) > cos_flat and abs((c - at) @ normal) < 1e-6:
            faces.append(f)
    if not faces:
        return None
    edges = [e for f in faces for e in f.Edges()]
    try:
        out = solid.fillet(radius, edges)
    except Exception:  # OpenCASCADE raises a variety of errors here
        return None
    return out if out.isValid() else None
