# MeshToMod editable history for Autodesk Fusion.
#
# In Fusion: Utilities > Add-Ins > Scripts and Add-Ins, press the + beside "My Scripts", pick
# this folder (keep base.step in it if there is one), then select the script and press Run.
# It opens a new design with one timeline feature per step: the base, then each boss,
# fillet, chamfer, hole and edit. Sizes are user parameters (Modify > Change Parameters):
# change hole_1_diameter and the hole and everything after it update.
#
# Made by MeshToMod (https://meshtomod.com). AGPL-3.0.

import json
import os
import traceback

import adsk.core
import adsk.fusion

HISTORY = json.loads("__HISTORY__")
NAME = "__NAME__"
# Fusion's API works in centimetres; the history is in millimetres.
CM = 0.1
# How close a point must be to a plane to count as on it (mm).
ON_PLANE = 1e-3


def run(context):
    app = adsk.core.Application.get()
    try:
        message = Builder(app).build()
    except Exception:
        message = "MeshToMod could not build the history:\n" + traceback.format_exc()
    app.userInterface.messageBox(message, "MeshToMod")


def point(p):
    return adsk.core.Point3D.create(p[0] * CM, p[1] * CM, p[2] * CM)


def at(plane, uv):
    """A point in a sketch plane's 2D, in the model (mm)."""
    o, x, n = plane["origin"], plane["x_dir"], plane["normal"]
    y = [n[1] * x[2] - n[2] * x[1], n[2] * x[0] - n[0] * x[2], n[0] * x[1] - n[1] * x[0]]
    return [o[i] + uv[0] * x[i] + uv[1] * y[i] for i in range(3)]


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def on_plane(geometry, plane):
    """Whether a construction plane's geometry is the history's plane."""
    n = geometry.normal
    o = mm(geometry.origin)
    gap = [o[i] - plane["origin"][i] for i in range(3)]
    parallel = abs(dot([n.x, n.y, n.z], plane["normal"])) > 0.9999
    return parallel and abs(dot(gap, plane["normal"])) < ON_PLANE


def mm(p):
    return [p.x / CM, p.y / CM, p.z / CM]


class Builder:
    def __init__(self, app):
        self.app = app
        self.folder = os.path.dirname(os.path.realpath(__file__))
        app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        self.design = adsk.fusion.Design.cast(app.activeProduct)
        self.design.designType = adsk.fusion.DesignTypes.ParametricDesignType
        self.comp = self.design.rootComponent
        self.body = None
        self.points = None

    def build(self):
        skipped = []
        for op in HISTORY["ops"]:
            marker = self.design.timeline.markerPosition
            try:
                self.run(op)
            except Exception as exc:  # a step that won't build is left out, as MeshToMod does
                # Roll back whatever the step got as far as making.
                timeline = self.design.timeline
                while timeline.count > marker:
                    timeline.item(timeline.count - 1).entity.deleteMe()
                if self.points is not None and not self.points.isValid:
                    self.points = None
                skipped.append("{} ({})".format(op["name"], exc))
        app = self.app
        app.activeViewport.fit()
        done = len(HISTORY["ops"]) - len(skipped)
        message = f"Built {done} steps of {NAME}. Sizes are under Modify > Change Parameters."
        if skipped:
            message += "\n\nLeft out: " + "; ".join(skipped)
        return message

    # Parameters ---------------------------------------------------------------------------

    def param(self, name, value_mm, comment):
        """A user parameter in mm; returns its name for use in expressions."""
        params = self.design.userParameters
        existing = params.itemByName(name)
        if existing is None:
            value = adsk.core.ValueInput.createByString(f"{value_mm:.6g} mm")
            params.add(name, value, "mm", comment)
        return name

    # Planes and sketches ------------------------------------------------------------------

    def plane(self, plane):
        """A construction plane through the sketch plane's origin, square to its normal."""
        o = plane["origin"]
        x = plane["x_dir"]
        p1, p2, p3 = o, [o[i] + 10 * x[i] for i in range(3)], at(plane, [0.0, 10.0])
        try:
            # Three sketch points: stays parametric, so the timeline can be edited.
            if self.points is None:
                self.points = self.comp.sketches.add(self.comp.xYConstructionPlane)
                self.points.name = "MeshToMod points"
                self.points.isVisible = False
            pts = [self.points.sketchPoints.add(point(p)) for p in (p1, p2, p3)]
            inp = self.comp.constructionPlanes.createInput()
            inp.setByThreePoints(*pts)
            cp = self.comp.constructionPlanes.add(inp)
            if not on_plane(cp.geometry, plane):
                cp.deleteMe()
                raise RuntimeError("the points didn't keep their height")
        except Exception:
            # Fall back to a plane placed directly, inside a base feature.
            base = self.comp.features.baseFeatures.add()
            base.startEdit()
            inp = self.comp.constructionPlanes.createInput()
            n = plane["normal"]
            inp.setByPlane(
                adsk.core.Plane.create(point(o), adsk.core.Vector3D.create(n[0], n[1], n[2]))
            )
            inp.targetBaseFeature = base
            cp = self.comp.constructionPlanes.add(inp)
            base.finishEdit()
        cp.isLightBulbOn = False
        return cp

    def sketch(self, op):
        sk = self.comp.sketches.add(self.plane(op["plane"]))
        sk.name = op["name"]
        return sk

    def to_sketch(self, sk, plane, uv):
        return sk.modelToSketchSpace(point(at(plane, uv)))

    def draw(self, sk, op):
        plane, shape, pid = op["plane"], op["sketch"], op["id"]
        lines = sk.sketchCurves.sketchLines
        arcs = sk.sketchCurves.sketchArcs
        if "circle" in shape:
            c = shape["circle"]
            centre = self.to_sketch(sk, plane, c["center"])
            circle = sk.sketchCurves.sketchCircles.addByCenterRadius(centre, c["diameter"] / 2 * CM)
            # The diameter is a parameter: hole_1_diameter and so on.
            name = self.param(f"{pid}_diameter", c["diameter"], op["name"] + " diameter")
            text = adsk.core.Point3D.create(centre.x + c["diameter"] * CM, centre.y, 0)
            dim = sk.sketchDimensions.addDiameterDimension(circle, text)
            dim.parameter.expression = name
            return
        if "rect" in shape:
            segments = rect_loop(shape["rect"])
        elif "slot" in shape:
            segments = slot_loop(shape["slot"])
        else:
            segments = [seg for loop in op["loops"] for seg in loop]
        for seg in segments:
            if "line" in seg:
                p, q = (self.to_sketch(sk, plane, uv) for uv in seg["line"])
                lines.addByTwoPoints(p, q)
            else:
                p, m, q = (self.to_sketch(sk, plane, uv) for uv in seg["arc"])
                arcs.addByThreePoints(p, m, q)

    def direction(self, sk, normal):
        """Which way along the sketch's own normal the step's normal points."""
        z = sk.xDirection.crossProduct(sk.yDirection)
        return 1 if dot([z.x, z.y, z.z], normal) > 0 else -1

    # Steps --------------------------------------------------------------------------------

    def operation(self, mode):
        ops = adsk.fusion.FeatureOperations
        return {
            "new": ops.NewBodyFeatureOperation,
            "join": ops.JoinFeatureOperation,
            "cut": ops.CutFeatureOperation,
        }[mode]

    def finish(self, feature, op):
        feature.timelineObject.name = op["name"]
        if op["mode"] == "new":
            self.body = feature.bodies.item(0)
            self.body.name = NAME

    def run(self, op):
        kind = op["op"]
        if kind == "base":
            self.import_base(op)
        elif kind == "extrude":
            self.extrude(op)
        elif kind == "revolve":
            self.revolve(op)
        elif kind in ("fillet", "chamfer"):
            self.edges(op)
        else:
            raise RuntimeError("unknown step " + kind)

    def import_base(self, op):
        path = os.path.join(self.folder, op["file"])
        manager = self.app.importManager
        before = self.comp.occurrences.count
        bodies_before = self.comp.bRepBodies.count
        manager.importToTarget(manager.createSTEPImportOptions(path), self.comp)
        if self.comp.occurrences.count > before:
            # Fusion puts an imported STEP in a component of its own: work inside it.
            self.comp = self.comp.occurrences.item(self.comp.occurrences.count - 1).component
            self.body = self.comp.bRepBodies.item(0)
        else:
            self.body = self.comp.bRepBodies.item(bodies_before)
        self.body.name = NAME

    def extrude(self, op):
        sk = self.sketch(op)
        self.draw(sk, op)
        profiles = adsk.core.ObjectCollection.create()
        for i in range(sk.profiles.count):
            profiles.add(sk.profiles.item(i))
        features = self.comp.features.extrudeFeatures
        inp = features.createInput(profiles, self.operation(op["mode"]))
        way = adsk.fusion.ExtentDirections
        side = way.PositiveExtentDirection
        if self.direction(sk, op["plane"]["normal"]) < 0:
            side = way.NegativeExtentDirection
        if op.get("through"):
            extent = adsk.fusion.ThroughAllExtentDefinition.create()
        else:
            word = "depth" if op["mode"] == "cut" else "height"
            if op.get("from_face") or op["mode"] == "new":
                expr = self.param(f"{op['id']}_{word}", op["distance"], f"{op['name']} {word}")
            else:
                expr = f"{op['distance']:.6g} mm"
            extent = adsk.fusion.DistanceExtentDefinition.create(
                adsk.core.ValueInput.createByString(expr)
            )
        inp.setOneSideExtent(extent, side)
        if op["mode"] != "new":
            inp.participantBodies = [self.body]
        self.finish(features.add(inp), op)

    def revolve(self, op):
        sk = self.sketch(op)
        self.draw(sk, op)
        plane = op["plane"]
        # The sketch's x axis is the axis of turning.
        axis = sk.sketchCurves.sketchLines.addByTwoPoints(
            self.to_sketch(sk, plane, [0.0, 0.0]), self.to_sketch(sk, plane, [10.0, 0.0])
        )
        axis.isConstruction = True
        profiles = adsk.core.ObjectCollection.create()
        for i in range(sk.profiles.count):
            profiles.add(sk.profiles.item(i))
        features = self.comp.features.revolveFeatures
        inp = features.createInput(profiles, axis, self.operation(op["mode"]))
        inp.setAngleExtent(False, adsk.core.ValueInput.createByString("360 deg"))
        if op["mode"] != "new":
            inp.participantBodies = [self.body]
        self.finish(features.add(inp), op)

    def edges(self, op):
        found = adsk.core.ObjectCollection.create()
        if "face" in op:
            for e in face_edges(self.body, op["face"]["point"], op["face"]["normal"]):
                found.add(e)
        else:
            for a, b in op["edges"]:
                for e in edges_between(self.body, a, b):
                    found.add(e)
        if found.count == 0:
            raise RuntimeError("no edges found")
        key = "radius" if op["op"] == "fillet" else "size"
        name = self.param(f"{op['id']}_{key}", op[key], f"{op['name']} {key}")
        value = adsk.core.ValueInput.createByString(name)
        if op["op"] == "fillet":
            features = self.comp.features.filletFeatures
            inp = features.createInput()
            try:
                inp.edgeSetInputs.addConstantRadiusEdgeSet(found, value, False)
            except AttributeError:  # Fusion before 2023
                inp.addConstantRadiusEdgeSet(found, value, False)
        else:
            features = self.comp.features.chamferFeatures
            try:
                inp = features.createInput2()
                inp.chamferEdgeSets.addEqualDistanceChamferEdgeSet(found, value, False)
            except AttributeError:  # Fusion before 2023
                inp = features.createInput(found, False)
                inp.setToEqualDistance(value)
        feature = features.add(inp)
        feature.timelineObject.name = op["name"]


def edges_between(body, a, b):
    """Edges along the line where two planes meet: both ends on both planes."""
    na, nb = a["normal"], b["normal"]
    line = [
        na[1] * nb[2] - na[2] * nb[1],
        na[2] * nb[0] - na[0] * nb[2],
        na[0] * nb[1] - na[1] * nb[0],
    ]
    size = dot(line, line) ** 0.5
    if size < 1e-9:
        return []
    line = [c / size for c in line]
    out = []
    for i in range(body.edges.count):
        e = body.edges.item(i)
        if e.startVertex is None or e.endVertex is None:
            continue
        ends = [mm(e.startVertex.geometry), mm(e.endVertex.geometry)]
        if any(
            abs(dot(p, na) - a["offset"]) > ON_PLANE or abs(dot(p, nb) - b["offset"]) > ON_PLANE
            for p in ends
        ):
            continue
        run = [ends[1][k] - ends[0][k] for k in range(3)]
        length = dot(run, run) ** 0.5
        if length > 10 * ON_PLANE and abs(dot(run, line)) > 0.99 * length:
            out.append(e)
    return out


def face_edges(body, point_mm, normal):
    """Edges of the flat faces square to normal through point: a sink's floor or a top."""
    out = []
    for i in range(body.faces.count):
        f = body.faces.item(i)
        if f.geometry.surfaceType != adsk.core.SurfaceTypes.PlaneSurfaceType:
            continue
        p = f.pointOnFace
        ok, n = f.evaluator.getNormalAtPoint(p)
        on = [mm(p)[k] - point_mm[k] for k in range(3)]
        if ok and abs(dot([n.x, n.y, n.z], normal)) > 0.9999 and abs(dot(on, normal)) < ON_PLANE:
            out += [f.edges.item(k) for k in range(f.edges.count)]
    return out


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


def slot_loop(s):
    a, b, r = s["a"], s["b"], s["width"] / 2
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = (dx * dx + dy * dy) ** 0.5
    ux, uy = dx / length, dy / length
    px, py = -uy * r, ux * r
    return [
        {"line": [[a[0] + px, a[1] + py], [b[0] + px, b[1] + py]]},
        {"arc": [[b[0] + px, b[1] + py], [b[0] + ux * r, b[1] + uy * r], [b[0] - px, b[1] - py]]},
        {"line": [[b[0] - px, b[1] - py], [a[0] - px, a[1] - py]]},
        {"arc": [[a[0] - px, a[1] - py], [a[0] - ux * r, a[1] - uy * r], [a[0] + px, a[1] + py]]},
    ]
