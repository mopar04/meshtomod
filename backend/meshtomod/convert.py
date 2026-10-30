"""Mesh to STEP conversion pipeline: flat faces (M1), holes, bosses and fillets (M2), cones,
spheres and tori (M3a), freeform surfaces (M3b), and one-sided scans thickened into walls
(M3d)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import cadquery as cq
import fast_simplification
import numpy as np
import trimesh

from .brep import (
    UnsupportedGeometry,
    check_supported,
    curved_regions,
    merge_coplanar,
    rebuild_solid,
    snap_planes,
)
from .cells import Corner, cell_solid
from .cylinders import consolidate, find_cylinders, planes_around, shared_lengths
from .ends import close_flat_ends
from .features import EDGE_KINDS, Feature, apply_features, cylinder_between, plan_features
from .freeform import Freeform, find_freeform
from .orient import TurnAxis, distinct_axes
from .progress import stage
from .quality import GAP_WARNING, FitScore, ScanQuality, deviations, fit_score, scan_quality
from .revolve import Revolved, find_revolved
from .segment import Geo, Region, segment_planes
from .step_io import face_type_counts
from .thicken import Skin, ThickenOptions, find_skin, thicken
from .tolerance import ToleranceLevel

__all__ = ["ConversionResult", "ThickenOptions", "UnsupportedGeometry", "convert", "repair"]

MIN_FEATURE_TOLS = 5.0
LOOSE_PIECE_SHARE = 0.1
# Below this share of the scan within the working tolerance, the model is only part of the
# part: it is still returned, marked partial, so the deviation map shows where it misses.
MIN_PLAUSIBLE_FIT_PCT = 90.0
PARTIAL_NOTE = (
    "Partial match: the model matches only {:.0f}% of the scan, so parts of it are not "
    "the part. The deviation map shows where (red is furthest off); check those areas "
    "before using the model."
)
CLOSED_NOTE = (
    "The scan was open where a flat face wasn't scanned (the side the part stood on, or an "
    "end). It was closed flat across the scan's edge there; check that face is right."
)
# Dense scans are decimated to about this many triangles before segmentation.
WORKING_FACES = 60_000
MIN_WORKING_FACES = 5_000
COARSE_DIVISIONS = 150
DECIMATION_AGGRESSION = 4
DECIMATION_PASSES = 6
# Solids under this share of the part's volume after adding or cutting a freeform surface.
SPECK_SHARE = 1e-6


@dataclass
class ConversionResult:
    solid: cq.Shape  # one solid, or a compound when the scan has separate bodies
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
    revolved: list[Revolved] = field(default_factory=list)
    """Cones, spheres and tori: the whole part if it is turned, or features on its faces."""
    freeform: list[Freeform] = field(default_factory=list)
    """B-spline surfaces: a whole curved side of the part, or a mound or scoop on a face."""
    skin: Skin | None = None
    """A one-sided scan, thickened into the wall that is the solid."""
    volume_mm3: float | None = None
    """Measured from a fine triangulation where OpenCASCADE's own integration is unreliable
    (a thickened skin's long B-spline edges throw it off by a few percent)."""
    partial: bool = False
    """Matches under MIN_PLAUSIBLE_FIT_PCT of the scan: returned with a warning, not refused."""

    def report(self) -> dict:
        bb = self.solid.BoundingBox()
        return {
            "faces": len(self.solid.Faces()),
            "face_types": face_type_counts(self.solid),
            "volume_mm3": round(
                self.volume_mm3 if self.volume_mm3 is not None else self.solid.Volume(), 3
            ),
            "size_mm": [round(bb.xlen, 3), round(bb.ylen, 3), round(bb.zlen, 3)],
            "scan_quality": self.scan.as_dict(),
            "fit": self.fit.as_dict(),
            "features": ([self.skin.as_dict()] if self.skin else [])
            + [r.as_dict() for r in self.revolved]
            + [f.as_dict() for f in self.freeform]
            + [f.as_dict() for f in self.features if f.kind != "sharp"],
            "notes": self.notes,
            "partial": self.partial,
            "axes": [a.as_dict() for a in self.axes()],
        }

    def axes(self) -> list[TurnAxis]:
        """Lines the part can be stood on: a turned part's axis first, then turned features,
        round bodies, bosses and holes, one per line."""
        found = []
        for r in sorted(self.revolved, key=lambda r: r.kind != "turned"):
            diameters = r.dims.get("diameters_mm") or [
                r.dims.get("head_mm") or r.dims.get("diameter_mm") or 0.0
            ]
            found.append(TurnAxis(r.axis.point, r.axis.direction, r.kind, max(diameters)))
        for kind in ("body", "boss", "hole"):
            found += [
                TurnAxis(f.start, f.axis, kind, 2 * f.radius)
                for f in self.features
                if f.kind == kind
            ]
        return distinct_axes(found)


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
    # Gentle passes: at the library's default aggressiveness a few collapses cut across
    # corners and leave big triangles millimetres off the surface.
    verts, faces = mesh.vertices, mesh.faces
    for _ in range(DECIMATION_PASSES):
        verts, faces = fast_simplification.simplify(
            verts, faces, target_count=max_faces, agg=DECIMATION_AGGRESSION
        )
        if len(faces) <= 1.05 * max_faces:
            break
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


def convert(
    mesh: trimesh.Trimesh, level: ToleranceLevel, thickness: ThickenOptions | None = None
) -> ConversionResult:
    """thickness: how to thicken a one-sided scan (an open sheet, not a closed part) into a
    wall. Without it, such a scan gets the defaults and a note saying so."""
    stage("check")
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

    stage("simplify")
    light = working_mesh(mesh)
    if not mesh.is_watertight:
        skin = find_skin(light, working, thickness or ThickenOptions(), np.random.default_rng(0))
        if skin is not None:
            return convert_skin(mesh, skin, scan, level, working, defaulted=thickness is None)
    # Only the working mesh is closed: the fit is still measured on what was scanned.
    light, capped = close_flat_ends(light, working.fit_tolerance_mm)
    work = Geo.of(light)
    stage("faces")
    regions = segment_planes(work, working)
    stage("features")
    patch_of, patches = find_cylinders(work, regions, working)
    planes, _ = planes_around(work, regions, patch_of, patches, working)
    patches = [p for p in patches if (patch_of == p.id).any()]
    for p in patches:
        p.faces = np.flatnonzero(patch_of == p.id)
    snap_planes(work, planes, working)
    planes = merge_coplanar(work, planes, working)
    planes, patches, labels = consolidate(work, planes, patches, working)
    features, caps, skipped = plan_features(
        work, planes, patches, shared_lengths(work, labels), working
    )
    stage("curves")
    revolved, features, planes, patches, labels = take_revolved(
        work, planes, patches, labels, features, working, scan.noise_mm
    )
    freeform, features, planes, patches, labels = take_freeform(
        work, planes, patches, labels, features, working
    )
    if revolved or freeform:
        # The curved areas it listed are mostly what the turned shapes and freeform took.
        _, _, skipped = plan_features(work, planes, patches, shared_lengths(work, labels), working)
    modelled = {f.patch for f in features}
    patch_area = {p.id: float(work.area_faces[p.faces].sum()) for p in patches}
    note = check_supported(
        planes,
        working,
        unmodelled=sum(a for pid, a in patch_area.items() if pid not in modelled),
        curved_total=sum(patch_area.values())
        + sum(float(work.area_faces[r.faces].sum()) for r in revolved)
        + sum(f.area_mm2 for f in freeform),
    )
    stage("solid")
    base_planes = [r for r in planes if r.id not in caps]
    other_side = {
        f.patch: {f.planes[0]: f.planes[1], f.planes[1]: f.planes[0]}
        for f in features
        if f.kind in EDGE_KINDS
    }

    base_ids = {r.id for r in base_planes}

    def through(rid: int, nb: int) -> int | None:
        # Across a fillet or chamfer to the plane beyond, and across a chamfer beyond that
        # (a small round on a chamfer's edge).
        for _ in range(3):
            nb = other_side.get(nb, {}).get(rid)
            if nb is None or nb in base_ids:
                return nb
        return None

    body = next((f for f in features if f.kind == "body"), None)
    turned = [r.add for r in revolved if r.kind == "turned"]
    adds = [(r.kind, r.add) for r in revolved if r.add is not None and r.kind != "turned"]
    cuts = [(r.kind, r.cut) for r in revolved if r.cut is not None]
    try:
        if any(f.form == "surface" for f in freeform):
            # A whole curved side: the planes alone don't close, so start from blocks.
            raise UnsupportedGeometry("freeform side")
        if turned and not base_planes:
            base = turned[0] if len(turned) == 1 else cq.Compound.makeCompound(turned)
            turned = []
        elif body:
            base = cylinder_between(body.start, body.end, body.radius)
        else:
            base = rebuild_solid(work, base_planes, working.fit_tolerance_mm, labels, through)
    except (UnsupportedGeometry, ValueError):
        # Gaps, warps or ragged edges kept the traced faces from closing; build from blocks.
        patch_by_id = {p.id: p for p in patches}
        plane_by_id = {r.id: r for r in planes}
        corners = [
            Corner(
                plane_by_id[f.patch].faces if f.kind == "chamfer" else patch_by_id[f.patch].faces,
                plane_by_id[f.planes[0]],
                plane_by_id[f.planes[1]],
                f.kind == "chamfer" or patch_by_id[f.patch].convex,
            )
            for f in features
            if f.kind in EDGE_KINDS
        ]
        fixed = [
            (patch_by_id[f.patch].faces, f.kind == "hole")
            for f in features
            if f.kind in ("hole", "boss")
        ]
        # A slot is cut afterwards, so its walls and ends are inside the base.
        fixed += [
            (
                np.concatenate(
                    [patch_by_id[i].faces for i in (f.patch, *f.also)]
                    + [plane_by_id[i].faces for i in f.planes]
                ),
                True,
            )
            for f in features
            if f.kind == "slot"
        ]
        # A turned feature's faces within the part's faces are cut later; those beyond them
        # (a dome) are added later.
        for r in revolved:
            fixed += [(r.faces[r.inside], True), (r.faces[~r.inside], False)]
        ignore = np.concatenate([f.faces for f in freeform]) if freeform else None
        base = cell_solid(
            work,
            base_planes,
            working.fit_tolerance_mm,
            working.min_feature_mm,
            corners,
            fixed,
            ignore,
        )
    adds = [("turned", t) for t in turned] + adds
    base = apply_freeform(base, freeform)
    solid, failed = apply_features(base, features, planes, adds, cuts)
    dist = deviations(mesh, solid, working.fit_tolerance_mm)
    # A model that misses much of the scan is still handed back, but never silently.
    plausible = fit_score(mesh, dist, working).within_tolerance_pct
    partial = plausible < MIN_PLAUSIBLE_FIT_PCT
    notes = [PARTIAL_NOTE.format(plausible)] if partial else []
    notes += [CLOSED_NOTE] if capped else []
    return ConversionResult(
        solid,
        mesh,
        planes,
        scan,
        fit_score(mesh, dist, level),
        dist,
        features,
        notes + [n for n in (note, skipped, failed) if n],
        revolved,
        freeform,
        partial=partial,
    )


def convert_skin(
    mesh: trimesh.Trimesh,
    skin: Skin,
    scan: ScanQuality,
    level: ToleranceLevel,
    working: ToleranceLevel,
    defaulted: bool,
) -> ConversionResult:
    """The wall for a one-sided scan. Its fit is scored against the scanned surface, not the
    solid: for a centred wall that surface is inside it."""
    stage("solid")
    solid, sheet = thicken(skin, working.fit_tolerance_mm)
    dist = deviations(mesh, sheet, working.fit_tolerance_mm)
    plausible = fit_score(mesh, dist, working).within_tolerance_pct
    partial = plausible < MIN_PLAUSIBLE_FIT_PCT
    o = skin.options
    side = {
        "in": "behind the scanned surface",
        "out": "on the scanned side",
        "centred": "centred on the scanned surface",
    }[o.direction]
    finish = {"square": "square", "chamfer": "chamfered", "round": "fully rounded"}[o.edge]
    note = (
        f"This scan is an open surface (one side only), so it was thickened into a "
        f"{o.thickness_mm:g} mm wall {side}, with {finish} edges."
    )
    if skin.trimmed_mm > 0:
        note += f" Its ragged border was trimmed {skin.trimmed_mm:.1f} mm in to a smooth outline."
    if defaulted:
        note += " If it is one side of a solid part, scan the other side too."
    # A skin's free border is its edge, not a gap to close.
    if scan.warning:
        rest = scan.warning.replace(GAP_WARNING.format(scan.open_edges), "").strip()
        scan = replace(scan, warning=" ".join(rest.split()) or None)
    return ConversionResult(
        solid,
        mesh,
        [],
        scan,
        fit_score(mesh, dist, level),
        dist,
        notes=([PARTIAL_NOTE.format(plausible)] if partial else []) + [note],
        skin=skin,
        volume_mm3=solid_volume(solid, working.fit_tolerance_mm),
        partial=partial,
    )


def solid_volume(shape: cq.Shape, tol: float) -> float:
    """Volume from a triangulation fine enough that its chords sag far less than tol."""
    verts, tris = shape.tessellate(tol / 50, angularTolerance=0.02)
    mesh = trimesh.Trimesh(np.array([v.toTuple() for v in verts]), np.array(tris), process=False)
    return float(abs(mesh.volume))


def take_revolved(
    work: Geo,
    planes: list[Region],
    patches: list,
    labels: np.ndarray,
    features: list[Feature],
    level: ToleranceLevel,
    noise_mm: float,
) -> tuple[list[Revolved], list[Feature], list[Region], list, np.ndarray]:
    """Find turned shapes among what the plane, hole, boss and fillet rules left curved, and
    take their planes, patches and features out of those rules' hands.

    Each shape's faces get a label of their own that no plane has, so the base solid is
    rebuilt with the faces it stands on whole (as if the shape weren't there), and the shape
    is then added to it or cut from it."""
    modelled = {f.patch for f in features if f.kind != "sharp"}
    modelled |= {i for f in features for i in f.also}
    seeds = curved_regions(planes, level) | {p.id for p in patches if p.id not in modelled}
    cuttable = {f.patch for f in features if f.kind in ("hole", "slot")}
    revolved = find_revolved(work, planes, patches, labels, seeds, cuttable, noise_mm, level)
    if not revolved:
        return revolved, features, planes, patches, labels
    used = set().union(*(r.labels for r in revolved))
    features = [f for f in features if f.patch not in used and not ({*f.also, *f.planes} & used)]
    planes = [r for r in planes if r.id not in used]
    patches = [p for p in patches if p.id not in used]
    for x in [*planes, *patches]:
        x.neighbours -= used
    labels = labels.copy()
    top = max(int(labels.max()), *(r.id for r in planes), *(p.id for p in patches), 0)
    for k, r in enumerate(revolved):
        labels[r.faces] = top + 1 + k
    return revolved, features, planes, patches, labels


def take_freeform(
    work: Geo,
    planes: list[Region],
    patches: list,
    labels: np.ndarray,
    features: list[Feature],
    level: ToleranceLevel,
) -> tuple[list[Freeform], list[Feature], list[Region], list, np.ndarray]:
    """Fit freeform surfaces to the curved areas left over (facets, and patches no feature
    took), and take what they cover out of the other rules' hands, as take_revolved does."""
    modelled = {f.patch for f in features if f.kind != "sharp"}
    modelled |= {i for f in features for i in f.also}
    seeds = curved_regions(planes, level) | {p.id for p in patches if p.id not in modelled}
    freeform = find_freeform(work, planes, patches, labels, seeds, level)
    if not freeform:
        return freeform, features, planes, patches, labels
    used = set().union(*(f.labels for f in freeform))
    features = [f for f in features if f.patch not in used and not ({*f.also, *f.planes} & used)]
    planes = [r for r in planes if r.id not in used]
    patches = [p for p in patches if p.id not in used]
    # A flat face can give up the faces it took from the curve beside it.
    taken = np.concatenate([f.faces for f in freeform])
    for r in planes:
        kept = r.faces[~np.isin(r.faces, taken)]
        if len(kept) < len(r.faces):
            r.faces = kept
            r.area = float(work.area_faces[kept].sum())
    planes = [r for r in planes if len(r.faces)]
    for x in [*planes, *patches]:
        x.neighbours -= used
    labels = labels.copy()
    top = max(int(labels.max()), *(r.id for r in planes), *(p.id for p in patches), 0)
    for k, f in enumerate(freeform):
        labels[f.faces] = top + 1 + k
    return freeform, features, planes, patches, labels


def apply_freeform(base: cq.Shape, freeform: list[Freeform]) -> cq.Shape:
    """Mounds are added and scoops and curved sides cut, before the other features: a fillet
    or hole on the part then sees the finished faces."""
    for f in freeform:
        bodies = len(base.Solids())
        result = base.fuse(f.add).clean() if f.add is not None else base.cut(f.cut).clean()
        # Where the surface leaves a face at a shallow angle, a speck can be left over.
        parts = [p for p in result.Solids() if p.Volume() > SPECK_SHARE * base.Volume()]
        if len(parts) != bodies or not all(p.isValid() for p in parts):
            raise UnsupportedGeometry("A freeform surface could not be joined to the part.")
        base = parts[0] if bodies == 1 else cq.Compound.makeCompound(parts)
    return base
