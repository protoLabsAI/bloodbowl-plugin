import { useLayoutEffect, useMemo, useRef } from "react";
import { Color, Matrix4, Vector3 } from "three";
import { archetypeFor, boundsOf, buildVoxels } from "./voxelPlayer";
import { paletteFor } from "./teamPalette";

/** One world unit is a square; a voxel is a small fraction of it. Sized so a lineman
 *  stands a little under a square tall and a Big Guy towers without covering neighbours. */
const VOXEL = 0.085;

/**
 * A player built from unit cubes — ONE InstancedMesh per pawn, coloured per instance.
 *
 * MechArena runs an InstancedMesh per (part, material) pair because its mechs animate
 * part-by-part. A Blood Bowl player is rigid: it walks, it falls over, it does not
 * articulate — so one mesh with per-instance colour is the same picture for a fraction of
 * the draw calls, and twenty-two of them sit comfortably on a board.
 */
export function VoxelPawn({ player, selected }) {
  const ref = useRef();

  const { voxels, palette, centre } = useMemo(() => {
    const archetype = archetypeFor(player.role, player.position);
    const v = buildVoxels(archetype, { st: player.ST });
    const b = boundsOf(v);
    return {
      voxels: v,
      palette: paletteFor(player.side, player.team),
      // Centre on the square in X/Z and stand on the turf in Y — a model that floats or
      // sinks is the first thing anybody notices.
      centre: new Vector3((b.minX + b.maxX) / 2, b.minY, (b.minZ + b.maxZ) / 2),
    };
  }, [player.role, player.position, player.ST, player.side, player.team]);

  useLayoutEffect(() => {
    const mesh = ref.current;
    if (!mesh) return;
    const m = new Matrix4();
    const c = new Color();
    voxels.forEach((v, i) => {
      m.setPosition((v.x - centre.x) * VOXEL, (v.y - centre.y) * VOXEL, (v.z - centre.z) * VOXEL);
      mesh.setMatrixAt(i, m);
      c.set(palette[v.slot] || palette.primary);
      // Selection lifts the whole kit rather than outlining it — an outline at a shallow
      // camera angle is the invisible-legal-square bug wearing a different hat.
      if (selected) c.offsetHSL(0, 0.05, 0.16);
      mesh.setColorAt(i, c);
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    // THE SHADER IS COMPILED BEFORE `instanceColor` EXISTS. `setColorAt` allocates the
    // attribute lazily on its first call, but the material's program was already built
    // without it — so every cube renders the material's flat white and both teams come out
    // identical. Invalidating the material forces a recompile that reads the attribute.
    // Nothing about the scene graph looks wrong when this is missing: the instances are
    // all there, correctly placed, and uniformly the wrong colour.
    if (mesh.material) mesh.material.needsUpdate = true;
    mesh.computeBoundingSphere();
    // Testability hook. There is no DOM per cube and getComputedStyle sees nothing, so
    // the browser harness reads the scene's own state instead — the MechArena split, at
    // the one place a WebGL view can actually be asserted on.
    if (typeof window !== "undefined") {
      window.__bbVoxel = window.__bbVoxel || {};
      window.__bbVoxel[player.id] = {
        count: voxels.length,
        hasInstanceColor: !!mesh.instanceColor,
        materialColor: mesh.material?.color?.getHexString?.() ?? null,
        firstColor: mesh.instanceColor ? Array.from(mesh.instanceColor.array.slice(0, 3)) : null,
      };
    }
  }, [voxels, palette, centre, selected]);

  return (
    <instancedMesh ref={ref} args={[undefined, undefined, voxels.length]} castShadow receiveShadow>
      <boxGeometry args={[VOXEL, VOXEL, VOXEL]} />
      <meshStandardMaterial roughness={0.85} metalness={0.05} />
    </instancedMesh>
  );
}
