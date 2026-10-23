import { Canvas } from "@react-three/fiber";
import { Bounds, GizmoHelper, GizmoViewport, Grid, OrbitControls } from "@react-three/drei";
import type * as THREE from "three";

export default function Viewer({ geometry }: { geometry: THREE.BufferGeometry | null }) {
  return (
    <Canvas camera={{ position: [120, 90, 120], fov: 40, near: 0.1, far: 100000 }}>
      <color attach="background" args={["#1d2127"]} />
      <hemisphereLight args={["#ffffff", "#444444", 1.2]} />
      <directionalLight position={[100, 200, 150]} intensity={1.4} />
      {geometry && (
        <Bounds fit clip observe margin={1.4}>
          <mesh geometry={geometry}>
            <meshStandardMaterial color="#b8c4d0" metalness={0.1} roughness={0.6} flatShading />
          </mesh>
        </Bounds>
      )}
      <Grid infiniteGrid cellSize={10} sectionSize={100} fadeDistance={2000} />
      <OrbitControls makeDefault />
      <GizmoHelper alignment="bottom-right" margin={[64, 64]}>
        <GizmoViewport />
      </GizmoHelper>
    </Canvas>
  );
}
