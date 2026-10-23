"""Confidence metrics: how clean is the scan, and how well does the model match it."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import cadquery as cq
import numpy as np
import trimesh
from scipy.spatial import cKDTree

from .tolerance import LEVELS, ToleranceLevel

NOISE_NEIGHBOURS = 16
# A fit tolerance must cover this many sigma of scan noise to separate noise from features.
NOISE_SIGMAS = 3.5


@dataclass(frozen=True)
class ScanQuality:
    noise_mm: float
    """Estimated scanner noise (1 sigma) from local plane fits."""
    open_edges: int
    """Mesh edges with only one triangle: gaps in scan coverage."""
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
    """Typical RMS of local plane fits, using only neighbours that face the same way, so patches
    that straddle a sharp edge do not count as noise."""
    verts, normals = mesh.vertices, mesh.vertex_normals
    k = min(NOISE_NEIGHBOURS, len(verts))
    if k < 6:
        return 0.0
    tree = cKDTree(verts)
    step = max(1, len(verts) // 4000)  # subsample large scans
    centres = np.arange(0, len(verts), step)
    _, idx = tree.query(verts[centres], k=k)
    same_side = np.einsum("ijk,ik->ij", normals[idx], normals[centres]) > np.cos(np.radians(30))

    rms = []
    for row, keep in zip(idx, same_side, strict=True):
        pts = verts[row[keep]]
        if len(pts) < 6:
            continue
        s = np.linalg.svd(pts - pts.mean(axis=0), compute_uv=False)[-1]
        # The fit absorbs 3 degrees of freedom; correct the bias.
        rms.append(s / np.sqrt(len(pts) - 3))
    return float(np.median(rms)) if rms else 0.0


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
        gap = f"The scan has gaps ({open_edges} open edges); they will be closed by the model."
        warning = f"{warning} {gap}" if warning else gap

    return ScanQuality(
        noise_mm=round(noise, 4),
        open_edges=open_edges,
        suggested_level=(suggested or LEVELS["rough"]).key,
        effective_tolerance_mm=effective,
        warning=warning,
    )


def deviations(mesh: trimesh.Trimesh, shape: cq.Shape, tol: float) -> np.ndarray:
    """Distance from every scan vertex to the model surface."""
    verts, tris = shape.tessellate(tol / 4, angularTolerance=0.1)
    model = trimesh.Trimesh(np.array([v.toTuple() for v in verts]), np.array(tris), process=False)
    _, dist, _ = trimesh.proximity.closest_point(model, mesh.vertices)
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
