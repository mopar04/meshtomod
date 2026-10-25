import * as THREE from "three";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";
import { PLYLoader } from "three/examples/jsm/loaders/PLYLoader.js";
import { ThreeMFLoader } from "three/examples/jsm/loaders/3MFLoader.js";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import type { Origin, UpAxis } from "./api";

export const ACCEPTED = ".stl,.obj,.ply,.3mf";

export const UNITS = { mm: 1, cm: 10, m: 1000, in: 25.4 } as const;
export type Units = keyof typeof UNITS;

/** 3MF stores its units; the other formats are bare numbers and need asking. */
export function needsUnits(file: File): boolean {
  return !file.name.toLowerCase().endsWith(".3mf");
}

/** Parse a mesh file in the browser into one geometry, in the file's own units and axes. */
export async function parseMeshFile(file: File): Promise<THREE.BufferGeometry> {
  return parseBuffer(await file.arrayBuffer(), file.name.split(".").pop()?.toLowerCase() ?? "");
}

export function parseBuffer(buffer: ArrayBuffer, ext: string): THREE.BufferGeometry {
  switch (ext) {
    case "stl":
      return new STLLoader().parse(buffer);
    case "ply":
      return new PLYLoader().parse(buffer);
    case "obj":
      return mergeObject(new OBJLoader().parse(new TextDecoder().decode(buffer)));
    case "3mf":
      return mergeObject(new ThreeMFLoader().parse(buffer));
    default:
      throw new Error(`Unsupported file type: .${ext}`);
  }
}

/** CAD Z up to three.js Y up, for display only. */
export function toThree(geometry: THREE.BufferGeometry): THREE.BufferGeometry {
  geometry.rotateX(-Math.PI / 2);
  geometry.computeVertexNormals();
  geometry.computeBoundingSphere();
  return geometry;
}

/** Y up in the export: the scan's up (Z) becomes Y and the base lands on XZ. */
const Z_TO_Y = new THREE.Matrix4().set(1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1);

export function rowsToMatrix(rows: number[][]): THREE.Matrix4 {
  // Rows as the backend writes them; Matrix4.set takes row-major values too.
  const r = rows.length === 3 ? [...rows.map((row) => [...row, 0]), [0, 0, 0, 1]] : rows;
  return new THREE.Matrix4().set(...(r.flat() as Parameters<THREE.Matrix4["set"]>));
}

/**
 * Scan frame to view frame (Z up), before converting: the chosen face's rotation, then the
 * chosen point of the box moved onto the origin, worked out in the export's axes. The same
 * steps as the backend's orient.place, on the scan instead of the model, so close but not
 * exact; after converting the backend's own matrix is used.
 */
export function previewMatrix(
  scan: THREE.BufferGeometry,
  rotation: number[][] | null,
  origin: Origin,
  up: UpAxis,
): THREE.Matrix4 {
  const turn = rotation ? rowsToMatrix(rotation) : new THREE.Matrix4();
  const toExport = up === "y" ? Z_TO_Y.clone() : new THREE.Matrix4();
  const g = scan.clone().applyMatrix4(toExport.clone().multiply(turn));
  g.computeBoundingBox();
  const { min, max } = g.boundingBox!;
  g.dispose();
  const point = min.clone().add(max).multiplyScalar(0.5);
  if (origin === "bottom") {
    if (up === "y") point.y = min.y;
    else point.z = min.z;
  } else if (origin === "corner") point.copy(min);
  const shift = new THREE.Matrix4();
  if (origin !== "keep") shift.makeTranslation(-point.x, -point.y, -point.z);
  return toExport.clone().invert().multiply(shift).multiply(toExport).multiply(turn);
}

function mergeObject(root: THREE.Object3D): THREE.BufferGeometry {
  // Keep positions only, so parts with different attribute sets can be merged.
  const parts: THREE.BufferGeometry[] = [];
  root.updateMatrixWorld(true);
  root.traverse((child) => {
    if (child instanceof THREE.Mesh) {
      let g = (child.geometry as THREE.BufferGeometry).clone().applyMatrix4(child.matrixWorld);
      if (g.index) g = g.toNonIndexed();
      const positionsOnly = new THREE.BufferGeometry();
      positionsOnly.setAttribute("position", g.getAttribute("position"));
      parts.push(positionsOnly);
    }
  });
  if (parts.length === 0) throw new Error("File contains no meshes");
  const merged = mergeGeometries(parts);
  if (!merged) throw new Error("Could not merge mesh parts");
  return merged;
}
