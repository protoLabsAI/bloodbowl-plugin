import { useFrame } from "@react-three/fiber";
import { useRef } from "react";
import { labelTexture } from "./label";
import { SIDE_COLOR, poseFor, squareToWorld } from "./pitchMath";
import { useModel } from "./useModel";
import { VOXEL, VoxelPawn } from "./VoxelPawn";
import { archetypeFor, gridHeight } from "./voxelPlayer";

/** One player. A primitive for now — and the point of the split is that swapping this
 *  mesh for a loaded .glb changes nothing else: position, pose and picking all come from
 *  the pure layer and the engine's own state. */
export function Pawn({ p, selected, carrying, onPick, getBuffer }) {
  const ref = useRef();
  // An uploaded mesh if the coach has one for this positional, else null — and null is
  // the ordinary case. The primitive below is not a placeholder to be replaced later; it
  // is the permanent fallback, so a missing or unreadable model degrades to a playable
  // board rather than an empty square.
  const model = useModel(getBuffer, p.team, p.position);
  const [wx, , wz] = squareToWorld(p.x, p.y);
  const pose = poseFor(p.down);

  // Tween toward the target square rather than snapping. The engine emits one
  // player_moved per square, so a run arrives as several state updates and this makes it
  // read as a walk instead of a series of teleports. Cosmetic only — it never feeds back
  // into game state, which the engine owns.
  useFrame((_, dt) => {
    const g = ref.current;
    if (!g) return;
    const k = Math.min(1, dt * 8);
    g.position.x += (wx - g.position.x) * k;
    g.position.z += (wz - g.position.z) * k;
    // FEET ON THE TURF. This used to tween toward 0.5 — the capsule's half-height, since
    // a capsule's origin is its centre. Every other body stands on its own feet at y=0,
    // so that offset left them all hovering half a square above the pitch.
    g.position.y += (-pose.sagY - g.position.y) * k;
    g.rotation.z += (pose.pitchX - g.rotation.z) * k;
  });

  const colour = SIDE_COLOR[p.side];
  // Both bodies are built with their depth along Z, so untouched they face the TOUCHLINE.
  // Turn them down the length of the pitch, toward the End Zone they are attacking: home
  // scores at row 26 (world +X), away at row 1 (world -X). Applied on an INNER group so
  // the outer one keeps owning position and the knock-down pitch — a fall then tips along
  // the pitch's length whichever way the player is facing, and the ball and badge below
  // stay put instead of spinning with the body.
  const facing = p.side === "home" ? Math.PI / 2 : -Math.PI / 2;
  const height = gridHeight(archetypeFor(p.role, p.position)) * VOXEL;
  return (
    <group ref={ref} position={[wx, 0, wz]}>
      {model ? (
        // Sized to a square and dropped to the turf. A tabletop mesh arrives at whatever
        // scale it was exported in, so it is normalised rather than trusted.
        <group rotation={[0, facing, 0]} onClick={(e) => { e.stopPropagation(); onPick?.(p); }}>
          <primitive object={model} scale={0.9} position={[0, 0, 0]} />
          {selected && (
            <mesh position={[0, 0.02, 0]} rotation={[-Math.PI / 2, 0, 0]}>
              <ringGeometry args={[0.38, 0.48, 24]} />
              <meshBasicMaterial color={colour} />
            </mesh>
          )}
        </group>
      ) : (
        // No uploaded mesh — the VOXEL build, not a capsule. It is the default rather
        // than a placeholder: it reads the positional's archetype off the roster's own
        // Keywords and its bulk off ST, so a Troll and a Gnoblar differ before any asset
        // exists. An upload replaces it; nothing requires one.
        <group rotation={[0, facing, 0]} onClick={(e) => { e.stopPropagation(); onPick?.(p); }}>
          <VoxelPawn player={p} selected={selected} />
        </group>
      )}
      {/* The ball rides ABOVE its carrier. In 2D it was a badge on the token, which a
          camera can hide behind a pawn the moment the view is not top-down. */}
      {carrying && (
        <mesh position={[0, height + 0.16, 0]}>
          <sphereGeometry args={[0.16, 16, 12]} />
          <meshStandardMaterial color="#e8c34a" emissive="#8a6a10" emissiveIntensity={0.5} />
        </mesh>
      )}
      {/* Badge on a flat plane above the pawn: readable from any orbit angle, and it does
          not rotate with the knockdown pitch because it sits outside that mesh. */}
      <mesh position={[0, height + 0.02, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[0.8, 0.4]} />
        <meshBasicMaterial map={labelTexture(p.badge || p.id)} transparent depthWrite={false} />
      </mesh>
    </group>
  );
}
