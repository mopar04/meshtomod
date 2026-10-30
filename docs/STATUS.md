# Status

_Last updated: 2026-10-30 (M4 editable history; Zac's batch print scans run; auto-simplify)_

## Done
- **M0 scaffold:** API, mesh loading, viewer, Docker, CI.
- **M1 flat-faced parts:** segmentation, plane snapping, exact solid rebuild, confidence
  metrics, deviation view, STEP download. AGPL-3.0, naming (MeshToMod: repo, package and app renamed 2026-10-27), UI reference notes.
- **M2 mechanical features:**
  - Cylinder finding (`cylinders.py`): pairs of planar strips seed a cylinder fit, which is
    grown over triangles and refitted. Checks reject gentle curves that fit a plane, wide
    "cylinders" fitted to a few big flat triangles, and corners passing as one gentle cylinder.
  - Features (`features.py`): through and blind holes, counterbores, slots, bosses, fillets,
    chamfers and round bodies. Hole sizes snap to standard metric clearance / close fit / tap
    drill and inch sizes only within 0.35x the tolerance, otherwise they are kept as measured.
    Fillets below the level's smallest radius are made sharp.
  - Base solid: the exact plane-intersection rebuild, with a fallback (`cells.py`) that cuts a
    block with the fitted planes near their regions and keeps the inside blocks. The fallback
    survives gaps, warps and ragged rims, and keeps each separate body of an assembly scan.
  - Real scans: loose floating pieces are dropped and counted. Dense scans are decimated
    gently (several low-aggression passes; the default one cut corners). Noise is estimated
    on a densified copy, so clean coarse meshes don't read as noisy. Faces warped a little for
    their size (bent sheet metal) stay flat. Deviation scoring is chunked and approximates
    far-off points, and the block fuse runs in rounds (10 GB down to 3 GB on a 2.2M-face scan).
  - Guards: refuse a result that matches under 90% of the scan at the working tolerance, and
    refuse parts where over 15% of the surface is freeform (curved but not a feature).
  - API returns `features` and `notes`; the viewer lists features ("4× Hole Ø6.6 mm (M6
    clearance), through") and notes.
  - Tests: five CAD-built parts (holed plate, filleted block, boss plate, blind hole block,
    bracket) at 0.05 mm noise recover exact face types, feature sizes and volume.

- **M3a cones, spheres and tori** (`revolve.py`): any shape turned about an axis is found
  from where its face normals point, folded into a 2D profile, fitted with lines and arcs,
  and rebuilt by revolving the outline. Reported as a turned part (a whole lathe part), a
  countersink, a dome, a tapered boss, or a turned boss or recess. Sizes and cone angles snap
  to round or standard values. On the batch prints' designs (turned part, surfaces plate)
  and on dense noisy copies of them (0.02 and 0.05 mm) every surface comes back as drawn;
  the calibration part's countersink now reads 4.5 mm, head 9, 90 degrees. Precise is right
  on the designs and on 0.02 mm noise (a laser scan). Takes 20 to 40 s on the designs and
  about 1 to 2 minutes on a million-face scan. Not yet tried on Zac's real scans of these
  prints. Limits: at Rough (1 mm, 5 mm smallest feature) the plate is still refused as
  curved, since its 2 mm rounds and small cones are below what Rough keeps; at Precise on a
  scan too noisy for it (0.05 mm) the ring is missed. At Precise on the dense 0.02 mm copy the
  flat-face rebuild leaves five slivers (under 3 mm² each) along the plate's bottom edge: a
  planar-step issue to look at with M2.5.

- **M3b freeform surfaces** (`freeform.py`): a smooth curved area that is not a plane, a
  feature or a turned shape is fitted as one B-spline surface, seen from one side as a height
  field, and joined to the part with a boolean: a mound added to a face, a scoop cut into one,
  or a whole curved top cut off a block. Reported as "Freeform mound, 7 mm tall", "Freeform
  scoop, 3.5 mm deep" or "Freeform surface". On three test designs (a mouse-like lofted top,
  a lopsided mound on a plate, a tilted thumb scoop in a block) and on dense noisy copies
  turned to an odd angle (0.02 and 0.05 mm), each comes back as the flat faces plus one
  B-spline face, within 0.006 mm of the drawn volume per mm² and over 99.5% within 0.3 mm.
  Takes 20 to 50 s on the noisy copies, up to 100 s on the coarser design meshes. Not yet
  tried on Zac's freeform print scan. Limits: one height field per area (a fully rounded
  body like the smoke detector or bottle is still refused); a hole or boss in a freeform
  face isn't modelled yet.

- **M3d thicken skin** (`thicken.py`): a scan that is one smooth open sheet (one side of
  something, like the wave skin print) is fitted as one B-spline surface inside a smoothed
  outline of its torn border, and thickened into a wall: 3 mm by default, behind the scanned
  surface, on its scanned side, or centred on it, with square, chamfered (t/3) or rounded
  edges. On a gently curved wave and a dome, designs and dense noisy copies (0.05 mm, a torn
  border up to 1.5 mm deep, turned to an odd angle), the wall comes back as three B-spline
  faces (five with a chamfer or round), within 0.004 mm of the drawn volume per mm² on the
  designs and 2% on the noisy copies, and over 99.5% of the scan within 0.3 mm. Takes 7 to 10 s
  (about 1.5 to 2.5 minutes with rounded edges). A wall too thick for the curve is refused.
  The options are in the code but not yet in the API or viewer (after the production PR); for
  now every skin gets the defaults and a note saying so. Not yet tried on Zac's wave skin
  scan.

- **M3c sink, extrude and feature recipe** (geometry; the UI comes after the production PR):
  - `sink.py`: the user's sketch (rectangle, rounded rectangle, circle or slot, turned to any
    angle) placed at a point on the model, square to the average surface under its footprint,
    then cut in (sink) or added on (extrude) with a flat floor or top and an optional fillet
    on its edges. On a curved face the floor is the depth below the lowest point under the
    sketch, and the top the height above the highest, so neither leaves a sliver of the old
    surface. A sink that would split the part, or an extrusion that misses it, is refused;
    one hanging past an edge, or a fillet that doesn't fit, gets a note. Tested on a box's
    top and side, a cylinder's side and the mouse-like freeform top, with exact volumes.
  - `recipe.py`: the recipe JSON beside the STEP. Every step the conversion found, in the
    order a designer would make them (turned body, round body, bosses, turned and freeform
    adds, fillets and chamfers, holes and slots, turned cuts), with the positions and sizes
    CAD needs: hole and boss axes, the two planes a fillet joins, a turned shape's profile as
    lines and arcs in a plane through its axis. Then the user's sinks and extrusions, marked
    to replay. Positions follow the placement, so they match the STEP.

- **Auto-simplify on upload** (`simplify.py`, Zac 2026-10-30): scans over a level's limit
  (Rough 500k, Functional 1M, Precise 2M triangles) are simplified before converting instead
  of refused; the server opens up to 5M. Checked against the original (within half the fit
  tolerance plus the scan noise) and redone with more triangles if not. The Mesh panel says
  when a scan will be simplified, and the result notes "Simplified from X to Y triangles". On
  a dense noisy turned part cut to a tenth, the Ø4 cross hole and every turned face come
  back as from the full scan; a 6.2M-triangle scan goes to 1M in about 70 s. Not yet tried
  on one of Zac's big raw exports.

## Real scans tried (Zac's, in `Developer/Nurbsmith/Test Scans`)
- Scale checks out: tape roll 92.5/76.8/48.5 vs 93/76/48.5; cap 102.2–102.8 vs 102.
- Box with floating bits: bits dropped; Rough converts (98% within 1 mm); Functional is held
  back by faces warped up to 2 mm.
- RC stand (two bent plates on a threaded post), full scan: both plates with holes and
  fillets, 82% within 0.3 mm, so still refused. The post and the round feet are not modelled.
- Bin (thin walls), tape roll (open end, rough edges), cap (front and back as separate scans),
  smoke detector and bottle (freeform): not converting yet.
- **Batch prints, from the Drive folder "MeshToMod Scans" (2026-10-30, Functional):** the 10
  files under 10 MB were run through convert and the editable history. Each history replays
  with no step left out and matches the model's volume.
  - Turned Part Top: turned body Ø29.7 × 43.75 plus the Ø4 cross hole, through. 99.8% within
    0.3 mm. Was refused as "curved surfaces" until the half-seen cross hole and the unscanned,
    uneven base were handled (see DECISIONS).
  - Peg: Ø16 round body with an Ø8 pin, 99.96%. Was 15% (only the Ø8 was built) until a
    stepped round part became one round body per step.
  - Peg Base 96.7% (Ø8.4 through hole, chamfers); Surfaces Plate bottom 99.4%; Freeform
    Bottom 99.8%; Freeform Top 96.9% (one freeform surface); Size Gauge Top 95.6% and Bottom
    93.9% (11 and 12 holes); Surfaces Plate Top 90.1% (bosses, countersink, holes; seven
    small curved areas left flat).
  - Turned Part Bottom: refused. It is the underside view only, in two separate pieces, half
    the part; it needs merging with the top scan.
  - Each Top/Bottom pair is one part scanned from both sides. Converted alone, each half is
    closed flat where it wasn't seen, so holes read as blind. Merged (`merge.py`) and
    converted: Size Gauge 97.7% with most holes through; Surfaces Plate 93.2% but the tapered
    boss reads as a cone to a point; Freeform 81%; Turned Part refused (the bottom scan has a
    thin ring about 35 mm across, likely the turntable or a brim). Converting merged scans is
    next, with an "other side" upload in the web app.
  - Not run: the 10 files of 10 MB or more (thin wall, Cali, bottle, wave skin, merged
    scans), which the Drive connector won't download.

## In progress
- **M4 editable history (2026-10-30, Zac: Fusion, Onshape, FreeCAD; "Merge once complete"):**
  the Export menu now offers STEP (default), an editable history for each tool, or one zip
  with everything. Each history rebuilds the part as a base solid plus one feature per step
  (bosses, turned shapes, fillets, chamfers, holes, slots, the user's sinks and extrusions,
  trim flush), each with its own size to change. The steps rebuild every test part exactly
  in CadQuery (holed plate, standoff, bracket, turned part, surfaces plate, round bodies).
  Not yet tried inside Fusion, Onshape or FreeCAD: Zac's first run in each is the test.
  Stepped round parts (a peg) start from the biggest round body, with a join per step.
  Sinks and extrusions join the history once the viewer can make them.

- **Real-scan readiness, part 1 (scanner holder):** Zac's own design (`tests/fixtures/
  scanner_holder.stl`) is now a ground-truth test. It went from 77% to 95% within 0.3 mm,
  with volume within 0.5%. Changes:
  - Bowed walls (a huge-radius sliver of a cylinder) stay planes.
  - The block solid starts from the oriented bounding box.
  - Planes cut through to the sharp corner under their fillets, and blocks there vote by
    that sharp corner.
  - Hole walls count as solid until drilled, and boss walls as empty until added.
  - Fillets are applied together, biggest first, keeping those that work together.
  - Stray zero-length edges no longer break fillets.
  - The scan of the holder converts at 87%. Its deep pockets were not fully scanned, which
    Zac says is a coverage problem, not ours.

- **Calibration part** (`tools/calibration_part.py`, 19 known features; Zac is printing and
  scanning it). The design mesh went from 90% to 98% within 0.3 mm at Functional. Now right:
  the holes including the non-standard 7.3 mm one, the countersunk M4 hole (4.5, head 9, 90
  degrees, since M3a), the counterbore (10 mm, 4 deep, over
  5.5 mm), the blind hole depth (5.0), the slot (6 × 20), the boss (16 × 20) with its cross
  hole, the thin wall, the r8 and r3 corner fillets and the 1.5 mm chamfers. Still wrong:
  - the corner blends where the r8 fillets meet the top chamfer (read as r11 fillets), and some
    fillets and chamfers that don't apply together,
  - the rounded pocket corners (rebuilt as small flats),
  - the ramp's overhanging end, which leaves a top face that OpenCASCADE's mesher can't
    tessellate in memory (it does after STEP export).

- **Merging two scans of one part** (`merge.py`; `/api/convert` takes an optional
  `second_file`). A turntable scan sees one side; the user flips the part and scans again.
  The second scan is placed on the first with no help from the user:
  - starts from every way of matching the two scans' main face directions, with the boxes
    lined up by ends or middles, then trimmed point-to-plane ICP;
  - the pose is picked by overlap, plus how much of each scan's open rim the other carries
    on from, less points left in open air in front of the other scan. Without the rim and air
    terms a flat part merges with its bottom laid on its top;
  - a final pass pins each scan's rim point to point to the other, which fixes the height
    where both scans overlap only on upright walls (0.2 mm off before, 0.004 mm after);
  - where both saw the surface, the first scan is kept.
  On Zac's calibration print our merge matches the design at 96% within 0.3 mm (his
  scanner software's merge: 62%, about 1 mm and 1.6 degrees off), and converts at 92% (his:
  90%). Takes 30 to 80 s on 500k faces.
- Bowed faces: the "mostly curved" check now measures a face's bow without stray points (a
  seam, a sliver of the next face). The calibration print's underside is bowed about 0.45 mm
  and was refused as curved before.

- **Batch test prints** (`tools/batch_prints.py`, `tools/thin_wall_part.py`), printed and
  scanned by Zac 2026-10-25. Each scan lined up on its design (within 0.3 mm): thin wall
  94-98.5%, freeform 99.6/99.9%, size gauge 97%, surfaces plate 98-99%, turned part 97-99.5%,
  peg 100% and base 96%, wave skin (one side) 99.5%. Scans read 0.1-0.15 mm inside the part
  on every print (FDM layer ridges). Our merges on the design: thin wall 96.5% (Zac's
  scanner software 96.8%), freeform 99.8%, surfaces plate 98.9%, turned part 99.5%, size
  gauge 85% (0.25 mm off in height: a 4 mm plate leaves little wall to line up on).
  Converting the merges: thin wall 93.3% (the tube's outside is missed), size gauge 97.7%;
  the freeform was refused as curved; the surfaces plate and turned part were too, before
  M3a.

- **Placement before export** (`orient.py`, Zac 2026-10-25): Orca-style lay on face
  (hull faces the part rests on stably, hover and click in the viewer), squared to X/Y,
  origin at bottom centre / box centre / low corner / kept, Z up or Y up, and trim flush
  (cut bumps up to N mm below the base face). The STEP's base face is exactly level and on
  the origin. Tested on tilted CAD parts with and without noise; not yet on Zac's scans.
- **Stand a part on its axis** (`orient.py`, Zac's idea 2026-10-28): the report lists every
  axis the model found (a turned part's, then turned features, round bodies, bosses and
  holes, one per line), and placement can stand the part on one: the axis exactly vertical,
  the end with more flat face down, the biggest wall along X, and with the new "axis" origin
  the axis through the origin and the bottom on the ground. On the turned part scanned at an
  odd angle (clean and 0.02 mm noise) it stands on its Ø30 base with the axis exactly through
  the origin; the surfaces plate stood on its tapered boss lies flat, long side on X. Not yet
  in the API or viewer (drawing the axes and picking one), which comes after the production
  PR; the scan frame is kept, so it can always be undone.

- **Thin-wall fixes (2026-10-29):** a standoff or tube (a boss with a bore through it) was
  dropped whole, because its ring-shaped top touches only the boss and the bore; the thin-wall
  tray design went from 96.5% to 100% and the standoff plate is now a test part. A scan with
  gaps near its inside corners crashed the block solid ("Bnd_Box is void", a sliver block a
  fraction of a cubic millimetre); it now comes back as a partial match.

- **Open ends (2026-10-29, `ends.py`):** a scan with its base never scanned now closes that
  face exactly at the rim. A tape-roll-like ring (93 × 76 × 48.5) with an open end was
  refused as "mostly curved"; now 100% with exact volume, clean and at 0.02 and 0.05 mm
  noise. The open box and holed plate were 0.3 mm too thick at the open side (the block
  solid stopped at the bounding box); now exact. Not yet tried on Zac's tape roll scan,
  whose rough edge may not count as flat (trimming ragged rims is next).
- Found on the way: a perfectly clean, densely subdivided CAD mesh of the standoff plate loses
  its boss after decimation (90%, partial), while the coarse CAD export and noisy scans give
  100%. Real scans are noisy, but a dense STL from CAD would hit it.

## Production launch (started 2026-10-27, Zac: "push MeshToMod towards production")
Where the work happens: production work is done from Zac's Claude Duet chat, in PR #11
(branch `claude/project-thread-9iwv7n`, draft until Zac says merge). Sessions in the Mesh to Mod
project: read that PR and this section before touching jobs, limits, deploy files or the
privacy page, and leave notes here or on the PR rather than starting parallel production work.

Done in PR #11:
- Background jobs (`jobs.py`): `POST /api/jobs` + poll `GET /api/jobs/{id}`; the UI polls and
  shows its place in the queue. One conversion at a time (configurable), a short queue, each job
  in a spawned process with a timeout; out-of-memory fails one job, not the server.
  `/api/convert` still works (waits on the same queue).
- Limits (`config.py`, `MESHTOMOD_*` env): upload size, triangle count, queue length, one job at
  a time and 20 per hour per IP, results deleted after 60 min (also after a restart), uploaded
  scans deleted when the job ends, CORS from env.
- Production stack: `docker-compose.prod.yml`, Caddy (static app, HTTPS, `/api` proxy, 420 MB
  body cap, security headers, redirect domains), non-root API container with a healthcheck, no
  access log. Guide: `docs/DEPLOY.md`. CI validates the Caddyfile and builds both images.
- Privacy page and a source link in the footer (AGPL).

Stuck conversions (2026-10-30, Zac closed the page mid-conversion and was then told "You
already have a scan converting" with no way out; PR #22):
- The page shows the running conversion: its stage ("Finding flat faces… step 5 of 10"), a bar
  that moves by stage, the time so far, and Cancel. Cancel kills the job's process on the
  server (`DELETE /api/jobs/{id}`). Stages come from `progress.stage()` calls in the pipeline.
- A job no page has checked on for 3 minutes (`MESHTOMOD_JOB_ABANDON_S`) is stopped, so a
  closed page can't hold the server or the one-scan-at-a-time limit.
- The browser remembers its latest job (`lastJob.ts`). A reloaded or reopened page shows the
  job's progress, or its result (model, deviation map, report, STEP download) if it finished
  while away; results are still kept one hour. Without the original mesh open, the viewer
  offers Model and Deviation only. A finished job is also found after a server restart.
- Not tried in a real browser here (no browser in the build session): checked with the type
  check, the API end to end on a local server (stages, cancel, a closed page's job stopped and
  its files deleted) and tests in `test_jobs.py`.

Still open before a public launch:
- Done: repo renamed to `mopar04/meshtomod` and public; meshtomod.com and meshtomodel.com
  registered on Cloudflare.
- Hosting: DigitalOcean droplet, Basic 4 vCPU / 8 GB, $48/mo (Zac said go ahead 2026-10-29).
  `deploy/bootstrap.sh` sets the server up from its initialization script; `deploy/update.sh`
  deploys each green commit on `main` every 5 minutes. Zac: merge PR #11, create the droplet,
  add the four DNS records (steps in `docs/DEPLOY.md`).
- Decide: keep `scanner_holder.stl` as a public fixture; a contact address on the privacy page.
  (Default units: mm. Donate page: Zac's Buy Me a Coffee, below.)
- The UI has no way to add the second scan for merging (API only).

## Next (M2.5: real-scan readiness, agreed with Zac)
- Thin walls: detect wall pairs, measure thickness, patch missing wall areas from it (bin).
- Open and partial scans: trim ragged rims so a rough open end counts as flat (a flat open
  end is closed since 2026-10-29).
- Dense, perfectly clean CAD meshes: keep a boss through decimation (standoff plate).
- Merge: try the cap's front and back; check the merged calibration scan's misses (the 3.4
  mm hole, fillets and chamfers that don't apply on the scan).
- Top-only scans (no underside) are refused: unmodelled partial fillets and the boss add up
  past the 15% freeform limit. Thicken (M3d) only takes smooth sheets, not these: a blocky
  part's top half isn't one height field, so it still needs the other side or a closing face.
- Fewer spurious planes on warped real faces (RC stand gives about 1,000 faces).

## Known limits
- Noise matrix (5 test parts × noise 0 / 0.02 / 0.1 / 0.3 mm × 3 levels): correct up to 0.1 mm
  noise, with a few misses (the bracket at 0.1 mm Functional keeps 1 of 3 cylinders; the
  holed plate at 0.02 mm Rough finds 3 of 4 holes). At 0.3 mm noise results are poor; the
  guard catches some but not all. Zac's laser scans are 0.01 to 0.03 mm.
- Freeform areas must each be a height field (seen whole from one side). A cone, sphere or
  torus is only modelled as part of a shape turned about an axis (not, say, a ball-end fillet
  along a straight edge).
- A skin is one height-field sheet with one outline: no holes in it, and its corners round
  off by about the smallest feature size. Rounded edges are slow (fillets on B-splines).
- Jobs live in the API process: a restart drops queued and running jobs. One API process only.

## Zac's calls (2026-10-29)
- Low-match results come back marked partial, not refused (done: `partial` in the report and
  a warning first among the notes; the viewer shows it with the notes until a badge lands
  after the production PR).
- Default units: mm (already the highlighted choice when a file has no units).
- Public hosting from day one (the signup itself is in the production PR, #11).
- Donations: a Buy Me a Coffee pop-up with "Don't remind me again" (done: `DonatePrompt.tsx`,
  shown once per visit after 90 s). It opens Zac's page, https://buymeacoffee.com/voxelautocraft;
  `VITE_DONATE_URL` overrides it. The footer link in App.tsx still reads only `VITE_DONATE_URL`
  (empty in `Dockerfile.prod`), so it's hidden until the next PR points it at the same page.

## Handoff from the Claude app project (2026-10-29), where each item stands
- Lay flat on a face and centre to origin, through to STEP: done since 2026-10-25 (Placement).
- Low-fit results: decided and done (above).
- Finish M3: done (M3a to M3d, the axis step, M3c in PR #18); the viewer side of thicken,
  axes, sink/extrude and the recipe download comes after the production PR.
- Real-scan readiness (thin walls from measured thickness, keep real open ends open, merge
  front and back): in progress in Duet. Flat open ends close (above); thin walls next. The
  cap's front and back need its two scans in the MeshToMod Scans Drive folder.
- Click-to-delete stray scan bits in the viewer: after the production PR (it touches App.tsx
  and api.py).
- Zac: print and scan the next test batch, including a reprint of calibration part #1.

## Open questions for Zac
- Keep `scanner_holder.stl` as a public fixture?
- A contact address for the privacy page.
