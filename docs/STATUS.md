# Status

_Last updated: 2026-10-23_

## Done
- Technical plan ([docs/PLAN.md](PLAN.md)).
- M0 scaffold: FastAPI with health, tolerance presets and mesh upload/summary; STL/OBJ/PLY/3MF
  loading; synthetic fixtures with noise; STEP round-trip test proving the OpenCASCADE
  toolchain keeps analytic faces; React + three.js viewer with drag and drop and accuracy picker;
  Docker Compose; GitHub Actions CI.

## In progress
- Nothing yet.

## Next (M1: planar parts)
- Mesh repair and adaptive decimation.
- Normal-based region growing segmentation.
- Plane fitting with angle/parallel snapping.
- Stitch fitted planes into a solid and export STEP; test on box and plate fixtures with noise.
- Download STEP from the UI.

## Open questions for Zac
- Which scanner and file format you usually get.
- Default units: mm or inches.
- Local-only at first, or public hosting from day one.
