"""Mesh to STEP conversion pipeline (M1: flat-faced parts)."""

from __future__ import annotations

from dataclasses import dataclass

import cadquery as cq
import numpy as np
import trimesh

from .brep import UnsupportedGeometry, check_supported, merge_coplanar, rebuild_solid, snap_planes
from .quality import FitScore, ScanQuality, deviations, fit_score, scan_quality
from .segment import Region, segment_planes
from .step_io import face_type_counts
from .tolerance import ToleranceLevel

__all__ = ["ConversionResult", "UnsupportedGeometry", "convert", "repair"]


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

    def report(self) -> dict:
        bb = self.solid.BoundingBox()
        return {
            "faces": len(self.solid.Faces()),
            "face_types": face_type_counts(self.solid),
            "volume_mm3": round(self.solid.Volume(), 3),
            "size_mm": [round(bb.xlen, 3), round(bb.ylen, 3), round(bb.zlen, 3)],
            "scan_quality": self.scan.as_dict(),
            "fit": self.fit.as_dict(),
        }


def repair(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    mesh = mesh.copy()
    mesh.merge_vertices()
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.update_faces(mesh.unique_faces())
    mesh.remove_unreferenced_vertices()
    trimesh.repair.fix_normals(mesh)
    trimesh.repair.fill_holes(mesh)
    return mesh


def convert(mesh: trimesh.Trimesh, level: ToleranceLevel) -> ConversionResult:
    mesh = repair(mesh)
    scan = scan_quality(mesh, level)

    # Never chase scan noise: if the scan is rougher than the chosen level, fit to what the
    # noise allows (and say so in the scan report) instead of modelling artifacts.
    working = level
    if scan.effective_tolerance_mm > level.fit_tolerance_mm:
        widen = scan.effective_tolerance_mm / level.fit_tolerance_mm
        working = level.with_overrides(
            fit_tolerance_mm=scan.effective_tolerance_mm,
            min_feature_mm=level.min_feature_mm * widen,
        )

    regions = segment_planes(mesh, working)
    snap_planes(mesh, regions, working)
    regions = merge_coplanar(mesh, regions, working)
    check_supported(regions, scan.noise_mm, working.fit_tolerance_mm)

    solid = rebuild_solid(mesh, regions, working.fit_tolerance_mm)
    dist = deviations(mesh, solid, working.fit_tolerance_mm)
    return ConversionResult(solid, mesh, regions, scan, fit_score(mesh, dist, level), dist)
