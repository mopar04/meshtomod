"""Open ends of a scan, closed with their plane.

A turntable scan never sees the face the part stood on, and a tube scanned from outside can
miss an end. Each leaves an opening in the mesh. When an opening's rim lies flat, the missing
face was flat too: close it with that plane, exactly at the rim. Rims on one plane close
together as one face with holes, so a tube's end becomes a ring, not a disc over the bore.

An opening whose rim is not flat (a gap at a corner, a pocket the scanner couldn't reach) is
left open; the block solid fills it from the planes around it.
"""

from __future__ import annotations

import cadquery as cq
import numpy as np
import trimesh

# A rim within this many tolerances of its plane is flat.
FLAT_TOLS = 2.0
# Flat rims this close in angle and within a tolerance in offset are on one plane.
COPLANAR_DEG = 2.0


def close_flat_ends(mesh: trimesh.Trimesh, tol: float) -> tuple[trimesh.Trimesh, int]:
    """The mesh with its flat openings closed, and how many faces were added to close them.
    Openings that aren't flat stay open. A cap that doesn't meet its rim edge for edge is
    left out, and the mesh comes back unchanged if the caps don't join it cleanly."""
    flat = []
    for loop in boundary_loops(mesh):
        pts = mesh.vertices[loop]
        centre = pts.mean(axis=0)
        normal = np.linalg.svd(pts - centre)[2][2]
        if np.abs((pts - centre) @ normal).max() <= FLAT_TOLS * tol:
            flat.append((loop, normal, float(normal @ centre)))
    if not flat:
        return mesh, 0

    vertices = mesh.vertices.copy()
    caps = []
    for group in _coplanar(flat, tol):
        normal = group[0][1]
        normals = [n if n @ normal > 0 else -n for _, n, _ in group]
        offsets = [d if n @ normal > 0 else -d for _, n, d in group]
        normal = np.mean(normals, axis=0)
        normal /= np.linalg.norm(normal)
        offset = float(np.mean(offsets))
        loops = [loop for loop, _, _ in group]
        for loop in loops:
            p = vertices[loop]
            vertices[loop] = p - np.outer(p @ normal - offset, normal)
        for outer, holes in _faces(loops, vertices, normal):
            cap = _cap(vertices[outer], [vertices[h] for h in holes])
            if cap is not None:
                caps.append(([outer, *holes], *cap))

    # Each cap's rim points are the scan's own rim points; its new inside points go last.
    scan_edges = {tuple(e) for e in _directed(mesh.faces)}
    faces = [mesh.faces]
    for rims, points, tris in caps:
        index = {tuple(np.round(vertices[i], 9)): int(i) for rim in rims for i in rim}
        ids = [index.get(tuple(np.round(p, 9)), -1) for p in points]
        fresh = [k for k, i in enumerate(ids) if i < 0]
        for k, i in zip(fresh, range(len(vertices), len(vertices) + len(fresh)), strict=True):
            ids[k] = i
        cap = np.asarray(ids)[tris]
        # The cap's own border must be its rims, edge for edge, or it would leave a crack.
        if _border(cap) != {e for rim in rims for e in _loop_edges(rim)}:
            continue
        vertices = np.vstack([vertices, points[fresh]])
        # A closed mesh runs each edge once each way: the cap runs its rim against the scan.
        if any(tuple(e) in scan_edges for e in _directed(cap)):
            cap = cap[:, ::-1]
        faces.append(cap)
    if len(faces) == 1:
        return mesh, 0
    closed = trimesh.Trimesh(vertices, np.vstack(faces), process=False)
    # Every edge on at most two faces, running once each way.
    directed = _directed(closed.faces)
    if len(np.unique(directed, axis=0)) < len(directed):
        return mesh, 0
    if closed.is_watertight and closed.volume < 0:
        closed.invert()
    return closed, sum(len(f) for f in faces[1:])


def _loop_edges(loop: np.ndarray) -> set[tuple[int, int]]:
    return {tuple(sorted(e)) for e in zip(loop.tolist(), np.roll(loop, -1).tolist(), strict=True)}


def _border(faces: np.ndarray) -> set[tuple[int, int]]:
    edges, counts = np.unique(np.sort(_directed(faces), axis=1), axis=0, return_counts=True)
    return {tuple(e) for e in edges[counts == 1].tolist()}


def _directed(faces: np.ndarray) -> np.ndarray:
    return np.stack([faces, np.roll(faces, -1, axis=1)], axis=2).reshape(-1, 2)


def boundary_loops(mesh: trimesh.Trimesh) -> list[np.ndarray]:
    """The mesh's open rims as closed loops of vertex indices. A rim that touches itself
    (a vertex on more than two open edges) is left out: it can't be told apart cleanly."""
    edges = mesh.edges_sorted
    _, index, counts = np.unique(edges, axis=0, return_index=True, return_counts=True)
    open_edges = edges[index[counts == 1]]
    if len(open_edges) == 0:
        return []
    degree = np.bincount(open_edges.ravel(), minlength=len(mesh.vertices))
    loops = []
    for component in trimesh.graph.connected_components(open_edges, min_len=3):
        if (degree[component] != 2).any():
            continue
        loops.append(_walk(open_edges, component))
    return loops


def _walk(edges: np.ndarray, component: np.ndarray) -> np.ndarray:
    members = set(component.tolist())
    nxt: dict[int, list[int]] = {}
    for a, b in edges:
        if a in members:
            nxt.setdefault(int(a), []).append(int(b))
            nxt.setdefault(int(b), []).append(int(a))
    start = int(component[0])
    loop, prev, here = [start], -1, start
    while True:
        a, b = nxt[here]
        step = a if a != prev else b
        if step == start:
            break
        loop.append(step)
        prev, here = here, step
    return np.array(loop)


def _coplanar(flat, tol: float) -> list[list]:
    groups: list[list] = []
    cos = np.cos(np.radians(COPLANAR_DEG))
    for item in flat:
        _, n, d = item
        for group in groups:
            _, gn, gd = group[0]
            sign = 1.0 if n @ gn > 0 else -1.0
            if abs(n @ gn) >= cos and abs(sign * d - gd) <= tol:
                group.append(item)
                break
        else:
            groups.append([item])
    return groups


def _faces(loops, vertices, normal) -> list[tuple[np.ndarray, list[np.ndarray]]]:
    """Sort the rims on one plane into outlines and the holes inside them (even-odd)."""
    u = np.cross(normal, [1.0, 0.0, 0.0] if abs(normal[0]) < 0.9 else [0.0, 1.0, 0.0])
    u /= np.linalg.norm(u)
    v = np.cross(normal, u)
    flat = [np.c_[vertices[loop] @ u, vertices[loop] @ v] for loop in loops]
    depth = [
        sum(_inside(flat[i][0], flat[j]) for j in range(len(loops)) if j != i)
        for i in range(len(loops))
    ]
    faces = []
    for i, loop in enumerate(loops):
        if depth[i] % 2:
            continue
        holes = [
            loops[j]
            for j in range(len(loops))
            if depth[j] == depth[i] + 1 and _inside(flat[j][0], flat[i])
        ]
        faces.append((loop, holes))
    return faces


def _inside(point: np.ndarray, polygon: np.ndarray) -> bool:
    x, y = point
    a, b = polygon, np.roll(polygon, -1, axis=0)
    crosses = (a[:, 1] > y) != (b[:, 1] > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        at = a[:, 0] + (y - a[:, 1]) * (b[:, 0] - a[:, 0]) / (b[:, 1] - a[:, 1])
    return bool(np.count_nonzero(crosses & (x < at)) % 2)


def _cap(outer: np.ndarray, holes: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray] | None:
    """The flat face inside the rims as points and triangles, or None if it can't be made
    (a rim too small or too thin to hold a face)."""

    def wire(points: np.ndarray) -> cq.Wire:
        return cq.Wire.makePolygon([cq.Vector(*p) for p in points], close=True)

    try:
        face = cq.Face.makeFromWires(wire(outer), [wire(h) for h in holes])
        verts, tris = face.tessellate(1.0, 0.5)
    except Exception:  # OpenCASCADE refuses degenerate outlines in several ways
        return None
    if not tris:
        return None
    return np.array([v.toTuple() for v in verts]), np.array(tris, dtype=int)
