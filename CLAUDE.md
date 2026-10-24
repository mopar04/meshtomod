# CLAUDE.md

Working notes for Claude (claude.ai projects) and Claude Code (local or cloud). The repo is the
source of truth: read and update the docs below so any session can pick up the work.

## Before you start
1. Read `docs/STATUS.md` for where things stand, then `docs/PLAN.md` for the design.
2. Check `docs/DECISIONS.md` before changing direction on anything listed there.

## When you finish a piece of work
- Update `docs/STATUS.md` (done / in progress / next).
- Add a dated entry to `docs/DECISIONS.md` for any choice a later session should not relitigate.
- Keep `docs/PLAN.md` current if the design changed.

## Roles
- Zac (mopar04) owns the product. UI/UX discussion happens with Claude as PM in the claude.ai project.
- Technical build, logistics, roadblocks and ideas: Claude Code threads, via PRs to this repo.

## Layout
- `backend/nurbsmith/`: Python geometry package and FastAPI app
  - `mesh_io.py` load and summarise meshes (trimesh)
  - `tolerance.py` accuracy level presets
  - `step_io.py` STEP read/write and face-type checks (CadQuery / OpenCASCADE)
  - `segment.py` region growing into planar regions, noise filter merges
  - `brep.py` plane snapping, freeform check, exact solid rebuild from plane intersections
  - `cylinders.py` cylinder fitting: strips of planes grown into hole/boss/fillet patches
  - `features.py` patches to CAD features (hole, boss, fillet, round body), standard sizes
  - `cells.py` fallback solid from plane-cut blocks, for scans with gaps and warps
  - `quality.py` scan noise estimate, fit score, deviation colours
  - `convert.py` the pipeline: repair, quality, decimate, segment, cylinders, snap, rebuild,
    features, score
  - `fixtures.py` synthetic meshes with known answers, plus scanner-like noise
  - `api.py` HTTP API
- `backend/tests/`: pytest suite
- `frontend/`: React + TypeScript + three.js (react-three-fiber) viewer
- `docker-compose.yml`: api on :8000, web on :5173

## Commands
```sh
# backend (Python 3.12; CadQuery has no 3.13 wheels yet)
cd backend
uv venv -p 3.12 .venv && uv pip install -p .venv -e ".[dev]"
.venv/bin/pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/uvicorn nurbsmith.api:app --reload

# frontend
cd frontend
npm ci
npm run build      # typecheck + bundle
npm run dev        # proxies /api to :8000 (override with API_URL)
```

## Conventions
- Display name is **NURBSmith**; code identifiers are `nurbsmith`.
- License is AGPL-3.0. Reusing CNC Kitchen's AGPL code (ScanRuler, BumpMesh) is fine with
  credit in the file header.
- Units are millimetres internally. Inches are a display/input concern only.
- Every geometry stage gets tests against `fixtures.py` meshes with known ground truth, with and
  without noise. Check results by face type (`face_type_counts`) and deviation, not just "it ran".
- Exported shapes must pass `isValid()`; never write an invalid B-rep.
- CI (GitHub Actions) runs ruff, pytest and the frontend build on every PR.
