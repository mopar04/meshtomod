import { useEffect, useState } from "react";
import type * as THREE from "three";
import {
  convertMesh,
  fetchBuffer,
  getTolerances,
  uploadMesh,
  type Conversion,
  type MeshSummary,
  type ToleranceLevel,
} from "./api";
import { ACCEPTED, needsUnits, parseBuffer, parseMeshFile, Placement, UNITS, type Units } from "./loadMesh";
import UnitsPrompt from "./UnitsPrompt";
import Viewer, { type ViewMode } from "./Viewer";

const UNITS_KEY = "nurbsmith.units";
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
  const [placement, setPlacement] = useState<Placement | null>(null);
  const [scan, setScan] = useState<THREE.BufferGeometry | null>(null);
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

  function choose(f: File) {
    const remembered = storedUnits();
    if (needsUnits(f) && !remembered) setPendingFile(f);
    else open(f, needsUnits(f) ? remembered! : "mm");
  }

  async function open(f: File, u: Units) {
    setFile(f);
    setUnits(u);
    setResult(null);
    setModel(null);
    setDeviation(null);
    setSummary(null);
    setError(null);
    setMode("scan");
    setStatus(`Loading ${f.name}…`);
    try {
      const geometry = await parseMeshFile(f);
      geometry.scale(UNITS[u], UNITS[u], UNITS[u]);
      const place = new Placement(geometry);
      setPlacement(place);
      setScan(place.apply(geometry));
      const res = await uploadMesh(f, u);
      setSummary(res.summary);
      setStatus(f.name);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStatus(f.name);
    }
  }

  async function runConversion() {
    if (!file || !placement) return;
    setBusy(true);
    setError(null);
    setStatus(`Converting ${file.name}…`);
    try {
      const res = await convertMesh(file, level, units);
      const [stl, ply] = await Promise.all([
        fetchBuffer(res.downloads.model_stl),
        fetchBuffer(res.downloads.deviation_ply),
      ]);
      setModel(placement.apply(parseBuffer(stl, "stl")));
      setDeviation(placement.apply(parseBuffer(ply, "ply")));
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
        <h1>NURBSmith</h1>
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
            </dl>
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
        <Viewer scan={scan} model={model} deviation={deviation} mode={mode} />
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
