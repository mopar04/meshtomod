# Technical plan

Full version with diagram: [NUrbSmith technical plan](https://claude.ai/artifact/LAaeiRK5iiGU7FMVsyvpKS)
(Claude Doc). This file is the in-repo copy; keep both in step when the design changes.

## Approach

Segment, classify, fit, rebuild:

1. **Upload** STL, OBJ, PLY or 3MF.
2. **Repair + decimate**: fill holes, fix normals, adaptive decimation for large scans.
3. **Segment** into regions by curvature and normal-based region growing.
4. **Classify** each region as plane, cylinder, cone, sphere, torus, fillet, freeform, or noise.
   Regions smaller than the level's minimum feature size are merged into neighbours.
5. **Fit + snap** each primitive within the fit tolerance (RANSAC then least squares). Snap
   angles, parallelism, radii and hole sizes to clean or standard values.
6. **Rebuild** a watertight B-rep in OpenCASCADE, with edges from face-face intersections.
7. **Sink / extrude** user operations on the rebuilt solid.
8. **Export** STEP (AP214/AP242) plus a feature recipe JSON.

STEP carries geometry only, never a feature tree. Clean analytic faces make direct editing work
well in every CAD tool; for a real timeline we generate Onshape FeatureScript and a Fusion API
script from the recipe (M4).

## Stack

| Layer | Choice |
| --- | --- |
| Kernel and STEP | OpenCASCADE via CadQuery / OCP |
| Mesh | trimesh, numpy, scipy (Open3D if needed) |
| Fitting | region growing + RANSAC + least squares; OCC B-spline fitting for freeform |
| API / jobs | FastAPI; Redis + RQ worker once conversions are long-running |
| Frontend | React, TypeScript, three.js via react-three-fiber |
| Deploy | Docker Compose |

## Tolerance levels

| Level | Fit tolerance | Smallest kept feature | Fillets |
| --- | --- | --- | --- |
| Rough | 1.0 mm | 5 mm | all made sharp |
| Functional (default) | 0.3 mm | 2 mm | kept above 2 mm radius |
| Precise | 0.1 mm | 0.5 mm | all kept |

Defined in `backend/nurbsmith/tolerance.py`; users can override individual values.

## Sink and extrude

User picks a point and a shape (rectangle, circle, slot, rounded rectangle) with size in mm or
inches. We place a sketch plane at the averaged surface normal under the footprint, then
extrude-cut (sink) or extrude-add (boss) far enough for a fully flat face. Optional edge fillet and
hole pattern. Each step goes into the feature recipe so it stays editable in CAD.

## Milestones

- **M0 Scaffold**: repo, API upload, viewer, CI, synthetic fixtures. (current)
- **M1 Planar parts**: segmentation, plane fitting and snapping, stitching, STEP export.
- **M2 Mechanical features**: cylinders, cones, holes with standard sizes, fillets, chamfers,
  tolerance levels wired through, deviation heat map.
- **M3 Freeform + sink/extrude**: B-spline patches, sink/extrude UI, feature recipe.
- **M4 Editable history**: Onshape FeatureScript and Fusion script from the recipe.

Out of scope for now: accounts and billing, modelled threads (cylinder + thread note instead),
assemblies, Shapr3D-specific output (it imports the same STEP).

## Risks

- No feature tree in STEP: mitigated by clean faces plus M4 replay scripts.
- Freeform panels (dash, A-pillar): few large B-spline patches; fallback to sewn shell + thicken.
- Watertight stitching: rebuild edges from intersections, validate with BRepCheck.
- Noisy or incomplete scans: repair step; user can tag regions as plane or ignore.
- Large scans: adaptive decimation, background jobs with progress.
