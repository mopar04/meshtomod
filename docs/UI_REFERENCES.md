# UI references

Zac's direction (2026-10-23): copy the minimal but very functional interactivity of CNC Kitchen's
browser tools. NURBSmith is AGPL-3.0 like ScanRuler, so we can reuse its code. Credit CNC
Kitchen in the header of any file that does, and note it in DECISIONS.md.

Sources: [bumpmesh.com](https://bumpmesh.com), [scanruler.com](https://scanruler.com/),
[CNCKitchen/ScanRuler](https://github.com/CNCKitchen/ScanRuler).

## BumpMesh (bumpmesh.com, CNC Kitchen)
- Drop a file and start straight away: no install, no sign-up.
- One side panel with a preset gallery and sliders that update the preview live.
- Paint areas to exclude them; flat downward-facing faces are detected and left alone
  automatically.
- One clear export button that gives you a file ready to print.

## ScanRuler (scanruler.com, CNC Kitchen, AGPL-3.0)
- Workspaces as tabs across the top. All of them share the loaded scan. Undo/redo, save and load
  project, and settings sit in the same bar.
- Opening an STL asks for its units (mm, cm, m, in), with a "don't ask again" option.
- **Click a point and the region grows from it, then a primitive is fitted** (plane, cylinder,
  cone, sphere, torus). There are also brush, window and lasso tools to mark regions by hand.
- Deviation colour map with a legend and a choice of colour maps.
- View bar in the bottom-left of the viewport: transparent, mesh, backfaces, labels, section,
  split view, colour plot.
- Any created element can be reopened and edited later. Every action can be undone.
- Files stay in the browser. There's an optional local MCP helper so an AI agent can drive it.
- A "Reverse Engineer" workspace (announced 2026-10-21) has an AI agent rebuild a scan as
  parametric CAD. This overlaps with NURBSmith; see the plan's risks.

## What this means for NURBSmith
| Pattern | Where we use it |
| --- | --- |
| Drop file, no sign-up, instant viewer | Already in M0; keep it this way |
| Units prompt for STL | Done in M1: asked on load, with a "don't ask again" option |
| One side panel, presets plus live sliders | Accuracy level buttons plus override sliders |
| Click to grow a region | Correcting regions ("this is a plane", "ignore this") and placing sink/extrude |
| Deviation colour map with legend | Done in M1: the Deviation view |
| View bar in the bottom-left | Done in M1: Scan / Model / Overlay / Deviation |
| Undo/redo, editable operations | Sink/extrude list in M3 |
| Big single export button | Download STEP |
