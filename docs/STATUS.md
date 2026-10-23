# Status

_Last updated: 2026-10-23_

## Done
- **M0 scaffold:** API, mesh loading, viewer, Docker, CI.
- **M1 flat-faced parts:**
  - Pipeline: repair, segmentation (`segment.py`), snapping and solid rebuild (`brep.py`),
    orchestration (`convert.py`), confidence metrics (`quality.py`).
  - API: `POST /api/convert` (file, level, units) and downloads of the STEP, the model STL and
    a deviation-coloured PLY.
  - Viewer: units prompt for STL/OBJ/PLY, Convert button, confidence card with "use suggested
    level", Scan / Model / Overlay / Deviation view bar with legend, Download STEP, and a donate
    link once `VITE_DONATE_URL` is set.
  - Tests: box, L-bracket, stepped block and window frame, each rotated arbitrarily, at noise
    0.02 to 0.3 mm and all three levels. They recover the exact face count, with a mean surface
    error well under the noise. Curved parts are refused with a clear message.
- AGPL-3.0 license, NURBSmith naming, UI reference notes.

## In progress
- Nothing.

## Next (M2: mechanical features)
- Cylinder, cone, sphere and torus fitting; classify regions by curvature.
- Holes snapped to standard drill and tap sizes (mm and inch).
- Fillets and chamfers as blend surfaces between fitted faces.
- Real-scan test corpus. We need sample scans from Zac.
- Adaptive decimation for large scans (1M+ triangles) and a background job queue.

## Known limits
- Only flat-faced parts convert. Anything curved is refused.
- Conversion runs inside the request, which is fine for test parts but not for big scans.
- Results are kept in a temp folder, last 50 only.

## Open questions for Zac
- Which scanner and file format you usually get, and a couple of sample scans.
- Default units: mm or inches.
- Local-only at first, or public hosting from day one.
- Donate link URL.
