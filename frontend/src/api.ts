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
  return json<{ filename: string; summary: MeshSummary; lay_flat: RestingFace[] }>(
    await fetch("/api/meshes", { method: "POST", body: form(file, { units }) }),
  );
}

export async function convertMesh(
  file: File,
  level: string,
  units: Units,
  place: PlacementOptions,
) {
  const fields: Record<string, string> = { level, units, origin: place.origin, up: place.up };
  if (place.face) {
    fields.lay_flat = place.face.normal.join(",");
    fields.trim_flush = String(place.trimFlushMm);
  }
  return json<Conversion>(
    await fetch("/api/convert", { method: "POST", body: form(file, fields) }),
  );
}

export async function fetchBuffer(url: string): Promise<ArrayBuffer> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Download failed (${res.status})`);
  return res.arrayBuffer();
}
