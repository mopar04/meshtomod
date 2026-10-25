"""HTTP API: mesh upload/summary, conversion to STEP, and result downloads."""

from __future__ import annotations

import logging
import re
import tempfile
import uuid
from collections import OrderedDict
from pathlib import Path

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from . import __version__
from .brep import UnsupportedGeometry
from .convert import convert, drop_loose_pieces
from .merge import MergeError, merge_scans
from .mesh_io import SUPPORTED_FORMATS, MeshLoadError, load_mesh, summarize
from .orient import (
    check_options,
    homogeneous,
    lay_flat_rotation,
    place,
    resting_faces,
)
from .quality import deviation_colors
from .step_io import write_step
from .tolerance import DEFAULT_LEVEL, LEVELS, get_level

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_STORED_RESULTS = 50
UNIT_SCALE = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}

app = FastAPI(title="NURBSmith", version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_results_dir = Path(tempfile.mkdtemp(prefix="nurbsmith-"))
_results: OrderedDict[str, Path] = OrderedDict()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.get("/api/tolerances")
def tolerances() -> dict:
    return {
        "default": DEFAULT_LEVEL.key,
        "levels": [lvl.as_dict() for lvl in LEVELS.values()],
    }


async def _read_mesh(file: UploadFile, units: str = "mm"):
    if units not in UNIT_SCALE:
        raise HTTPException(422, f"Unknown units {units!r}; expected one of {list(UNIT_SCALE)}")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Mesh is larger than {MAX_UPLOAD_BYTES // 2**20} MB")
    try:
        mesh = load_mesh(data, file.filename or "")
    except MeshLoadError as exc:
        raise HTTPException(422, str(exc)) from exc
    if UNIT_SCALE[units] != 1.0:
        mesh.apply_scale(UNIT_SCALE[units])
    return mesh


@app.post("/api/meshes")
async def upload_mesh(file: UploadFile = File(...), units: str = Form("mm")) -> dict:
    mesh = await _read_mesh(file, units)
    faces = await run_in_threadpool(lambda: resting_faces(drop_loose_pieces(mesh.copy())))
    return {
        "filename": file.filename,
        "summary": summarize(mesh).as_dict(),
        "supported_formats": list(SUPPORTED_FORMATS),
        "lay_flat": [f.as_dict() for f in faces],
    }


def _lay_flat(text: str) -> np.ndarray | None:
    """'nx,ny,nz': the outward normal of the face to lay the part on, in the scan's frame."""
    if not text:
        return None
    try:
        normal = np.array([float(v) for v in text.split(",")])
    except ValueError:
        normal = np.zeros(0)
    if normal.shape != (3,) or not np.isfinite(normal).all() or np.linalg.norm(normal) < 1e-9:
        raise HTTPException(422, "lay_flat must be a face normal written as x,y,z")
    return normal / np.linalg.norm(normal)


@app.post("/api/convert")
async def convert_mesh(
    file: UploadFile = File(...),
    level: str = Form(DEFAULT_LEVEL.key),
    units: str = Form("mm"),
    second_file: UploadFile | None = File(None),
    lay_flat: str = Form(""),
    origin: str = Form("keep"),
    up: str = Form("z"),
    trim_flush: float = Form(0.0),
) -> dict:
    """Convert a scan. second_file is an optional scan of the other side of the same part
    (the bottom, or the back); it is lined up with the first and merged before converting.

    Placement of the STEP: lay_flat is the normal of the face to rest the part on (one of the
    upload's lay_flat suggestions), origin the point moved onto the origin (keep, center,
    bottom, corner) and up the CAD tool's up axis (z: base on XY; y: base on XZ). trim_flush
    (mm, with lay_flat) cuts bumps up to that deep off the base so it sits flush."""
    try:
        tol = get_level(level)
        check_options(origin, up, trim_flush)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    normal = _lay_flat(lay_flat)
    mesh = await _read_mesh(file, units)
    merged = None
    if second_file is not None:
        other = await _read_mesh(second_file, units)
        try:
            merged = await run_in_threadpool(merge_scans, mesh, other)
        except MergeError as exc:
            raise HTTPException(422, str(exc)) from exc
        mesh = merged.mesh
    laid = None
    laid_or_eye = np.eye(3)
    if normal is not None:
        # As the upload suggested it: on the scan without its loose bits.
        laid = laid_or_eye = lay_flat_rotation(drop_loose_pieces(mesh.copy()), normal)
        mesh = mesh.copy()
        mesh.apply_transform(homogeneous(laid))
    try:
        result = await run_in_threadpool(convert, mesh, tol)
    except UnsupportedGeometry as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # OpenCASCADE failures surface as assorted exception types
        log.exception("Conversion failed for %s at %s", file.filename, level)
        raise HTTPException(
            422, "Could not rebuild a clean solid from this scan. Try a looser accuracy level."
        ) from exc

    placement = await run_in_threadpool(place, result.solid, laid, origin, up, trim_flush)
    # Viewer files (and the report's sizes) are in the view frame: Z up, placed. The scan
    # stays in the frame it was converted in until here.
    result.solid = placement.view_solid
    result.scan_mesh = result.scan_mesh.copy()
    result.scan_mesh.apply_transform(placement.view @ np.linalg.inv(homogeneous(laid_or_eye)))
    if placement.note:
        result.notes.append(placement.note)

    result_id = uuid.uuid4().hex
    folder = _results_dir / result_id
    folder.mkdir()
    write_step(placement.export_solid, folder / "model.step")
    result.solid.exportStl(str(folder / "model.stl"), tolerance=tol.fit_tolerance_mm / 4)
    scan = result.scan_mesh.copy()
    scan.visual.vertex_colors = deviation_colors(result.deviation_mm, tol.fit_tolerance_mm)
    scan.export(folder / "deviation.ply")
    _remember(result_id, folder)

    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(file.filename or "model").stem) or "model"
    report = result.report()
    report["placement"] = placement.as_dict()
    if merged is not None:
        report["merge"] = merged.report()
    return {
        "id": result_id,
        "level": tol.as_dict(),
        "report": report,
        "downloads": {
            "step": f"/api/results/{result_id}/{stem}.step",
            "model_stl": f"/api/results/{result_id}/model.stl",
            "deviation_ply": f"/api/results/{result_id}/deviation.ply",
        },
    }


@app.get("/api/results/{result_id}/{name}")
def get_result(result_id: str, name: str) -> FileResponse:
    folder = _results.get(result_id)
    if folder is None:
        raise HTTPException(404, "Result expired or not found; convert again")
    if name.endswith(".step"):
        return FileResponse(folder / "model.step", media_type="application/step", filename=name)
    if name in ("model.stl", "deviation.ply"):
        return FileResponse(folder / name, media_type="application/octet-stream")
    raise HTTPException(404, "Unknown file")


def _remember(result_id: str, folder: Path) -> None:
    _results[result_id] = folder
    while len(_results) > MAX_STORED_RESULTS:
        _, old = _results.popitem(last=False)
        for f in old.iterdir():
            f.unlink()
        old.rmdir()
