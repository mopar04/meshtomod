"""Editable history: the steps that make the model, in a form CAD can make again.

A STEP file holds the finished shape only. To change one hole later, a designer needs the
part as a timeline: a base solid, then each boss, fillet, chamfer, hole and edit as its own
feature with its own size. This module turns a conversion into that timeline, as plain
operations every CAD tool has: sketch on a plane, extrude or revolve it (new, join or cut),
fillet or chamfer the edges where two planes meet.

The base is the solid before any of those features: the faces the scan was rebuilt from,
plus any freeform surface. It travels as its own STEP (base.step) and the scripts import it,
except for a single turned part or a round body, which they make from its sketch.

Everything is in millimetres in the STEP's frame. Sketches are 2D in a plane given by its
origin, x direction and normal (y is normal x x_dir). Extrusions run along the normal.
`replay` makes the same steps with CadQuery: the tests use it to prove that the steps really
rebuild the model, which is what the FreeCAD, Fusion and Onshape scripts then do.
"""

from __future__ import annotations

import re

import cadquery as cq
import numpy as np

from .convert import ConversionResult
from .features import OVERSHOOT_MM
from .orient import transform_shape
from .sink import Edit, EditResult

HISTORY_VERSION = 1
# How close a point must be to a plane to count as on it when finding edges (mm). The planes
# are written to 1e-9; the slack is for each CAD tool's own rounding.
ON_PLANE_MM = 1e-3
# A plane far enough to stand in for "through all" (mm beyond the part).
THROUGH_MM = 1.0


def _r(v) -> list[float]:
    return np.round(np.asarray(v, float), 9).tolist()


def _unit(v) -> np.ndarray:
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


def _square(normal: np.ndarray) -> np.ndarray:
    """An x direction square to normal: the part's X projected (Y if normal is along X)."""
    ref = np.array([1.0, 0.0, 0.0]) if abs(normal[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    x = ref - (ref @ normal) * normal
    return x / np.linalg.norm(x)


class _Frame:
    """The converted solid's frame to the STEP's (a rigid 4x4)."""

    def __init__(self, matrix: np.ndarray | None):
        self.m = np.eye(4) if matrix is None else np.asarray(matrix, float)

    def point(self, p) -> np.ndarray:
        return (self.m @ np.r_[np.asarray(p, float), 1.0])[:3]

    def direction(self, d) -> np.ndarray:
        return _unit(self.m[:3, :3] @ np.asarray(d, float))

    def plane(self, normal, offset: float) -> dict:
        n = self.direction(normal)
        return {"normal": _r(n), "offset": round(float(offset + n @ self.m[:3, 3]), 9)}


def _plane(origin, normal, x_dir=None) -> dict:
    n = _unit(normal)
    x = _square(n) if x_dir is None else _unit(x_dir)
    return {"origin": _r(origin), "x_dir": _r(x), "normal": _r(n)}


def _local(plane: dict, p) -> list[float]:
    o, x, n = (np.array(plane[k]) for k in ("origin", "x_dir", "normal"))
    d = np.asarray(p, float) - o
    return _r([d @ x, d @ np.cross(n, x)])


def _arc(a, m, b) -> dict:
    """A three-point arc, plus its centre and radius for tools that want those."""
    a, m, b = (np.asarray(p, float) for p in (a, m, b))
    d = 2 * (a[0] * (m[1] - b[1]) + m[0] * (b[1] - a[1]) + b[0] * (a[1] - m[1]))
    sa, sm, sb = a @ a, m @ m, b @ b
    c = (
        np.array(
            [
                sa * (m[1] - b[1]) + sm * (b[1] - a[1]) + sb * (a[1] - m[1]),
                sa * (b[0] - m[0]) + sm * (a[0] - b[0]) + sb * (m[0] - a[0]),
            ]
        )
        / d
    )
    return {
        "arc": [_r(a), _r(m), _r(b)],
        "center": _r(c),
        "radius": round(float(np.linalg.norm(a - c)), 9),
    }


def _loops(face: cq.Shape, plane: dict) -> list[list[dict]]:
    """A flat face's boundary as loops of lines and three-point arcs in the plane's 2D."""
    loops = []
    for f in face.Faces():
        if f.Area() < 1e-9:
            continue
        for wire in [f.outerWire(), *f.innerWires()]:
            loop = []
            for e in wire.Edges():
                a, b = e.startPoint(), e.endPoint()
                if e.geomType() == "CIRCLE":
                    mid = e.positionAt(0.5)
                    loop.append(_arc(*(_local(plane, p.toTuple()) for p in (a, mid, b))))
                elif (a - b).Length > 1e-9:
                    loop.append({"line": [_local(plane, a.toTuple()), _local(plane, b.toTuple())]})
            loops.append(loop)
    return loops


def _revolve_op(name: str, mode: str, profile: cq.Shape, axis_point, axis_dir, frame: _Frame):
    """Revolve a profile about an axis: the sketch's x axis is the axis, so every tool can
    revolve about its own sketch axis."""
    p0, d = frame.point(axis_point), frame.direction(axis_dir)
    moved = transform_shape(profile, frame.m)
    centre = np.array(moved.Center().toTuple())
    radial = (centre - p0) - ((centre - p0) @ d) * d
    if np.linalg.norm(radial) < 1e-9:
        radial = _square(d)
    plane = _plane(p0, np.cross(d, _unit(radial)), d)
    return {
        "op": "revolve",
        "name": name,
        "mode": mode,
        "plane": plane,
        "loops": _loops(moved, plane),
    }


def _feature_ops(result: ConversionResult, frame: _Frame) -> list[dict]:
    planes = {r.id: r.plane for r in result.regions}
    features = [f for f in result.features if f.kind != "sharp"]
    count: dict[str, int] = {}

    def name(kind: str, label: str | None = None) -> str:
        count[kind] = count.get(kind, 0) + 1
        title = f"{kind.replace('_', ' ').capitalize()} {count[kind]}"
        return f"{title} ({label})" if label else title

    ops: list[dict] = []
    # The order apply_features uses: bosses, turned shapes added, fillets, chamfers, turned
    # cuts, then holes and slots.
    for f in features:
        if f.kind != "boss":
            continue
        height = float(np.linalg.norm(f.end - f.start))
        ops.append(
            {
                "op": "extrude",
                "name": name("boss", f.label),
                "mode": "join",
                "plane": _plane(frame.point(f.start), frame.direction(f.end - f.start)),
                "sketch": {"circle": {"center": [0.0, 0.0], "diameter": round(2 * f.radius, 9)}},
                "distance": round(height, 9),
                "from_face": True,
            }
        )
    turned_added = result.start != "turned"
    for r in result.revolved:
        if r.add_profile is None or (r.kind == "turned" and not turned_added):
            continue
        ops.append(
            _revolve_op(name(r.kind), "join", r.add_profile, r.axis.point, r.axis.direction, frame)
        )
    edges = [f for f in features if f.kind in ("fillet", "chamfer")]
    for kind in ("fillet", "chamfer"):
        sizes = sorted({round(f.radius, 6) for f in edges if f.kind == kind}, reverse=True)
        for size in sizes:
            between = [
                [
                    frame.plane(planes[f.planes[0]].normal, planes[f.planes[0]].offset),
                    frame.plane(planes[f.planes[1]].normal, planes[f.planes[1]].offset),
                ]
                for f in edges
                if f.kind == kind
                and round(f.radius, 6) == size
                and f.planes[0] in planes
                and f.planes[1] in planes
            ]
            key = "radius" if kind == "fillet" else "size"
            ops.append({"op": kind, "name": name(kind), key: size, "edges": between})
    for r in result.revolved:
        if r.cut_profile is not None:
            ops.append(
                _revolve_op(
                    name(r.kind), "cut", r.cut_profile, r.axis.point, r.axis.direction, frame
                )
            )
    for f in features:
        if f.kind not in ("hole", "slot"):
            continue
        # Sketched on the face it is drilled from (at f.end), cut into the part.
        d = frame.direction(f.start - f.end)
        if f.kind == "hole":
            sketch = {"circle": {"center": [0.0, 0.0], "diameter": round(2 * f.radius, 9)}}
            plane = _plane(frame.point(f.end), d)
        else:
            # Centred between the slot's two round ends, its length along the sketch's x.
            length = float(np.linalg.norm(f.span))
            plane = _plane(frame.point(f.end + f.span / 2), d, frame.direction(f.span))
            sketch = {
                "slot": {
                    "a": [round(-length / 2, 9), 0.0],
                    "b": [round(length / 2, 9), 0.0],
                    "width": round(2 * f.radius, 9),
                }
            }
        op = {
            "op": "extrude",
            "name": name(f.kind, f.label),
            "mode": "cut",
            "plane": plane,
            "sketch": sketch,
            "distance": round(float(np.linalg.norm(f.end - f.start)), 9),
            "from_face": True,
        }
        if f.through:
            op["through"] = True
        ops.append(op)
    return ops


def _start_op(result: ConversionResult, frame: _Frame) -> dict:
    if result.start == "turned":
        turned = next(r for r in result.revolved if r.kind == "turned")
        op = _revolve_op(
            "Turned body",
            "new",
            turned.add_profile,
            turned.axis.point,
            turned.axis.direction,
            frame,
        )
        return op
    if result.start == "body":
        body = next(f for f in result.features if f.kind == "body")
        d = frame.direction(body.end - body.start)
        height = float(np.linalg.norm(body.end - body.start))
        return {
            "op": "extrude",
            "name": "Round body",
            "mode": "new",
            "plane": _plane(frame.point(body.start), d),
            "sketch": {"circle": {"center": [0.0, 0.0], "diameter": round(2 * body.radius, 9)}},
            "distance": round(height, 9),
        }
    return {"op": "base", "name": "Base", "file": "base.step"}


def _edit_ops(edits, frame: _Frame) -> list[dict]:
    ops = []
    for i, (edit, done) in enumerate(edits, 1):
        edit: Edit
        done: EditResult
        normal = frame.direction(done.normal)
        plane = _plane(frame.point(done.origin), normal, frame.direction(done.x_dir))
        s = edit.sketch
        if s.shape == "circle":
            sketch = {"circle": {"center": [0.0, 0.0], "diameter": s.width_mm}}
        elif s.shape == "slot":
            half = (s.width_mm - s.height_mm) / 2
            sketch = {"slot": {"a": [-half, 0.0], "b": [half, 0.0], "width": s.height_mm}}
        else:
            r = s.corner_radius_mm if s.shape == "rounded_rectangle" else 0.0
            sketch = {
                "rect": {
                    "center": [0.0, 0.0],
                    "width": s.width_mm,
                    "height": s.height_mm,
                    "corner_radius": r,
                }
            }
        sink = edit.mode == "sink"
        ops.append(
            {
                "op": "extrude",
                "name": f"{'Sink' if sink else 'Extrude'} {i}",
                "mode": "cut" if sink else "join",
                "plane": plane,
                "sketch": sketch,
                "distance": round(done.length_mm, 9),
            }
        )
        if edit.fillet_mm > 0:
            level = frame.point(done.origin) + (0.0 if sink else done.length_mm) * normal
            ops.append(
                {
                    "op": "fillet",
                    "name": f"{'Sink' if sink else 'Extrude'} {i} fillet",
                    "radius": edit.fillet_mm,
                    "face": {"point": _r(level), "normal": _r(normal)},
                }
            )
    return ops


def _trim_op(export_solid: cq.Shape, up: str) -> dict:
    """Trim to sit flush: cut everything below the base plane, which is where the placed
    solid's bottom is."""
    bb = export_solid.BoundingBox()
    k = 1 if up == "y" else 2
    down = np.zeros(3)
    down[k] = -1.0
    low = (bb.ymin, bb.zmin)[k - 1]
    origin = np.array([bb.center.x, bb.center.y, bb.center.z])
    origin[k] = low
    size = 2 * bb.DiagonalLength + 2.0
    return {
        "op": "extrude",
        "name": "Trim flush",
        "mode": "cut",
        "plane": _plane(origin, down),
        "sketch": {
            "rect": {"center": [0.0, 0.0], "width": size, "height": size, "corner_radius": 0.0}
        },
        "distance": round(bb.DiagonalLength + 1.0, 9),
    }


def build_history(
    result: ConversionResult,
    edits: list[tuple[Edit, EditResult]] = (),
    matrix: np.ndarray | None = None,
    trim: tuple[cq.Shape, str] | None = None,
) -> tuple[dict, cq.Shape | None]:
    """The history and the base solid to save as base.step (None when the history makes
    its own). matrix (4x4) takes the converted solid's frame to the STEP's, as for the
    recipe. trim is (the placed STEP solid, its up axis) when the part was trimmed flush."""
    frame = _Frame(matrix)
    first = _start_op(result, frame)
    ops = [first, *_feature_ops(result, frame), *_edit_ops(edits, frame)]
    if trim is not None:
        ops.append(_trim_op(*trim))
    for op in ops:
        # A short name for the op's parameters in CAD: "Hole 2 (M6 tap drill)" is hole_2.
        op["id"] = re.sub(r"[^a-z0-9]+", "_", op["name"].split(" (")[0].lower()).strip("_")
    base = None
    if first["op"] == "base":
        source = result.base if result.base is not None else result.solid
        base = transform_shape(source, frame.m)
    size = (base if base is not None else transform_shape(result.solid, frame.m)).BoundingBox()
    history = {
        "version": HISTORY_VERSION,
        "units": "mm",
        "size": round(size.DiagonalLength, 6),
        "ops": ops,
    }
    return history, base


# ---------------------------------------------------------------------------------------------
# Replay with CadQuery: the reference the scripts follow
# ---------------------------------------------------------------------------------------------


def cq_plane(plane: dict) -> cq.Plane:
    return cq.Plane(
        origin=cq.Vector(*plane["origin"]),
        xDir=cq.Vector(*plane["x_dir"]),
        normal=cq.Vector(*plane["normal"]),
    )


def sketch_face(plane: dict, sketch: dict) -> cq.Workplane:
    wp = cq.Workplane(cq_plane(plane))
    if "circle" in sketch:
        c = sketch["circle"]
        return wp.center(*c["center"]).circle(c["diameter"] / 2)
    if "slot" in sketch:
        s = sketch["slot"]
        a, b = np.array(s["a"]), np.array(s["b"])
        mid, run = (a + b) / 2, b - a
        angle = float(np.degrees(np.arctan2(run[1], run[0])))
        length = float(np.linalg.norm(run)) + s["width"]
        return wp.center(*mid).slot2D(length, s["width"], angle)
    if "rect" in sketch:
        r = sketch["rect"]
        wp = wp.center(*r["center"])
        if r["corner_radius"] > 0:
            shape = cq.Sketch().rect(r["width"], r["height"]).vertices().fillet(r["corner_radius"])
            return wp.placeSketch(shape)
        return wp.rect(r["width"], r["height"])
    raise ValueError(f"Unknown sketch {sorted(sketch)}")


def loops_face(plane: dict, loops: list[list[dict]]) -> cq.Shape:
    p = cq_plane(plane)

    def at(uv) -> cq.Vector:
        return p.toWorldCoords((uv[0], uv[1]))

    wires = []
    for loop in loops:
        edges = []
        for seg in loop:
            if "line" in seg:
                edges.append(cq.Edge.makeLine(*(at(q) for q in seg["line"])))
            else:
                edges.append(cq.Edge.makeThreePointArc(*(at(q) for q in seg["arc"])))
        wires.append(cq.Wire.assembleEdges(edges))
    # Outer loops hold the inner ones; each face is an outer loop and the holes inside it.
    faces = []
    by_area = sorted(wires, key=lambda w: -cq.Face.makeFromWires(w).Area())
    used: set[int] = set()
    for i, outer in enumerate(by_area):
        if i in used:
            continue
        outer_face = cq.Face.makeFromWires(outer)
        inner = []
        for j in range(i + 1, len(by_area)):
            if j in used:
                continue
            probe = cq.Face.makeFromWires(by_area[j]).Center()
            if outer_face.isInside(probe):
                inner.append(by_area[j])
                used.add(j)
        faces.append(cq.Face.makeFromWires(outer, inner))
    return faces[0] if len(faces) == 1 else cq.Compound.makeCompound(faces)


def edges_between(solid: cq.Shape, a: dict, b: dict, tol: float = ON_PLANE_MM) -> list[cq.Edge]:
    """Edges along the line where two planes meet: both ends on both planes, and running
    along that line. What every script searches for before a fillet or chamfer."""
    na, nb = np.array(a["normal"]), np.array(b["normal"])
    line = np.cross(na, nb)
    if np.linalg.norm(line) < 1e-9:
        return []
    line /= np.linalg.norm(line)
    out = []
    for e in solid.Edges():
        pts = np.array([e.startPoint().toTuple(), e.endPoint().toTuple()])
        if np.abs(pts @ na - a["offset"]).max() > tol or np.abs(pts @ nb - b["offset"]).max() > tol:
            continue
        run = pts[1] - pts[0]
        if np.linalg.norm(run) > 10 * tol and abs(run @ line) > 0.99 * np.linalg.norm(run):
            out.append(e)
    return out


def face_edges(solid: cq.Shape, point, normal, tol: float = ON_PLANE_MM) -> list[cq.Edge]:
    """Edges of the flat faces square to normal through point: a sink's floor or an
    extrusion's top."""
    n, p = np.array(normal), np.array(point)
    out = []
    for f in solid.Faces():
        if f.geomType() != "PLANE":
            continue
        fn = np.array(f.normalAt().toTuple())
        if abs(fn @ n) > 0.9999 and abs((np.array(f.Center().toTuple()) - p) @ n) < tol:
            out += f.Edges()
    return out


def _combine(solid: cq.Shape | None, tool: cq.Shape, mode: str) -> cq.Shape:
    if mode == "new" or solid is None:
        return tool
    out = (solid.fuse(tool) if mode == "join" else solid.cut(tool)).clean()
    parts = out.Solids()
    if len(parts) != len(solid.Solids()) or not all(p.isValid() for p in parts):
        raise ValueError("would split, merge or break the part")
    return parts[0] if len(parts) == 1 else cq.Compound.makeCompound(parts)


def replay(history: dict, base: cq.Shape | None = None) -> tuple[cq.Shape, list[str]]:
    """Make the history with CadQuery: the solid and the names of steps that failed (left
    out, as the scripts do)."""
    solid = base
    failed: list[str] = []
    for op in history["ops"]:
        try:
            if op["op"] == "base":
                solid = base
            elif op["op"] == "extrude":
                plane, distance = op["plane"], op["distance"]
                if op.get("from_face"):
                    # CAD tools take a sketch on a face; OpenCASCADE's booleans are surer with
                    # some overlap, so start inside the part (and go on past a through hole).
                    n = np.array(plane["normal"])
                    plane = {**plane, "origin": _r(np.array(plane["origin"]) - OVERSHOOT_MM * n)}
                    distance += OVERSHOOT_MM * (2 if op.get("through") else 1)
                tool = sketch_face(plane, op["sketch"]).extrude(distance).val()
                solid = _combine(solid, tool, op["mode"])
            elif op["op"] == "revolve":
                p = op["plane"]
                face = loops_face(p, op["loops"])
                o, x = cq.Vector(*p["origin"]), cq.Vector(*p["x_dir"])
                tools = [cq.Solid.revolve(f, 360.0, o, o + x) for f in face.Faces()]
                tool = tools[0]
                for t in tools[1:]:
                    tool = tool.fuse(t).clean()
                solid = _combine(solid, tool, op["mode"])
            elif op["op"] in ("fillet", "chamfer"):
                if "face" in op:
                    edges = face_edges(solid, op["face"]["point"], op["face"]["normal"])
                else:
                    edges = [e for a, b in op["edges"] for e in edges_between(solid, a, b)]
                if not edges:
                    raise ValueError("no edges found")
                if op["op"] == "fillet":
                    out = solid.fillet(op["radius"], edges)
                else:
                    out = solid.chamfer(op["size"], None, edges)
                if not all(p.isValid() for p in out.Solids()):
                    raise ValueError("invalid")
                solid = out
        except Exception:  # OpenCASCADE raises a variety of errors; the step is left out
            failed.append(op["name"])
    return solid, failed
