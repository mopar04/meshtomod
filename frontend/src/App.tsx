import { useEffect, useState } from "react";
import type * as THREE from "three";
import { getTolerances, uploadMesh, type MeshSummary, type ToleranceLevel } from "./api";
import { ACCEPTED, loadMeshFile } from "./loadMesh";
import Viewer from "./Viewer";

export default function App() {
  const [geometry, setGeometry] = useState<THREE.BufferGeometry | null>(null);
  const [summary, setSummary] = useState<MeshSummary | null>(null);
  const [levels, setLevels] = useState<ToleranceLevel[]>([]);
  const [level, setLevel] = useState("functional");
  const [status, setStatus] = useState("Drop a mesh (STL, OBJ, PLY, 3MF) to start.");

  useEffect(() => {
    getTolerances()
      .then((t) => {
        setLevels(t.levels);
        setLevel(t.default);
      })
      .catch(() => setStatus("Backend not reachable. Start it with docker compose up."));
  }, []);

  async function open(file: File) {
    setStatus(`Loading ${file.name}…`);
    setSummary(null);
    try {
      setGeometry(await loadMeshFile(file));
      setStatus(`Analysing ${file.name}…`);
      const res = await uploadMesh(file);
      setSummary(res.summary);
      setStatus(file.name);
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err));
    }
  }

  const current = levels.find((l) => l.key === level);

  return (
    <div
      className="app"
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        const file = e.dataTransfer.files[0];
        if (file) open(file);
      }}
    >
      <aside className="panel">
        <h1>NUrbSmith</h1>
        <label className="button">
          Open mesh
          <input
            type="file"
            accept={ACCEPTED}
            hidden
            onChange={(e) => e.target.files?.[0] && open(e.target.files[0])}
          />
        </label>
        <p className="status">{status}</p>

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

        {summary && (
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
      </aside>
      <main className="viewport">
        <Viewer geometry={geometry} />
      </main>
    </div>
  );
}
