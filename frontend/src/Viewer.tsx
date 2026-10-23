import { Canvas } from "@react-three/fiber";
import { Bounds, GizmoHelper, GizmoViewport, Grid, OrbitControls } from "@react-three/drei";
import type * as THREE from "three";

export type ViewMode = "scan" | "model" | "overlay" | "deviation";

interface Props {
  scan: THREE.BufferGeometry | null;
  model: THREE.BufferGeometry | null;
  deviation: THREE.BufferGeometry | null;
  mode: ViewMode;
}

export default function Viewer({ scan, model, deviation, mode }: Props) {
  const showScan = scan && (mode === "scan" || mode === "overlay" || !model);
  const showModel = model && (mode === "model" || mode === "overlay");
  const showDeviation = deviation && mode === "deviation";

  return (
    <Canvas camera={{ position: [120, 90, 120], fov: 40, near: 0.1, far: 100000 }}>
      <color attach="background" args={["#1d2127"]} />
      <hemisphereLight args={["#ffffff", "#444444", 1.2]} />
      <directionalLight position={[100, 200, 150]} intensity={1.4} />
      <Bounds key={scan?.uuid ?? "empty"} fit clip observe margin={1.4}>
        {showScan && (
          <mesh geometry={scan}>
            {/* Keyed by mode: three.js only recompiles for a transparency change on a new material. */}
            <meshStandardMaterial
              key={mode}
              color="#b8c4d0"
              metalness={0.1}
              roughness={0.6}
              flatShading
              transparent={mode === "overlay"}
              opacity={mode === "overlay" ? 0.35 : 1}
              depthWrite={mode !== "overlay"}
            />
          </mesh>
        )}
        {showModel && (
          <mesh geometry={model}>
            {/* Pushed back slightly so the translucent scan reads cleanly on top in overlay. */}
            <meshStandardMaterial
              color="#5b8def"
              metalness={0.15}
              roughness={0.45}
              polygonOffset
              polygonOffsetFactor={2}
              polygonOffsetUnits={2}
            />
          </mesh>
        )}
        {showDeviation && (
          <mesh geometry={deviation}>
            <meshStandardMaterial vertexColors roughness={0.7} />
          </mesh>
        )}
      </Bounds>
      <Grid infiniteGrid cellSize={10} sectionSize={100} fadeDistance={2000} />
      <OrbitControls makeDefault />
      <GizmoHelper alignment="bottom-right" margin={[64, 64]}>
        <GizmoViewport />
      </GizmoHelper>
    </Canvas>
  );
}
