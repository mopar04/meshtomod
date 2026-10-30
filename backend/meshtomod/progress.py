"""Where a conversion has got to, for the progress bar.

The pipeline calls stage("faces") and so on as it goes. Outside a job nothing listens, so the
calls cost nothing; inside one, jobs.run_job listens and writes each stage to the job folder,
where the server reads it when the browser asks for the job's status.

The bar moves by stage, not by time: how long each stage takes depends on the scan, so the
weights below are rough shares of a typical conversion, not a promise.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

# (key, what the user sees, rough share of the work). In pipeline order.
STAGES: list[tuple[str, str, int]] = [
    ("load", "Reading the scan", 2),
    ("merge", "Lining up the two scans", 5),
    ("check", "Checking the scan", 5),
    ("simplify", "Simplifying the mesh", 8),
    ("faces", "Finding flat faces", 15),
    ("features", "Finding holes, bosses and fillets", 15),
    ("curves", "Finding turned and curved shapes", 15),
    ("solid", "Building the solid", 22),
    ("fit", "Measuring the fit", 8),
    ("save", "Saving the STEP and preview", 5),
]
_INDEX = {key: i for i, (key, _, _) in enumerate(STAGES)}
_TOTAL = sum(w for _, _, w in STAGES)

# One listener per thread: a job process has one, and in thread mode (tests) each worker
# thread runs its own job.
_local = threading.local()


def stage(key: str) -> None:
    """Tell whoever is listening that the conversion has reached this stage."""
    listener: Callable[[str], None] | None = getattr(_local, "listener", None)
    if listener is not None:
        try:
            listener(key)
        except Exception:  # progress is a nicety; it must never break a conversion
            pass


def listen(listener: Callable[[str], None] | None) -> None:
    """Set (or with None, clear) the listener for this thread."""
    _local.listener = listener


def describe(key: str) -> dict:
    """The stage as the API reports it: its label, its place, and the share done before it."""
    i = _INDEX[key]
    done = sum(w for _, _, w in STAGES[:i])
    return {
        "stage": key,
        "label": STAGES[i][1],
        "step": i + 1,
        "steps": len(STAGES),
        "percent": round(100 * done / _TOTAL),
    }
