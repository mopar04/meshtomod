import { useState } from "react";
import type { Conversion, HistoryTool } from "./api";

type Format = "step" | HistoryTool;

const FORMATS: { key: Format; label: string; hint: string }[] = [
  {
    key: "step",
    label: "STEP (any CAD)",
    hint: "The finished shape. Opens in every CAD tool, without a feature history.",
  },
  {
    key: "fusion",
    label: "Editable history: Fusion",
    hint: "A script that rebuilds the part as a timeline, each hole, boss and fillet with its own size. Run it from Scripts and Add-Ins.",
  },
  {
    key: "onshape",
    label: "Editable history: Onshape",
    hint: "A custom feature with every size in its dialog. Paste it into a Feature Studio.",
  },
  {
    key: "freecad",
    label: "Editable history: FreeCAD",
    hint: "A macro that builds a PartDesign Body, one feature per step.",
  },
  {
    key: "all",
    label: "Everything (zip)",
    hint: "The STEP plus the history for all three tools.",
  },
];
const KEY = "meshtomod.export";

function stored(): Format {
  try {
    const f = localStorage.getItem(KEY);
    return FORMATS.some((x) => x.key === f) ? (f as Format) : "step";
  } catch {
    return "step";
  }
}

/** One Export menu: STEP by default, or the editable history for a CAD tool. Each history
 * zip has a HOW_TO_OPEN.txt for its tool. */
export default function Export({ downloads }: { downloads: Conversion["downloads"] }) {
  const history = downloads.history ?? {};
  const choices = FORMATS.filter((f) => f.key === "step" || history[f.key]);
  const [format, setFormat] = useState<Format>(stored);
  const chosen = choices.find((f) => f.key === format) ?? choices[0];
  const href = chosen.key === "step" ? downloads.step : history[chosen.key]!;

  return (
    <>
      <h2>Export</h2>
      <select
        className="select"
        value={chosen.key}
        onChange={(e) => {
          const f = e.target.value as Format;
          setFormat(f);
          try {
            localStorage.setItem(KEY, f);
          } catch {
            // Private mode: it starts on STEP next visit.
          }
        }}
      >
        {choices.map((f) => (
          <option key={f.key} value={f.key}>
            {f.label}
          </option>
        ))}
      </select>
      <p className="note">{chosen.hint}</p>
      <a className="button primary wide" href={href} download>
        Download
      </a>
    </>
  );
}
