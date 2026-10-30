"""The editable history as files to download: one zip for each CAD tool.

Each zip holds the script for that tool with this part's steps put in, base.step when the
script starts from it, and HOW_TO_OPEN.txt. The templates are in meshtomod/cad.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from importlib import resources
from pathlib import Path

TOOLS = ("fusion", "onshape", "freecad")

HOW_TO = {
    "fusion": """Open in Autodesk Fusion
1. Unzip this file. Keep the {name} folder as it is: the script and base.step go together.
2. In Fusion: Utilities > Add-Ins > Scripts and Add-Ins.
3. Press the + beside My Scripts and pick the {name} folder.
4. Select {name} in the list and press Run.
A new design opens with one timeline feature per step. Sizes are in
Modify > Change Parameters: change hole_1_diameter, say, and the hole and everything after
it update. Steps Fusion can't make are listed when it finishes and left out.
""",
    "onshape": """Open in Onshape
1. Unzip this file.
2. In a Part Studio, import base.step (if there is one): Import, or drag it in.
3. Create a Feature Studio (the + at the bottom left > Create Feature Studio). Keep the two
   lines Onshape starts it with, delete anything else, and paste all of {name}.fs below them.
4. Back in the Part Studio, open the custom features menu, add "MeshToMod {name}" and pick
   the imported part.
Every size is a field in that feature's dialog: change Hole 1 diameter, say, and the hole
and everything after it update. Steps Onshape can't make are named in a warning and left
out.
""",
    "freecad": """Open in FreeCAD (1.0 or newer)
1. Unzip this file. Keep {name}.FCMacro and base.step in the same folder.
2. In FreeCAD: File > Open, pick {name}.FCMacro, then press Execute macro (the green
   triangle in the Macro toolbar) while it is open.
It builds a PartDesign Body with one feature per step and saves {name}.FCStd beside the
macro. Change a hole by double-clicking its sketch (the diameter) or its Pocket (the depth).
Steps FreeCAD can't make are listed in the Report view and left out.
""",
}

INTRO = """MeshToMod editable history for {name}

STEP files hold the finished shape only. This adds the steps that make it: the base solid,
then each boss, fillet, chamfer, hole and edit as its own feature with its own size, so you
can change one hole without remodelling the part. Sizes are in millimetres.

"""


def safe_name(stem: str) -> str:
    """A name every tool takes for a file, a script and a feature: letters, digits and _."""
    name = re.sub(r"[^A-Za-z0-9_]+", "_", stem).strip("_") or "part"
    return name if name[0].isalpha() else "part_" + name


def _template(filename: str) -> str:
    return resources.files("meshtomod.cad").joinpath(filename).read_text()


def parameters(history: dict) -> list[tuple[str, str, float]]:
    """The sizes a user can change: (key, label, mm), the same names in every tool."""
    out = []
    for op in history["ops"]:
        if op["op"] == "extrude":
            if "circle" in op["sketch"]:
                d = op["sketch"]["circle"]["diameter"]
                out.append((f"{op['id']}_diameter", f"{op['name']} diameter", d))
            if not op.get("through") and (op.get("from_face") or op["mode"] == "new"):
                word = "depth" if op["mode"] == "cut" else "height"
                out.append((f"{op['id']}_{word}", f"{op['name']} {word}", op["distance"]))
        elif op["op"] == "fillet" and "edges" in op:
            out.append((f"{op['id']}_radius", f"{op['name']} radius", op["radius"]))
        elif op["op"] == "chamfer":
            out.append((f"{op['id']}_size", f"{op['name']} size", op["size"]))
    return out


def _fill(template: str, history: dict, name: str) -> str:
    """The template with the history as a Python string literal (JSON's string syntax is
    Python's too), and the part's name."""
    data = json.dumps(json.dumps(history, separators=(",", ":")))
    return template.replace('"__HISTORY__"', data).replace("__NAME__", name)


def _number(x: float) -> str:
    """A plain decimal, which FeatureScript reads (no exponent)."""
    text = f"{x:.9f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def featurescript_literal(value) -> str:
    """JSON-like data as a FeatureScript literal: maps, arrays, strings, numbers, booleans."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return _number(float(value))
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(featurescript_literal(v) for v in value) + "]"
    if isinstance(value, dict):
        items = ", ".join(f"{json.dumps(k)} : {featurescript_literal(v)}" for k, v in value.items())
        return "{ " + items + " }"
    raise TypeError(f"Can't write {type(value).__name__} in FeatureScript")


def fusion_files(history: dict, name: str) -> dict[str, str]:
    manifest = {
        "autodeskProduct": "Fusion",
        "type": "script",
        "author": "MeshToMod",
        "description": {"": f"Rebuilds {name} as an editable timeline (MeshToMod)."},
        "supportedOS": "windows|mac",
        "editEnabled": True,
    }
    return {
        f"{name}/{name}.py": _fill(_template("fusion_script.py"), history, name),
        f"{name}/{name}.manifest": json.dumps(manifest, indent=2),
    }


def onshape_files(history: dict, name: str) -> dict[str, str]:
    lines = []
    if history["ops"][0]["op"] == "base":
        lines += [
            '        annotation { "Name" : "Imported part", '
            '"Filter" : EntityType.BODY && BodyType.SOLID, "MaxNumberOfPicks" : 1 }',
            "        definition.part is Query;",
        ]
    for key, label, mm in parameters(history):
        top = max(10000.0, 10 * mm)
        lines += [
            f"        annotation {{ {json.dumps('Name')} : {json.dumps(label)} }}",
            f"        isLength(definition.{key}, {{ (millimeter) : "
            f"[0.001, {_number(mm)}, {_number(top)}] }} as LengthBoundSpec);",
        ]
    text = (
        _template("onshape_feature.fs")
        .replace("__HISTORY__", featurescript_literal(history))
        .replace("__PARAMETERS__", "\n".join(lines))
        .replace("__NAME__", name)
    )
    return {f"{name}.fs": text}


def freecad_files(history: dict, name: str) -> dict[str, str]:
    return {f"{name}.FCMacro": _fill(_template("freecad_macro.py"), history, name)}


def write_history_zips(folder: Path, history: dict, stem: str, base_step: Path | None) -> dict:
    """history-<tool>.zip for each tool, and history-all.zip with every tool and the model's
    STEP. Returns {tool: zip filename}."""
    name = safe_name(stem)
    makers = {"fusion": fusion_files, "onshape": onshape_files, "freecad": freecad_files}
    every: dict[str, bytes] = {}
    out = {}
    for tool in TOOLS:
        files = {k: v.encode() for k, v in makers[tool](history, name).items()}
        if base_step is not None:
            # Fusion runs a script from its own folder, so base.step goes in it.
            where = f"{name}/base.step" if tool == "fusion" else "base.step"
            files[where] = base_step.read_bytes()
        files["HOW_TO_OPEN.txt"] = (INTRO + HOW_TO[tool]).format(name=name).encode()
        zip_name = f"history-{tool}.zip"
        _write_zip(folder / zip_name, files)
        out[tool] = zip_name
        for path, data in files.items():
            if path != "HOW_TO_OPEN.txt":
                every[f"{tool}/{path}"] = data
    model = folder / "model.step"
    if model.exists():
        every[f"{name}.step"] = model.read_bytes()
    every["HOW_TO_OPEN.txt"] = (
        INTRO.format(name=name)
        + f"{name}.step opens in any CAD tool. The fusion, onshape and freecad folders hold\n"
        + "the editable history for each:\n\n"
        + "\n".join(HOW_TO[t].format(name=name) for t in TOOLS)
    ).encode()
    _write_zip(folder / "history-all.zip", every)
    out["all"] = "history-all.zip"
    (folder / "history.json").write_text(json.dumps(history, indent=1))
    return out


def _write_zip(path: Path, files: dict[str, bytes]) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    path.write_bytes(buffer.getvalue())
