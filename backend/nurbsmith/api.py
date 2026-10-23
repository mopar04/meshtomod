"""HTTP API. M0: health, tolerance presets, and mesh upload with a summary."""

from __future__ import annotations

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .mesh_io import SUPPORTED_FORMATS, MeshLoadError, load_mesh, summarize
from .tolerance import DEFAULT_LEVEL, LEVELS

MAX_UPLOAD_BYTES = 200 * 1024 * 1024

app = FastAPI(title="NUrbSmith", version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.get("/api/tolerances")
def tolerances() -> dict:
    return {
        "default": DEFAULT_LEVEL.key,
        "levels": [lvl.as_dict() for lvl in LEVELS.values()],
    }


@app.post("/api/meshes")
async def upload_mesh(file: UploadFile = File(...)) -> dict:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Mesh is larger than {MAX_UPLOAD_BYTES // 2**20} MB")
    try:
        mesh = load_mesh(data, file.filename or "")
    except MeshLoadError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "filename": file.filename,
        "summary": summarize(mesh).as_dict(),
        "supported_formats": list(SUPPORTED_FORMATS),
    }
