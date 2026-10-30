# MeshToMod editable history for FreeCAD (1.0 or newer).
#
# In FreeCAD, File > Open this file (keep base.step beside it if there is one), then press
# Execute macro (the green triangle in the Macro toolbar). It builds a PartDesign Body with
# one feature per step: the base, then each boss, fillet, chamfer, hole and edit. Change a
# hole by double-clicking its sketch (the diameter) or its Pocket (the depth); everything
# after it updates.
#
# Made by MeshToMod (https://meshtomod.com). AGPL-3.0.

import json
import math
import os

import FreeCAD as App
import Part
import Sketcher

HISTORY = json.loads("__HISTORY__")
NAME = "__NAME__"
# How close a point must be to a plane to count as on it (mm).
ON_PLANE = 1e-3

V = App.Vector


def place(plane, flip=False):
    """The sketch placement for a plane. flip turns the sketch over: a Pocket cuts against
    the sketch's normal, so a cut along the plane's normal is sketched upside down."""
    o, x, n = (V(*plane[k]) for k in ("origin", "x_dir", "normal"))
    if flip:
        n = -n
    return App.Placement(o, App.Rotation(x, n.cross(x), n, "ZXY"))


def local(uv, flip):
    # Flipped sketches have their y the other way round.
    return V(uv[0], -uv[1] if flip else uv[1], 0)


class Builder:
    def __init__(self, doc, folder):
        self.doc = doc
        self.folder = folder
        self.body = doc.addObject("PartDesign::Body", "Body")
        if hasattr(self.body, "AllowCompound"):
            self.body.AllowCompound = True
        self.xy = next(f for f in self.body.Origin.OriginFeatures if f.Role == "XY_Plane")

    def sketch(self, op, flip=False):
        sk = self.body.newObject("Sketcher::SketchObject", op["id"] + "_sketch")
        sk.Label = op["name"] + " sketch"
        try:
            sk.AttachmentSupport = [(self.xy, "")]
        except AttributeError:  # FreeCAD before 1.0
            sk.Support = [(self.xy, "")]
        sk.MapMode = "FlatFace"
        sk.AttachmentOffset = place(op["plane"], flip)
        return sk

    def draw(self, sk, shape, flip):
        if "circle" in shape:
            c = shape["circle"]
            i = sk.addGeometry(Part.Circle(local(c["center"], flip), V(0, 0, 1), c["diameter"] / 2))
            n = sk.addConstraint(Sketcher.Constraint("Diameter", i, c["diameter"]))
            sk.renameConstraint(n, "diameter")
        elif "slot" in shape:
            s = shape["slot"]
            a, b, r = s["a"], s["b"], s["width"] / 2
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = (dx * dx + dy * dy) ** 0.5
            px, py = -dy / length * r, dx / length * r
            self.loop(
                sk,
                [
                    {"line": [[a[0] + px, a[1] + py], [b[0] + px, b[1] + py]]},
                    {
                        "arc": [
                            [b[0] + px, b[1] + py],
                            [b[0] + dx / length * r, b[1] + dy / length * r],
                            [b[0] - px, b[1] - py],
                        ]
                    },
                    {"line": [[b[0] - px, b[1] - py], [a[0] - px, a[1] - py]]},
                    {
                        "arc": [
                            [a[0] - px, a[1] - py],
                            [a[0] - dx / length * r, a[1] - dy / length * r],
                            [a[0] + px, a[1] + py],
                        ]
                    },
                ],
                flip,
            )
        elif "rect" in shape:
            self.loop(sk, rect_loop(shape["rect"]), flip)
        sk.recompute()

    def loop(self, sk, segments, flip):
        for seg in segments:
            if "line" in seg:
                p, q = seg["line"]
                sk.addGeometry(Part.LineSegment(local(p, flip), local(q, flip)))
            else:
                sk.addGeometry(arc(*(local(p, flip) for p in seg["arc"])))

    def run(self, op):
        kind = op["op"]
        if kind == "base":
            path = os.path.join(self.folder, op["file"])
            shape = Part.read(path)
            base = self.doc.addObject("Part::Feature", "Base")
            base.Label = "Base (from MeshToMod)"
            base.Shape = shape.Solids[0] if len(shape.Solids) == 1 else shape
            self.body.BaseFeature = base
            return base
        if kind == "extrude":
            cut = op["mode"] == "cut"
            sk = self.sketch(op, flip=cut)
            self.draw(sk, op["sketch"], cut)
            feat = self.body.newObject("PartDesign::Pocket" if cut else "PartDesign::Pad", op["id"])
            feat.Profile = sk
            if op.get("through"):
                feat.Type = "ThroughAll"
            else:
                feat.Type = "Length"
                feat.Length = op["distance"]
            sk.Visibility = False
            return feat
        if kind == "revolve":
            sk = self.sketch(op)
            self.loop(sk, [seg for loop in op["loops"] for seg in loop], False)
            sk.recompute()
            cut = op["mode"] == "cut"
            feat = self.body.newObject(
                "PartDesign::Groove" if cut else "PartDesign::Revolution", op["id"]
            )
            feat.Profile = sk
            # The sketch's x axis is the axis of turning.
            feat.ReferenceAxis = (sk, ["H_Axis"])
            feat.Angle = 360.0
            sk.Visibility = False
            return feat
        if kind in ("fillet", "chamfer"):
            self.doc.recompute()
            tip = self.body.Tip
            if "face" in op:
                found = face_edges(tip.Shape, op["face"]["point"], op["face"]["normal"])
            else:
                found = sorted({i for a, b in op["edges"] for i in edges_between(tip.Shape, a, b)})
            if not found:
                raise RuntimeError("no edges found")
            feat = self.body.newObject(
                "PartDesign::Fillet" if kind == "fillet" else "PartDesign::Chamfer", op["id"]
            )
            feat.Base = (tip, [f"Edge{i + 1}" for i in found])
            if kind == "fillet":
                feat.Radius = op["radius"]
            else:
                feat.Size = op["size"]
            return feat
        raise RuntimeError("unknown step " + kind)


def arc(p, m, q):
    """The arc from p through m to q, counter-clockwise as the sketcher wants it."""
    ax, ay, bx, by, cx, cy = p.x, p.y, m.x, m.y, q.x, q.y
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    ux = (
        (ax * ax + ay * ay) * (by - cy)
        + (bx * bx + by * by) * (cy - ay)
        + (cx * cx + cy * cy) * (ay - by)
    ) / d
    uy = (
        (ax * ax + ay * ay) * (cx - bx)
        + (bx * bx + by * by) * (ax - cx)
        + (cx * cx + cy * cy) * (bx - ax)
    ) / d
    centre = V(ux, uy, 0)
    angle = [math.atan2(v.y - uy, v.x - ux) for v in (p, m, q)]
    sweep = [(t - angle[0]) % (2 * math.pi) for t in angle]
    if sweep[1] < sweep[2]:  # p to q counter-clockwise passes m
        start, end = angle[0], angle[0] + sweep[2]
    else:
        start, end = angle[2], angle[2] + 2 * math.pi - sweep[2]
    circle = Part.Circle(centre, V(0, 0, 1), (p - centre).Length)
    return Part.ArcOfCircle(circle, start, end)


def rect_loop(r):
    cx, cy = r["center"]
    w, h, k = r["width"] / 2, r["height"] / 2, r["corner_radius"]
    if k <= 0:
        pts = [[cx - w, cy - h], [cx + w, cy - h], [cx + w, cy + h], [cx - w, cy + h]]
        return [{"line": [pts[i], pts[(i + 1) % 4]]} for i in range(4)]
    d = k * (1 - 0.5**0.5)  # from the corner to the arc's middle, along each side
    return [
        {"line": [[cx - w + k, cy - h], [cx + w - k, cy - h]]},
        {"arc": [[cx + w - k, cy - h], [cx + w - d, cy - h + d], [cx + w, cy - h + k]]},
        {"line": [[cx + w, cy - h + k], [cx + w, cy + h - k]]},
        {"arc": [[cx + w, cy + h - k], [cx + w - d, cy + h - d], [cx + w - k, cy + h]]},
        {"line": [[cx + w - k, cy + h], [cx - w + k, cy + h]]},
        {"arc": [[cx - w + k, cy + h], [cx - w + d, cy + h - d], [cx - w, cy + h - k]]},
        {"line": [[cx - w, cy + h - k], [cx - w, cy - h + k]]},
        {"arc": [[cx - w, cy - h + k], [cx - w + d, cy - h + d], [cx - w + k, cy - h]]},
    ]


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def edges_between(shape, a, b):
    """Indexes of the edges along the line where two planes meet."""
    na, nb = a["normal"], b["normal"]
    line = V(*na).cross(V(*nb))
    if line.Length < 1e-9:
        return []
    line.normalize()
    out = []
    for i, e in enumerate(shape.Edges):
        ends = [v.Point for v in e.Vertexes]
        if len(ends) != 2:
            continue
        if any(
            abs(dot(p, na) - a["offset"]) > ON_PLANE or abs(dot(p, nb) - b["offset"]) > ON_PLANE
            for p in ends
        ):
            continue
        run = ends[1] - ends[0]
        if run.Length > 10 * ON_PLANE and abs(run.dot(line)) > 0.99 * run.Length:
            out.append(i)
    return out


def face_edges(shape, point, normal):
    """Indexes of the edges of the flat faces square to normal through point."""
    n, p = V(*normal), V(*point)
    found = set()
    for f in shape.Faces:
        if not isinstance(f.Surface, Part.Plane):
            continue
        fn = f.normalAt(0, 0)
        if abs(fn.dot(n)) > 0.9999 and abs((f.CenterOfMass - p).dot(n)) < ON_PLANE:
            for e in f.Edges:
                found.update(i for i, x in enumerate(shape.Edges) if x.isSame(e))
    return sorted(found)


def main():
    try:
        folder = os.path.dirname(os.path.abspath(__file__))
    except NameError:  # run some other way than from the Macro dialog
        folder = os.getcwd()
    doc = App.newDocument(NAME)
    builder = Builder(doc, folder)
    skipped = []
    for op in HISTORY["ops"]:
        before = set(o.Name for o in doc.Objects)
        try:
            feat = builder.run(op)
            doc.recompute()
            if feat is not None and (not feat.isValid() or feat.Shape.isNull()):
                raise RuntimeError("invalid")
        except Exception as exc:  # a step that won't build is left out, as MeshToMod does
            for name in set(o.Name for o in doc.Objects) - before:
                obj = doc.getObject(name)
                if obj is None:
                    continue
                if obj in builder.body.Group:
                    builder.body.removeObject(obj)
                doc.removeObject(name)
            doc.recompute()
            skipped.append("{} ({})".format(op["name"], exc))
    doc.recompute()
    if App.GuiUp:
        import FreeCADGui

        FreeCADGui.SendMsgToActiveView("ViewFit")
        FreeCADGui.activeDocument().activeView().viewIsometric()
    out = os.path.join(folder, NAME + ".FCStd")
    doc.saveAs(out)
    message = f"MeshToMod: built {len(HISTORY['ops']) - len(skipped)} steps, saved {out}"
    if skipped:
        message += "\nLeft out: " + "; ".join(skipped)
    App.Console.PrintMessage(message + "\n")


main()
