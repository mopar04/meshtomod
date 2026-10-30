"""Server settings, read from MESHTOMOD_* environment variables.

Defaults suit a laptop. A public server sets them in its compose file (see docs/DEPLOY.md).
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


def _int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    return int(value) if value else default


def _list(name: str, default: str) -> list[str]:
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


@dataclass
class Settings:
    # Uploads. The proxy in front should also cap the request body (multipart is parsed to
    # disk before the API sees the size).
    max_upload_mb: int = 200
    # Scans over this many triangles are refused: loading and simplifying one takes about
    # 0.8 GB per million (6.2 million peaked at 4.9 GB). Below it, dense scans are simplified
    # for the chosen accuracy level (simplify.py) instead.
    max_faces: int = 5_000_000
    # Jobs: how many conversions run at once, how many may wait, and how long one may take
    # (enforced only with process isolation, where a stuck job can be killed).
    max_running_jobs: int = 1
    max_queued_jobs: int = 4
    job_timeout_s: int = 900
    # "process": each conversion in a fresh process (memory is freed, timeouts are real).
    # "thread": in the server process, for tests and quick local runs.
    job_isolation: str = "process"
    # Address space limit for a job process in MB, 0 for none. The container limit is the
    # main guard; this one fails a single job cleanly instead of killing the server.
    job_memory_mb: int = 0
    # Fair use, per client address: jobs at once and jobs per hour.
    jobs_per_client: int = 1
    jobs_per_client_hour: int = 20
    # A queued or running job nobody has checked on for this long is stopped (the browser
    # checks every couple of seconds while its page is open; a hidden tab may slow to once a
    # minute). 0 never stops one.
    job_abandon_s: int = 180
    # The header holding the real client address when behind a proxy (X-Real-IP from our
    # Caddyfile, CF-Connecting-IP behind Cloudflare). Empty: use the socket address. Only
    # set this when the API can't be reached except through that proxy.
    client_ip_header: str = ""
    # Results (STEP, model and deviation files) are deleted after this long.
    result_ttl_min: int = 60
    data_dir: Path = field(default_factory=lambda: Path(tempfile.gettempdir()) / "meshtomod")
    cors_origins: list[str] = field(default_factory=lambda: ["http://localhost:5173"])

    @classmethod
    def from_env(cls) -> Settings:
        d = cls()
        return cls(
            max_upload_mb=_int("MESHTOMOD_MAX_UPLOAD_MB", d.max_upload_mb),
            max_faces=_int("MESHTOMOD_MAX_FACES", d.max_faces),
            max_running_jobs=max(1, _int("MESHTOMOD_MAX_RUNNING_JOBS", d.max_running_jobs)),
            max_queued_jobs=_int("MESHTOMOD_MAX_QUEUED_JOBS", d.max_queued_jobs),
            job_timeout_s=_int("MESHTOMOD_JOB_TIMEOUT_S", d.job_timeout_s),
            job_isolation=os.environ.get("MESHTOMOD_JOB_ISOLATION", d.job_isolation),
            job_memory_mb=_int("MESHTOMOD_JOB_MEMORY_MB", d.job_memory_mb),
            jobs_per_client=_int("MESHTOMOD_JOBS_PER_CLIENT", d.jobs_per_client),
            jobs_per_client_hour=_int("MESHTOMOD_JOBS_PER_CLIENT_HOUR", d.jobs_per_client_hour),
            job_abandon_s=_int("MESHTOMOD_JOB_ABANDON_S", d.job_abandon_s),
            client_ip_header=os.environ.get("MESHTOMOD_CLIENT_IP_HEADER", "").strip(),
            result_ttl_min=_int("MESHTOMOD_RESULT_TTL_MIN", d.result_ttl_min),
            data_dir=Path(os.environ.get("MESHTOMOD_DATA_DIR", str(d.data_dir))),
            cors_origins=_list("MESHTOMOD_CORS_ORIGINS", ",".join(d.cors_origins)),
        )

    def check(self) -> None:
        if self.job_isolation not in ("process", "thread"):
            raise ValueError("MESHTOMOD_JOB_ISOLATION must be process or thread")

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 2**20
