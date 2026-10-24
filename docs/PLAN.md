# Technical plan

Full version with diagram: [NURBSmith technical plan](https://claude.ai/artifact/LAaeiRK5iiGU7FMVsyvpKS)
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

## Confidence metrics

Shown with every result, so a rough scan never turns into a model of its own artifacts.

- **Scan quality (before fitting):** scanner noise (1 sigma, from local plane fits that ignore
  sharp edges) and coverage gaps. A fit tolerance has to cover 3.5 sigma of noise. If the
  chosen level is tighter than that, the fit is widened to what the noise allows, the warning
  says so, and the tightest level the scan supports is suggested.
- **Fit score (after fitting):** % of scan surface area within the chosen tolerance of the
  model, plus mean, 95th percentile and worst deviation. A colour map shows where the misses
  are: green within half the tolerance, yellow at the tolerance, red at twice the tolerance.
  The score is always measured against the level the user picked, so it stays honest.

## Sink and extrude

User picks a point and a shape (rectangle, circle, slot, rounded rectangle) with size in mm or
inches. We place a sketch plane at the averaged surface normal under the footprint, then
extrude-cut (sink) or extrude-add (boss) far enough for a fully flat face. Optional edge fillet and
hole pattern. Each step goes into the feature recipe so it stays editable in CAD.

## Milestones

- **M0 Scaffold**: repo, API upload, viewer, CI, synthetic fixtures. (done)
- **M1 Planar parts**: segmentation, plane fitting and snapping, stitching, STEP export,
  confidence metrics, units prompt, deviation view. (done)
- **M2 Mechanical features**: cylinders, holes with standard sizes, bosses, fillets, round
  bodies, block-based fallback solid for scans with gaps. (done; cones and chamfer detection
  moved to M3)
- **M2.5 Real-scan readiness**: thin walls patched from measured thickness, open ends closed,
  front/back scans of one part merged, background jobs for big scans.
- **M3 Freeform + sink/extrude**: B-spline patches, sink/extrude UI, feature recipe.
  **Thicken skin** for one-sided scans: wall thickness (default 3 mm), direction (in, out,
  centred), and edge finish (square, chamfer, or full round = thickness / 2). The ragged scan
  border is trimmed to a smooth outline first.
- **M4 Editable history**: Onshape FeatureScript and Fusion script from the recipe.
- **M5 Combine scans**: convert two scans, position them in the viewer (drag and rotate,
  face-to-face and axis snaps, or 3-point alignment), boolean union, then fillet or chamfer the
  new seam. Parts that don't touch get a connecting extrude or loft. Recorded in the recipe.

Out of scope for now: accounts and billing, modelled threads (cylinder + thread note instead),
assemblies, Shapr3D-specific output (it imports the same STEP).

## Risks

- No feature tree in STEP: mitigated by clean faces plus M4 replay scripts.
- Freeform panels (dash, A-pillar): few large B-spline patches; fallback to sewn shell + thicken.
- Watertight stitching: rebuild edges from intersections, validate with BRepCheck.
- Noisy or incomplete scans: repair step; user can tag regions as plane or ignore.
- Large scans: adaptive decimation, background jobs with progress.
- Overlap with ScanRuler (CNC Kitchen), whose "Reverse Engineer" workspace (2026-10-21) has an
  AI agent rebuild scans as parametric CAD. Our edge: one-click automatic conversion,
  tolerance levels, sink/extrude, thicken, combine scans, and editable history in Fusion and
  Onshape. Its code is AGPL like ours, so we can reuse it with credit (see UI_REFERENCES.md).
