"""The feature recipe: what the model is made of, step by step, as JSON beside the STEP.

A STEP file holds shapes only, never how they were made. The recipe lists the steps a
designer would have used, in the order apply_features uses them (bosses and turned shapes
added, then fillets and chamfers, then holes and turned cuts), each with the positions and
sizes CAD needs to make it again, then the user's own sinks and extrusions in the order
they made them. Every position is in the STEP's frame (after placement).

Steps marked "replay": true can be made again on the imported STEP as ordinary CAD
operations (a sketch and an extrude): the user's edits. The others describe what the
conversion found, which the STEP already contains; M4 turns them into a parametric history.
"""

from __future__ import annotations

import numpy as np

from .convert import ConversionResult
from .revolve import Arc2, Revolved
from .sink import Edit, EditResult

RECIPE_VERSION = 1
ADDED = ("boss",)
EDGES = ("fillet", "chamfer")
CUT = ("hole", "slot")


class _Frame:
    """Moves points, directions and planes from the converted solid's frame to the STEP's."""

    def __init__(self, matrix: np.ndarray | None):
        self.m = np.eye(4) if matrix is None else np.asarray(matrix, float)

    def point(self, p) -> list[float]:
        return np.round((self.m @ np.r_[np.asarray(p, float), 1.0])[:3], 4).tolist()

    def direction(self, d) -> list[float]:
        d = self.m[:3, :3] @ np.asarray(d, float)
        return np.round(d / np.linalg.norm(d), 6).tolist()

    def plane(self, normal, offset: float) -> dict:
        n = self.m[:3, :3] @ np.asarray(normal, float)
        return {
            "normal": np.round(n, 6).tolist(),
            "offset_mm": round(float(offset + n @ self.m[:3, 3]), 4),
        }


def _axis(frame: _Frame, point, direction) -> dict:
    return {"point": frame.point(point), "direction": frame.direction(direction)}


def _feature(f, planes: dict, frame: _Frame) -> dict:
    step = f.as_dict()
    if f.kind in EDGES:
        step["between"] = [
            frame.plane(planes[k].normal, planes[k].offset) for k in f.planes if k in planes
        ]
        return step
    # From where it starts to where it ends: a boss from its base up, a hole from its far
    # end back to the face it is drilled from, so either way the axis points out of the part.
    step["axis"] = _axis(frame, f.start, f.end - f.start)
    step["start"] = frame.point(f.start)
    step["end"] = frame.point(f.end)
    if f.kind == "slot":
        step["span"] = [frame.point(f.start), frame.point(f.start + f.span)]
    return step


def _revolved(r: Revolved, frame: _Frame) -> dict:
    """A turned shape: its axis and the outline revolved about it, drawn in a plane through
    the axis (x_dir away from it), as lines and three-point arcs."""
    d = r.axis.direction / np.linalg.norm(r.axis.direction)
    ref = np.array([1.0, 0.0, 0.0]) if abs(d[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    x = ref - (ref @ d) * d
    x /= np.linalg.norm(x)

    def at(q) -> list[float]:
        return frame.point(r.axis.point + q[0] * d + q[1] * x)

    out = r.outline
    profile = []
    for i, piece in enumerate(out.pieces):
        # Piece i runs from corner i to the next (an open outline has one more corner).
        a, b = out.points[i], out.points[(i + 1) % len(out.points)]
        if isinstance(piece, Arc2) and out.mids[i] is not None:
            profile.append({"arc": [at(a), at(out.mids[i]), at(b)]})
        else:
            profile.append({"line": [at(a), at(b)]})
    return {
        **r.as_dict(),
        "axis": _axis(frame, r.axis.point, d),
        "sketch_plane": {
            "origin": frame.point(r.axis.point),
            "x_dir": frame.direction(x),
            "normal": frame.direction(np.cross(d, x)),
        },
        "profile": profile,
        "closed": out.closed,
        "operation": "add" if r.add is not None and r.cut is None else "cut",
    }


def _edit(edit: Edit, result: EditResult, frame: _Frame) -> dict:
    step = result.as_dict(edit)
    plane = step["plane"]
    step["plane"] = {
        "origin": frame.point(plane["origin"]),
        "normal": frame.direction(plane["normal"]),
        "x_dir": frame.direction(plane["x_dir"]),
    }
    step["replay"] = True
    return step


def build_recipe(
    result: ConversionResult,
    edits: list[tuple[Edit, EditResult]] = (),
    matrix: np.ndarray | None = None,
) -> dict:
    """The recipe for a converted part and the user's edits to it. matrix (4x4) takes the
    converted solid's frame to the STEP's: a placement's export matrix times the inverse of
    the lay-flat turn, or None if the STEP is in the solid's frame."""
    frame = _Frame(matrix)
    planes = {r.id: r.plane for r in result.regions}
    features = [f for f in result.features if f.kind != "sharp"]
    steps: list[dict] = []
    if result.skin is not None:
        steps.append({**result.skin.as_dict(), "replay": False})
    if result.shell is not None:
        steps.append({**result.shell.as_dict(), "replay": False})
    turned = [r for r in result.revolved if r.kind == "turned"]
    steps += [{**_revolved(r, frame), "replay": False} for r in turned]
    steps += [{**_feature(f, planes, frame), "replay": False} for f in features if f.kind == "body"]
    steps += [{**_feature(f, planes, frame), "replay": False} for f in features if f.kind in ADDED]
    for r in result.revolved:
        if r.kind != "turned" and r.add is not None and r.cut is None:
            steps.append({**_revolved(r, frame), "replay": False})
    steps += [{**f.as_dict(), "replay": False} for f in result.freeform]
    steps += [{**_feature(f, planes, frame), "replay": False} for f in features if f.kind in EDGES]
    steps += [{**_feature(f, planes, frame), "replay": False} for f in features if f.kind in CUT]
    for r in result.revolved:
        if r.kind != "turned" and not (r.add is not None and r.cut is None):
            steps.append({**_revolved(r, frame), "replay": False})
    steps += [_edit(edit, done, frame) for edit, done in edits]
    return {"version": RECIPE_VERSION, "units": "mm", "steps": steps}
