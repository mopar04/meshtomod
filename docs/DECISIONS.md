# Decisions

Newest first. One entry per choice a later session should not have to relitigate.

## 2026-10-31 (thin-walled covers, PR #26)
- **Shells are tried first, on a 250k-face copy.** Right after simplify, the converter checks
  whether the scan is a thin wall: most faces have an opposite-facing face a steady distance
  behind them. If so it rebuilds the outside as flat panels and extrudes each one inward by
  the measured thickness, instead of fitting solid blocks. 60k faces is too coarse to pair a
  1.6 mm wall; 250k is enough and still quick. Solid parts (box, plate, frame, bracket, a
  2 mm plate) are tested not to be taken for shells.
- **Gently bowed panels count as flat on a shell.** Panels are grown with at least 0.5 mm
  tolerance, and parallel pieces in one plane are merged even when they don't touch. A
  printed cover's panels sag; the CAD intent is flat.
- **Holes in the wall are kept only when there is no surface behind them.** A gap in the scan
  is filled when the scan shows surface along the panel at any depth from 1 mm proud to just
  behind the wall (pinholes, logo letters scanned as dents); an opening is kept when at least
  half of it has nothing (vent slots, U notch, open underside). Real vents scored at most
  0.17 and real gaps at least 0.67.
- **Standoffs are tubes square to a panel, sized as one family.** A sealed end (the scanner
  skinned the hole over, seen as a dome) gets the usual pilot hole for the post's screw: M2
  1.7, M2.5 2.1, M3 2.5, M4 3.4, M5 4.2 mm, with a note. Posts within a tolerance of each
  other get the same size.
- **Refuse only a badly wrong model.** A result is refused when under 50% of the scan is
  within max(1 mm, 3 tolerances); under 90% within tolerance it is returned as partial with
  the deviation map (Zac's 2026-10-28 call). It is also marked partial when the model has
  more solids than the scan has pieces, since that means a part fell apart.

## 2026-10-30 (stuck conversions, PR #22)
- **A job belongs to whoever is watching it.** The page checks a job's status every 1.5 s;
  that check doubles as a heartbeat. A queued or running job nobody has checked on for 3
  minutes is stopped and its files deleted. Why 3: a hidden browser tab can slow its checks to
  once a minute, and with one conversion at a time an abandoned job blocks everyone queued
  behind it. Not "cancel when the tab closes": a reload must keep the job.
- **The job id is the key to a job**, as it already was for results: whoever has it can see,
  download or cancel. It is 128 random bits and only ever sent to the page that started the
  job. The "one at a time" refusal does not hand out the other job's id, since a shared
  address (an office, a cafe) is not the same person.
- **The progress bar moves by stage, not by time.** Ten named stages with rough weights
  (`progress.py`); how long each takes depends on the scan, so no time estimates are shown,
  only the time so far.
- **Results are still kept one hour** (`MESHTOMOD_RESULT_TTL_MIN`, privacy page). The browser
  keeps its latest job id for two hours (an hour of result after up to 15 minutes of
  converting, with room to spare) and the server decides what is still there.
- **Cancel with thread isolation can't stop the work** (tests and quick local runs): the job
  counts as cancelled at once and its result is thrown away when it ends.

## 2026-10-30 (auto-simplify dense scans, Zac: "Add auto-simplify")
- **Dense scans are simplified, not refused** (`simplify.py`). Each accuracy level has a
  triangle limit (`ToleranceLevel.simplify_above_faces`: Rough 500k, Functional 1M, Precise
  2M); a scan over it is simplified to that count with the same gentle quadric decimation as
  the working mesh. The pipeline segments and fits on 60k triangles anyway, so these limits
  are about keeping the deviation map and fit score honest, not about features. Scans at or
  under the limit are untouched.
- **Every simplification is checked, and redone with more triangles if it fails.** 99.9% of
  a fixed sample of the original's points must be within half the fit tolerance plus three
  times the scan's noise of the simplified surface (smoothing noise isn't a loss), and at
  most 5 points twice that far (a filled-in hole on a big part is under 0.1% of its points).
  On a fail it retries at 1.6 times the count; if only the whole scan passes and it's under
  2.5M it is kept whole. Over 2.5M (what the server converts) the best try is kept and the
  note says detail may be softened. The check measures each point against its 12 nearest
  triangle centres instead of trimesh's exact search (15 s to 1 s on 2M triangles, same
  numbers; a miss can only overstate the distance).
- **Two steps: a cap at load, then the level.** `jobs.load_input` caps each upload at the
  Precise limit with Precise's check, so a merge of two scans never holds two huge meshes;
  `simplify_for_level` then simplifies for the chosen level and reports from the uploaded
  count (errors of the two steps added, an upper bound). Both are outside the job lifecycle
  code (one call each in `jobs.py`).
- **`MESHTOMOD_MAX_FACES` is now the most the server will open: 5M** (was 2.5M, the most it
  converted). Loading and simplifying cost about 0.8 GB per million triangles (6.2M peaked at
  4.9 GB, 70 s), and the API container has 7 GB. 5M is about a 200 MB binary STL, the upload
  limit. A server whose `.env` was copied before this still says 2500000 there; raise it.

## 2026-10-30 (merged Top/Bottom scans)
- **A merge keeps every big piece of each scan and stitches the seam** (`merge.py`). Each
  scan keeps the pieces that are at least a tenth of its biggest (a scan of a plate can come
  apart at a thin edge), the second scan is trimmed to where it lies off the first (measured
  to the first's surface, not its vertices), and the two rims are zipped together where they
  run within 2 mm of each other. The merged scan is one closed piece where the scans met, so
  the convert sees the true edges instead of a crack and a closed-flat guess.
- **A flat that a turned part already makes is not a separate base.** A leftover of the
  turntable side (the ring the part stood on) that lies on the turned surface is dropped
  from the planes, so the turned part uses its own scanned end.
- **A flat face square to a turned shape that reaches well past it is what the shape stands
  on, not its cap,** and small flat slivers lying on that face are part of it. A ring boss
  whose bore runs through a plate otherwise took the plate's far side for its top when the
  scan hadn't joined that side to the plate's edges. Pieces of one flat face (split by noise
  or a seam) count as one face to stand on.
- **A tapered boss's top is its top edge** (the highest 2% of the profile), not the mean of
  the flat, and an arc that ends on the axis is centred on it (a dome's cap).


## 2026-10-30 (M4: editable history)
- **Targets: Fusion, Onshape and FreeCAD** (Zac: "usable on commonly used accessible paid apps
  like fusion, onshape, and freecad"; Creo is not a target). STEP stays the default export.
- **One Export menu** (Zac's question, answered by the coordinator): STEP by default, then
  "Editable history" for each tool, then "Everything" (one zip). Each history zip has a
  HOW_TO_OPEN.txt for its tool.
- **The history starts from a base solid, then one feature per step.** The base is the
  solid before bosses, turned features, fillets, chamfers and holes (the planes plus any
  freeform), saved as base.step. A single turned part starts from a revolve of its profile
  and a round body from an extruded circle, so those stay editable too. Freeform surfaces
  stay inside base.step: no CAD tool edits a scanned B-spline usefully.
- **Steps are plain operations every tool has** (`history.py`): sketch on a plane and extrude
  or revolve it (new, join, cut), fillet or chamfer the edges where two planes meet. Holes and
  bosses are sketched on their face with their true depth or height, as a designer would.
  Fillet and chamfer edges are found when the script runs, by the two planes they join, not
  by edge numbers, which differ between tools.
- **`replay` makes the same steps with CadQuery, and the tests require it to rebuild the
  model exactly** (face types, volume, bounding box) on the test parts. That is the check on
  the steps themselves; the scripts then follow them line for line.
- **One template per tool** (`meshtomod/cad/`): a fixed program with the part's steps put in
  as data. Sizes are named the same everywhere (hole_1_diameter, boss_1_height,
  fillet_1_radius): Fusion user parameters, fields in the Onshape feature's dialog, sketch
  constraints and Pocket/Pad lengths in FreeCAD. A step a tool can't make is left out and
  listed, as `apply_features` does.
- **Not yet run inside Fusion, Onshape or FreeCAD.** None can run here (FreeCAD's 800 MB
  download was declined in this environment; Fusion and Onshape need an account). Zac's first
  try in each is the real test; fixes follow from what he sees.
- **The donate link reads one constant** (`DONATE_URL` in DonatePrompt.tsx, Zac's page unless
  `VITE_DONATE_URL` overrides it), now that the footer can change after PR #11.

## 2026-10-30 (Zac's turned part scan)
- **A cross hole the scan only half saw is still a hole.** A scanner looking down into a
  small cross hole sees the lower half of each mouth, so its walls never make a cylinder
  that goes 85% of the way round, and the turned grouping used to refuse the whole body
  over them. Now faces sunk well inside the turned outline (a small share of the body) don't
  stop the grouping; they are fitted as a cylinder across the axis (`cross_holes`) and cut
  through as their own hole feature, which the history makes an extrude cut.
- **If the fullest wedge's pieces make no outline, the profile is fitted again in the most
  typical wedge.** The fullest wedge can be the one through a hole's mouth, whose walls fit
  no piece of the profile. Always using the typical wedge broke parts that worked (which
  wedge is "typical" is close to chance on an even scan), so it is only the second try.
- **An open end that isn't flat is closed square to the axis.** A turned part stood on its
  base: the base was never scanned and the edge where the scan stops is torn and wavy, so
  `close_flat_ends` can't cap it. When the outline ends at an open edge that runs all round
  the axis, the profile is closed there with a flat face, with the usual "closed flat" note.
- **A stepped round part is a round body per step.** All its flat faces are square to the
  axis, so each step reads as a round body; all of them are built, end to end, and the
  history starts from the biggest and joins each other step to it.
- **Only a flat at the very end of the shape is added when the wedge missed it.** Flat faces
  square to the axis inside the shape are the floor of a cross hole, not a step.

## 2026-10-29 (open ends and gappy scans)
- **An opening whose rim lies flat is closed with that plane** (`ends.py`). A turntable scan
  never sees the face the part stood on; a tube can miss an end. If the rim is within two
  tolerances of one plane, the missing face was flat: it is closed exactly at the rim, before
  segmentation, in the working mesh only (the fit is still scored on what was scanned). Rims
  on one plane close together as one face with holes, so a tube's end is a ring, never a disc
  over the bore. A rim that isn't flat (a gap over a corner) is left to the block solid. The
  report says a face was closed, since nothing scanned backs it up. One-sided skins are
  decided first and never closed.
- **Slivers never sink a rebuild.** If the glued fuse of block solids comes back empty, that
  batch is fused one block at a time and any block that breaks it is left out, up to 0.1% of
  the volume; past that the scan is refused as before.

## 2026-10-29 (Zac's calls: partial results, units, donations)
- **A low match is returned, marked partial, not refused** (Zac). Under 90% of the scan within
  the working tolerance the model still comes back, with `partial: true` in the report and a
  warning first among the notes pointing at the deviation map. Applies to one-sided skins too.
  Other refusals stay: a curved part we can't model yet, scans that can't be merged, edits
  that would split the part.
- **mm is the default unit** (Zac). Files without units still ask, with mm highlighted: a
  wrong guess on an inch file is 25 times off.
- **Donations: a Buy Me a Coffee pop-up** (Zac, like CNC Kitchen's tools). Once per visit at
  most, after 90 s on the page, never over another dialog; "Don't remind me again" hides it
  for good in that browser. It opens Zac's page, https://buymeacoffee.com/voxelautocraft (he
  sent it as http; we use https), and `VITE_DONATE_URL` can override it. It mounts beside the app in
  `main.tsx`, so it doesn't touch App.tsx while the production PR owns it; after that it can
  wait for the first finished conversion instead of a timer.

## 2026-10-29 (M3c: sink, extrude, feature recipe)
- **Edits work on the converted solid, not the scan.** The sketch is placed where the user
  clicked, square to the area-weighted mean normal of the solid's surface under the whole
  footprint (rays through the footprint, twice, so the normal settles). A single point's
  normal would tilt the cut on a curved or bumpy face.
- **Depth from the lowest point, height from the highest.** A sink's floor is the depth below
  the lowest surface point under the sketch, an extrusion's top the height above the highest.
  Either way the new face is fully flat with no sliver of the old surface, which is what a
  person means by "3 mm deep" on a curved face. On a curved face an extrusion starts 0.5 mm
  inside so it joins cleanly; on a flat one it starts on the face, so nothing hangs below an
  edge.
- **Refuse rather than guess.** A sink that would cut the part in two, or an extrusion that
  doesn't touch it, is refused. A fillet that doesn't fit leaves the edges sharp with a note.
- **Recipe v1 lists what was found, then the user's edits.** Found steps are marked
  `replay: false`: the STEP already holds them, and M4 turns them into a parametric history.
  Sinks and extrusions are `replay: true`: plain sketch-and-extrude steps any CAD tool can
  redo on the imported STEP. Every position is in the STEP's frame (after placement), so
  numbers in the recipe match what CAD shows.

## 2026-10-28 (stand a part on its axis)
- **Any turned or drilled line is an axis to stand on.** Not only a whole turned part's: the
  report's `axes` also lists turned features, round bodies, bosses and holes, so a plate can
  be stood on a boss, and a cross hole is there to pick too. Lines that coincide (a
  counterbore and its hole) are listed once, the turned part's first.
- **Which end goes down: the one with more flat face.** That is the end it would stand on (a
  lathe part's base, not its ball end). When the ends tie (a plain rod, or no flat ends) the
  axis keeps whichever way it already pointed up, so a nearly upright scan isn't flipped.
- **"axis" is an origin, not a separate switch.** Standing on an axis is a rotation, the same
  slot as laying flat; where it lands is the origin choice as before, plus "axis": the axis
  through the origin with the part's bottom on the ground. The biggest wall along the axis is
  squared to X, as laying flat does.

## 2026-10-28 (M3d: thicken skin)
- **A skin is a whole scan that is one smooth open sheet.** It is only looked for when the
  scan isn't closed: every smoothed normal must face the same way as the overall normal (no
  more than 5% of the area facing away), the biggest border loop must enclose at least 80% of
  the area seen from that side, and one height-field B-spline (`freeform.fit_surface`) must
  fit it. Anything else (a box missing its bottom, the top half of a blocky part) goes
  through the pipeline for closed parts as before.
- **We build the wall ourselves, not with OpenCASCADE's offset.** `BRepOffset` (cadquery's
  `Face.thicken`) gave solids whose booleans failed on dense scan edges and whose resewn
  rebuilds were garbage. Instead: the two faces are the surface and its offset (a height field
  refitted with 2, 4 or 8 times the spans until it is within 5% of the tolerance), a slab is
  cut between them, and a ruled side wall along the surface normal at the outline splits off
  the wall. Every face is a B-spline, and the part passes `isValid()`.
- **Volume is measured on a fine tessellation.** OpenCASCADE's `Volume()` and `Area()` were
  off by a few percent on these solids, in its own "precise" mode too. `ConversionResult.
  volume_mm3` carries the measured value into the report.
- **The outline is the scan's border, smoothed and pulled in.** Smoothed over the level's
  smallest feature (2 mm at Functional) with the shrink taken back out, then pulled in until
  90% of it lies on scanned surface (less the tolerance). Corners round off by about the
  smoothing; that is the price of a clean edge on a torn border.
- **Defaults: 3 mm, behind the scanned surface, square edges.** Behind ("in") keeps the
  scanned face where it was scanned. Chamfer is t/3; "round" is a fillet of 0.49 t, just under
  t/2 so a thin strip of the side wall stays between the two fillets. With the defaults
  the note also says to scan the other side if this is one side of a solid part.
- **Skins fit with one extra step of look-ahead.** Adding spans stops when one step doesn't help;
  the wave skin needed (4, 3) spans after a (3, 2) that was no better. Look-ahead is only on
  for skins: turned on for freeform areas it overfitted the curved top.

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

## 2026-10-29 (hosting)
- **A DigitalOcean droplet, not App Platform** (Zac said go ahead with DigitalOcean). Basic
  Regular 4 vCPU / 8 GB at $48/mo. App Platform needs a dedicated container for 8 GB ($98 for
  2 vCPU, $156 for 4), caps local disk at 4 GB, runs in a gVisor sandbox (spawned
  OpenCASCADE processes and memory limits untested there) and has no volumes. The droplet runs
  the same `docker-compose.prod.yml` CI already builds.
- **Deploys pull, they aren't pushed.** The server checks `main` every 5 minutes and deploys a
  commit only after all its GitHub checks pass, never mid-conversion (up to an hour), rolling
  back if it doesn't come up healthy. The repo is public, so this needs no SSH key in GitHub,
  no deploy token and no GitHub settings change; merging is deploying. Cost: up to 5 minutes'
  delay, and anyone who can merge to `main` can deploy (the same people as today).
- **Nobody logs in to set it up.** The droplet's initialization script runs
  `deploy/bootstrap.sh` (Docker, swap, key-only SSH, log rotation, the updater). No DigitalOcean
  connector exists, so Zac creates the droplet and the DNS records; everything else is code.

## 2026-10-27 (production prep)
- **Public hosting is the goal now** (Zac asked to push MeshToMod to production). Draft PR only
  until he says merge; nothing is deployed or bought without his yes.
- **Jobs without Redis.** An in-process queue in the API plus one spawned process per
  conversion. A single server doesn't need RQ, a killed process frees its memory, and a timeout
  can actually stop a job. Cost: a restart drops queued jobs, and the API must run as one
  process. Move to Redis + RQ only if we need more than one API server.
- **Spawn, not fork, for job processes.** OpenCASCADE and threads don't survive a fork.
- **Keep nothing the user uploaded.** The scan is deleted when its job ends; results after
  60 minutes. No access log. The privacy page states this and must stay true.
- **Caddy in front** for HTTPS and the request size cap (multipart bodies are written to disk
  before the API can check sizes). If DNS is on Cloudflare, records are DNS-only: its proxy
  cuts requests at 100 s and uploads at 100 MB.
- **Fair use per IP:** one job at a time and 20 per hour, queue of 4, 2.5M triangles max.
  Starting values; tune once real traffic shows up.

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
