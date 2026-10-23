"""Mesh to STEP conversion pipeline: flat faces (M1) plus holes, bosses and fillets (M2)."""

from __future__ import annotations

from dataclasses import dataclass, field

import cadquery as cq
import fast_simplification
import numpy as np
import trimesh

from .brep import UnsupportedGeometry, check_supported, merge_coplanar, rebuild_solid, snap_planes
from .cells import cell_solid
from .cylinders import consolidate, find_cylinders, planes_around, shared_lengths
from .features import Feature, apply_features, plan_features
from .quality import FitScore, ScanQuality, deviations, fit_score, scan_quality
from .segment import Geo, Region, segment_planes
from .step_io import face_type_counts
from .tolerance import ToleranceLevel

__all__ = ["ConversionResult", "UnsupportedGeometry", "convert", "repair"]

MIN_FEATURE_TOLS = 5.0
LOOSE_PIECE_SHARE = 0.1
# Dense scans are decimated to about this many triangles before segmentation.
WORKING_FACES = 60_000
MIN_WORKING_FACES = 5_000
COARSE_DIVISIONS = 150


@dataclass
class ConversionResult:
    solid: cq.Solid
    scan_mesh: trimesh.Trimesh
    """The repaired input; deviation_mm is indexed by its vertices."""
    regions: list[Region]
    scan: ScanQuality
    fit: FitScore
    deviation_mm: np.ndarray
    """Per scan vertex, distance to the model. Drives the colour map."""
    features: list[Feature] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    """Approximations made, for the user to know about."""

    def report(self) -> dict:
        bb = self.solid.BoundingBox()
        return {
            "faces": len(self.solid.Faces()),
            "face_types": face_type_counts(self.solid),
            "volume_mm3": round(self.solid.Volume(), 3),
            "size_mm": [round(bb.xlen, 3), round(bb.ylen, 3), round(bb.zlen, 3)],
            "scan_quality": self.scan.as_dict(),
            "fit": self.fit.as_dict(),
            "features": [f.as_dict() for f in self.features if f.kind != "sharp"],
            "notes": self.notes,
        }


def repair(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    mesh = mesh.copy()
    mesh.merge_vertices()
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.update_faces(mesh.unique_faces())
    mesh.remove_unreferenced_vertices()
    mesh = drop_loose_pieces(mesh)
    trimesh.repair.fix_normals(mesh)
    trimesh.repair.fill_holes(mesh)
    return mesh


def drop_loose_pieces(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Keep the part, drop what floats around it: bits of turntable, stray scan fragments.

    Anything not connected to the part and under a tenth of its surface area goes. The count
    is kept in mesh.metadata["loose_pieces"] for the scan report.
    """
    labels = trimesh.graph.connected_component_labels(mesh.face_adjacency, len(mesh.faces))
    areas = np.bincount(labels, weights=mesh.area_faces)
    keep = areas >= LOOSE_PIECE_SHARE * areas.max()
    dropped = int((~keep).sum())
    if dropped:
        mesh.update_faces(keep[labels])
        mesh.remove_unreferenced_vertices()
    mesh.metadata["loose_pieces"] = dropped + int(mesh.metadata.get("loose_pieces", 0))
    return mesh


def working_mesh(mesh: trimesh.Trimesh, max_faces: int = WORKING_FACES) -> trimesh.Trimesh:
    """A lighter copy of a dense scan to segment and fit on.

    Scanner exports run to millions of tiny triangles whose normals are mostly noise; quadric
    decimation keeps the shape (and sharp edges) with far fewer, better-shaped triangles. Fit
    scores and deviations are still measured against the full scan. Decimation can leave a few
    defects (doubled faces, fins, specks), which are cleaned up; if the result is still not a
    closed surface where the scan was, the full scan is used instead.
    """
    if len(mesh.faces) < MIN_WORKING_FACES:
        # A coarse mesh (a CAD export) has huge triangles that say little about where a curve
        # starts; split them to a scan-like size.
        size = float(np.linalg.norm(mesh.extents)) / COARSE_DIVISIONS
        verts, faces = trimesh.remesh.subdivide_to_size(mesh.vertices, mesh.faces, size)
        return trimesh.Trimesh(verts, faces)
    if len(mesh.faces) <= max_faces:
        return mesh
    verts, faces = fast_simplification.simplify(mesh.vertices, mesh.faces, target_count=max_faces)
    # A face and its mirror image: a collapsed sliver. Drop both.
    _, inverse, counts = np.unique(
        np.sort(faces, axis=1), axis=0, return_inverse=True, return_counts=True
    )
    faces = faces[counts[inverse.ravel()] == 1]
    light = trimesh.Trimesh(verts, faces)
    # Specks: tiny loose pieces.
    parts = light.split(only_watertight=False)
    biggest = max(p.area for p in parts)
    light = _manifold(
        repair(trimesh.util.concatenate([p for p in parts if p.area >= 0.01 * biggest]))
    )
    if mesh.is_watertight and not light.is_watertight:
        return mesh
    return light


def _manifold(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Remove the faces around fins (an edge shared by more than two faces) and around edges
    two faces run the same way along, which decimation and hole filling occasionally leave."""
    for _ in range(5):
        directed = np.stack([mesh.faces, np.roll(mesh.faces, -1, axis=1)], axis=2).reshape(-1, 2)
        _, inv_u, count_u = np.unique(
            np.sort(directed, axis=1), axis=0, return_inverse=True, return_counts=True
        )
        _, inv_d, count_d = np.unique(directed, axis=0, return_inverse=True, return_counts=True)
        bad = (count_u[inv_u.ravel()] > 2) | (count_d[inv_d.ravel()] > 1)
        if not bad.any():
            break
        keep = np.ones(len(mesh.faces), bool)
        keep[np.flatnonzero(bad) // 3] = False
        mesh.update_faces(keep)
        mesh.remove_unreferenced_vertices()
    return mesh


def convert(mesh: trimesh.Trimesh, level: ToleranceLevel) -> ConversionResult:
    mesh = repair(mesh)
    scan = scan_quality(mesh, level)

    # Never chase scan noise: if the scan is rougher than the chosen level, fit to what the
    # noise allows (and say so in the scan report) instead of modelling artifacts.
    working = level
    tol = scan.effective_tolerance_mm
    if tol > level.fit_tolerance_mm:
        # Features smaller than a few tolerances can't be told from noise. Use the ratio of the
        # loosest preset rather than scaling the level's own, so a widened Functional or
        # Precise never ends up coarser than Rough would be.
        working = level.with_overrides(
            fit_tolerance_mm=tol,
            min_feature_mm=max(level.min_feature_mm, MIN_FEATURE_TOLS * tol),
        )

    work = Geo.of(working_mesh(mesh))
    regions = segment_planes(work, working)
    patch_of, patches = find_cylinders(work, regions, working)
    planes, _ = planes_around(work, regions, patch_of, patches, working)
    patches = [p for p in patches if (patch_of == p.id).any()]
    for p in patches:
        p.faces = np.flatnonzero(patch_of == p.id)
    snap_planes(work, planes, working)
    planes = merge_coplanar(work, planes, working)
    planes, patches, labels = consolidate(work, planes, patches, working)
    note = check_supported(planes, working)

    features, caps, skipped = plan_features(
        work, planes, patches, shared_lengths(work, labels), working
    )
    base_planes = [r for r in planes if r.id not in caps]
    other_side = {
        f.patch: {f.planes[0]: f.planes[1], f.planes[1]: f.planes[0]}
        for f in features
        if f.kind in ("fillet", "sharp")
    }

    def through(rid: int, nb: int) -> int | None:
        return other_side.get(nb, {}).get(rid)

    try:
        base = rebuild_solid(work, base_planes, working.fit_tolerance_mm, labels, through)
    except (UnsupportedGeometry, ValueError):
        # Gaps, warps or ragged edges kept the traced faces from closing; build from blocks.
        base = cell_solid(work, base_planes, working.fit_tolerance_mm, working.min_feature_mm)
    solid, failed = apply_features(base, features, planes)
    dist = deviations(mesh, solid, working.fit_tolerance_mm)
    return ConversionResult(
        solid,
        mesh,
        planes,
        scan,
        fit_score(mesh, dist, level),
        dist,
        features,
        [n for n in (note, skipped, failed) if n],
    )
