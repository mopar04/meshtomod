"""STEP export and import via OpenCASCADE (CadQuery)."""

from __future__ import annotations

from pathlib import Path

import cadquery as cq


def write_step(shape: cq.Shape | cq.Workplane, path: str | Path) -> Path:
    path = Path(path)
    if isinstance(shape, cq.Workplane):
        shape = shape.val()
    if not shape.isValid():
        raise ValueError("Refusing to export an invalid B-rep")
    cq.exporters.export(shape, str(path), exportType="STEP")
    return path


def read_step(path: str | Path) -> cq.Shape:
    return cq.importers.importStep(str(path)).val()


def face_type_counts(shape: cq.Shape) -> dict[str, int]:
    """Count faces by surface type (PLANE, CYLINDER, BSPLINE, ...). Used to check fit quality."""
    counts: dict[str, int] = {}
    for face in shape.Faces():
        kind = face.geomType()
        counts[kind] = counts.get(kind, 0) + 1
    return counts
