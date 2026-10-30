import os
import time

import pytest
from fastapi.testclient import TestClient

from meshtomod import api, fixtures
from meshtomod.config import Settings
from meshtomod.jobs import Busy, Job, JobManager, RateLimited

client = TestClient(api.app)


def _wait(job_id: str) -> dict:
    for _ in range(600):
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def _stl(mesh) -> bytes:
    return mesh.export(file_type="stl")


def test_job_runs_and_its_files_download():
    res = client.post("/api/jobs", files={"file": ("bracket.stl", _stl(fixtures.l_bracket()))})
    assert res.status_code == 202
    assert res.json()["status"] in ("queued", "running")
    body = _wait(res.json()["id"])
    assert body["status"] == "done"
    result = body["result"]
    assert result["id"] == body["id"]
    assert result["report"]["face_types"] == {"PLANE": 8}
    assert client.get(result["downloads"]["step"]).content.startswith(b"ISO-10303-21")
    # The uploaded scan is not kept, only the results.
    folder = api.jobs.results_dir / body["id"]
    assert sorted(p.name for p in folder.iterdir()) == [
        "base.step",
        "deviation.ply",
        "history-all.zip",
        "history-freecad.zip",
        "history-fusion.zip",
        "history-onshape.zip",
        "history.json",
        "model.step",
        "model.stl",
        "outcome.json",
    ]


def test_failed_job_reports_why():
    res = client.post("/api/jobs", files={"file": ("ball.stl", _stl(fixtures.ball()))})
    body = _wait(res.json()["id"])
    assert body["status"] == "failed"
    assert body["code"] == 422
    assert "curved" in body["detail"]
    assert client.get(f"/api/results/{body['id']}/model.stl").status_code == 404


def test_unknown_job_and_result_ids_are_404():
    assert client.get("/api/jobs/" + "0" * 32).status_code == 404
    assert client.get("/api/results/../../etc/passwd").status_code == 404
    assert client.get("/api/results/" + "0" * 32 + "/model.stl").status_code == 404


def test_bad_options_are_refused_before_queueing():
    files = {"file": ("box.stl", _stl(fixtures.box()))}
    assert client.post("/api/jobs", files=files, data={"level": "nope"}).status_code == 422
    assert client.post("/api/jobs", files=files, data={"units": "ft"}).status_code == 422
    dwg = {"file": ("box.dwg", b"xx")}
    assert client.post("/api/jobs", files=dwg).status_code == 422


def test_too_many_triangles_is_a_clear_413(monkeypatch):
    monkeypatch.setattr(api.settings, "max_faces", 10)
    files = {"file": ("box.stl", _stl(fixtures.box()))}
    res = client.post("/api/meshes", files=files)
    assert res.status_code == 413
    assert "triangles" in res.json()["detail"]
    res = client.post("/api/convert", files=files)
    assert res.status_code == 413
    assert "Decimate" in res.json()["detail"]


def test_dense_scan_is_simplified_and_says_so(monkeypatch):
    from meshtomod.tolerance import LEVELS

    scan = fixtures.subdivided(fixtures.l_bracket(), 1.0)
    monkeypatch.setitem(LEVELS, "rough", LEVELS["rough"].with_overrides(simplify_above_faces=2_000))
    files = {"file": ("bracket.stl", _stl(scan))}
    res = client.post("/api/convert", files=files, data={"level": "rough"})
    assert res.status_code == 200
    report = res.json()["report"]
    assert report["face_types"] == {"PLANE": 8}
    done = report["simplified"]
    assert done["from_faces"] == len(scan.faces)
    assert done["to_faces"] <= 2_100 and not done["softened"]
    assert any(n.startswith(f"Simplified from {len(scan.faces):,} to ") for n in report["notes"])


def test_upload_size_limit(monkeypatch):
    monkeypatch.setattr(api.settings, "max_upload_mb", 0)
    res = client.post("/api/meshes", files={"file": ("box.stl", _stl(fixtures.box()))})
    assert res.status_code == 413


def _manager(tmp_path, **kw) -> JobManager:
    return JobManager(Settings(data_dir=tmp_path, job_isolation="thread", **kw))


def test_full_queue_is_busy(tmp_path):
    jobs = _manager(tmp_path, max_queued_jobs=0)
    with pytest.raises(Busy):
        jobs.submit("1.2.3.4", {"input": "x.stl"}, [])


def test_one_job_at_a_time_per_client(tmp_path):
    jobs = _manager(tmp_path)
    jobs._jobs["a" * 32] = Job("a" * 32, tmp_path, {}, "1.2.3.4", status="running")
    jobs._started = True  # no workers: nothing should run
    with pytest.raises(Busy):
        jobs.submit("1.2.3.4", {"input": "x.stl"}, [])
    jobs.submit("5.6.7.8", {"input": "x.stl"}, [])  # someone else can


def test_hourly_limit_per_client(tmp_path):
    jobs = _manager(tmp_path, jobs_per_client=10, jobs_per_client_hour=2)
    jobs._started = True
    jobs.submit("1.2.3.4", {"input": "x.stl"}, [])
    jobs.submit("1.2.3.4", {"input": "x.stl"}, [])
    with pytest.raises(RateLimited):
        jobs.submit("1.2.3.4", {"input": "x.stl"}, [])


def test_rate_limit_is_a_429(monkeypatch):
    monkeypatch.setattr(api.settings, "jobs_per_client_hour", 0)
    res = client.post("/api/jobs", files={"file": ("box.stl", _stl(fixtures.box()))})
    assert res.status_code == 429
    assert "Retry-After" in res.headers


def test_old_results_are_swept(tmp_path):
    jobs = _manager(tmp_path, result_ttl_min=60)
    old, new = jobs.results_dir / ("a" * 32), jobs.results_dir / ("b" * 32)
    for folder in (old, new):
        folder.mkdir()
        (folder / "outcome.json").write_text('{"ok": true}')
    two_hours_ago = time.time() - 7200
    os.utime(old, (two_hours_ago, two_hours_ago))
    assert jobs.sweep() == 1
    assert not old.exists() and new.exists()
    # A restarted server still serves results that haven't expired.
    assert _manager(tmp_path).result_folder("b" * 32) == new


def test_client_address_from_proxy_header(monkeypatch):
    seen = []
    monkeypatch.setattr(api.jobs, "submit", lambda c, o, i: seen.append(c) or _fake_job())
    files = {"file": ("box.stl", _stl(fixtures.box()))}
    client.post("/api/jobs", files=files, headers={"X-Real-IP": "9.9.9.9"})
    monkeypatch.setattr(api.settings, "client_ip_header", "X-Real-IP")
    client.post("/api/jobs", files=files, headers={"X-Real-IP": "9.9.9.9"})
    assert seen == ["testclient", "9.9.9.9"]


def _fake_job() -> Job:
    return Job("c" * 32, api.jobs.results_dir, {}, "x")


def test_process_isolation_converts(tmp_path):
    jobs = JobManager(Settings(data_dir=tmp_path, job_isolation="process"))
    opts = {
        "level": "functional",
        "units": "mm",
        "max_faces": 10**6,
        "input": "input-1.stl",
        "filename": "box.stl",
        "lay_flat": None,
        "origin": "keep",
        "up": "z",
        "trim_flush": 0.0,
    }
    job = jobs.submit("1.2.3.4", opts, [("input-1.stl", _stl(fixtures.box()))])
    assert job.finished.wait(300)
    assert job.status == "done", job.detail
    assert job.result["report"]["face_types"] == {"PLANE": 6}


def test_process_timeout_kills_the_job(tmp_path):
    jobs = JobManager(Settings(data_dir=tmp_path, job_isolation="process", job_timeout_s=0))
    opts = {"input": "input-1.stl"}
    job = jobs.submit("1.2.3.4", opts, [("input-1.stl", b"solid x\nendsolid x\n")])
    assert job.finished.wait(60)
    assert job.status == "failed" and job.code == 504
