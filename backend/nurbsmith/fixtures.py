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


def l_bracket(base=(40.0, 20.0, 5.0), upright_height: float = 30.0) -> trimesh.Trimesh:
    """Non-convex: a base plate with an upright wall at one end."""
    bx, by, bz = base
    plate = trimesh.creation.box(extents=base)
    wall = trimesh.creation.box(
        extents=(bz, by, upright_height),
        transform=trimesh.transformations.translation_matrix(
            ((bx - bz) / 2, 0, (upright_height - bz) / 2)
        ),
    )
    return plate.union(wall, engine="manifold")


def stepped_block() -> trimesh.Trimesh:
    """Three steps: lots of concave and convex edges, and 4-plane corners."""
    parts = [
        trimesh.creation.box(
            extents=(60.0 - 20 * i, 30.0, 10.0),
            transform=trimesh.transformations.translation_matrix((10.0 * i, 0, 5.0 + 10 * i)),
        )
        for i in range(3)
    ]
    return trimesh.boolean.union(parts, engine="manifold")


def frame(outer=(50.0, 40.0, 6.0), window=(30.0, 20.0)) -> trimesh.Trimesh:
    """A plate with a rectangular through-window: faces with inner loops."""
    plate = trimesh.creation.box(extents=outer)
    cut = trimesh.creation.box(extents=(window[0], window[1], outer[2] * 2))
    return plate.difference(cut, engine="manifold")


def subdivided(mesh: trimesh.Trimesh, max_edge: float = 3.0) -> trimesh.Trimesh:
    """Split long triangles so noise looks like a real scan's dense sampling."""
    verts, faces = trimesh.remesh.subdivide_to_size(mesh.vertices, mesh.faces, max_edge=max_edge)
    return trimesh.Trimesh(verts, faces)


def rotated(mesh: trimesh.Trimesh, seed: int = 1) -> trimesh.Trimesh:
    """Arbitrary orientation, so tests do not lean on world axes."""
    out = mesh.copy()
    out.apply_transform(
        trimesh.transformations.random_rotation_matrix(np.random.default_rng(seed).random(3))
    )
    return out
