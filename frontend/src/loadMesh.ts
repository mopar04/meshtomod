import * as THREE from "three";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";
import { PLYLoader } from "three/examples/jsm/loaders/PLYLoader.js";
import { ThreeMFLoader } from "three/examples/jsm/loaders/3MFLoader.js";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";

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

/**
 * Scans and CAD are Z-up in millimetres; three.js is Y-up. The scan decides the placement
 * (centred, standing on the grid) and every result is moved the same way so they overlay.
 */
export class Placement {
  private offset = new THREE.Vector3();

  constructor(scan: THREE.BufferGeometry) {
    const g = scan.clone().rotateX(-Math.PI / 2);
    g.computeBoundingBox();
    const box = g.boundingBox!;
    this.offset.set(
      -(box.min.x + box.max.x) / 2,
      -box.min.y,
      -(box.min.z + box.max.z) / 2,
    );
  }

  apply(geometry: THREE.BufferGeometry): THREE.BufferGeometry {
    geometry.rotateX(-Math.PI / 2);
    geometry.translate(this.offset.x, this.offset.y, this.offset.z);
    geometry.computeVertexNormals();
    return geometry;
  }
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
