"""HTTP API: mesh upload/summary, conversion jobs, and result downloads."""

from __future__ import annotations

import asyncio
import threading

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import __version__
from .config import Settings
from .convert import drop_loose_pieces
from .jobs import UNIT_SCALE, Busy, JobManager, RateLimited
from .mesh_io import SUPPORTED_FORMATS, MeshLoadError, file_format, load_mesh, summarize
from .orient import check_options, resting_faces
from .tolerance import DEFAULT_LEVEL, LEVELS, get_level

settings = Settings.from_env()
settings.check()
jobs = JobManager(settings)
# Upload analysis (summary and lay-flat faces) runs in the request; cap how many at once.
_analyses = threading.BoundedSemaphore(max(2, settings.max_running_jobs + 1))

app = FastAPI(title="MeshToMod", version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Busy)
def _busy(_: Request, exc: Busy) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=503, headers={"Retry-After": "60"})


@app.exception_handler(RateLimited)
def _rate_limited(_: Request, exc: RateLimited) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=429, headers={"Retry-After": "600"})


def client_address(request: Request) -> str:
    if settings.client_ip_header:
        value = request.headers.get(settings.client_ip_header, "")
        if value:
            return value.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.get("/api/tolerances")
def tolerances() -> dict:
    return {
        "default": DEFAULT_LEVEL.key,
        "levels": [lvl.as_dict() for lvl in LEVELS.values()],
    }


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, f"Mesh is larger than {settings.max_upload_mb} MB")
    return data


def _check_units(units: str) -> None:
    if units not in UNIT_SCALE:
        raise HTTPException(422, f"Unknown units {units!r}; expected one of {list(UNIT_SCALE)}")


@app.post("/api/meshes")
async def upload_mesh(file: UploadFile = File(...), units: str = Form("mm")) -> dict:
    _check_units(units)
    data = await _read_upload(file)
    if not _analyses.acquire(blocking=False):
        raise Busy("The converter is busy. Try again in a minute.")
    try:

        def analyse():
            mesh = load_mesh(data, file.filename or "")
            if len(mesh.faces) > settings.max_faces:
                raise HTTPException(
                    413,
                    f"This scan has {len(mesh.faces):,} triangles; this server takes up to "
                    f"{settings.max_faces:,}. Decimate it first (Blender, Meshmixer).",
                )
            if UNIT_SCALE[units] != 1.0:
                mesh.apply_scale(UNIT_SCALE[units])
            return mesh, resting_faces(drop_loose_pieces(mesh.copy()))

        try:
            mesh, faces = await run_in_threadpool(analyse)
        except MeshLoadError as exc:
            raise HTTPException(422, str(exc)) from exc
    finally:
        _analyses.release()
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


async def _submit(
    request: Request,
    file: UploadFile,
    level: str,
    units: str,
    second_file: UploadFile | None,
    lay_flat: str,
    origin: str,
    up: str,
    trim_flush: float,
):
    try:
        get_level(level)
        check_options(origin, up, trim_flush)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    _check_units(units)
    normal = _lay_flat(lay_flat)
    uploads = [file] + ([second_file] if second_file is not None else [])
    inputs = []
    for i, upload in enumerate(uploads, start=1):
        try:
            ext = file_format(upload.filename or "")
        except MeshLoadError as exc:
            raise HTTPException(422, str(exc)) from exc
        inputs.append((f"input-{i}.{ext}", await _read_upload(upload)))
    opts = {
        "level": level,
        "units": units,
        "max_faces": settings.max_faces,
        "input": inputs[0][0],
        "filename": file.filename or "model.stl",
        "lay_flat": None if normal is None else normal.tolist(),
        "origin": origin,
        "up": up,
        "trim_flush": trim_flush,
    }
    if second_file is not None:
        opts["second_input"] = inputs[1][0]
        opts["second_filename"] = second_file.filename or "second.stl"
    return jobs.submit(client_address(request), opts, inputs)


_CONVERT_DOC = """second_file is an optional scan of the other side of the same part (the bottom, or
    the back); it is lined up with the first and merged before converting.

    Placement of the STEP: lay_flat is the normal of the face to rest the part on (one of the
    upload's lay_flat suggestions), origin the point moved onto the origin (keep, center,
    bottom, corner) and up the CAD tool's up axis (z: base on XY; y: base on XZ). trim_flush
    (mm, with lay_flat) cuts bumps up to that deep off the base so it sits flush."""


@app.post("/api/jobs", status_code=202)
async def start_job(
    request: Request,
    file: UploadFile = File(...),
    level: str = Form(DEFAULT_LEVEL.key),
    units: str = Form("mm"),
    second_file: UploadFile | None = File(None),
    lay_flat: str = Form(""),
    origin: str = Form("keep"),
    up: str = Form("z"),
    trim_flush: float = Form(0.0),
) -> dict:
    job = await _submit(request, file, level, units, second_file, lay_flat, origin, up, trim_flush)
    return job.as_dict(jobs.position(job))


start_job.__doc__ = "Queue a conversion; poll GET /api/jobs/{id} for the result. " + _CONVERT_DOC


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Job expired or not found; convert again")
    return job.as_dict(jobs.position(job))


@app.post("/api/convert")
async def convert_mesh(
    request: Request,
    file: UploadFile = File(...),
    level: str = Form(DEFAULT_LEVEL.key),
    units: str = Form("mm"),
    second_file: UploadFile | None = File(None),
    lay_flat: str = Form(""),
    origin: str = Form("keep"),
    up: str = Form("z"),
    trim_flush: float = Form(0.0),
) -> dict:
    job = await _submit(request, file, level, units, second_file, lay_flat, origin, up, trim_flush)
    await asyncio.to_thread(job.finished.wait)
    if job.status == "failed":
        raise HTTPException(job.code, job.detail)
    return job.result


convert_mesh.__doc__ = (
    "Convert a scan and wait for the result (same queue as /api/jobs). " + _CONVERT_DOC
)


@app.get("/api/results/{result_id}/{name}")
def get_result(result_id: str, name: str) -> FileResponse:
    folder = jobs.result_folder(result_id)
    if folder is None:
        raise HTTPException(404, "Result expired or not found; convert again")
    if name.endswith(".step"):
        return FileResponse(folder / "model.step", media_type="application/step", filename=name)
    if name in ("model.stl", "deviation.ply"):
        return FileResponse(folder / name, media_type="application/octet-stream")
    raise HTTPException(404, "Unknown file")
