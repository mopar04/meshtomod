import type { JobStatus } from "./api";

/** The conversion in progress: what it's doing, a bar, the time so far, and Cancel. */
export default function JobProgress({
  name,
  job,
  cancelling,
  onCancel,
}: {
  name: string;
  job: JobStatus;
  cancelling: boolean;
  onCancel: () => void;
}) {
  const p = job.status === "running" ? job.progress : null;
  let label = "Starting…";
  if (job.status === "queued" && job.position) {
    label = `Waiting for ${job.position} other scan${job.position > 1 ? "s" : ""} to finish…`;
  } else if (p) {
    label = `${p.label}… (step ${p.step} of ${p.steps})`;
  }
  const minutes = job.unwatched_stop_s ? Math.max(1, Math.round(job.unwatched_stop_s / 60)) : 0;
  return (
    <div className="job" role="status">
      <div className="job-row">
        <span className="job-name">Converting {name}</span>
        {job.elapsed_s !== undefined && <span className="muted">{clock(job.elapsed_s)}</span>}
      </div>
      <div className="job-bar">
        {/* A sliver while queued or starting, so the bar reads as a bar. */}
        <div style={{ width: `${Math.max(p?.percent ?? 0, 2)}%` }} />
      </div>
      <div className="job-row">
        <span className="muted">{label}</span>
        <button className="link" disabled={cancelling} onClick={onCancel}>
          {cancelling ? "Cancelling…" : "Cancel"}
        </button>
      </div>
      {minutes > 0 && (
        <p className="note">
          You can reload or close this page: come back within {minutes} minutes and it picks up
          here. After that the conversion stops so it doesn't hold the server.
        </p>
      )}
    </div>
  );
}

function clock(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = String(seconds % 60).padStart(2, "0");
  return `${m}:${s}`;
}
