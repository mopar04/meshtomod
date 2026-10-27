"""Loading and summarising input meshes."""

from __future__ import annotations

import io
from dataclasses import asdict, dataclass
from pathlib import PurePath

import numpy as np
import trimesh

SUPPORTED_FORMATS = ("stl", "obj", "ply", "3mf")


class MeshLoadError(ValueError):
    pass


@dataclass(frozen=True)
class MeshSummary:
    vertices: int
    faces: int
    watertight: bool
    bounds_min: list[float]
    bounds_max: list[float]
    extents: list[float]
    area: float
    volume: float | None

    def as_dict(self) -> dict:
        return asdict(self)


def file_format(filename: str) -> str:
    ext = PurePath(filename).suffix.lower().lstrip(".")
    if ext not in SUPPORTED_FORMATS:
        raise MeshLoadError(f"Unsupported format {ext!r}; expected one of {SUPPORTED_FORMATS}")
    return ext


def load_mesh(data: bytes, filename: str) -> trimesh.Trimesh:
    """Load a mesh from raw bytes, merging multi-body scenes into one mesh."""
    fmt = file_format(filename)
    try:
        loaded = trimesh.load(io.BytesIO(data), file_type=fmt, force="mesh", process=True)
    except Exception as exc:  # trimesh raises many different types
        raise MeshLoadError(f"Could not read {filename}: {exc}") from exc
    if not isinstance(loaded, trimesh.Trimesh) or len(loaded.faces) == 0:
        raise MeshLoadError(f"{filename} contains no triangles")
    return loaded


def summarize(mesh: trimesh.Trimesh) -> MeshSummary:
    lo, hi = mesh.bounds
    return MeshSummary(
        vertices=len(mesh.vertices),
        faces=len(mesh.faces),
        watertight=bool(mesh.is_watertight),
        bounds_min=np.round(lo, 6).tolist(),
        bounds_max=np.round(hi, 6).tolist(),
        extents=np.round(mesh.extents, 6).tolist(),
        area=round(float(mesh.area), 6),
        volume=round(float(mesh.volume), 6) if mesh.is_watertight else None,
    )
