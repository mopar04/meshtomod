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

export interface ConversionReport {
  faces: number;
  face_types: Record<string, number>;
  volume_mm3: number;
  size_mm: number[];
  scan_quality: {
    noise_mm: number;
    open_edges: number;
    suggested_level: string;
    warning: string | null;
  };
  fit: {
    within_tolerance_pct: number;
    mean_mm: number;
    p95_mm: number;
    max_mm: number;
  };
}

export interface Conversion {
  id: string;
  level: ToleranceLevel;
  report: ConversionReport;
  downloads: { step: string; model_stl: string; deviation_ply: string };
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `Request failed (${res.status})`);
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
  return json<{ filename: string; summary: MeshSummary }>(
    await fetch("/api/meshes", { method: "POST", body: form(file, { units }) }),
  );
}

export async function convertMesh(file: File, level: string, units: Units) {
  return json<Conversion>(
    await fetch("/api/convert", { method: "POST", body: form(file, { level, units }) }),
  );
}

export async function fetchBuffer(url: string): Promise<ArrayBuffer> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Download failed (${res.status})`);
  return res.arrayBuffer();
}
