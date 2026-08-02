import { Line } from "@react-three/drei";
import { GEO, squareToWorld, zoneOf } from "./pitchMath";
import { labelTexture } from "./label";

// The three bands must be TELLABLE APART, not merely different. The 2D board once shipped
// an away-half tint that was invisible on dark and nobody noticed for weeks; three greens
// within a few percent of each other is the same mistake with extra steps.
const TURF = { centre: "#2c6b3f", wide: "#1d4a2b", endzone: "#3a2f10" };

/** A flat label lying on the turf, drawn from a canvas texture (see label.js). */
function Decal({ text, position, rotation, w = 6, h = 3 }) {
  return (
    <mesh position={position} rotation={rotation}>
      <planeGeometry args={[w, h]} />
      <meshBasicMaterial map={labelTexture(text, { color: "#d8c48a" })} transparent depthWrite={false} />
    </mesh>
  );
}

/** The pitch: one mesh per square so the bands read and a raycast lands on a square that
 *  already knows its own coordinates — no inverse maths to get wrong. */
export function Pitch({ legal, onPick }) {
  const squares = [];
  for (let y = 1; y <= GEO.length; y++) {
    for (let x = 1; x <= GEO.width; x++) {
      const [wx, , wz] = squareToWorld(x, y);
      const isLegal = legal?.has(`${x},${y}`);
      squares.push(
        <mesh
          key={`${x},${y}`}
          position={[wx, 0, wz]}
          rotation={[-Math.PI / 2, 0, 0]}
          receiveShadow
          onClick={(e) => { e.stopPropagation(); onPick?.(x, y); }}
        >
          <planeGeometry args={[0.96, 0.96]} />
          {/* A legal square is FILLED, not outlined. The 2D view shipped a 2px
              status-green outline on dark green turf and it was invisible; in 3D an
              outline at a shallow camera angle would be worse still. */}
          <meshStandardMaterial color={isLegal ? "#7bd88f" : TURF[zoneOf(x, y)]} />
        </mesh>,
      );
    }
  }

  // World extents follow the swapped axes: length along X, width along Z.
  const half = { x: GEO.length / 2, z: GEO.width / 2 };
  const line = (pts, color = "#dfe7e0", w = 2) => <Line points={pts} color={color} lineWidth={w} />;

  return (
    <group>
      {squares}
      {/* The board is centred, so the boundary between rows 13 and 14 — the Line of
          Scrimmage — is exactly x = 0, and the End Zone boundaries sit one square in from
          each end. No offset arithmetic to get wrong. */}
      {line([[0, 0.02, -half.z], [0, 0.02, half.z]], "#f2d16b", 3)}
      {[-half.x + GEO.endZone, half.x - GEO.endZone].map((x) => (
        <group key={x}>{line([[x, 0.02, -half.z], [x, 0.02, half.z]])}</group>
      ))}
      {[GEO.wideZone, GEO.width - GEO.wideZone].map((bx) => {
        const wz = squareToWorld(bx, 1)[2] + 0.5;
        return <group key={bx}>{line([[-half.x, 0.02, wz], [half.x, 0.02, wz]], "#9fb3a5", 1)}</group>;
      })}
      {/* Label the End Zones by WHO SCORES THERE, not by whose they are. "HOME END ZONE"
          is what the 2D board says and it is strictly true — but you score in the
          OPPOSITION's End Zone, so possessive labelling answers the wrong question. It
          cost a live false bug report: a home carrier standing in row 1 under a "HOME"
          sign looks exactly like an unscored touchdown. The engine was right
          (`touchdown_row("home") === 26`); the sign was the defect. */}
      {/* The plane lies flat and is then spun about Z so the text reads ALONG the End
          Zone (across the pitch width, 15 squares) rather than through its 1-square
          depth. w/h are the plane's LOCAL axes, so they are sized for the label before
          that spin, not after. */}
      <Decal text="AWAY SCORES HERE" position={[-half.x + 0.5, 0.03, 0]} rotation={[-Math.PI / 2, 0, -Math.PI / 2]} w={11} h={2.2} />
      <Decal text="HOME SCORES HERE" position={[half.x - 0.5, 0.03, 0]} rotation={[-Math.PI / 2, 0, Math.PI / 2]} w={11} h={2.2} />
    </group>
  );
}
