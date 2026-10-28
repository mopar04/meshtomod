# Status

_Last updated: 2026-10-28 (M3d thicken skin)_

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

## Real scans tried (Zac's, in `Developer/Nurbsmith/Test Scans`)
- Scale checks out: tape roll 92.5/76.8/48.5 vs 93/76/48.5; cap 102.2–102.8 vs 102.
- Box with floating bits: bits dropped; Rough converts (98% within 1 mm); Functional is held
  back by faces warped up to 2 mm.
- RC stand (two bent plates on a threaded post), full scan: both plates with holes and
  fillets, 82% within 0.3 mm, so still refused. The post and the round feet are not modelled.
- Bin (thin walls), tape roll (open end, rough edges), cap (front and back as separate scans),
  smoke detector and bottle (freeform): not converting yet.

## In progress
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

## Next (M2.5: real-scan readiness, agreed with Zac)
- Thin walls: detect wall pairs, measure thickness, patch missing wall areas from it (bin).
- Open and partial scans: close an open end with a plane; trim ragged rims.
- Merge: try the cap's front and back; check the merged calibration scan's misses (the 3.4
  mm hole, fillets and chamfers that don't apply on the scan).
- Top-only scans (no underside) are refused: unmodelled partial fillets and the boss add up
  past the 15% freeform limit. Thicken (M3d) only takes smooth sheets, not these: a blocky
  part's top half isn't one height field, so it still needs the other side or a closing face.
- Fewer spurious planes on warped real faces (RC stand gives about 1,000 faces).
- Background jobs: a 2M-face scan takes 5 to 10 minutes.

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
- Conversion runs inside the request. Results are kept in a temp folder, last 50 only.

## Open questions for Zac
- Default units: mm or inches.
- Local-only at first, or public hosting from day one.
- Donate link URL.
