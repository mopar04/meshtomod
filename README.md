# MeshToMod

Turn a scanned mesh (STL, OBJ, PLY, 3MF) into a clean, editable STEP solid built from real CAD
geometry: planes, cylinders, cones, fillets, holes, and smooth freeform patches only where nothing
simpler fits. The goal is a functional part you can modify in Fusion 360, Onshape, FreeCAD or
Shapr3D and then print or machine, not a pixel-perfect copy of the scan.

- Pick an accuracy level (Rough, Functional, Precise). Detail below that level, like embossed text
  or mold lines, is treated as noise and smoothed away.
- Sink or extrude simple shapes (for example a 4 x 6 in pad on a dash) to make flat mounting faces.
- Export STEP plus a feature recipe that can replay the build as a timeline in Onshape and Fusion.

Status: flat-faced parts convert end to end (milestone M1). Curved surfaces are next. See
[docs/STATUS.md](docs/STATUS.md).

## Run it

```sh
docker compose up --build
```

Web UI at http://localhost:5173, API at http://localhost:8000/docs.

Without Docker:

```sh
cd backend && uv venv -p 3.12 .venv && uv pip install -p .venv -e ".[dev]"
.venv/bin/uvicorn meshtomod.api:app --reload      # API on :8000
cd ../frontend && npm install && npm run dev       # UI on :5173, proxies /api
```

Set `VITE_DONATE_URL` when building the frontend to show a donate link.

## Docs

- [docs/PLAN.md](docs/PLAN.md): architecture, tolerance design, milestones, risks
- [docs/DECISIONS.md](docs/DECISIONS.md): dated decision log
- [docs/STATUS.md](docs/STATUS.md): what is done, in progress, next
- [docs/UI_REFERENCES.md](docs/UI_REFERENCES.md): UI patterns we follow (BumpMesh, ScanRuler)
- [CLAUDE.md](CLAUDE.md): working notes for Claude and Claude Code sessions

## License

Free and open source under the [GNU AGPL-3.0](LICENSE). If you run a modified version as a
web service, you must publish your changes. Inspired by CNC Kitchen's open-source tools.
