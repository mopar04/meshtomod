# CLAUDE.md

Working notes for Claude (claude.ai projects) and Claude Code (local or cloud). The repo is the
source of truth: read and update the docs below so any session can pick up the work.

## Before you start
1. Read `docs/STATUS.md` for where things stand, then `docs/PLAN.md` for the design.
2. Check `docs/DECISIONS.md` before changing direction on anything listed there.
3. List the open PRs, drafts included (see "Who's working on what" below).

## When you finish a piece of work
- Update `docs/STATUS.md` (done / in progress / next).
- Add a dated entry to `docs/DECISIONS.md` for any choice a later session should not relitigate.
- Keep `docs/PLAN.md` current if the design changed.

## Roles
- Zac (mopar04) owns the product. Merges happen only on his word.
- Two Claude workspaces write to this repo. The repo and its open PRs keep them in sync:
  - **Claude app project "Mesh to Mod"**: UI/UX and product discussion with Claude as PM, and
    any work that needs files on Zac's Mac (scans in `Developer/Nurbsmith/Test Scans`, prints).
  - **Duet** (Zac's Claude Duet threads): technical build, logistics, roadblocks and the
    production push, via PRs.

## Who's working on what
Open PRs are the in-flight list: one place, always current, no merge needed to update it.
1. Before you start, list open PRs (drafts too). If one touches the files you need, work on
   other files or ask Zac first. Never two workspaces on the same files at once.
2. Push your branch and open a draft PR as soon as you start, not when you finish, so the
   other workspace sees the claim. Start its description with:
   ```
   Workspace: Duet | Claude app
   Touches: main files or folders
   Status: one line, kept current
   Handoff: what's done, what's next, what waits on Zac
   ```
3. Work on your own branch: `duet/<topic>` or `app/<topic>` when you pick the name; a name
   your session is given (like `claude/...`) is fine, since the PR says which workspace.
   Never commit to a branch another workspace's open PR uses.
4. Update Status and Handoff whenever you stop. Merged or closed takes it off the list.
5. Shared docs (`docs/STATUS.md`, `docs/DECISIONS.md`) change in the same PR as the work.
   On a conflict there, merge `main` in and keep both sides.
6. Local files stay on Zac's Mac: push code, findings and numbers, not raw scans, unless
   Zac says so.
7. No GitHub access in your session? Ask Zac what's open before you write to the repo.

## Layout
- `backend/meshtomod/`: Python geometry package and FastAPI app
  - `mesh_io.py` load and summarise meshes (trimesh)
  - `tolerance.py` accuracy level presets
  - `step_io.py` STEP read/write and face-type checks (CadQuery / OpenCASCADE)
  - `segment.py` region growing into planar regions, noise filter merges
  - `brep.py` plane snapping, freeform check, exact solid rebuild from plane intersections
  - `cylinders.py` cylinder fitting: strips of planes grown into hole/boss/fillet patches
  - `features.py` patches to CAD features (hole, boss, fillet, round body), standard sizes
  - `revolve.py` shapes turned about an axis (cones, spheres, tori) fitted as a 2D profile
  - `freeform.py` other smooth curved areas as B-spline height fields (curved top, mound, scoop)
  - `thicken.py` one-sided scans (an open sheet) thickened into a solid wall
  - `ends.py` flat openings in a scan (an unscanned base or tube end) closed with their plane
  - `cells.py` fallback solid from plane-cut blocks, for scans with gaps and warps
  - `quality.py` scan noise estimate, fit score, deviation colours
  - `orient.py` placement before export: lay on face, true up, trim flush, origin, up axis,
    stand on an axis
  - `sink.py` the user's sketch sunk into or extruded from the model, square to the surface
  - `recipe.py` feature recipe JSON: what the model is made of, in designer order
  - `history.py` editable history: base solid plus one CAD step per feature, and a CadQuery
    replay that proves the steps rebuild the model
  - `history_files.py` the history as a zip per CAD tool, from the templates in `cad/`
    (Fusion script, Onshape custom feature, FreeCAD macro)
  - `convert.py` the pipeline: repair, quality, decimate, segment, cylinders, snap, rebuild,
    features, score
  - `fixtures.py` synthetic meshes with known answers, plus scanner-like noise
  - `jobs.py` conversion jobs: queue, one spawned process per job, limits, result cleanup
  - `config.py` server settings from `MESHTOMOD_*` environment variables
  - `api.py` HTTP API
- `backend/tests/`: pytest suite
- `frontend/`: React + TypeScript + three.js (react-three-fiber) viewer
- `docker-compose.yml`: api on :8000, web on :5173 (development)
- `docker-compose.prod.yml`, `frontend/Caddyfile`, `.env.example`: production (`docs/DEPLOY.md`)
- `deploy/`: server bootstrap and the auto-updater that deploys green commits on main

## Commands
```sh
# backend (Python 3.12; CadQuery has no 3.13 wheels yet)
cd backend
uv venv -p 3.12 .venv && uv pip install -p .venv -e ".[dev]"
.venv/bin/pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/uvicorn meshtomod.api:app --reload

# frontend
cd frontend
npm ci
npm run build      # typecheck + bundle
npm run dev        # proxies /api to :8000 (override with API_URL)
```

## Conventions
- The name is **MeshToMod** everywhere: site meshtomod.com (meshtomodel.com redirects to it),
  repo `mopar04/meshtomod`, Python package `meshtomod`. The old name NURBSmith only survives in
  history and in the calibration part's embossed text.
- License is AGPL-3.0. Reusing CNC Kitchen's AGPL code (ScanRuler, BumpMesh) is fine with
  credit in the file header.
- Units are millimetres internally. Inches are a display/input concern only.
- Every geometry stage gets tests against `fixtures.py` meshes with known ground truth, with and
  without noise. Check results by face type (`face_type_counts`) and deviation, not just "it ran".
- Exported shapes must pass `isValid()`; never write an invalid B-rep.
- CI (GitHub Actions) runs ruff, pytest and the frontend build on every PR.
