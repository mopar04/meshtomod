"""Placement before export: lay the part on a face and move it onto the origin.

Scans come off the scanner at whatever angle and position the turntable left them. Like the
"lay on face" tool in Bambu Studio and OrcaSlicer, we offer the flat faces the part could rest
on (faces of its convex hull it would sit stably on), the user picks one, and the export is
turned so that face points straight down. The part is then squared up about the vertical so
its walls run along X and Y, and moved so a chosen point (box centre, bottom centre or the
low corner) sits on the origin. CAD tools differ on which way is up: Onshape, FreeCAD and
Shapr3D are Z up; SolidWorks (and Fusion set to Y up) are Y up, so the base lands on XZ.

Frames used here:
- scan: the uploaded mesh, in millimetres;
- view: Z up, laid flat and anchored. The viewer, model.stl and deviation.ply use it;
- export: the view frame turned for the chosen up axis. Only the STEP uses it.
"""

from __future__ import annotations

from dataclasses import dataclass

import cadquery as cq
import numpy as np
import trimesh
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.gp import gp_Trsf
from scipy.spatial import ConvexHull, QhullError

ORIGINS = ("keep", "center", "bottom", "corner")
UP_AXES = ("z", "y")
DOWN = np.array([0.0, 0.0, -1.0])

# Hull triangles within this angle and offset of each other are one resting face. Scan noise
# breaks a flat face's hull into many slightly tilted triangles.
FACE_ANGLE_DEG = 4.0
FACE_OFFSET_SHARE = 0.005
MIN_FACE_SHARE = 0.01
MAX_CANDIDATES = 8
# After converting, the model's own bottom face and walls are made exact when this close.
SNAP_DEG = 3.0

# Y up: the scan's up (Z) becomes Y, and the base lands on the XZ plane.
Z_TO_Y = np.array(
    [[1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, -1.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0]]
)


@dataclass
class RestingFace:
    normal: np.ndarray
    """Outward, in scan coordinates: the direction that ends up pointing down."""
    area_mm2: float
    triangles: np.ndarray
    """(n, 3, 3) hull triangles, in scan coordinates, for the viewer to highlight."""
    rotation: np.ndarray
    """3x3 scan-to-view rotation: this face down, walls along X and Y."""

    def as_dict(self) -> dict:
        return {
            "normal": [round(float(v), 6) for v in self.normal],
            "area_mm2": round(self.area_mm2, 1),
            "rotation": np.round(self.rotation, 9).tolist(),
            "triangles": np.round(self.triangles.reshape(-1), 3).tolist(),
        }


def resting_faces(mesh: trimesh.Trimesh, limit: int = MAX_CANDIDATES) -> list[RestingFace]:
    """The flat faces the part could lie on, biggest first.

    Faces of the convex hull, grouped so a noisy scanned face counts as one, kept only if the
    part's centre of mass falls over the face (so it would not tip over), as slicers do.
    """
    try:
        hull = mesh.convex_hull
    except (QhullError, ValueError):
        return []
    if len(hull.faces) < 4:
        return []
    centre = mesh.center_mass if mesh.is_volume else mesh.centroid
    normals = hull.face_normals
    areas = hull.area_faces
    offsets = np.einsum("ij,ij->i", normals, hull.triangles_center)
    max_offset = FACE_OFFSET_SHARE * float(np.linalg.norm(mesh.extents))
    cos_angle = np.cos(np.radians(FACE_ANGLE_DEG))
    free = np.ones(len(normals), dtype=bool)
    faces: list[RestingFace] = []
    for seed in np.argsort(-areas):
        if not free[seed]:
            continue
        same = (normals @ normals[seed] > cos_angle) & (abs(offsets - offsets[seed]) < max_offset)
        group = np.flatnonzero(free & same)
        free[group] = False
        area = float(areas[group].sum())
        if area < MIN_FACE_SHARE * hull.area:
            continue
        normal = (normals[group] * areas[group, None]).sum(axis=0)
        normal /= np.linalg.norm(normal)
        tris = hull.triangles[group]
        if not _over_face(centre, normal, tris.reshape(-1, 3)):
            continue
        faces.append(RestingFace(normal, area, tris, lay_flat_rotation(mesh, normal)))
    faces.sort(key=lambda f: -f.area_mm2)
    return faces[:limit]


def _over_face(centre: np.ndarray, normal: np.ndarray, points: np.ndarray) -> bool:
    """Does the centre of mass, dropped along the normal, land on the face?"""
    u, v = _plane_axes(normal)
    flat = np.column_stack([points @ u, points @ v])
    try:
        outline = ConvexHull(flat)
    except QhullError:
        return False
    p = np.array([centre @ u, centre @ v, 1.0])
    margin = 1e-6 * float(np.ptp(flat, axis=0).max())
    return bool((outline.equations @ p <= margin).all())


def _plane_axes(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    helper = np.array([1.0, 0.0, 0.0]) if abs(normal[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(normal, helper)
    u /= np.linalg.norm(u)
    return u, np.cross(normal, u)


def rotation_between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """The smallest rotation taking unit vector a onto unit vector b."""
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    axis = np.cross(a, b)
    s, c = float(np.linalg.norm(axis)), float(a @ b)
    if s < 1e-12:
        if c > 0:
            return np.eye(3)
        u, _ = _plane_axes(a)  # opposite: half a turn about any perpendicular axis
        return 2.0 * np.outer(u, u) - np.eye(3)
    k = axis / s
    kx = np.array([[0.0, -k[2], k[1]], [k[2], 0.0, -k[0]], [-k[1], k[0], 0.0]])
    return np.eye(3) + s * kx + (1.0 - c) * kx @ kx


def yaw(angle: float) -> np.ndarray:
    c, s = np.cos(angle), np.sin(angle)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def lay_flat_rotation(mesh: trimesh.Trimesh, normal: np.ndarray) -> np.ndarray:
    """Turn the face with this outward normal straight down, then about the vertical so the
    footprint's smallest bounding rectangle lines up with X and Y, its long side along X."""
    level = rotation_between(np.asarray(normal, dtype=float), DOWN)
    points = mesh.convex_hull.vertices @ level.T
    to_rect, extents = trimesh.bounds.oriented_bounds_2D(points[:, :2])
    angle = float(np.arctan2(to_rect[1, 0], to_rect[0, 0]))
    if extents[0] < extents[1]:
        angle += np.pi / 2
    return yaw(angle) @ level


def homogeneous(rotation: np.ndarray, translation=(0.0, 0.0, 0.0)) -> np.ndarray:
    m = np.eye(4)
    m[:3, :3] = rotation
    m[:3, 3] = translation
    return m


def transform_shape(shape: cq.Shape, matrix: np.ndarray) -> cq.Shape:
    """A rigid transform of a B-rep (rotation and translation only)."""
    t = gp_Trsf()
    t.SetValues(*matrix[:3, :4].ravel().tolist())
    return cq.Shape.cast(BRepBuilderAPI_Transform(shape.wrapped, t, True).Shape())


def true_up(solid: cq.Shape) -> np.ndarray:
    """A small rotation that makes the model's bottom face exactly level and its biggest wall
    exactly square to X or Y, when they are already within a few degrees (as after laying the
    scan flat). Scan noise leaves them a fraction of a degree off otherwise."""
    planes = [f for f in solid.Faces() if f.geomType() == "PLANE"]
    if not planes:
        return np.eye(3)
    cos_snap = np.cos(np.radians(SNAP_DEG))
    zmin = solid.BoundingBox().zmin
    span = max(solid.BoundingBox().zlen, 1e-9)
    info = [(np.array(f.normalAt().toTuple()), f.Area(), f.Center().z) for f in planes]
    down = [(n, a, z) for n, a, z in info if n @ DOWN > cos_snap]
    rotation = np.eye(3)
    if down:
        # The resting face is the biggest of the down-facing faces near the bottom.
        low = [d for d in down if d[2] - zmin < 0.05 * span] or down
        n = max(low, key=lambda d: d[1])[0]
        rotation = rotation_between(n, DOWN)
    walls = [(rotation @ n, a) for n, a, _ in info if abs(n[2]) < np.sin(np.radians(SNAP_DEG))]
    if walls:
        n = max(walls, key=lambda w: w[1])[0]
        angle = float(np.arctan2(n[1], n[0]))
        off = (angle + np.pi / 4) % (np.pi / 2) - np.pi / 4
        if abs(off) < np.radians(SNAP_DEG):
            rotation = yaw(-off) @ rotation
    return rotation


def anchor_offset(bounds: np.ndarray, origin: str, up: str) -> np.ndarray:
    """The translation putting the chosen point of an export-frame bounding box (2x3) on the
    origin: the box centre, the centre of its bottom, or its low corner."""
    if origin == "keep":
        return np.zeros(3)
    lo, hi = bounds
    point = (lo + hi) / 2
    vertical = {"z": 2, "y": 1}[up]
    if origin == "bottom":
        point[vertical] = lo[vertical]
    elif origin == "corner":
        point = lo.copy()
    return -point


def trim_flush(solid: cq.Shape, allowance_mm: float) -> tuple[cq.Shape, float, str | None]:
    """Cut away whatever pokes up to allowance_mm below the base (the biggest level, down-facing
    face), so the whole face sits flush on the bed instead of rocking on a bump or a stray end.
    Returns the solid, the depth cut, and a note when there was more below than allowed."""
    cos_level = np.cos(np.radians(0.1))
    base = [
        f
        for f in solid.Faces()
        if f.geomType() == "PLANE" and np.array(f.normalAt().toTuple()) @ DOWN > cos_level
    ]
    if not base:
        return solid, 0.0, "Trim to sit flush needs a flat base face; nothing was trimmed."
    z0 = max(base, key=lambda f: f.Area()).Center().z
    bb = solid.BoundingBox()
    depth = z0 - bb.zmin
    if depth < 1e-6:
        return solid, 0.0, None
    if depth > allowance_mm + 1e-9:
        return (
            solid,
            0.0,
            f"Not trimmed flush: material goes {depth:.2f} mm below the base face, more than "
            f"the {allowance_mm:g} mm allowed.",
        )
    pad = 1.0 + bb.DiagonalLength
    cutter = cq.Solid.makeBox(
        bb.xlen + 2 * pad,
        bb.ylen + 2 * pad,
        depth + pad,
        cq.Vector(bb.xmin - pad, bb.ymin - pad, z0 - depth - pad),
    )
    trimmed = solid.cut(cutter).clean()
    if not trimmed.isValid() or not trimmed.Faces():
        return solid, 0.0, "Trimming flush failed on this model; it was left as converted."
    return trimmed, depth, None


@dataclass
class Placement:
    view: np.ndarray
    """4x4, scan frame to view frame (Z up). What the viewer applies to the scan."""
    export: np.ndarray
    """4x4, scan frame to the STEP's frame."""
    view_solid: cq.Shape
    export_solid: cq.Shape
    origin: str
    up: str
    trimmed_mm: float = 0.0
    note: str | None = None

    def as_dict(self) -> dict:
        return {
            "origin": self.origin,
            "up": self.up,
            "matrix": np.round(self.view, 9).tolist(),
            "trimmed_mm": round(self.trimmed_mm, 4),
        }


def check_options(origin: str, up: str, trim_mm: float = 0.0) -> None:
    if origin not in ORIGINS:
        raise ValueError(f"Unknown origin {origin!r}; expected one of {list(ORIGINS)}")
    if up not in UP_AXES:
        raise ValueError(f"Unknown up axis {up!r}; expected one of {list(UP_AXES)}")
    if not 0.0 <= trim_mm <= 50.0:
        raise ValueError("Trim to sit flush must be between 0 and 50 mm")


def place(
    solid: cq.Shape, laid: np.ndarray | None, origin: str, up: str, trim_mm: float = 0.0
) -> Placement:
    """Where the converted solid goes. laid is the 3x3 rotation the scan was turned by before
    converting (None if it was converted as scanned); the solid is in that turned frame.
    trim_mm (only when laid flat) cuts bumps up to that deep off the base face."""
    check_options(origin, up, trim_mm)
    laid_m = homogeneous(np.eye(3) if laid is None else laid)
    turn = np.eye(4) if laid is None else homogeneous(true_up(solid))
    level = transform_shape(solid, turn)
    trimmed, note = 0.0, None
    if laid is not None and trim_mm > 0:
        level, trimmed, note = trim_flush(level, trim_mm)
    to_export = Z_TO_Y if up == "y" else np.eye(4)
    bb = transform_shape(level, to_export).BoundingBox()
    bounds = np.array([[bb.xmin, bb.ymin, bb.zmin], [bb.xmax, bb.ymax, bb.zmax]])
    shift = homogeneous(np.eye(3), anchor_offset(bounds, origin, up)) @ to_export
    view_shift = np.linalg.inv(to_export) @ shift
    return Placement(
        view=view_shift @ turn @ laid_m,
        export=shift @ turn @ laid_m,
        view_solid=transform_shape(level, view_shift),
        export_solid=transform_shape(level, shift),
        origin=origin,
        up=up,
        trimmed_mm=trimmed,
        note=note,
    )
