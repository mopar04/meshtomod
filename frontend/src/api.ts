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

export async function uploadMesh(file: File) {
  const form = new FormData();
  form.append("file", file);
  return json<{ filename: string; summary: MeshSummary }>(
    await fetch("/api/meshes", { method: "POST", body: form }),
  );
}
