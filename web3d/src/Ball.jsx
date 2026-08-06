import { squareToWorld } from "./pitchMath";

/** How high a ball still in flight floats above its square. */
const AIR_Y = 1.15;
const R = 0.16;

/**
 * The ball when nobody is holding it.
 *
 * A carried ball rides above its carrier (see Pawn); this is the other three states, and
 * they are genuinely different things the engine tracks separately:
 *
 *   - ON THE GROUND — loose after a bounce, a fumble, a drop. Anyone may try to pick it up.
 *   - IN THE AIR — kicked off and not yet landed. `Ball.in_air` exists precisely because
 *     `in_play` only ever meant "on the board somewhere", and a kick-off question can stop
 *     the drive while the ball is still up there: it "cannot be caught until after the
 *     Kick-off Event has been resolved". Drawn hovering, with a ring on the square it is
 *     coming down on, so the board says where it will land rather than only where it is.
 *   - NOT IN PLAY — nothing to draw.
 */
export function Ball({ ball }) {
  if (!ball?.in_play || ball.carrier) return null;
  const [wx, , wz] = squareToWorld(ball.x, ball.y);
  const y = ball.in_air ? AIR_Y : R;
  return (
    <group position={[wx, 0, wz]}>
      <mesh position={[0, y, 0]} castShadow>
        <sphereGeometry args={[R, 18, 14]} />
        <meshStandardMaterial
          color="#e8c34a"
          emissive="#8a6a10"
          emissiveIntensity={ball.in_air ? 0.75 : 0.45}
          transparent={ball.in_air}
          opacity={ball.in_air ? 0.75 : 1}
        />
      </mesh>
      {/* The landing square. Filled ring rather than an outline: a thin line on turf at a
          shallow camera angle is the invisible-legal-squares bug again. */}
      {ball.in_air && (
        <mesh position={[0, 0.03, 0]} rotation={[-Math.PI / 2, 0, 0]}>
          <ringGeometry args={[0.22, 0.36, 28]} />
          <meshBasicMaterial color="#e8c34a" transparent opacity={0.85} />
        </mesh>
      )}
    </group>
  );
}
