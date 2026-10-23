# NUrbSmith

Turn a scanned mesh (STL, OBJ, PLY, 3MF) into a clean, editable STEP solid built from real CAD
geometry: planes, cylinders, cones, fillets, holes, and smooth freeform patches only where nothing
simpler fits. The goal is a functional part you can modify in Fusion 360, Onshape, FreeCAD or
Shapr3D and then print or machine, not a pixel-perfect copy of the scan.

- Pick an accuracy level (Rough, Functional, Precise). Detail below that level, like embossed text
  or mold lines, is treated as noise and smoothed away.
- Sink or extrude simple shapes (for example a 4 x 6 in pad on a dash) to make flat mounting faces.
- Export STEP plus a feature recipe that can replay the build as a timeline in Onshape and Fusion.

Status: early scaffold (milestone M0). See [docs/STATUS.md](docs/STATUS.md).

## Run it

```sh
docker compose up --build
```

Web UI at http://localhost:5173, API at http://localhost:8000/docs.

Without Docker:

```sh
cd backend && uv venv -p 3.12 .venv && uv pip install -p .venv -e ".[dev]"
.venv/bin/uvicorn nurbsmith.api:app --reload      # API on :8000
cd ../frontend && npm install && npm run dev       # UI on :5173, proxies /api
```

## Docs

- [docs/PLAN.md](docs/PLAN.md): architecture, tolerance design, milestones, risks
- [docs/DECISIONS.md](docs/DECISIONS.md): dated decision log
- [docs/STATUS.md](docs/STATUS.md): what is done, in progress, next
- [CLAUDE.md](CLAUDE.md): working notes for Claude and Claude Code sessions
