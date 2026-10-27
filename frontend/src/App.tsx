import { useEffect, useMemo, useState } from "react";
import * as THREE from "three";
import {
  type Feature,
  type Origin,
  type PlacementOptions,
  type RestingFace,
  type UpAxis,
  convertMesh,
  fetchBuffer,
  getTolerances,
  uploadMesh,
  type Conversion,
  type MeshSummary,
  type ToleranceLevel,
} from "./api";
import {
  ACCEPTED,
  needsUnits,
  parseBuffer,
  parseMeshFile,
  previewMatrix,
  rowsToMatrix,
  toThree,
  UNITS,
  type Units,
} from "./loadMesh";
import UnitsPrompt from "./UnitsPrompt";
import Viewer, { type ViewMode } from "./Viewer";

const UNITS_KEY = "meshtomod.units";
const UP_KEY = "meshtomod.up";

const ORIGINS: { key: Origin; label: string; hint: string }[] = [
  { key: "bottom", label: "Bottom", hint: "Centre of the base on the origin" },
  { key: "center", label: "Centre", hint: "Centre of the bounding box on the origin" },
  { key: "corner", label: "Corner", hint: "Low corner on the origin, part in +X +Y +Z" },
  { key: "keep", label: "Keep", hint: "Where the scanner put it" },
];
const UP_AXES: { key: UpAxis; label: string; hint: string }[] = [
  { key: "z", label: "Z up", hint: "Base on XY. Onshape, FreeCAD, Shapr3D, Fusion set to Z up" },
  { key: "y", label: "Y up", hint: "Base on XZ. SolidWorks, Fusion set to Y up" },
];

function storedUp(): UpAxis {
  try {
    return localStorage.getItem(UP_KEY) === "y" ? "y" : "z";
  } catch {
    return "z";
  }
}
const DONATE_URL = import.meta.env.VITE_DONATE_URL as string | undefined;

function storedUnits(): Units | null {
  try {
    const u = localStorage.getItem(UNITS_KEY);
    return u && u in UNITS ? (u as Units) : null;
  } catch {
    return null;
  }
}

export default function App() {
  const [file, setFile] = useState<File | null>(null);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [units, setUnits] = useState<Units>("mm");
  const [raw, setRaw] = useState<THREE.BufferGeometry | null>(null);
  const [restingFaces, setRestingFaces] = useState<RestingFace[]>([]);
  const [face, setFace] = useState<RestingFace | null>(null);
  const [origin, setOrigin] = useState<Origin>("bottom");
  const [up, setUp] = useState<UpAxis>(storedUp);
  const [trimFlushMm, setTrimFlushMm] = useState(0);
  const [picking, setPicking] = useState(false);
  const [model, setModel] = useState<THREE.BufferGeometry | null>(null);
  const [deviation, setDeviation] = useState<THREE.BufferGeometry | null>(null);
  const [mode, setMode] = useState<ViewMode>("scan");
  const [summary, setSummary] = useState<MeshSummary | null>(null);
  const [result, setResult] = useState<Conversion | null>(null);
  const [levels, setLevels] = useState<ToleranceLevel[]>([]);
  const [level, setLevel] = useState("functional");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("Drop a mesh (STL, OBJ, PLY, 3MF) to start.");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getTolerances()
      .then((t) => {
        setLevels(t.levels);
        setLevel(t.default);
      })
      .catch(() => setError("Backend not reachable. Start it with docker compose up."));
  }, []);

  useEffect(() => {
    if (!picking) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setPicking(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [picking]);

  function choose(f: File) {
    const remembered = storedUnits();
    if (needsUnits(f) && !remembered) setPendingFile(f);
    else open(f, needsUnits(f) ? remembered! : "mm");
  }

  async function open(f: File, u: Units) {
    setFile(f);
    setUnits(u);
    clearResult();
    setSummary(null);
    setRaw(null);
    setRestingFaces([]);
    setFace(null);
    setTrimFlushMm(0);
    setPicking(false);
    setError(null);
    setMode("scan");
    setStatus(`Loading ${f.name}…`);
    try {
      const geometry = await parseMeshFile(f);
      geometry.scale(UNITS[u], UNITS[u], UNITS[u]);
      setRaw(geometry);
      const res = await uploadMesh(f, u);
      setSummary(res.summary);
      setRestingFaces(res.lay_flat);
      setStatus(f.name);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStatus(f.name);
    }
  }

  function clearResult() {
    setResult(null);
    setModel(null);
    setDeviation(null);
    setMode("scan");
  }

  /** Placement changes make a result stale: it was exported in the old place. */
  function changePlacement(change: () => void) {
    change();
    if (result) clearResult();
  }

  const place: PlacementOptions = { face, origin, up, trimFlushMm };

  // Scan frame to view frame: the backend's after converting, a preview before.
  const toView = useMemo(() => {
    if (result) return rowsToMatrix(result.report.placement.matrix);
    if (!raw) return null;
    return previewMatrix(raw, face?.rotation ?? null, origin, up);
  }, [raw, result, face, origin, up]);
  const scan = useMemo(
    () => (raw && toView ? toThree(raw.clone().applyMatrix4(toView)) : null),
    [raw, toView],
  );
  const faceMeshes = useMemo(() => {
    if (!picking || !toView) return [];
    return restingFaces.map((rf) => {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(rf.triangles, 3));
      return toThree(g.applyMatrix4(toView));
    });
  }, [picking, restingFaces, toView]);

  async function runConversion() {
    if (!file || !raw) return;
    setBusy(true);
    setError(null);
    setStatus(`Converting ${file.name}…`);
    try {
      setPicking(false);
      const res = await convertMesh(file, level, units, place);
      const [stl, ply] = await Promise.all([
        fetchBuffer(res.downloads.model_stl),
        fetchBuffer(res.downloads.deviation_ply),
      ]);
      setModel(toThree(parseBuffer(stl, "stl")));
      setDeviation(toThree(parseBuffer(ply, "ply")));
      setResult(res);
      setMode("overlay");
      setStatus(file.name);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStatus(file.name);
    } finally {
      setBusy(false);
    }
  }

  const current = levels.find((l) => l.key === level);
  const report = result?.report;
  const suggested = report && levels.find((l) => l.key === report.scan_quality.suggested_level);

  return (
    <div
      className="app"
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        const f = e.dataTransfer.files[0];
        if (f) choose(f);
      }}
    >
      <aside className="panel">
        <h1>MeshToMod</h1>
        <p className="tagline">Scan in, editable STEP out.</p>

        <label className="button wide">
          Open mesh
          <input
            type="file"
            accept={ACCEPTED}
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (f) choose(f);
            }}
          />
        </label>
        <p className="status">{status}</p>
        {error && <p className="error">{error}</p>}

        <h2>Accuracy</h2>
        <div className="levels">
          {levels.map((l) => (
            <button
              key={l.key}
              className={l.key === level ? "active" : ""}
              onClick={() => setLevel(l.key)}
            >
              {l.label}
            </button>
          ))}
        </div>
        {current && (
          <dl>
            <dt>Fit tolerance</dt>
            <dd>{current.fit_tolerance_mm} mm</dd>
            <dt>Smallest kept feature</dt>
            <dd>{current.min_feature_mm} mm</dd>
            <dt>Fillets</dt>
            <dd>
              {current.keep_fillets
                ? current.min_fillet_radius_mm > 0
                  ? `kept above ${current.min_fillet_radius_mm} mm`
                  : "all kept"
                : "made sharp"}
            </dd>
          </dl>
        )}

        <h2>Placement</h2>
        <div className="levels">
          <button
            className={picking ? "active" : ""}
            disabled={!restingFaces.length}
            onClick={() => setPicking((p) => !p)}
            title="Show the flat faces the part can rest on, then click one"
          >
            {picking ? "Click a face…" : "Lay on face"}
          </button>
          <button
            disabled={!face}
            onClick={() => changePlacement(() => setFace(null))}
            title="Back to the scanner's orientation"
          >
            As scanned
          </button>
        </div>
        {face && (
          <p className="note">
            Resting on a {Math.round(face.area_mm2).toLocaleString()} mm² face, squared to X and Y.
          </p>
        )}
        <div className="options">
          <span>Origin</span>
          <div className="levels small">
            {ORIGINS.map((o) => (
              <button
                key={o.key}
                className={o.key === origin ? "active" : ""}
                title={o.hint}
                onClick={() => changePlacement(() => setOrigin(o.key))}
              >
                {o.label}
              </button>
            ))}
          </div>
          <span>Up</span>
          <div className="levels small">
            {UP_AXES.map((a) => (
              <button
                key={a.key}
                className={a.key === up ? "active" : ""}
                title={a.hint}
                onClick={() =>
                  changePlacement(() => {
                    setUp(a.key);
                    try {
                      localStorage.setItem(UP_KEY, a.key);
                    } catch {
                      /* private mode: just don't remember */
                    }
                  })
                }
              >
                {a.label}
              </button>
            ))}
          </div>
          {face && (
            <>
              <span title="Shave bumps and stray ends up to this deep off the base, so the whole face sits flush on the bed">
                Trim flush
              </span>
              <label className="trim">
                <input
                  type="number"
                  min={0}
                  max={50}
                  step={0.05}
                  value={trimFlushMm}
                  onChange={(e) =>
                    changePlacement(() => setTrimFlushMm(Math.max(0, Number(e.target.value) || 0)))
                  }
                />
                mm
              </label>
            </>
          )}
        </div>
        <p className="note">{UP_AXES.find((a) => a.key === up)!.hint}</p>

        <button className="primary wide" disabled={!file || busy} onClick={runConversion}>
          {busy ? "Converting…" : "Convert to STEP"}
        </button>

        {report && (
          <>
            <h2>Confidence</h2>
            <div className="score">
              <span className="score-value">{report.fit.within_tolerance_pct.toFixed(1)}%</span>
              <span className="muted">
                of the scan within {result!.level.fit_tolerance_mm} mm of the model
              </span>
            </div>
            <dl>
              <dt>Average deviation</dt>
              <dd>{report.fit.mean_mm.toFixed(2)} mm</dd>
              <dt>95% of points within</dt>
              <dd>{report.fit.p95_mm.toFixed(2)} mm</dd>
              <dt>Worst point</dt>
              <dd>{report.fit.max_mm.toFixed(2)} mm</dd>
              <dt>Scan noise</dt>
              <dd>±{report.scan_quality.noise_mm.toFixed(2)} mm</dd>
            </dl>
            {report.scan_quality.warning && <p className="warning">{report.scan_quality.warning}</p>}
            {suggested && suggested.key !== level && (
              <button className="link" onClick={() => setLevel(suggested.key)}>
                Use {suggested.label} instead
              </button>
            )}

            <h2>Model</h2>
            <dl>
              <dt>Faces</dt>
              <dd>
                {Object.entries(report.face_types)
                  .map(([k, v]) => `${v} ${k.toLowerCase()}`)
                  .join(", ")}
              </dd>
              <dt>Size</dt>
              <dd>{report.size_mm.map((v) => v.toFixed(1)).join(" × ")} mm</dd>
              {report.placement.trimmed_mm > 0 && (
                <>
                  <dt>Trimmed flush</dt>
                  <dd>{report.placement.trimmed_mm.toFixed(2)} mm off the base</dd>
                </>
              )}
            </dl>
            {report.features.length > 0 && (
              <>
                <h2>Features</h2>
                <ul className="features">
                  {groupFeatures(report.features).map(([text, count]) => (
                    <li key={text}>
                      {count > 1 && <span className="count">{count}×</span>}
                      {text}
                    </li>
                  ))}
                </ul>
              </>
            )}
            {report.notes.map((n) => (
              <p className="note" key={n}>
                {n}
              </p>
            ))}
            <a className="button primary wide" href={result!.downloads.step} download>
              Download STEP
            </a>
          </>
        )}

        {summary && !report && (
          <>
            <h2>Mesh</h2>
            <dl>
              <dt>Triangles</dt>
              <dd>{summary.faces.toLocaleString()}</dd>
              <dt>Size</dt>
              <dd>{summary.extents.map((v) => v.toFixed(1)).join(" × ")} mm</dd>
              <dt>Watertight</dt>
              <dd>{summary.watertight ? "yes" : "no (will be repaired)"}</dd>
            </dl>
          </>
        )}

        <footer>
          Free and open source.{" "}
          {DONATE_URL && (
            <a href={DONATE_URL} target="_blank" rel="noreferrer">
              Support the project
            </a>
          )}
        </footer>
      </aside>

      <main className="viewport">
        <Viewer
          scan={scan}
          model={model}
          deviation={deviation}
          mode={mode}
          faces={faceMeshes}
          onPickFace={(i) => {
            changePlacement(() => setFace(restingFaces[i]));
            setPicking(false);
          }}
        />
        {picking && (
          <div className="hint">Click the face to lay the part on. Esc to cancel.</div>
        )}
        {model && (
          <div className="viewbar">
            {(["scan", "model", "overlay", "deviation"] as ViewMode[]).map((m) => (
              <button key={m} className={m === mode ? "active" : ""} onClick={() => setMode(m)}>
                {m[0].toUpperCase() + m.slice(1)}
              </button>
            ))}
          </div>
        )}
        {mode === "deviation" && result && (
          <div className="legend">
            <span>0</span>
            <div className="legend-bar" />
            <span>{(2 * result.level.fit_tolerance_mm).toFixed(1)}+ mm</span>
            <small>yellow = {result.level.fit_tolerance_mm} mm tolerance</small>
          </div>
        )}
      </main>

      {pendingFile && (
        <UnitsPrompt
          fileName={pendingFile.name}
          onPick={(u, remember) => {
            if (remember) {
              try {
                localStorage.setItem(UNITS_KEY, u);
              } catch {
                /* private mode: just don't remember */
              }
            }
            const f = pendingFile;
            setPendingFile(null);
            open(f, u);
          }}
        />
      )}
    </div>
  );
}

function describe(f: Feature): string {
  const mm = (v: number) => `${+v.toFixed(2)} mm`;
  if (f.type === "fillet") return `Fillet R${mm(f.radius_mm!)}`;
  if (f.type === "body") return `Round body \u00d8${mm(f.diameter_mm!)}, ${mm(f.height_mm!)} tall`;
  if (f.type === "boss") return `Boss \u00d8${mm(f.diameter_mm!)}, ${mm(f.height_mm!)} tall`;
  const depth = f.through ? "through" : `${mm(f.depth_mm!)} deep`;
  const std = f.standard ? ` (${f.standard})` : "";
  return `Hole \u00d8${mm(f.diameter_mm!)}${std}, ${depth}`;
}

/** Identical features listed once with a count, e.g. "4× Hole Ø6.6 mm (M6 clearance), through". */
function groupFeatures(features: Feature[]): [string, number][] {
  const counts = new Map<string, number>();
  for (const f of features) {
    const text = describe(f);
    counts.set(text, (counts.get(text) ?? 0) + 1);
  }
  return [...counts.entries()];
}
