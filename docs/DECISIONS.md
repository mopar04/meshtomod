# Decisions

Newest first. One entry per choice a later session should not have to relitigate.

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
