import * as THREE from "three";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";
import { PLYLoader } from "three/examples/jsm/loaders/PLYLoader.js";
import { ThreeMFLoader } from "three/examples/jsm/loaders/3MFLoader.js";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";

export const ACCEPTED = ".stl,.obj,.ply,.3mf";

/** Parse a mesh file in the browser into one geometry for the viewer. */
export async function loadMeshFile(file: File): Promise<THREE.BufferGeometry> {
  const ext = file.name.split(".").pop()?.toLowerCase();
  const buffer = await file.arrayBuffer();
  let geometry: THREE.BufferGeometry;

  switch (ext) {
    case "stl":
      geometry = new STLLoader().parse(buffer);
      break;
    case "ply":
      geometry = new PLYLoader().parse(buffer);
      break;
    case "obj":
      geometry = mergeObject(new OBJLoader().parse(new TextDecoder().decode(buffer)));
      break;
    case "3mf":
      geometry = mergeObject(new ThreeMFLoader().parse(buffer));
      break;
    default:
      throw new Error(`Unsupported file type: .${ext}`);
  }

  // Scans and CAD are Z-up; three.js is Y-up. Centre the part and stand it on the grid.
  geometry.rotateX(-Math.PI / 2);
  geometry.computeVertexNormals();
  geometry.center();
  geometry.computeBoundingBox();
  geometry.translate(0, -geometry.boundingBox!.min.y, 0);
  return geometry;
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
