"""Confidence metrics: how clean is the scan, and how well does the model match it."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import cadquery as cq
import numpy as np
import trimesh
from scipy.spatial import cKDTree
from scipy.stats import chi2

from .progress import stage
from .tolerance import LEVELS, ToleranceLevel

NOISE_NEIGHBOURS = 16
NOISE_QUANTILE = 0.25
LINE_LIKE_RATIO = 0.25
NOISE_PATCH_SIGMAS = 8.0
NOISE_MAX_PATCH = 64
NOISE_DENSIFY_DIVISIONS = 100
NOISE_DENSE_VERTICES = 2000
NOISE_OUTLIER_EDGES = 4.0
DEVIATION_DIVISIONS = 50
DEVIATION_CHUNK = 5_000
# Points further than this many tolerances (plus a model edge) from every model vertex get an
# approximate distance.
FAR_TOLS = 3
# A fit tolerance must cover this many sigma of scan noise to separate noise from features.
NOISE_SIGMAS = 3.5


@dataclass(frozen=True)
class ScanQuality:
    noise_mm: float
    """Estimated scanner noise (1 sigma) from local plane fits."""
    open_edges: int
    """Mesh edges with only one triangle: gaps in scan coverage."""
    loose_pieces: int
    """Small pieces not attached to the part (turntable, stray fragments) that were dropped."""
    suggested_level: str
    """Tightest level whose fit tolerance comfortably exceeds the noise."""
    effective_tolerance_mm: float
    """Tolerance actually used: the chosen level's, widened if the noise demands it."""
    warning: str | None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class FitScore:
    within_tolerance_pct: float
    """Share of scan surface area within the fit tolerance of the model."""
    mean_mm: float
    p95_mm: float
    max_mm: float

    def as_dict(self) -> dict:
        return asdict(self)


def estimate_noise(mesh: trimesh.Trimesh) -> float:
    """Scanner noise (1 sigma) from local plane fits.

    Patches that straddle an edge or a curve read high, so we take a low quantile of the patch
    residuals (the cleanly flat patches) and calibrate it against the chi-square distribution
    pure noise would give. Line-like patches (points strung along one edge of a decimated mesh)
    are skipped: a plane through a line fits them perfectly whatever the noise. A second pass
    widens each patch to several sigma, because a patch smaller than the noise is just a blob
    and fits a plane too well. Vertex normals are not used; on noisy scans they are noise.
    """
    verts = _densified(mesh)
    if len(verts) < NOISE_NEIGHBOURS:
        return 0.0
    tree = cKDTree(verts)
    step = max(1, len(verts) // 4000)  # subsample large scans
    centres = verts[::step]
    dist, idx = tree.query(centres, k=NOISE_NEIGHBOURS)
    sigma = _patch_noise([verts[row] for row in idx])

    radius = max(float(np.median(dist[:, -1])), NOISE_PATCH_SIGMAS * sigma)
    if sigma > 0 and radius > np.median(dist[:, -1]):
        rng = np.random.default_rng(0)
        patches = []
        for row in tree.query_ball_point(centres, radius):
            if len(row) > NOISE_MAX_PATCH:
                row = rng.choice(row, NOISE_MAX_PATCH, replace=False)
            if len(row) >= NOISE_NEIGHBOURS // 2:
                patches.append(verts[row])
        sigma = _patch_noise(patches)
    return sigma


def _densified(mesh: trimesh.Trimesh) -> np.ndarray:
    """Vertices, plus extra points on any triangle much larger than the scan's sampling.

    A coarse mesh (an exported CAD model, a heavily decimated scan) has almost no vertices away
    from its edges, so every patch would straddle one. Points added inside big triangles lie
    exactly on them and show that the faces themselves are flat. On a dense scan only outlier
    triangles are split, since interpolated points would understate the noise.
    """
    max_edge = float(np.linalg.norm(mesh.extents)) / NOISE_DENSIFY_DIVISIONS
    if len(mesh.vertices) >= NOISE_DENSE_VERTICES:
        max_edge = max(max_edge, NOISE_OUTLIER_EDGES * float(np.median(mesh.edges_unique_length)))
    if mesh.edges_unique_length.max() <= max_edge:
        return mesh.vertices
    verts, _ = trimesh.remesh.subdivide_to_size(mesh.vertices, mesh.faces, max_edge)
    return verts


def _patch_noise(patches: list[np.ndarray]) -> float:
    rms, dofs = [], []
    for pts in patches:
        s = np.linalg.svd(pts - pts.mean(axis=0), compute_uv=False)
        if s[1] > LINE_LIKE_RATIO * s[0]:
            rms.append(s[2] / np.sqrt(len(pts) - 3))
            dofs.append(len(pts) - 3)
    if not rms:
        return 0.0
    dof = float(np.median(dofs))
    calibration = np.sqrt(chi2.ppf(NOISE_QUANTILE, dof) / dof)
    return float(np.quantile(rms, NOISE_QUANTILE) / calibration)


GAP_WARNING = "The scan has gaps ({} open edges); they will be closed by the model."


def scan_quality(mesh: trimesh.Trimesh, level: ToleranceLevel) -> ScanQuality:
    noise = estimate_noise(mesh)
    open_edges = int(len(trimesh.grouping.group_rows(mesh.edges_sorted, require_count=1)))

    needed = round(NOISE_SIGMAS * noise, 3)
    ok = [lvl for lvl in LEVELS.values() if lvl.fit_tolerance_mm >= needed]
    suggested = min(ok, key=lambda lvl: lvl.fit_tolerance_mm) if ok else None
    effective = max(level.fit_tolerance_mm, needed)

    warning = None
    if suggested is None:
        warning = (
            f"Scan noise is about \u00b1{noise:.2f} mm, rougher than even the Rough level, "
            f"so the model was fitted to {effective:.2f} mm. A better scan will help more than "
            "any setting."
        )
    elif level.fit_tolerance_mm < needed:
        warning = (
            f"Scan noise is about \u00b1{noise:.2f} mm, too rough for {level.label} "
            f"({level.fit_tolerance_mm} mm), so the model was fitted to {effective:.2f} mm. "
            f"{suggested.label} is the tightest level this scan supports."
        )
    if open_edges:
        gap = GAP_WARNING.format(open_edges)
        warning = f"{warning} {gap}" if warning else gap
    loose = int(mesh.metadata.get("loose_pieces", 0))
    if loose:
        note = f"Ignored {loose} loose piece{'s' if loose > 1 else ''} not attached to the part."
        warning = f"{warning} {note}" if warning else note

    return ScanQuality(
        noise_mm=round(noise, 4),
        open_edges=open_edges,
        loose_pieces=loose,
        suggested_level=(suggested or LEVELS["rough"]).key,
        effective_tolerance_mm=effective,
        warning=warning,
    )


def deviations(mesh: trimesh.Trimesh, shape: cq.Shape, tol: float) -> np.ndarray:
    """Distance from every scan vertex to the model surface."""
    stage("fit")
    return distances(np.asarray(mesh.vertices), shape, tol)


def distances(points: np.ndarray, shape: cq.Shape, tol: float) -> np.ndarray:
    """Distance from each point to the model surface, exact within a few tolerances."""
    verts, tris = shape.tessellate(tol / 4, angularTolerance=0.1)
    verts = np.array([v.toTuple() for v in verts])
    # A big flat face tessellates into long thin triangles whose bounding boxes overlap half
    # the part, which makes the nearest-triangle search explode; split them up first.
    max_edge = float(np.linalg.norm(np.ptp(verts, axis=0))) / DEVIATION_DIVISIONS
    verts, tris = trimesh.remesh.subdivide_to_size(verts, np.array(tris), max_edge)
    model = trimesh.Trimesh(verts, tris, process=False)
    # The exact search gets slow and memory hungry for points far from the model (a part of
    # the scan it does not cover), so those get the distance to the nearest model vertex, an
    # upper bound that is already well out of tolerance.
    dist, _ = cKDTree(model.vertices).query(points)
    near = np.flatnonzero(dist <= FAR_TOLS * tol + max_edge)
    for i in range(0, len(near), DEVIATION_CHUNK):
        idx = near[i : i + DEVIATION_CHUNK]
        dist[idx] = trimesh.proximity.closest_point(model, points[idx])[1]
    return dist


def fit_score(mesh: trimesh.Trimesh, dist: np.ndarray, level: ToleranceLevel) -> FitScore:
    # Weight each vertex by a third of its adjacent triangle area.
    weights = np.zeros(len(mesh.vertices))
    np.add.at(weights, mesh.faces.ravel(), np.repeat(mesh.area_faces / 3, 3))
    within = weights[dist <= level.fit_tolerance_mm].sum() / weights.sum()
    return FitScore(
        within_tolerance_pct=round(float(within) * 100, 2),
        mean_mm=round(float(np.average(dist, weights=weights)), 4),
        p95_mm=round(float(np.percentile(dist, 95)), 4),
        max_mm=round(float(dist.max()), 4),
    )


def deviation_colors(dist: np.ndarray, tol: float) -> np.ndarray:
    """RGBA per vertex: green inside half the tolerance, through yellow at the tolerance, to red
    at twice the tolerance and beyond."""
    t = np.clip(dist / (2 * tol), 0.0, 1.0)
    stops = np.array([0.0, 0.25, 0.5, 1.0])
    colours = np.array([[46, 160, 67], [46, 160, 67], [230, 200, 40], [214, 48, 49]], float)
    rgb = np.stack([np.interp(t, stops, colours[:, c]) for c in range(3)], axis=1)
    return np.hstack([rgb, np.full((len(t), 1), 255.0)]).astype(np.uint8)
