import { useState } from "react";
import { UNITS, type Units } from "./loadMesh";

const LABELS: Record<Units, string> = { mm: "Millimetres", cm: "Centimetres", m: "Metres", in: "Inches" };

export default function UnitsPrompt({
  fileName,
  onPick,
}: {
  fileName: string;
  onPick: (units: Units, remember: boolean) => void;
}) {
  const [remember, setRemember] = useState(false);
  return (
    <div className="modal-backdrop">
      <div className="modal" role="dialog" aria-labelledby="units-title">
        <h2 id="units-title">What units is this file in?</h2>
        <p className="muted">{fileName} doesn't store its units.</p>
        <div className="unit-buttons">
          {(Object.keys(UNITS) as Units[]).map((u) => (
            <button key={u} className={u === "mm" ? "primary" : ""} onClick={() => onPick(u, remember)}>
              {LABELS[u]}
            </button>
          ))}
        </div>
        <label className="check">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          Don't ask again
        </label>
      </div>
    </div>
  );
}
