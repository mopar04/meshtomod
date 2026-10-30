/** The browser's note of its latest conversion, so a reloaded or reopened page can pick the
 * job up again: its progress while it runs, or its result once done. The server keeps
 * results for an hour, so the note is only worth keeping that long after the job started
 * (plus the longest a job may run); the server has the final say either way. */

const KEY = "meshtomod.lastJob";
const KEEP_MS = 2 * 60 * 60 * 1000;

export interface LastJob {
  id: string;
  /** The scan's file name, to say what the job was. */
  name: string;
  started: number;
}

export function rememberJob(id: string, name: string) {
  try {
    localStorage.setItem(KEY, JSON.stringify({ id, name, started: Date.now() }));
  } catch {
    /* private mode: just don't remember */
  }
}

export function lastJob(): LastJob | null {
  try {
    const job = JSON.parse(localStorage.getItem(KEY) ?? "null") as LastJob | null;
    if (job && /^[0-9a-f]{32}$/.test(job.id) && Date.now() - job.started < KEEP_MS) return job;
  } catch {
    /* unreadable: start fresh */
  }
  forgetJob();
  return null;
}

export function forgetJob() {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* nothing to forget */
  }
}
