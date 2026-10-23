"""Synthetic test meshes with known ground truth, optionally with scanner-like noise."""

from __future__ import annotations

import numpy as np
import trimesh


def add_noise(mesh: trimesh.Trimesh, sigma_mm: float, seed: int = 0) -> trimesh.Trimesh:
    """Jitter vertices along their normals, like a structured-light scanner."""
    rng = np.random.default_rng(seed)
    noisy = mesh.copy()
    offsets = rng.normal(0.0, sigma_mm, size=len(noisy.vertices))
    noisy.vertices = noisy.vertices + noisy.vertex_normals * offsets[:, None]
    return noisy


def box(size=(40.0, 30.0, 20.0), subdivisions: int = 3) -> trimesh.Trimesh:
    mesh = trimesh.creation.box(extents=size)
    for _ in range(subdivisions):
        mesh = mesh.subdivide()
    return mesh


def cylinder(radius: float = 10.0, height: float = 30.0, sections: int = 96) -> trimesh.Trimesh:
    return trimesh.creation.cylinder(radius=radius, height=height, sections=sections)


def plate_with_hole(
    size=(60.0, 40.0, 8.0), hole_radius: float = 6.0, sections: int = 96
) -> trimesh.Trimesh:
    plate = trimesh.creation.box(extents=size)
    tool = trimesh.creation.cylinder(radius=hole_radius, height=size[2] * 2, sections=sections)
    return plate.difference(tool, engine="manifold")
