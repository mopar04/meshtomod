// MeshToMod editable history for Onshape: __NAME__
//
// 1. In a Part Studio, import base.step if there is one (Import, or drag it in).
// 2. Create a Feature Studio (+ > Create Feature Studio). Keep the two lines Onshape starts
//    it with, delete anything else, and paste this whole file below them.
// 3. Back in the Part Studio, add "MeshToMod __NAME__" from the custom features menu, and
//    pick the imported part. Each step's size is a field in that feature's dialog: change
//    Hole 1 diameter, and the hole and everything after it update.
//
// Made by MeshToMod (https://meshtomod.com). AGPL-3.0.

const HISTORY = __HISTORY__;
// How close a point must be to a plane to count as on it (mm).
const ON_PLANE = 1e-3;

annotation { "Feature Type Name" : "MeshToMod __NAME__",
        "Feature Type Description" : "The steps MeshToMod found in the scan, each with its own size." }
export const meshToModHistory = defineFeature(function(context is Context, id is Id, definition is map)
    precondition
    {
__PARAMETERS__
    }
    {
        var part = qNothing();
        if (HISTORY.ops[0].op == "base")
        {
            part = definition.part;
        }
        var skipped = [];
        for (var i = 0; i < size(HISTORY.ops); i += 1)
        {
            const op = HISTORY.ops[i];
            if (op.op == "base")
                continue;
            const step = id + ("step" ~ i);
            try
            {
                part = runStep(context, step, definition, op, part);
            }
            catch (error)
            {
                skipped = append(skipped, op.name);
            }
            // The step's sketches are only its tools.
            try silent
            {
                opDeleteBodies(context, id + ("clean" ~ i), {
                            "entities" : qCreatedBy(step + "sketch", EntityType.BODY)
                        });
            }
        }
        if (size(skipped) > 0)
        {
            var message = "Left out: ";
            for (var k = 0; k < size(skipped); k += 1)
            {
                message = message ~ (k > 0 ? "; " : "") ~ skipped[k];
            }
            reportFeatureWarning(context, id, message);
        }
    });

function v3(a is array) returns Vector
{
    return vector(a[0], a[1], a[2]);
}

function mm3(a is array) returns Vector
{
    return vector(a[0], a[1], a[2]) * millimeter;
}

function mm2(a is array) returns Vector
{
    return vector(a[0], a[1]) * millimeter;
}

/** A size from the feature's dialog when it has one, else the history's own (mm). */
function sizeFor(definition is map, key is string, fallback is number) returns ValueWithUnits
{
    const value = definition[key];
    return value == undefined ? fallback * millimeter : value;
}

function runStep(context is Context, step is Id, definition is map, op is map, part is Query) returns Query
{
    if (op.op == "extrude")
        return extrudeStep(context, step, definition, op, part);
    if (op.op == "revolve")
        return revolveStep(context, step, op, part);
    if (op.op == "fillet" || op.op == "chamfer")
    {
        edgeStep(context, step, definition, op, part);
        return part;
    }
    throw regenError("Unknown step " ~ op.op);
}

function newSketch(context is Context, step is Id, op is map)
{
    const p = op.plane;
    return newSketchOnPlane(context, step + "sketch", {
                "sketchPlane" : plane(mm3(p.origin), v3(p.normal), v3(p.x_dir))
            });
}

function drawLoop(sketch, segments is array, tag is string)
{
    for (var k = 0; k < size(segments); k += 1)
    {
        const seg = segments[k];
        if (seg.line != undefined)
        {
            skLineSegment(sketch, tag ~ "l" ~ k, { "start" : mm2(seg.line[0]), "end" : mm2(seg.line[1]) });
        }
        else
        {
            skArc(sketch, tag ~ "a" ~ k, {
                        "start" : mm2(seg.arc[0]), "mid" : mm2(seg.arc[1]), "end" : mm2(seg.arc[2])
                    });
        }
    }
}

function rectLoop(r is map) returns array
{
    const cx = r.center[0];
    const cy = r.center[1];
    const w = r.width / 2;
    const h = r.height / 2;
    const k = r.corner_radius;
    if (k <= 0)
    {
        return [
                { "line" : [[cx - w, cy - h], [cx + w, cy - h]] },
                { "line" : [[cx + w, cy - h], [cx + w, cy + h]] },
                { "line" : [[cx + w, cy + h], [cx - w, cy + h]] },
                { "line" : [[cx - w, cy + h], [cx - w, cy - h]] }
            ];
    }
    const d = k * (1 - sqrt(0.5));
    return [
            { "line" : [[cx - w + k, cy - h], [cx + w - k, cy - h]] },
            { "arc" : [[cx + w - k, cy - h], [cx + w - d, cy - h + d], [cx + w, cy - h + k]] },
            { "line" : [[cx + w, cy - h + k], [cx + w, cy + h - k]] },
            { "arc" : [[cx + w, cy + h - k], [cx + w - d, cy + h - d], [cx + w - k, cy + h]] },
            { "line" : [[cx + w - k, cy + h], [cx - w + k, cy + h]] },
            { "arc" : [[cx - w + k, cy + h], [cx - w + d, cy + h - d], [cx - w, cy + h - k]] },
            { "line" : [[cx - w, cy + h - k], [cx - w, cy - h + k]] },
            { "arc" : [[cx - w, cy - h + k], [cx - w + d, cy - h + d], [cx - w + k, cy - h]] }
        ];
}

function slotLoop(s is map) returns array
{
    const a = s.a;
    const b = s.b;
    const r = s.width / 2;
    const length = sqrt((b[0] - a[0]) ^ 2 + (b[1] - a[1]) ^ 2);
    const ux = (b[0] - a[0]) / length;
    const uy = (b[1] - a[1]) / length;
    const px = -uy * r;
    const py = ux * r;
    return [
            { "line" : [[a[0] + px, a[1] + py], [b[0] + px, b[1] + py]] },
            { "arc" : [[b[0] + px, b[1] + py], [b[0] + ux * r, b[1] + uy * r], [b[0] - px, b[1] - py]] },
            { "line" : [[b[0] - px, b[1] - py], [a[0] - px, a[1] - py]] },
            { "arc" : [[a[0] - px, a[1] - py], [a[0] - ux * r, a[1] - uy * r], [a[0] + px, a[1] + py]] }
        ];
}

/** Join or cut a new body into the part, or make it the part. */
function combine(context is Context, step is Id, op is map, part is Query, tool is Query) returns Query
{
    if (op.mode == "new")
        return tool;
    if (op.mode == "join")
    {
        opBoolean(context, step + "boolean", {
                    "tools" : qUnion([part, tool]),
                    "operationType" : BooleanOperationType.UNION
                });
        // Whichever body the union kept: only one of these is left.
        return qUnion([part, tool, qCreatedBy(step + "boolean", EntityType.BODY)]);
    }
    opBoolean(context, step + "boolean", {
                "tools" : tool,
                "targets" : part,
                "operationType" : BooleanOperationType.SUBTRACTION
            });
    return part;
}

function extrudeStep(context is Context, step is Id, definition is map, op is map, part is Query) returns Query
{
    const sketch = newSketch(context, step, op);
    const shape = op.sketch;
    if (shape.circle != undefined)
    {
        const d = sizeFor(definition, op.id ~ "_diameter", shape.circle.diameter);
        skCircle(sketch, "circle", { "center" : mm2(shape.circle.center), "radius" : d / 2 });
    }
    else if (shape.rect != undefined)
    {
        drawLoop(sketch, rectLoop(shape.rect), "r");
    }
    else
    {
        drawLoop(sketch, slotLoop(shape.slot), "s");
    }
    skSolve(sketch);
    var depth = op.distance * millimeter;
    if (op.through == true)
    {
        depth = (HISTORY.size + 1) * millimeter;
    }
    else if (op.from_face == true || op.mode == "new")
    {
        depth = sizeFor(definition, op.id ~ (op.mode == "cut" ? "_depth" : "_height"), op.distance);
    }
    opExtrude(context, step + "extrude", {
                "entities" : qSketchRegion(step + "sketch", true),
                "direction" : v3(op.plane.normal),
                "endBound" : BoundingType.BLIND,
                "endDepth" : depth
            });
    return combine(context, step, op, part, qCreatedBy(step + "extrude", EntityType.BODY));
}

function revolveStep(context is Context, step is Id, op is map, part is Query) returns Query
{
    const sketch = newSketch(context, step, op);
    for (var k = 0; k < size(op.loops); k += 1)
    {
        drawLoop(sketch, op.loops[k], "p" ~ k);
    }
    skSolve(sketch);
    // The sketch's x axis is the axis of turning.
    opRevolve(context, step + "revolve", {
                "entities" : qSketchRegion(step + "sketch", true),
                "axis" : line(mm3(op.plane.origin), v3(op.plane.x_dir)),
                "angleForward" : 2 * PI * radian
            });
    return combine(context, step, op, part, qCreatedBy(step + "revolve", EntityType.BODY));
}

function onPlane(p is Vector, n is Vector, offset is number) returns boolean
{
    return abs(dot(p, n) - offset) <= ON_PLANE;
}

function edgeStep(context is Context, step is Id, definition is map, op is map, part is Query)
{
    var edges = [];
    if (op.face != undefined)
    {
        const n = v3(op.face.normal);
        const at = v3(op.face.point);
        for (var f in evaluateQuery(context, qOwnedByBody(part, EntityType.FACE)))
        {
            const s = evSurfaceDefinition(context, { "face" : f });
            if (s is Plane && abs(dot(s.normal, n)) > 0.9999 && abs(dot(s.origin / millimeter - at, n)) < ON_PLANE)
            {
                edges = concatenateArrays([edges, evaluateQuery(context, qAdjacent(f, AdjacencyType.EDGE, EntityType.EDGE))]);
            }
        }
    }
    else
    {
        for (var e in evaluateQuery(context, qOwnedByBody(part, EntityType.EDGE)))
        {
            const p = evEdgeTangentLine(context, { "edge" : e, "parameter" : 0 }).origin / millimeter;
            const q = evEdgeTangentLine(context, { "edge" : e, "parameter" : 1 }).origin / millimeter;
            const run = q - p;
            for (var pair in op.edges)
            {
                const na = v3(pair[0].normal);
                const nb = v3(pair[1].normal);
                var dir = cross(na, nb);
                if (norm(dir) < 1e-9)
                    continue;
                dir = normalize(dir);
                if (onPlane(p, na, pair[0].offset) && onPlane(q, na, pair[0].offset) &&
                    onPlane(p, nb, pair[1].offset) && onPlane(q, nb, pair[1].offset) &&
                    norm(run) > 10 * ON_PLANE && abs(dot(run, dir)) > 0.99 * norm(run))
                {
                    edges = append(edges, e);
                    break;
                }
            }
        }
    }
    if (size(edges) == 0)
        throw regenError("No edges found");
    if (op.op == "fillet")
    {
        opFillet(context, step + "fillet", {
                    "entities" : qUnion(edges),
                    "radius" : sizeFor(definition, op.id ~ "_radius", op.radius)
                });
    }
    else
    {
        opChamfer(context, step + "chamfer", {
                    "entities" : qUnion(edges),
                    "chamferType" : ChamferType.EQUAL_OFFSETS,
                    "width" : sizeFor(definition, op.id ~ "_size", op.size)
                });
    }
}
