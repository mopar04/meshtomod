"""Synthetic test meshes with known ground truth, optionally with scanner-like noise."""

from __future__ import annotations

import numpy as np
import trimesh


def add_noise(mesh: trimesh.Trimesh, sigma_mm: float, seed: int = 0) -> trimesh.Trimesh:
    """Jitter every vertex by isotropic Gaussian noise (sigma per axis), like a scanner.

    Not along vertex normals: at a CAD edge those point sideways, which would slide edge
    vertices within the face and leave long runs of them perfectly coplanar.
    """
    rng = np.random.default_rng(seed)
    noisy = mesh.copy()
    noisy.vertices = noisy.vertices + rng.normal(0.0, sigma_mm, size=noisy.vertices.shape)
    return noisy


def box(size=(40.0, 30.0, 20.0), subdivisions: int = 3) -> trimesh.Trimesh:
    mesh = trimesh.creation.box(extents=size)
    for _ in range(subdivisions):
        mesh = mesh.subdivide()
    return mesh


def cylinder(radius: float = 10.0, height: float = 30.0, sections: int = 96) -> trimesh.Trimesh:
    return trimesh.creation.cylinder(radius=radius, height=height, sections=sections)


def ball(radius: float = 15.0, squash: float = 0.6) -> trimesh.Trimesh:
    """A squashed sphere: curved everywhere, and not a cylinder anywhere."""
    mesh = trimesh.creation.icosphere(subdivisions=4, radius=radius)
    mesh.apply_scale([1.0, 1.0, squash])
    return mesh


def plate_with_hole(
    size=(60.0, 40.0, 8.0), hole_radius: float = 6.0, sections: int = 96
) -> trimesh.Trimesh:
    plate = trimesh.creation.box(extents=size)
    tool = trimesh.creation.cylinder(radius=hole_radius, height=size[2] * 2, sections=sections)
    return plate.difference(tool, engine="manifold")


def l_bracket(base=(40.0, 20.0, 5.0), upright_height: float = 30.0) -> trimesh.Trimesh:
    """Non-convex: a base plate with an upright wall at one end."""
    bx, by, bz = base
    plate = trimesh.creation.box(extents=base)
    wall = trimesh.creation.box(
        extents=(bz, by, upright_height),
        transform=trimesh.transformations.translation_matrix(
            ((bx - bz) / 2, 0, (upright_height - bz) / 2)
        ),
    )
    return plate.union(wall, engine="manifold")


def stepped_block() -> trimesh.Trimesh:
    """Three steps: lots of concave and convex edges, and 4-plane corners."""
    parts = [
        trimesh.creation.box(
            extents=(60.0 - 20 * i, 30.0, 10.0),
            transform=trimesh.transformations.translation_matrix((10.0 * i, 0, 5.0 + 10 * i)),
        )
        for i in range(3)
    ]
    return trimesh.boolean.union(parts, engine="manifold")


def frame(outer=(50.0, 40.0, 6.0), window=(30.0, 20.0)) -> trimesh.Trimesh:
    """A plate with a rectangular through-window: faces with inner loops."""
    plate = trimesh.creation.box(extents=outer)
    cut = trimesh.creation.box(extents=(window[0], window[1], outer[2] * 2))
    return plate.difference(cut, engine="manifold")


def subdivided(mesh: trimesh.Trimesh, max_edge: float = 3.0) -> trimesh.Trimesh:
    """Split long triangles so noise looks like a real scan's dense sampling."""
    verts, faces = trimesh.remesh.subdivide_to_size(mesh.vertices, mesh.faces, max_edge=max_edge)
    return trimesh.Trimesh(verts, faces)


def rotated(mesh: trimesh.Trimesh, seed: int = 1) -> trimesh.Trimesh:
    """Arbitrary orientation, so tests do not lean on world axes."""
    out = mesh.copy()
    out.apply_transform(
        trimesh.transformations.random_rotation_matrix(np.random.default_rng(seed).random(3))
    )
    return out


def sides(mesh: trimesh.Trimesh, up=(0.0, 0.0, 1.0)):
    """The part as a turntable scanner sees it from above and, turned over, from below: the
    faces a ray from 30 or 60 degrees above (or below) the turntable reaches unblocked. The
    two overlap only on the walls. Works on the faces as given, so pass the coarse CAD mesh
    and subdivide each side afterwards."""
    up = np.asarray(up, dtype=float)
    side = np.cross(up, [1.0, 0.0, 0.0] if abs(up[0]) < 0.9 else [0.0, 1.0, 0.0])
    side /= np.linalg.norm(side)
    other = np.cross(up, side)
    out = []
    for sign in (1.0, -1.0):
        seen = np.zeros(len(mesh.faces), dtype=bool)
        for elevation in np.radians([30.0, 60.0]):
            for azimuth in np.radians(np.arange(0.0, 360.0, 45.0)):
                ray = np.cos(elevation) * (np.cos(azimuth) * side + np.sin(azimuth) * other)
                ray = ray + sign * np.sin(elevation) * up
                facing = mesh.face_normals @ ray > 0.05
                idx = np.flatnonzero(facing & ~seen)
                if not len(idx):
                    continue
                start = mesh.triangles_center[idx] + 1e-3 * mesh.face_normals[idx]
                blocked = mesh.ray.intersects_any(start, np.tile(ray, (len(idx), 1)))
                seen[idx[~blocked]] = True
        part = mesh.copy()
        part.update_faces(seen)
        part.remove_unreferenced_vertices()
        out.append(part)
    return out[0], out[1]


# ---------------------------------------------------------------------------------------------
# Parts with curved features, built as real CAD and tessellated, so the ground truth is exact.
# ---------------------------------------------------------------------------------------------


def from_cad(shape, tolerance: float = 0.02, angular: float = 0.1) -> trimesh.Trimesh:
    """Tessellate a CadQuery shape or workplane into a mesh. angular is the most a triangle may
    turn from the next, in radians: on a gentle freeform surface it sets the density."""
    import cadquery as cq

    if isinstance(shape, cq.Workplane):
        shape = shape.val()
    verts, tris = shape.tessellate(tolerance, angularTolerance=angular)
    mesh = trimesh.Trimesh(np.array([v.toTuple() for v in verts]), np.array(tris))
    mesh.merge_vertices()
    trimesh.repair.fix_normals(mesh)
    return mesh


def holed_plate_cad():
    """60 x 40 x 8 plate with four M6 clearance holes (6.6 mm)."""
    import cadquery as cq

    return (
        cq.Workplane("XY")
        .box(60, 40, 8)
        .faces(">Z")
        .workplane()
        .pushPoints([(-20, -10), (20, -10), (-20, 10), (20, 10)])
        .hole(6.6)
    )


def filleted_block_cad():
    """50 x 30 x 20 block with its four vertical edges filleted at 5 mm."""
    import cadquery as cq

    return cq.Workplane("XY").box(50, 30, 20).edges("|Z").fillet(5)


def boss_plate_cad():
    """60 x 40 x 6 plate with a 16 mm boss, 10 mm tall."""
    import cadquery as cq

    return cq.Workplane("XY").box(60, 40, 6).faces(">Z").workplane().circle(8).extrude(10)


def standoff_plate_cad():
    """60 x 40 x 6 plate with a 12 mm standoff, 10 mm tall, bored through at 6 mm."""
    import cadquery as cq

    plate = cq.Workplane("XY").box(60, 40, 6).faces(">Z").workplane().center(10, 0)
    return plate.circle(6).extrude(10).faces(">Z").workplane().hole(6)


def blind_hole_block_cad():
    """40 x 40 x 20 block with a 10 mm blind hole, 12 mm deep."""
    import cadquery as cq

    return cq.Workplane("XY").box(40, 40, 20).faces(">Z").workplane().hole(10, 12)


def bracket_cad():
    """L-bracket with a 4 mm inside fillet and two 5.5 mm holes in the base."""
    import cadquery as cq

    base = cq.Workplane("XY").box(60, 30, 6).translate((0, 0, 3))
    wall = cq.Workplane("XY").box(6, 30, 40).translate((27, 0, 20))
    body = base.union(wall).edges("|Y").edges(cq.selectors.NearestToPointSelector((24, 0, 6)))
    body = body.fillet(4)
    return (
        body.faces("<Z")
        .workplane(centerOption="CenterOfBoundBox")
        .pushPoints([(-15, 0), (5, 0)])
        .hole(5.5)
    )


def holed_plate() -> trimesh.Trimesh:
    return from_cad(holed_plate_cad())


def filleted_block() -> trimesh.Trimesh:
    return from_cad(filleted_block_cad())


def boss_plate() -> trimesh.Trimesh:
    return from_cad(boss_plate_cad())


def standoff_plate() -> trimesh.Trimesh:
    return from_cad(standoff_plate_cad())


def blind_hole_block() -> trimesh.Trimesh:
    return from_cad(blind_hole_block_cad())


def bracket() -> trimesh.Trimesh:
    return from_cad(bracket_cad())


def stepped_peg_cad():
    """Like Zac's peg: a 16 mm round body, 19.4 mm long, with an 8 mm pin 6 mm long."""
    import cadquery as cq

    return cq.Workplane("XY").circle(8).extrude(19.4).faces(">Z").workplane().circle(4).extrude(6)


def stepped_peg() -> trimesh.Trimesh:
    return from_cad(stepped_peg_cad())


def turned_part_cad():
    """A lathe part about Z, 30 mm across and 44 mm tall: a Ø30 base with a 1 mm chamfer, a
    step to Ø20, a groove to Ø16 with a 45 degree flank, a cone to Ø12, a neck and a ball end,
    and a Ø4 cross hole."""
    import cadquery as cq

    # (radius, z), bottom to top, then closed down the axis.
    pts = [
        (0, 0), (14, 0), (15, 1), (15, 8), (10, 8), (10, 14), (8, 14), (8, 15), (10, 17),
        (10, 24), (6, 32), (6, 38),
    ]  # fmt: skip
    body = cq.Workplane("XZ").polyline(pts).lineTo(0, 38).close().revolve(360, (0, 0, 0), (0, 1, 0))
    ball = cq.Workplane("XY").workplane(offset=38).sphere(6.0)
    body = body.union(ball.intersect(cq.Workplane("XY").workplane(offset=38).circle(7).extrude(7)))
    cross = cq.Workplane("YZ").workplane(offset=-20).center(0, 20).circle(2.0).extrude(40)
    return body.cut(cross)


def surfaces_plate_cad():
    """70 x 50 x 6 plate with M3 and M5 countersunk holes, a dome (a cap of a 20 mm sphere,
    6 mm high), a tapered boss (Ø16 to Ø10, 8 mm) and a ring (Ø20, Ø10 bore through, 5 mm
    tall, 2 mm rounds on both top edges)."""
    import cadquery as cq

    t = 6.0
    p = cq.Workplane("XY").box(70, 50, t, centered=(True, True, False))
    p = (
        p.faces(">Z").workplane()
        .pushPoints([(-27, 15)]).cskHole(3.4, 6.5, 90)
        .pushPoints([(-27, -12)]).cskHole(5.5, 10.4, 90)
    )  # fmt: skip
    dome = cq.Workplane("XY").workplane(offset=t - 14).center(-2, 0).sphere(20.0)
    p = p.union(
        dome.intersect(cq.Workplane("XY").workplane(offset=t).center(-2, 0).circle(16).extrude(7))
    )
    p = p.union(cq.Workplane().add(cq.Solid.makeCone(8.0, 5.0, 8.0, cq.Vector(24, 13, t))))
    ring = (
        cq.Workplane("XY").workplane(offset=t).center(24, -12)
        .circle(10).circle(5).extrude(5).faces(">Z").edges().fillet(2.0)
    )  # fmt: skip
    p = p.union(ring)
    return p.cut(cq.Workplane("XY").center(24, -12).circle(5).extrude(20))


def turned_part() -> trimesh.Trimesh:
    return from_cad(turned_part_cad())


def surfaces_plate() -> trimesh.Trimesh:
    return from_cad(surfaces_plate_cad())


def freeform_top_cad():
    """80 x 50 base, flat underneath, with a smooth lofted top rising to 18 mm, like a mouse
    shell. The same design as the batch print."""
    import cadquery as cq

    sections = []
    for y, (edge, mid, peak_x) in zip(
        (-25, -12.5, 0, 12.5, 25),
        ((3, 6, 5), (4, 13, 8), (5, 18, 10), (4, 14, 8), (3, 7, 5)),
        strict=True,
    ):
        w = (
            cq.Workplane("XZ", origin=(0, y, 0))
            .moveTo(-40, 0).lineTo(40, 0).lineTo(40, edge)
            .spline([(peak_x, mid), (-40, edge)], includeCurrent=True)
            .close()
        )  # fmt: skip
        sections.append(w.val())
    return cq.Workplane().add(cq.Solid.makeLoft(sections, False))


def _height_face(fn, x: tuple[float, float], y: tuple[float, float], n: int = 41):
    """The B-spline face through z = fn(x, y), sampled on an n x n grid over the x and y
    ranges, facing up."""
    import cadquery as cq
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.GeomAPI import GeomAPI_PointsToBSplineSurface
    from OCP.gp import gp_Pnt
    from OCP.TColgp import TColgp_Array2OfPnt

    grid = TColgp_Array2OfPnt(1, n, 1, n)
    for i in range(n):
        for j in range(n):
            px = x[0] + (x[1] - x[0]) * i / (n - 1)
            py = y[0] + (y[1] - y[0]) * j / (n - 1)
            grid.SetValue(i + 1, j + 1, gp_Pnt(px, py, fn(px, py)))
    surface = GeomAPI_PointsToBSplineSurface(grid, 3, 3).Surface()
    return cq.Face(BRepBuilderAPI_MakeFace(surface, 1e-6).Face())


def _height_field(fn, x: tuple[float, float], y: tuple[float, float], sweep: float, n: int = 41):
    """The solid swept from the B-spline surface through z = fn(x, y) (sampled on an n x n
    grid over the x and y ranges) by sweep along Z: down for a negative sweep."""
    import cadquery as cq
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec

    face = _height_face(fn, x, y, n)
    return cq.Workplane().add(
        cq.Shape.cast(BRepPrimAPI_MakePrism(face.wrapped, gp_Vec(0, 0, sweep)).Shape())
    )


def _pillow(x: float, y: float) -> float:
    # An off-centre, lopsided mound: 7 mm above the plate at its top, meeting the plate along
    # an egg-shaped edge. Neither a sphere nor turned about any axis.
    u, v = (x + 4) / 22, y / 15
    return 5 + 7 * (1 - u * u - v * v) + 2.0 * u * v + 1.5 * u**3


def freeform_bump_plate_cad():
    """70 x 50 x 5 plate with a smooth lopsided mound on top (freeform, about 44 x 30 mm at its
    foot and 7 mm tall)."""
    import cadquery as cq

    plate = cq.Workplane("XY").box(70, 50, 5, centered=(True, True, False))
    mound = _height_field(_pillow, (-30, 22), (-20, 20), -30)
    keep = cq.Workplane("XY").box(70, 50, 20, centered=(True, True, False))
    return plate.union(mound.intersect(keep))


def _dish(x: float, y: float) -> float:
    # A thumb rest: a long shallow scoop, 3.5 mm deep, tilted and off-centre.
    u, v = (x - 3) / 18, (y - 2) / 11
    return 20 - 3.5 * (1 - u * u - v * v) + 0.8 * u - 0.6 * u * v


def freeform_recess_block_cad():
    """60 x 40 x 20 block with a smooth thumb scoop pressed into its top (freeform)."""
    import cadquery as cq

    block = cq.Workplane("XY").box(60, 40, 20, centered=(True, True, False))
    scoop = _height_field(_dish, (-22, 26), (-14, 18), 30)
    return block.cut(scoop.intersect(block))


# The freeform designs are tessellated finer than the default: OpenCASCADE meshes a gentle
# surface coarsely, and the chords would then sag up to 0.2 mm below the drawn surface.
FREEFORM_ANGULAR = 0.05


def freeform_top() -> trimesh.Trimesh:
    return from_cad(freeform_top_cad(), angular=FREEFORM_ANGULAR)


def freeform_bump_plate() -> trimesh.Trimesh:
    return from_cad(freeform_bump_plate_cad(), angular=FREEFORM_ANGULAR)


def freeform_recess_block() -> trimesh.Trimesh:
    return from_cad(freeform_recess_block_cad(), angular=FREEFORM_ANGULAR)


# ---------------------------------------------------------------------------------------------
# One-sided scans (skins): only the outside of a thin part, as an open sheet of triangles.
# ---------------------------------------------------------------------------------------------


def wave_wall_cad():
    """A 2 mm wall bent into an S, 70 mm long and 40 mm tall (the batch print): its centre line
    is y = 12 sin(pi x / 35), and each side is that line moved 1 mm along its normal."""
    import math

    import cadquery as cq

    xs = [-35 + 70 * i / 40 for i in range(41)]
    sides: tuple[list, list] = ([], [])
    for x in xs:
        y = 12 * math.sin(math.pi * x / 35)
        slope = 12 * math.pi / 35 * math.cos(math.pi * x / 35)
        n = math.hypot(1, slope)
        for side, sign in zip(sides, (1, -1), strict=True):
            side.append((x - sign * slope / n, y + sign / n))
    outline = (
        cq.Workplane("XY").moveTo(*sides[0][0]).spline(sides[0][1:], includeCurrent=True)
        .lineTo(*sides[1][-1]).spline(sides[1][-2::-1], includeCurrent=True).close()
    )  # fmt: skip
    return outline.extrude(40.0)


def wave_skin_face():
    """The wave wall's outside face (on the +y side of its centre line)."""
    wall = wave_wall_cad().val()
    return max(wall.Faces(), key=lambda f: f.Area() * (f.Center().y > 0))


def _cap(x: float, y: float) -> float:
    # A lopsided dome, like a helmet shell or a car panel: curved both ways, steepest (about
    # 50 degrees) at its rim.
    return 14 - x * x / 70 - y * y / 40 + 0.05 * x


CAP_AXES = (34.0, 22.0)


def cap_skin_face():
    """The dome's surface inside an ellipse 68 x 44 mm (seen from above): its outside, facing
    up."""
    import cadquery as cq
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common

    surface = _height_face(_cap, (-40, 40), (-28, 28))
    column = cq.Workplane("XY").ellipse(*CAP_AXES).extrude(60, both=True).val()
    return cq.Shape.cast(BRepAlgoAPI_Common(surface.wrapped, column.wrapped).Shape()).Faces()[0]


def skin_mesh(face) -> trimesh.Trimesh:
    """A face tessellated into an open sheet, its triangles facing the way the face does."""
    mesh = from_cad(face, angular=FREEFORM_ANGULAR)
    if (mesh.face_normals @ np.array(face.normalAt().toTuple())).sum() < 0:
        mesh.invert()
    return mesh


def wave_skin() -> trimesh.Trimesh:
    return skin_mesh(wave_skin_face())


def cap_skin() -> trimesh.Trimesh:
    return skin_mesh(cap_skin_face())


def ragged(mesh: trimesh.Trimesh, depth: float, seed: int = 0) -> trimesh.Trimesh:
    """Tear the border of a sheet like a scan's: triangles near it are dropped where a random,
    wavy band of up to depth mm from the border reaches."""
    rng = np.random.default_rng(seed)
    from scipy.spatial import cKDTree

    edges = mesh.edges_sorted
    _, index, counts = np.unique(edges, axis=0, return_index=True, return_counts=True)
    border = np.unique(edges[index[counts == 1]])
    tree = cKDTree(mesh.vertices[border])
    centres = mesh.triangles_center
    dist, nearest = tree.query(centres)
    # The band's depth varies along the border, with bumps a few mm apart.
    phase = rng.uniform(0, 2 * np.pi, 3)
    p = mesh.vertices[border][nearest]
    wave = sum(np.sin(p[:, k % 3] * (0.7 + 0.4 * k) + phase[k]) for k in range(3)) / 3
    reach = depth * (0.5 + 0.5 * wave) * rng.uniform(0.6, 1.0, len(centres))
    out = mesh.copy()
    out.update_faces(dist > reach)
    out.remove_unreferenced_vertices()
    return out


def tape_roll_cad():
    """A ring like Zac's tape roll: 93 mm across, a 76 mm bore, 48.5 mm tall."""
    import cadquery as cq

    return cq.Workplane("XY").circle(46.5).circle(38).extrude(48.5)


def tape_roll() -> trimesh.Trimesh:
    return from_cad(tape_roll_cad())


def unscanned_base(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """The scan of a part that stood on a turntable: its lowest face, facing down, is missing."""
    low = mesh.bounds[0, 2]
    drop = (mesh.triangles_center[:, 2] < low + 0.05) & (mesh.face_normals[:, 2] < -0.99)
    out = mesh.copy()
    out.update_faces(~drop)
    out.remove_unreferenced_vertices()
    return out
