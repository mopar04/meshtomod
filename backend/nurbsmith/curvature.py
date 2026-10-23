"""Tell flat areas of a scan from curved ones.

Each vertex gets the RMS of a plane fitted to its neighbourhood. On a flat face that is just
scanner noise; near a curve (or a sharp edge) it is larger. Planes are only seeded on flat
triangles, so a hole or fillet is never mistaken for a run of narrow flat strips. Sharp-edge
bands also read as not flat, but plane growing absorbs them afterwards because they still fit
the neighbouring plane.
"""

from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree

from .segment import Geo

MAX_CENTRES = 30000
MAX_PATCH = 48
MIN_PATCH = 8
# Neighbourhood radius: a few triangles across, and wide enough to see past the noise.
RADIUS_EDGES = 2.0
RADIUS_SIGMAS = 6.0
# A patch is flat if its residual is within this many sigma of noise (or a hair, when clean).
FLAT_SIGMAS = 2.0
FLAT_FLOOR_MM = 0.004


def flat_faces(mesh: Geo, noise_mm: float) -> np.ndarray:
    verts = mesh.vertices
    edges = verts[mesh.faces[:, [1, 2, 0]]] - verts[mesh.faces]
    edge_len = float(np.median(np.linalg.norm(edges, axis=2)))
    radius = max(RADIUS_EDGES * edge_len, RADIUS_SIGMAS * noise_mm)
    threshold = max(FLAT_SIGMAS * noise_mm, FLAT_FLOOR_MM)

    step = max(1, len(verts) // MAX_CENTRES)
    centre_idx = np.arange(0, len(verts), step)
    tree = cKDTree(verts)
    rng = np.random.default_rng(0)
    rms = np.full(len(centre_idx), np.inf)
    for i, row in enumerate(tree.query_ball_point(verts[centre_idx], radius)):
        if len(row) < MIN_PATCH:
            continue
        if len(row) > MAX_PATCH:
            row = rng.choice(row, MAX_PATCH, replace=False)
        pts = verts[row]
        s = np.linalg.svd(pts - pts.mean(axis=0), compute_uv=False)
        rms[i] = s[2] / np.sqrt(len(pts) - 3)

    if step > 1:
        _, nearest = cKDTree(verts[centre_idx]).query(verts)
        vertex_rms = rms[nearest]
    else:
        vertex_rms = rms
    return (vertex_rms[mesh.faces] <= threshold).all(axis=1)
