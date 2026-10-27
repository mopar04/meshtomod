import os
import tempfile

# Before nurbsmith.api is imported: run conversions in the test process, keep results in a
# throwaway folder, and don't rate limit the suite's many conversions.
os.environ.setdefault("MESHTOMOD_JOB_ISOLATION", "thread")
os.environ.setdefault("MESHTOMOD_DATA_DIR", tempfile.mkdtemp(prefix="meshtomod-test-"))
os.environ.setdefault("MESHTOMOD_JOBS_PER_CLIENT_HOUR", "100000")
