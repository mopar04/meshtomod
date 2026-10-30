import type { Units } from "./loadMesh";

export interface ToleranceLevel {
  key: string;
  label: string;
  fit_tolerance_mm: number;
  min_feature_mm: number;
  keep_fillets: boolean;
  min_fillet_radius_mm: number;
  angle_snap_deg: number;
}

export interface MeshSummary {
  vertices: number;
  faces: number;
  watertight: boolean;
  bounds_min: number[];
  bounds_max: number[];
  extents: number[];
  area: number;
  volume: number | null;
}

/** A face the part can rest on (from the upload), in the scan's own frame. */
export interface RestingFace {
  normal: number[];
  area_mm2: number;
  /** 3x3 rows: scan frame to laid flat (this face down, walls along X and Y). */
  rotation: number[][];
  /** Flat xyz list of the hull triangles that make up the face. */
  triangles: number[];
}

export type Origin = "keep" | "center" | "bottom" | "corner";
export type UpAxis = "z" | "y";

export interface PlacementOptions {
  face: RestingFace | null;
  origin: Origin;
  up: UpAxis;
  trimFlushMm: number;
}

export interface ConversionReport {
  faces: number;
  face_types: Record<string, number>;
  volume_mm3: number;
  size_mm: number[];
  scan_quality: {
    noise_mm: number;
    open_edges: number;
    loose_pieces: number;
    suggested_level: string;
    warning: string | null;
  };
  fit: {
    within_tolerance_pct: number;
    mean_mm: number;
    p95_mm: number;
    max_mm: number;
  };
  features: Feature[];
  notes: string[];
  placement: {
    origin: Origin;
    up: UpAxis;
    /** 4x4 rows: scan frame to the view frame (Z up, placed). */
    matrix: number[][];
    trimmed_mm: number;
  };
}

export interface Feature {
  type:
    | "hole"
    | "boss"
    | "fillet"
    | "body"
    | "slot"
    | "chamfer"
    | "turned"
    | "countersink"
    | "dome"
    | "tapered_boss"
    | "revolved_boss"
    | "revolved_cut"
    | "freeform"
    | "skin";
  diameter_mm?: number;
  radius_mm?: number;
  depth_mm?: number;
  height_mm?: number;
  through?: boolean;
  standard?: string;
  /** Slot. */
  width_mm?: number;
  length_mm?: number;
  /** Chamfer: its width. */
  size_mm?: number;
  /** Countersink: head diameter and included angle. */
  head_mm?: number;
  angle_deg?: number;
  /** Tapered boss: base and top diameters. */
  diameters_mm?: [number, number];
  /** Freeform: a whole curved side of the part, a mound on a face or a scoop in one. */
  form?: "surface" | "bump" | "recess";
  area_mm2?: number;
  /** Skin: a one-sided scan thickened into a wall: how thick, which side of the scanned
   * surface the material went, and how its rim is finished. */
  thickness_mm?: number;
  direction?: "in" | "out" | "centred";
  edge?: "square" | "chamfer" | "round";
  /** Turned shapes: how many faces of each kind (cone, sphere, torus, cylinder, plane). */
  surfaces?: Record<string, number>;
}

export interface Conversion {
  id: string;
  level: ToleranceLevel;
  report: ConversionReport;
  downloads: { step: string; model_stl: string; deviation_ply: string };
}

/** A request the server answered with an error: its message, and the HTTP status. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(body.detail ?? `Request failed (${res.status})`, res.status);
  }
  return res.json() as Promise<T>;
}

export async function getTolerances() {
  return json<{ default: string; levels: ToleranceLevel[] }>(await fetch("/api/tolerances"));
}

function form(file: File, fields: Record<string, string>) {
  const body = new FormData();
  body.append("file", file);
  for (const [k, v] of Object.entries(fields)) body.append(k, v);
  return body;
}

export async function uploadMesh(file: File, units: Units) {
  return json<{ filename: string; summary: MeshSummary; lay_flat: RestingFace[] }>(
    await fetch("/api/meshes", { method: "POST", body: form(file, { units }) }),
  );
}

/** Which stage a running conversion has reached. The bar moves by stage, not by time. */
export interface JobProgress {
  stage: string;
  label: string;
  step: number;
  steps: number;
  /** Rough share of the work done before this stage. */
  percent: number;
}

export interface JobStatus {
  id: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  /** Jobs ahead of this one while it waits. */
  position?: number;
  /** While running: seconds so far and the stage reached (null before the first). */
  elapsed_s?: number;
  progress?: JobProgress | null;
  /** While queued or running: the server stops a job nobody checks on for this long. */
  unwatched_stop_s?: number;
  detail?: string;
  result?: Conversion;
}

export const POLL_MS = 1500;

/** Queue a conversion. Big scans take minutes, so the server runs them as jobs: follow it
 * with watchJob, which also keeps telling the server someone is still watching. */
export async function startJob(file: File, level: string, units: Units, place: PlacementOptions) {
  const fields: Record<string, string> = { level, units, origin: place.origin, up: place.up };
  if (place.face) {
    fields.lay_flat = place.face.normal.join(",");
    fields.trim_flush = String(place.trimFlushMm);
  }
  return json<JobStatus>(await fetch("/api/jobs", { method: "POST", body: form(file, fields) }));
}

/** A job's status. Throws (with the server's reason) once it has expired. */
export async function getJob(id: string) {
  return json<JobStatus>(await fetch(`/api/jobs/${id}`));
}

/** Stop a queued or running job on the server. */
export async function cancelJob(id: string) {
  return json<JobStatus>(await fetch(`/api/jobs/${id}`, { method: "DELETE" }));
}

export const finished = (job: JobStatus) =>
  job.status === "done" || job.status === "failed" || job.status === "cancelled";

/** Check on a job every POLL_MS until it ends, telling onUpdate each time, and return its
 * last status. Stops early (returning null) once stillWanted() says nobody cares any more. */
export async function watchJob(
  job: JobStatus,
  onUpdate: (job: JobStatus) => void,
  stillWanted: () => boolean,
): Promise<JobStatus | null> {
  for (;;) {
    if (!stillWanted()) return null;
    onUpdate(job);
    if (finished(job)) return job;
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
    if (!stillWanted()) return null;
    job = await getJobRetrying(job.id);
  }
}

/** getJob, riding out a few failed checks in a row (a dropped connection, the server
 * restarting for an update). A job the server no longer knows (404) fails at once. */
async function getJobRetrying(id: string, tries = 8): Promise<JobStatus> {
  for (let i = 1; ; i++) {
    try {
      return await getJob(id);
    } catch (err) {
      if (i >= tries || (err instanceof ApiError && err.status === 404)) throw err;
      await new Promise((resolve) => setTimeout(resolve, POLL_MS * i));
    }
  }
}

export async function fetchBuffer(url: string): Promise<ArrayBuffer> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Download failed (${res.status})`);
  return res.arrayBuffer();
}
