"""Merge two partial scans of one part (top and bottom, front and back) into one mesh.

A turntable scanner sees one side of a part at a time; the user flips it and scans again.
The two scans overlap only where both saw the part (usually its sides), so the second is
placed on the first by:

1. Candidate orientations: each scan's two main face directions (the most common normal, then
   the most common one square to it) give a frame; every way of matching the frames axis to
   axis, with the boxes lined up along each axis by their ends or middles, is a start.
2. Point-to-plane ICP that only trusts close pairs, from each start.
3. Picking the pose that best completes the part: overlap, plus how much of each scan's
   open rim (where it stopped seeing the part) the other scan carries on from, less a penalty
   for points in open air in front of the other scan's surface. The rim and air terms rule
   out laying the bottom face on top of the top face, which overlaps just as well on a flat
   part but leaves both rims open, and puts it across any pockets the top scan saw into.
4. A last pass that also pins each scan's rim to the other, point to point: when the scans
   overlap only on upright walls, nothing else says how high the second one sits.

Where the scans overlap, the first scan's surface is kept and the second's dropped, and the
seam between them is stitched with a strip of triangles, so the part is one piece again.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np
import trimesh
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation

SAMPLES = 30000
COARSE_POINTS = 800
FINE_POINTS = 12000
# Candidate poses refined after the coarse round.
REFINE = 6
# A point sitting more than this far in front of the other scan's surface, within the reach
# below, is in space that scan saw as empty.
AIR_MM = 1.0
AIR_REACH_MM = 6.0
AIR_PENALTY = 2.0
AIR_SQUARE = 0.9
# Probes stepped this far past a scan's open rim count as carried on by the other scan when
# within reach of its surface, and not near that scan's own rim (two scans that stop at the
# same place leave the gap open). Only rims at least this share of the longest one count
# (scans are full of small holes).
RIM_STEP_MM = 1.0
RIM_REACH_MM = 1.5
RIM_MIN_SHARE = 0.2
RIM_NEAR_MM = 2.5
# Paired points must face within about 60 degrees of each other.
SAME_FACING = -0.5
# How far apart the final pass first looks for pairs.
REFINE_REACH_MM = 2.0
# Overlap counts pairs within this distance after alignment.
OVERLAP_MM = 0.3
# A pose is only trusted if this share of the second scan lies on the first.
MIN_OVERLAP = 0.05
# Pieces of a scan under this share of its biggest piece's area are specks (as in
# convert.LOOSE_PIECE_SHARE) and left out.
PIECE_SHARE = 0.1
# Where the second scan lies on the first, it is dropped. Noise leaves specks of each kind
# inside the other: patches under this area are given to their surroundings.
SPECK_MM2 = 4.0
# Seam stitching: a stretch of the second scan's rim within this of the first scan's rim is
# joined to it, if at least this many rim points long and if the two stretches are about as
# long as each other (within this ratio).
STITCH_MM = 2.0
STITCH_MIN_POINTS = 4
STITCH_LENGTH_RATIO = 2.0


class MergeError(ValueError):
    pass


@dataclass
class MergeResult:
    mesh: trimesh.Trimesh
    transform: np.ndarray
    """Moves the second scan onto the first."""
    overlap_pct: float
    """Share of the second scan lying on the first."""
    air_pct: float
    """Share of either scan in space the other saw as empty: high means a doubtful fit."""

    def report(self) -> dict:
        return {"overlap_pct": round(self.overlap_pct, 1), "air_pct": round(self.air_pct, 1)}


def merge_scans(first: trimesh.Trimesh, second: trimesh.Trimesh) -> MergeResult:
    a, b = _part(first), _part(second)
    pa, na = _sample(a, 0)
    pb, nb = _sample(b, 1)
    tree_a, tree_b = cKDTree(pa), cKDTree(pb)
    (rim_a, edge_a), (rim_b, edge_b) = _rim_probes(a), _rim_probes(b)
    inner_a, inner_b = _away_from(pa, edge_a), _away_from(pb, edge_b)
    fa, fb = _frame(a), _frame(b)
    rng = np.random.default_rng(0)
    pick = rng.choice(len(pb), min(COARSE_POINTS, len(pb)), replace=False)
    coarse, coarse_n = pb[pick], nb[pick]

    def _air_both(t, every):
        return _air(pb, nb, tree_a, pa, na, t, every) + _air(pa, na, tree_b, pb, nb, _inv(t), every)

    def _score(t, overlap, every):
        back = _inv(t)
        rims = _carried(rim_a, tree_b, inner_b, back) + _carried(rim_b, tree_a, inner_a, t)
        return overlap + rims - AIR_PENALTY * _air_both(t, every)

    starts = []
    for rot in _axis_matches():
        r = fa @ rot @ fb.T
        if np.linalg.det(r) < 0:
            continue
        ea, eb = pa @ fa, (pb @ r.T) @ fa
        ends = [
            (ea[:, k].min() - eb[:, k].min(), ea[:, k].max() - eb[:, k].max(),
             np.median(ea[:, k]) - np.median(eb[:, k]))
            for k in range(3)
        ]  # fmt: skip
        for shift in itertools.product(*ends):
            t = np.eye(4)
            t[:3, :3] = r
            t[:3, 3] = fa @ np.array(shift)
            t, overlap = _icp(coarse, coarse_n, tree_a, pa, na, t, rounds=8, start=4.0, end=0.6)
            starts.append((_score(t, overlap, every=20), t))

    picked: list[np.ndarray] = []
    for _, t in sorted(starts, key=lambda s: -s[0]):
        if not any(_same_pose(t, u) for u in picked):
            picked.append(t)
        if len(picked) == REFINE:
            break
    pick = rng.choice(len(pb), min(FINE_POINTS, len(pb)), replace=False)
    fine, fine_n = pb[pick], nb[pick]
    best = None
    for t in picked:
        t, overlap = _icp(fine, fine_n, tree_a, pa, na, t, rounds=20, start=1.0, end=OVERLAP_MM)
        t, overlap = _refine(pb, nb, edge_b, pa, na, edge_a, tree_a, t)
        score = _score(t, overlap, every=2)
        if best is None or score > best[0]:
            best = (score, t, overlap, _air_both(t, 2))
    if best is None or best[2] < MIN_OVERLAP:
        raise MergeError(
            "The two scans don't overlap enough to line them up. Scan so that both include "
            "the sides of the part."
        )
    _, t, overlap, air = best

    placed = b.copy()
    placed.apply_transform(t)
    # Keep the first scan where both saw the surface.
    placed.update_faces(_beyond(a, placed))
    placed.remove_unreferenced_vertices()
    return MergeResult(_stitched(a, placed), t, 100 * overlap, 100 * air)


def _beyond(a: trimesh.Trimesh, b: trimesh.Trimesh) -> np.ndarray:
    """Which of b's faces lie off a's surface: the ones to keep. Measured from each face's
    corners to a's surface, and cleaned of specks so the kept part ends in a clean rim along
    a's rim instead of a sieve."""
    off = (_surface_distance(a, b.vertices)[b.faces] > OVERLAP_MM).any(axis=1)
    adjacency = b.face_adjacency
    for kind in (True, False):  # specks kept inside the overlap, then holes in what's kept
        same = adjacency[(off[adjacency[:, 0]] == kind) & (off[adjacency[:, 1]] == kind)]
        labels = trimesh.graph.connected_component_labels(same, len(b.faces))
        area = np.bincount(labels, weights=b.area_faces * (off == kind))
        speck = (off == kind) & (area[labels] < SPECK_MM2)
        off[speck] = not kind
    return off


def _surface_distance(mesh: trimesh.Trimesh, points: np.ndarray, k: int = 8) -> np.ndarray:
    """How far each point is from the mesh's surface, measured to the k triangles whose
    centres are nearest (trimesh's exact search holds every pair in memory at once). Missing
    the true nearest triangle can only overstate the distance."""
    triangles = mesh.triangles
    _, near = cKDTree(triangles.mean(axis=1)).query(points, k=min(k, len(triangles)))
    near = near.reshape(len(points), -1)
    out = np.empty(len(points))
    for i in range(0, len(points), 20000):
        rows = near[i : i + 20000]
        pts = np.repeat(points[i : i + 20000], rows.shape[1], axis=0)
        closest = trimesh.triangles.closest_point(triangles[rows.ravel()], pts)
        out[i : i + 20000] = np.linalg.norm(closest - pts, axis=1).reshape(rows.shape).min(axis=1)
    return out


def _stitched(a: trimesh.Trimesh, b: trimesh.Trimesh) -> trimesh.Trimesh:
    """The two scans as one mesh, with each stretch of b's rim that runs along a's rim joined
    to it by a strip of triangles. Without the strip they are separate pieces: the region
    growing stops at the seam, and a small piece (the bottom of a turned part) is dropped as
    loose."""
    verts = np.concatenate([a.vertices, b.vertices])
    off = len(a.vertices)
    loops_a = [loop for loop in _rim_loops(a) if len(loop) >= STITCH_MIN_POINTS]
    if not loops_a:
        return trimesh.util.concatenate([a, b])
    rim_a = np.concatenate(loops_a)
    which = np.concatenate([np.full(len(loop), k) for k, loop in enumerate(loops_a)])
    where = np.concatenate([np.arange(len(loop)) for loop in loops_a])
    tree = cKDTree(a.vertices[rim_a])
    strips = []
    for loop in _rim_loops(b):
        d, j = tree.query(b.vertices[loop])
        for run in _runs(d < STITCH_MM):
            q = loop[run]
            ka = np.bincount(which[j[run]]).argmax()
            if (which[j[run]] != ka).any():
                continue
            la = loops_a[ka]
            n = len(la)
            # Where along a's rim each point lands, unwrapped round the loop. Across the seam
            # the two rims run opposite ways (both scans face outwards), so walking b's run
            # walks a's rim backwards.
            u = where[j[run]].astype(float)
            u = u[0] + np.concatenate([[0.0], np.cumsum((np.diff(u) + n / 2) % n - n / 2)])
            back = int(round(u[0] - u[-1]))
            if back < 1:
                continue
            if len(run) == len(loop) and back >= n - 3:  # the whole rim, round the whole loop
                back, q = n, np.r_[q, q[0]]
            p = la[(int(u[0]) - np.arange(back + 1)) % n]
            la_len = np.linalg.norm(np.diff(a.vertices[p], axis=0), axis=1).sum()
            lb_len = np.linalg.norm(np.diff(b.vertices[q], axis=0), axis=1).sum()
            if max(la_len, lb_len) > STITCH_LENGTH_RATIO * max(min(la_len, lb_len), 1e-9):
                continue
            strips.append(_zip(verts, p, q + off))
    faces = np.concatenate([a.faces, b.faces + off, *strips]) if strips else None
    if faces is None:
        return trimesh.util.concatenate([a, b])
    return trimesh.Trimesh(verts, faces, process=False)


def _rim_loops(mesh: trimesh.Trimesh) -> list[np.ndarray]:
    """The mesh's open rims as closed loops of vertices, each in the direction its faces run
    along it. Rims that touch themselves (a vertex on two rim edges out) are cut there."""
    edges = mesh.edges[trimesh.grouping.group_rows(mesh.edges_sorted, require_count=1)]
    if not len(edges):
        return []
    nxt: dict[int, int] = {}
    for u, v in edges:
        nxt.setdefault(int(u), int(v))
    loops, seen = [], set()
    for start in nxt:
        if start in seen:
            continue
        loop, u = [], start
        while u in nxt and u not in seen:
            seen.add(u)
            loop.append(u)
            u = nxt[u]
        if u == start and len(loop) >= 3:
            loops.append(np.array(loop))
    return loops


def _runs(near: np.ndarray) -> list[np.ndarray]:
    """The stretches of a closed loop where near holds, as index arrays in loop order."""
    n = len(near)
    if near.all():
        return [np.arange(n)]
    if not near.any():
        return []
    start = int(np.flatnonzero(~near)[0]) + 1
    order = (start + np.arange(n)) % n
    runs, cur = [], []
    for i in order:
        if near[i]:
            cur.append(i)
        elif cur:
            runs.append(np.array(cur))
            cur = []
    if cur:
        runs.append(np.array(cur))
    return [r for r in runs if len(r) >= STITCH_MIN_POINTS]


def _zip(verts: np.ndarray, p: np.ndarray, q: np.ndarray) -> np.ndarray:
    """Triangles joining two facing rim stretches, starting at the same end: q runs the way its
    faces run along it, p the opposite way (so p's own rim edges are p[k + 1] to p[k]). Each
    step takes the shorter of the two diagonals."""
    out = []
    i = k = 0
    while i < len(q) - 1 or k < len(p) - 1:
        step_q = k == len(p) - 1 or (
            i < len(q) - 1
            and np.linalg.norm(verts[q[i + 1]] - verts[p[k]])
            <= np.linalg.norm(verts[p[k + 1]] - verts[q[i]])
        )
        if step_q:
            out.append((q[i + 1], q[i], p[k]))
            i += 1
        else:
            out.append((p[k], p[k + 1], q[i]))
            k += 1
    return np.array(out, dtype=np.int64).reshape(-1, 3)


def _part(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """The scan without its specks: every piece at least PIECE_SHARE of the biggest. One side
    of a part can come out in more than one piece (a turned part seen from below: its base,
    and the underside of a step), and each is needed."""
    labels = trimesh.graph.connected_component_labels(mesh.face_adjacency, len(mesh.faces))
    areas = np.bincount(labels, weights=mesh.area_faces)
    out = mesh.copy()
    out.update_faces(areas[labels] >= PIECE_SHARE * areas.max())
    out.remove_unreferenced_vertices()
    return out


def _sample(mesh: trimesh.Trimesh, seed: int) -> tuple[np.ndarray, np.ndarray]:
    pts, faces = trimesh.sample.sample_surface(mesh, SAMPLES, seed=seed)
    return np.asarray(pts), mesh.face_normals[faces]


def _frame(mesh: trimesh.Trimesh) -> np.ndarray:
    """Columns: the most common face direction, the most common one square to it, and
    their cross product."""
    n, w = mesh.face_normals, mesh.area_faces
    first = _dominant(n, w)
    second = _dominant(n, w, first)
    return np.stack([first, second, np.cross(first, second)], axis=1)


def _dominant(n: np.ndarray, w: np.ndarray, square_to: np.ndarray | None = None) -> np.ndarray:
    near = np.cos(np.radians(10))
    pool = np.arange(len(n))
    if square_to is not None:
        pool = pool[np.abs(n @ square_to) < np.sin(np.radians(10))]
        if not len(pool):
            pool = np.arange(len(n))
    rng = np.random.default_rng(0)
    pool = rng.choice(pool, min(len(pool), 20000), replace=False)
    probe = pool[:500]
    counts = (n[pool] @ n[probe].T > near).T @ w[pool]
    d = n[probe[int(np.argmax(counts))]].copy()
    if square_to is not None:
        d -= (d @ square_to) * square_to
    return d / np.linalg.norm(d)


def _axis_matches():
    for perm in itertools.permutations(range(3)):
        for signs in itertools.product([1.0, -1.0], repeat=3):
            m = np.zeros((3, 3))
            m[range(3), perm] = signs
            yield m


def _icp(src, src_n, tree, dst, dst_n, t, rounds, start, end):
    """Point-to-plane ICP over pairs closer than a threshold that shrinks from start to end,
    facing the same way. Returns the pose and the share of src within end of dst."""
    for i in range(rounds):
        limit = start * (end / start) ** (i / max(rounds - 1, 1))
        p = src @ t[:3, :3].T + t[:3, 3]
        d, j = tree.query(p, workers=-1)
        close = (d < limit) & _facing(src_n @ t[:3, :3].T, dst_n[j])
        if close.sum() < 30:
            break
        q, n, p = dst[j[close]], dst_n[j[close]], p[close]
        step = np.linalg.lstsq(
            np.hstack([np.cross(p, n), n]), ((q - p) * n).sum(axis=1), rcond=None
        )[0]
        move = np.eye(4)
        move[:3, :3] = Rotation.from_rotvec(step[:3]).as_matrix()
        move[:3, 3] = step[3:]
        t = move @ t
    return t, _overlap(src, src_n, tree, dst_n, t, end)


def _facing(n: np.ndarray, m: np.ndarray) -> np.ndarray:
    """Pairs whose normals agree. A face lying on the back of another (a part's underside on
    its floor, which is one wall thickness away) is not a match."""
    return (n * m).sum(axis=1) > SAME_FACING


def _overlap(src, src_n, tree, dst_n, t, within) -> float:
    d, j = tree.query(src @ t[:3, :3].T + t[:3, 3], workers=-1)
    return float(((d < within) & _facing(src_n @ t[:3, :3].T, dst_n[j])).mean())


def _refine(pb, nb, edge_b, pa, na, edge_a, tree_a, t, rounds=15):
    """ICP both ways over every sample, with each scan's rim pinned point to point to the
    other scan. Where the overlap is all walls square to one direction, plane matching can't
    tell how far along it the scans sit; the rims (where each scan stopped seeing the part,
    usually at an edge the other scan carries round) can."""
    for i in range(rounds):
        # Starts wide: the coarse fit can leave the scans a few millimetres apart along
        # walls, where nothing but the rims pulls them together.
        limit = REFINE_REACH_MM * (OVERLAP_MM / REFINE_REACH_MM) ** (i / (rounds - 1))
        r = t[:3, :3]
        b, b_n, b_rim = pb @ r.T + t[:3, 3], nb @ r.T, edge_b @ r.T + t[:3, 3]
        tree_b = cKDTree(b)
        pairs = []
        d, j = tree_a.query(b, workers=-1)
        c = (d < limit) & _facing(b_n, na[j])
        pairs.append(_plane_rows(b[c], pa[j[c]], na[j[c]]))
        d, j = tree_b.query(pa, workers=-1)
        c = (d < limit) & _facing(na, b_n[j])
        pairs.append(_plane_rows(b[j[c]], pa[c], b_n[j[c]]))
        if len(b_rim):
            d, j = tree_a.query(b_rim, workers=-1)
            c = d < limit
            pairs.append(_point_rows(b_rim[c], pa[j[c]]))
        if len(edge_a):
            d, j = tree_b.query(edge_a, workers=-1)
            c = d < limit
            pairs.append(_point_rows(b[j[c]], edge_a[c]))
        a_mat = np.vstack([m for m, _ in pairs])
        if len(a_mat) < 30:
            break
        step = np.linalg.lstsq(a_mat, np.concatenate([v for _, v in pairs]), rcond=None)[0]
        move = np.eye(4)
        move[:3, :3] = Rotation.from_rotvec(step[:3]).as_matrix()
        move[:3, 3] = step[3:]
        t = move @ t
    return t, _overlap(pb, nb, tree_a, na, t, OVERLAP_MM)


def _plane_rows(p, q, n):
    """Linearised p moving by (w, v) onto the plane through q with normal n."""
    return np.hstack([np.cross(p, n), n]), ((q - p) * n).sum(axis=1)


def _point_rows(p, q):
    """Linearised p moving by (w, v) onto q: q - p = w x p + v, one row per axis."""
    eye = np.eye(3)
    rows = [np.hstack([np.cross(p, e), np.tile(e, (len(p), 1))]) for e in eye]
    return np.vstack(rows), (q - p).T.ravel()


def _air(src, src_n, tree, dst, dst_n, t, every) -> float:
    """Share of src points in open air in front of dst's surface, after moving src by t."""
    p = src[::every] @ t[:3, :3].T + t[:3, 3]
    d, j = tree.query(p, workers=-1)
    ahead = ((p - dst[j]) * dst_n[j]).sum(axis=1)
    # Straight out from the surface, not off to the side of it past an edge or into a hole.
    square = ahead > AIR_SQUARE * d
    return float(((ahead > AIR_MM) & square & (d < AIR_REACH_MM)).mean())


def _rim_probes(mesh: trimesh.Trimesh) -> tuple[np.ndarray, np.ndarray]:
    """Points just past the scan's main open rims, carrying on the surface each rim bounds,
    and the points on those rims."""
    edges = mesh.edges_sorted
    unique, first, count = np.unique(edges, axis=0, return_index=True, return_counts=True)
    open_edges, faces = unique[count == 1], mesh.edges_face[first[count == 1]]
    if not len(open_edges):
        return np.zeros((0, 3)), np.zeros((0, 3))
    # Group open edges into rims and keep the long ones.
    graph = trimesh.graph.connected_component_labels(open_edges, mesh.vertices.shape[0])
    rim = graph[open_edges[:, 0]]
    a, b = mesh.vertices[open_edges[:, 0]], mesh.vertices[open_edges[:, 1]]
    length = np.linalg.norm(b - a, axis=1)
    per_rim = np.bincount(rim, weights=length)
    keep = per_rim[rim] >= RIM_MIN_SHARE * per_rim.max()
    a, b, faces, length = a[keep], b[keep], faces[keep], length[keep]
    along = (b - a) / (length[:, None] + 1e-12)
    out = np.cross(along, mesh.face_normals[faces])
    mid = (a + b) / 2
    out *= np.sign(((mid - mesh.triangles_center[faces]) * out).sum(axis=1))[:, None]
    return mid + RIM_STEP_MM * out, mid


def _away_from(points: np.ndarray, rim: np.ndarray) -> np.ndarray:
    """Which points are clear of the scan's own rim."""
    if not len(rim):
        return np.ones(len(points), dtype=bool)
    d, _ = cKDTree(rim).query(points, workers=-1)
    return d > RIM_NEAR_MM


def _carried(probes: np.ndarray, tree: cKDTree, inner: np.ndarray, t: np.ndarray) -> float:
    """Share of rim probes (moved by t into the other scan's frame) that the other scan's
    surface reaches away from its own rim."""
    if not len(probes):
        return 0.0
    d, j = tree.query(probes @ t[:3, :3].T + t[:3, 3], workers=-1)
    return float(((d < RIM_REACH_MM) & inner[j]).mean())


def _inv(t: np.ndarray) -> np.ndarray:
    return np.linalg.inv(t)


def _same_pose(t: np.ndarray, u: np.ndarray) -> bool:
    turned = np.trace(t[:3, :3].T @ u[:3, :3]) > 2.99
    return turned and np.linalg.norm(t[:3, 3] - u[:3, 3]) < 1.0
