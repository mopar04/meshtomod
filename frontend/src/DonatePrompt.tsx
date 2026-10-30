import { useEffect, useState } from "react";

/** Zac's Buy Me a Coffee page. VITE_DONATE_URL at build time overrides it. */
export const DONATE_URL =
  (import.meta.env.VITE_DONATE_URL as string | undefined) || "https://buymeacoffee.com/voxelautocraft";
// Long enough that someone has used the app (a conversion takes about a minute) before it asks.
const DELAY_MS = 90_000;
const KEY = "meshtomod.donate";

function stored(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

function store(value: string) {
  try {
    localStorage.setItem(KEY, value);
  } catch {
    // Private mode: it asks again next visit, which is fine.
  }
}

/**
 * A small one-time ask for support, as CNC Kitchen's tools do: once per visit at most,
 * never on top of another dialog, and gone for good with "Don't remind me again".
 */
export default function DonatePrompt() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (stored() === "never") return;
    let timer = 0;
    const tryOpen = () => {
      // Wait while another dialog (the units question) is up.
      if (document.querySelector(".modal-backdrop")) timer = window.setTimeout(tryOpen, 10_000);
      else setOpen(true);
    };
    timer = window.setTimeout(tryOpen, DELAY_MS);
    return () => window.clearTimeout(timer);
  }, []);

  if (!open) return null;
  return (
    <div className="modal-backdrop">
      <div className="modal" role="dialog" aria-labelledby="donate-title">
        <h2 id="donate-title">Enjoying MeshToMod?</h2>
        <p className="muted">
          It's free and open source, with no ads or accounts. If it saved you some modelling time,
          a coffee helps keep it running.
        </p>
        <div className="unit-buttons">
          <a className="button primary" href={DONATE_URL} target="_blank" rel="noreferrer" onClick={() => setOpen(false)}>
            Buy me a coffee
          </a>
          <button onClick={() => setOpen(false)}>Maybe later</button>
        </div>
        <button
          className="link"
          onClick={() => {
            store("never");
            setOpen(false);
          }}
        >
          Don't remind me again
        </button>
      </div>
    </div>
  );
}
