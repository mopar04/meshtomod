"""Conversion jobs.

A conversion can take minutes and gigabytes, so the API doesn't run it inside the request.
Jobs wait in a short queue, a fixed number run at once, and each runs in a fresh process
(spawned, not forked: OpenCASCADE and threads don't survive a fork) that writes its files and
an outcome.json to the job's folder. A job that runs too long is killed; a job that dies
(out of memory) fails with a message instead of taking the server down.

The job id is also the result id: downloads are served from the job folder until it expires.
"""

from __future__ import annotations

import json
import logging
import multiprocessing
import re
import shutil
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .config import Settings

log = logging.getLogger(__name__)

UNIT_SCALE = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
OUTCOME = "outcome.json"


class JobError(Exception):
    """A conversion that can't be done, with the HTTP status and message for the user."""

    def __init__(self, code: int, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class Busy(Exception):
    """The queue is full, or this client already has a job running (HTTP 503)."""


class RateLimited(Exception):
    """This client has used its jobs for the hour (HTTP 429)."""


# --- the conversion itself (runs in the job process) -------------------------------------


def load_input(path: Path, filename: str, units: str, max_faces: int):
    from .mesh_io import MeshLoadError, load_mesh

    try:
        mesh = load_mesh(path.read_bytes(), filename)
    except MeshLoadError as exc:
        raise JobError(422, str(exc)) from exc
    if len(mesh.faces) > max_faces:
        raise JobError(
            413,
            f"{filename} has {len(mesh.faces):,} triangles; this server takes up to "
            f"{max_faces:,}. Decimate it first (Blender, Meshmixer) and try again.",
        )
    if UNIT_SCALE[units] != 1.0:
        mesh.apply_scale(UNIT_SCALE[units])
    return mesh


def run_conversion(folder: Path, opts: dict) -> dict:
    """Convert the scan(s) in folder and write model.step, model.stl and deviation.ply.

    Returns the API response for the result. Raises JobError for anything the user should
    hear about. Imports are local so the server process stays light."""
    from .brep import UnsupportedGeometry
    from .convert import convert, drop_loose_pieces
    from .merge import MergeError, merge_scans
    from .orient import homogeneous, lay_flat_rotation, place
    from .quality import deviation_colors
    from .step_io import write_step
    from .tolerance import get_level

    tol = get_level(opts["level"])
    units, max_faces = opts["units"], opts["max_faces"]
    mesh = load_input(folder / opts["input"], opts["filename"], units, max_faces)
    merged = None
    if opts.get("second_input"):
        other = load_input(folder / opts["second_input"], opts["second_filename"], units, max_faces)
        try:
            merged = merge_scans(mesh, other)
        except MergeError as exc:
            raise JobError(422, str(exc)) from exc
        mesh = merged.mesh
    laid = None
    laid_or_eye = np.eye(3)
    if opts.get("lay_flat") is not None:
        # As the upload suggested it: on the scan without its loose bits.
        normal = np.array(opts["lay_flat"])
        laid = laid_or_eye = lay_flat_rotation(drop_loose_pieces(mesh.copy()), normal)
        mesh = mesh.copy()
        mesh.apply_transform(homogeneous(laid))
    try:
        result = convert(mesh, tol)
    except UnsupportedGeometry as exc:
        raise JobError(422, str(exc)) from exc
    except Exception as exc:  # OpenCASCADE failures surface as assorted exception types
        log.exception("Conversion failed at %s", opts["level"])
        raise JobError(
            422, "Could not rebuild a clean solid from this scan. Try a looser accuracy level."
        ) from exc

    placement = place(result.solid, laid, opts["origin"], opts["up"], opts["trim_flush"])
    job_id = folder.name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(opts["filename"]).stem) or "model"
    write_step(placement.export_solid, folder / "model.step")
    # Before the solid moves to the view frame: the history starts from the converted one.
    history = write_history(folder, result, placement, laid_or_eye, opts["up"], stem)
    # Viewer files (and the report's sizes) are in the view frame: Z up, placed. The scan
    # stays in the frame it was converted in until here.
    result.solid = placement.view_solid
    result.scan_mesh = result.scan_mesh.copy()
    result.scan_mesh.apply_transform(placement.view @ np.linalg.inv(homogeneous(laid_or_eye)))
    if placement.note:
        result.notes.append(placement.note)

    result.solid.exportStl(str(folder / "model.stl"), tolerance=tol.fit_tolerance_mm / 4)
    scan = result.scan_mesh.copy()
    scan.visual.vertex_colors = deviation_colors(result.deviation_mm, tol.fit_tolerance_mm)
    scan.export(folder / "deviation.ply")

    report = result.report()
    report["placement"] = placement.as_dict()
    if merged is not None:
        report["merge"] = merged.report()
    return {
        "id": job_id,
        "level": tol.as_dict(),
        "report": report,
        "downloads": {
            "step": f"/api/results/{job_id}/{stem}.step",
            "model_stl": f"/api/results/{job_id}/model.stl",
            "deviation_ply": f"/api/results/{job_id}/deviation.ply",
            # Saved as history-<tool>.zip; downloaded as <part>-<tool>-history.zip.
            "history": {t: f"/api/results/{job_id}/{stem}-{t}-history.zip" for t in history},
        },
    }


def write_history(folder: Path, result, placement, laid: np.ndarray, up: str, stem: str) -> dict:
    """The editable history zips (history_files.py) beside the STEP: {tool: filename}, or
    nothing if they can't be made. The STEP never waits on them."""
    from .history import build_history
    from .history_files import write_history_zips
    from .orient import homogeneous
    from .step_io import write_step

    try:
        # The converted solid's frame to the STEP's: the placement without the lay-flat turn.
        matrix = placement.export @ np.linalg.inv(homogeneous(laid))
        trim = (placement.export_solid, up) if placement.trimmed_mm > 0 else None
        history, base = build_history(result, matrix=matrix, trim=trim)
        base_path = None
        if base is not None:
            base_path = write_step(base, folder / "base.step")
        return write_history_zips(folder, history, stem, base_path)
    except Exception:  # a history that can't be written leaves the STEP download as it is
        log.exception("Editable history failed")
        return {}


def run_job(folder: str, opts: dict, memory_mb: int = 0) -> None:
    """Job entry point: run the conversion and record the outcome in outcome.json."""
    path = Path(folder)
    if memory_mb:
        import resource

        limit = memory_mb * 2**20
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    try:
        outcome = {"ok": True, "result": run_conversion(path, opts)}
    except JobError as exc:
        outcome = {"ok": False, "code": exc.code, "detail": exc.detail}
    except MemoryError:
        outcome = {"ok": False, "code": 413, "detail": _TOO_BIG}
    except Exception:
        log.exception("Job %s crashed", path.name)
        outcome = {"ok": False, "code": 500, "detail": "Conversion failed unexpectedly."}
    (path / OUTCOME).write_text(json.dumps(outcome))


_TOO_BIG = "This scan needs more memory than the server has. Decimate it and try again."


# --- the queue (runs in the server process) ----------------------------------------------


@dataclass
class Job:
    id: str
    folder: Path
    opts: dict
    client: str
    status: str = "queued"  # queued, running, done, failed
    code: int = 200
    detail: str = ""
    result: dict | None = None
    finished: threading.Event = field(default_factory=threading.Event)

    @property
    def done(self) -> bool:
        return self.status in ("done", "failed")

    def as_dict(self, position: int | None) -> dict:
        body: dict = {"id": self.id, "status": self.status}
        if position is not None:
            body["position"] = position
        if self.status == "failed":
            body["code"], body["detail"] = self.code, self.detail
        if self.result is not None:
            body["result"] = self.result
        return body


class JobManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.results_dir = settings.data_dir / "results"
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._queue: deque[Job] = deque()
        self._jobs: dict[str, Job] = {}
        self._recent: dict[str, deque[float]] = {}
        self._started = False

    def _start(self) -> None:
        for i in range(self.settings.max_running_jobs):
            threading.Thread(target=self._work, name=f"job-worker-{i}", daemon=True).start()
        threading.Thread(target=self._sweep_forever, name="job-sweeper", daemon=True).start()
        self._started = True

    def submit(self, client: str, opts: dict, inputs: list[tuple[str, bytes]]) -> Job:
        """Queue a conversion. inputs are (file name on disk, bytes); opts name them."""
        s = self.settings
        now = time.time()
        with self._lock:
            if not self._started:
                self._start()
            if len(self._queue) >= s.max_queued_jobs:
                raise Busy("The converter is busy with other scans. Try again in a few minutes.")
            active = [j for j in self._jobs.values() if j.client == client and not j.done]
            if len(active) >= s.jobs_per_client:
                raise Busy("You already have a scan converting. Wait for it to finish.")
            recent = self._recent.setdefault(client, deque())
            while recent and recent[0] < now - 3600:
                recent.popleft()
            if len(recent) >= s.jobs_per_client_hour:
                raise RateLimited(
                    f"That's {s.jobs_per_client_hour} conversions this hour. Try again later."
                )
            recent.append(now)
            job_id = uuid.uuid4().hex
            folder = self.results_dir / job_id
            folder.mkdir()
            for name, data in inputs:
                (folder / name).write_bytes(data)
            job = Job(job_id, folder, opts, client)
            self._jobs[job_id] = job
            self._queue.append(job)
            self._wake.notify()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def busy(self) -> int:
        """Jobs queued or running. The deploy updater waits for 0 before restarting."""
        with self._lock:
            return sum(1 for j in self._jobs.values() if not j.done)

    def position(self, job: Job) -> int | None:
        """How many jobs are ahead of a queued job (0: next up)."""
        with self._lock:
            return self._queue.index(job) if job in self._queue else None

    def result_folder(self, job_id: str) -> Path | None:
        """The folder of a finished, successful job, also after a server restart."""
        if not JOB_ID.match(job_id):
            return None
        folder = self.results_dir / job_id
        try:
            ok = json.loads((folder / OUTCOME).read_text()).get("ok")
        except (OSError, ValueError):
            return None
        return folder if ok else None

    def _work(self) -> None:
        while True:
            with self._lock:
                while not self._queue:
                    self._wake.wait()
                job = self._queue.popleft()
                job.status = "running"
            try:
                outcome = self._run(job)
            except Exception:
                log.exception("Job %s could not run", job.id)
                outcome = {"ok": False, "code": 500, "detail": "Conversion failed unexpectedly."}
            for name in (job.opts["input"], job.opts.get("second_input")):
                if name:
                    (job.folder / name).unlink(missing_ok=True)  # keep no uploaded scans
            with self._lock:
                if outcome["ok"]:
                    job.status, job.result = "done", outcome["result"]
                else:
                    job.status, job.code, job.detail = "failed", outcome["code"], outcome["detail"]
                job.finished.set()

    def _run(self, job: Job) -> dict:
        s = self.settings
        if s.job_isolation == "thread":
            run_job(str(job.folder), job.opts)
        else:
            ctx = multiprocessing.get_context("spawn")
            proc = ctx.Process(target=run_job, args=(str(job.folder), job.opts, s.job_memory_mb))
            proc.start()
            proc.join(s.job_timeout_s)
            if proc.is_alive():
                proc.kill()
                proc.join()
                minutes = max(1, round(s.job_timeout_s / 60))
                return {
                    "ok": False,
                    "code": 504,
                    "detail": f"Conversion took longer than {minutes} minutes and was stopped. "
                    "Try a smaller or decimated scan.",
                }
        try:
            return json.loads((job.folder / OUTCOME).read_text())
        except (OSError, ValueError):
            # No outcome: the process died, almost always out of memory.
            return {"ok": False, "code": 413, "detail": _TOO_BIG}

    def sweep(self, now: float | None = None) -> int:
        """Delete finished jobs and result folders older than the result lifetime."""
        cutoff = (now or time.time()) - self.settings.result_ttl_min * 60
        with self._lock:
            live = {j.id for j in self._jobs.values() if not j.done}
        removed = 0
        for folder in self.results_dir.iterdir():
            if folder.name in live or not folder.is_dir():
                continue
            try:
                if folder.stat().st_mtime < cutoff:
                    shutil.rmtree(folder, ignore_errors=True)
                    removed += 1
            except FileNotFoundError:
                continue
        with self._lock:
            for job_id in [i for i, j in self._jobs.items() if j.done and not j.folder.exists()]:
                del self._jobs[job_id]
        return removed

    def _sweep_forever(self) -> None:
        while True:
            time.sleep(60)
            try:
                self.sweep()
            except Exception:
                log.exception("Result sweep failed")
