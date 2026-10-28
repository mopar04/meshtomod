# Decisions

Newest first. One entry per choice a later session should not have to relitigate.

## 2026-10-28 (M3b: freeform surfaces)
- **A freeform area is a height field.** Each curved area left over after planes, features
  and turned shapes is fitted as w = S(u, v) over the plane across it (`freeform.py`), a
  cubic B-spline. That covers a curved top, a mound on a face and a scoop in a face: the
  shapes a turntable scan sees from one side. A fully rounded body (a ball, a bottle) is not
  one height field and is still refused; patching several together is later work.
- **Few spans, chosen by the scan.** Spans are added (2, 3, 4, 6 ... 24 along the long side)
  until 99% of the points are within the tolerance, then only while each step cuts the RMS
  error by 20% or more. Past that the surface would follow noise. The smoothness penalty is
  only a whisper (1e-5): it carries the surface on straight past the edge, but stronger
  (1e-3) it flattened real curvature by 0.05 mm.
- **Poles at the Greville points.** Placing the B-spline's poles there makes the STEP surface
  exactly the fitted w = S(u, v), with no second fit to CAD.
- **The edge comes from a boolean, not the scan.** The surface is carried a few triangles
  past the area's edge and swept into a solid: a mound is added from its base face up, a
  scoop cut from its base face down, a whole curved top cut off a block. The exact edge where
  it meets the flat faces is where the solids meet. A mound or scoop's column follows the
  loop where the surface meets the base face, offset outward; the convex hull is the fallback
  when that loop crosses itself.
- **Growing an area: joint refit, then lie-on.** A neighbour joins if a surface through the
  area and it still fits and it lies on that surface (95% of points within the tolerance,
  mean normals within 10 degrees). Mean, not median, normals: on a noisy scan small triangles
  tilt 10 degrees or more each way, but the tilts cancel.
- **Facet or flat: compare the chord with the noise.** A flat region joins only if the
  surface fits it clearly better than its own plane. The noise is the same in both fits, so
  the difference in mean square error is the chord; it must be over 3 times its sampling
  spread and over 0.02 tolerances. A ratio test (surface RMS under 0.7 of the plane's) failed
  at 0.05 mm noise, where the noise hides a facet's chord. Flats smaller than a freeform area
  may be (4 smallest features square) join without the test.
- **Inside test samples fill each block.** Block samples are now drawn through the block
  itself, not its bounding box. A thin block cut at a slant fills 1% of its box and was judged
  by its middle point, which left the curved top's side walls 0.3 mm out on a tilted scan.
- **Freeform test parts are tessellated finer** (0.05 rad). OpenCASCADE's default left the
  design meshes' chords sagging up to 0.2 mm under the drawn surface.

## 2026-10-28 (M3a: cones, spheres, tori)
- **Order of M3** (Claude's call; Zac left order and tech to Claude): M3a revolved surfaces
  (cones, spheres, tori), M3b freeform B-spline patches, M3c feature recipe and sink/extrude
  (after the production PR, which touches the API and the app), M3d thicken skin.
- **Shapes turned about an axis are found as a 2D profile.** Every face on a shape turned
  about an axis lies on its profile when folded into (height, radius), so cones, cylinders and
  flat faces become lines there and spheres and tori become circular arcs. A group of curved
  regions is one shape when the face normals all cross one axis (`revolve.py`). The solid is
  rebuilt by revolving the fitted outline, so it is exact, and corners come from the pieces'
  intersections, the same rule as for planes.
- **Lines, then arcs, then short lines.** Within the tolerance a short line also fits a band
  of a big ball, and a gentle arc fits a narrow flat plus the edges of the rounds beside it,
  so the order pieces are looked for in decides the answer. Long lines (at least the smallest
  feature) go first, arcs second (also at least the smallest feature: a shorter band that
  turns is a blurred sharp edge), short lines last (down to the smallest chamfer).
- **Pieces settle by fit, not by order.** After merging, faces at a piece's edge move to the
  neighbour they lie on and face more closely, and pieces are refitted, until nothing moves.
  An arc that is then plainly a narrow flat with the edges of rounds beside it becomes the
  flat; an arc the pieces either side already explain (run on to their corner) is dropped.
- **Grouping by axis uses heavily smoothed normals** (12 rounds, 60 degree cut-off); the
  profile uses lightly smoothed ones. Noise tilts raw normals out of the plane through the
  axis, which made noisy shapes fail the twist test.
- **An axis square to a flat face wins if it fits.** A free fit is tried only when none of
  the part's flat faces gives an axis the shape's normals pass through; noise tilted free
  fits just past the angle snap (0.5 degrees at Precise), leaving the shape on no base.
- **Faces of a base plane are taken out of the shape.** In a scan the crease where a dome
  meets the plate is blurred, and its faces otherwise make a sliver of a flat at the foot.
- **A ball's centre is held on the axis**, its radius rounded, then its height refitted for
  that radius. A free fit let noise pull the centre off (20.3 for a drawn 20).
- **Sizes snap to round values first** (whole, then half, then tenth of a mm, within half the
  tolerance). So the plate's 10.4 mm countersink head reads 10.5. Known and accepted: within
  the tolerance either is right, and we don't guess the designer's numbers further.
- **Known limit:** below the smallest feature (2 mm at Functional), a flat between two rounds
  or a round on a shallow corner may come out as the corner. That is within the tolerance.

## 2026-10-27 (rename, finished)
- **Everything is renamed to MeshToMod** (Zac: "complete the renaming on GitHub"). The repo is
  `mopar04/meshtomod`, the Python package is `meshtomod`, and the frontend package is
  `meshtomod-web`. Test-print generators now write `meshtomod_*` files. Files already
  delivered keep their `nurbsmith_*` names, and Zac's `Developer/Nurbsmith` folder is his and
  stays as it is. The browser settings keys moved to `meshtomod.*`, so a saved units choice
  is asked once more. This replaces the "stay `nurbsmith`" part of the entry below.

## 2026-10-27 (rename)
- **The app is now called MeshToMod** (Zac). Domains: meshtomod.com is primary and
  meshtomodel.com redirects to it (nurbsmith.com was bought earlier). The Python package,
  code identifiers and the repo slug stay `nurbsmith` until Zac says otherwise. The
  calibration part keeps its embossed "NURBSmith" text, since it's already printed and
  scanned against that design.

## 2026-10-25 (placement)
- **Lay-on-face candidates come from the convex hull, like OrcaSlicer** (Zac asked for Orca's
  familiar interface). Only faces the part rests on stably are offered. We reimplemented the
  idea in Python rather than porting Orca's C++.
- **Placement is applied to the converted solid, not just the scan.** The scan is turned
  before converting (the pipeline doesn't care), then the solid's bottom face and main wall
  are trued up exactly, so the STEP's faces are exactly on the axes, not 0.1 degree off.
- **Trim flush cuts the solid, after the fit score.** The score still compares the model with
  the whole scan; trimmed bumps show up in the deviation view, which is honest about it.
- **The viewer shows the export's origin.** No more recentring for display: the grid origin
  is where the part will sit in CAD. Changing placement clears a result, so the download
  always matches what is shown.
- **API default is unchanged placement** (`origin=keep`, no lay flat). The UI defaults to
  bottom centre, Z up.

## 2026-10-25 (batch scans)
- **Merge pairs only points facing roughly the same way** (normals within about 120
  degrees). Without it, the thin-wall tray's underside matched onto its own floor, one wall
  thickness away and facing the other way. A stricter cut (60 degrees) dropped the pairs
  along sharp edges that pin the height, and the synthetic tests drifted 0.2 mm.
- **Test prints are designed by us and batch printed by Zac** (`tools/batch_prints.py`).
  Every opening is no deeper than it is wide, so a turntable scanner sees inside.

## 2026-10-24 (merging scans)
- **We merge top and bottom scans ourselves** (Zac agreed). Scanner software's merge was off
  by about 1 mm and 1.6 degrees on the calibration print, which reads as wrong thickness.
  The user uploads both scans; no manual point picking.
- **No open3d.** Its wheels need libEGL (a GPU stack) on the server. ICP is a short numpy /
  scipy routine instead.
- **A merge is judged by what it completes, not just overlap:** rim continuation and an
  open-air penalty. Overlap alone prefers stacking a flat part's bottom on its top.
- **Symmetric parts can land half-turned.** From below, a symmetric part looks the same
  turned round, so tests check that the placed scan lies on the part, not point for point.
- **Freeform check uses a face's bow without stray points**, so a warped print's underside
  stays one plane.

## 2026-10-24 (calibration part)
- **Hole sizes snap only within 0.35x the tolerance** on the diameter (0.1 mm at Functional),
  stricter than other sizes. Stamped, printed and cast holes are rarely drill sizes, and a
  size Zac measures with calipers must come out as measured (RC stand 13.15 / 16.2 / 9.15).
- **Plane fits weight vertices by area and drop outliers.** Fine detail (embossed text) and
  strips of a neighbouring fillet were pulling the top face of the calibration part 0.15 mm
  high and tilting it. Depths and heights are now exact on design meshes.
- **Slots and chamfers are features.** A closed slot (two half-round ends and two facing
  walls) is filled in the base and cut afterwards; a narrow even bevel between two faces is
  made sharp in the base and chamfered after the fillets, so it runs round filleted corners
  as it would in CAD.
- **A curved patch must turn like a cylinder.** Normals that lag steadily behind the arc
  (measured in slices so noise averages out) mean flat stretches and tight corners, such as a
  pocket end, a cone or a corner blend, posing as one cylinder.
- **Calibration part** (`tools/calibration_part.py`): one print with every feature. Its top
  edge chamfer and fillet run all the way round (CadQuery follows tangent edges), and the ramp
  overhangs that chamfer by about 1 mm. Kept as printed.

## 2026-10-24 (real scans)
- **Scan coverage is the user's job** (Zac). Fill gaps only from context: extended planes,
  measured wall thickness, closing an open end. When big regions were never scanned, say so
  rather than invent them.
- **Design files are the best tests.** A mesh exported from the user's CAD gives exact
  ground truth; Zac's scanner holder is the first (`tests/fixtures/`).
- **The base solid is the part before features.** Blocks near a fillet vote by the sharp
  corner, blocks in a hole count as solid, blocks in a boss as empty. Then features apply.

## 2026-10-24 (M2)
- **Features are CAD operations on a flat base solid.** The base is built from planes only;
  bosses are fused, fillets applied with OpenCASCADE's fillet, holes cut. A feature that
  fails is left out with a note, never a failed part. This is also the M4 replay order.
- **Standard sizes snap only within half the tolerance.** Otherwise the measured size is kept,
  rounded. A hole is never moved to a standard size the scan doesn't support.
- **Cylinders come from strip pairs, not curvature.** Two neighbouring planar strips give a
  seed cylinder; it is grown over triangles and refitted. A patch must not fit a plane, and
  its normals must turn about as far as its arc (0.5× to 2×).
- **Freeform share is checked after features are planned.** Curved patches that become no
  feature count as freeform with curved flat faces; over 15% of the surface is refused.
- **Warp is relative to face size.** A face counts as curved only above both 0.5× tolerance
  and 0.5% of its size (RMS). Real flat parts are warped; refusing them helped nobody.
- **Block-based fallback solid** (`cells.py`) when the exact rebuild cannot close, and every
  sizeable body is kept (an assembly scan can fall apart where a joining piece isn't
  modelled). A result under 90% within the working tolerance is refused, not returned.
- **Decimate gently.** fast-simplification at aggression 4 in repeated passes; the default
  made triangles that cut across corners by millimetres.
- **Next milestone is real-scan readiness** (thin walls, open ends, front/back merge) before
  M3, because Zac's real scans hit those first.

## 2026-10-23 (M1)
- **Open source under AGPL-3.0, with a donate link, no commercial plans** (Zac). This matches
  CNC Kitchen's tools and lets us reuse ScanRuler code with credit. Set the donate link with
  `VITE_DONATE_URL`. The repo stays private until Zac decides to make it public.
- **Display name was "NURBSmith"** (NURB in capitals; renamed MeshToMod on 2026-10-27). Code identifiers stay `nurbsmith`.
- **UI follows BumpMesh and ScanRuler** (Zac): see `docs/UI_REFERENCES.md`.
- **Never model scan noise.** Scan noise sets a floor on the fit tolerance (3.5 sigma). If the
  chosen level is tighter, we fit looser and warn, rather than chase artifacts. The fit score
  is still measured against the level the user picked.
- **Corners come from plane intersections, not mesh vertices.** Each region's boundary gives
  the order of its neighbours, and each change of neighbour is a corner (3-plane
  intersection). The output is exact even when the scan's edges are ragged.
- **Curved parts are refused in M1, not faceted.** A 12-sided prism can be "within tolerance"
  of a cylinder but is the wrong CAD. Curved surfaces are detected (shallow creases, or a
  region residual well above the noise) and get a clear message until M2.
- **Edge-preserving normal smoothing** (bilateral, 8 rounds, 60 deg cutoff) before region
  growing. Plain averaging blurred edges; raw normals fragmented noisy faces.
- **Scope added:** Thicken skin (M3) and Combine scans (M5), per Zac.

## 2026-10-23
- **Target CAD tools:** Fusion 360 and Onshape first. Shapr3D gets the same STEP (it imports STEP
  fine but has no parametric history), no special handling.
- **Geometry in Python with OpenCASCADE (CadQuery/OCP).** Same kernel as FreeCAD; writes
  AP214/AP242 that all target tools import. Browser does viewing and editing only.
- **Python 3.12.** CadQuery has no 3.13 wheels yet.
- **Editable history via replay scripts, not STEP.** STEP cannot carry a feature tree, so M4
  generates Onshape FeatureScript and Fusion API scripts from a feature recipe.
- **Tolerance levels:** Rough 1.0 mm / Functional 0.3 mm (default) / Precise 0.1 mm, each also
  setting the noise filter size. Starting values, to be tuned on real scans.
- **Threads ("screws")** are modelled as plain cylinders plus a thread annotation; CAD tools add
  cosmetic threads.
- **No job queue in M0.** Uploads are analysed inline; Redis + RQ comes in when conversion runs.
- **Repo is the source of truth** for plans, decisions and status so work continues from Claude
  or Claude Code.
