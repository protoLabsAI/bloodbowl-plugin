import { OrbitControls } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";
import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Pawn } from "./Pawn";
import { Pitch } from "./Pitch";

// The DS kit does the console handshake (operator bearer + live theme) and gives us a
// slug-aware fetch. `@vite-ignore` because the specifier is built at runtime from
// `window.__base` — it is "" on the host window and "/agents/<slug>" through the fleet
// proxy, and a build-time resolve would bake in the wrong one.
// Degrade rather than white-screen if the kit is not there. It is served by the CONSOLE,
// so it is absent from the plugin's own test harness and from any host that does not
// serve `_ds` — and a view that throws on import shows an empty canvas with no clue why.
// The fallback is a plain same-origin fetch against the derived base: no bearer, which is
// correct for an ungated instance and honestly fails with a 401 on a gated one.
const kitReady = import(/* @vite-ignore */ `${window.__base}/_ds/plugin-kit.js`)
  .then((kit) => {
    kit.initPluginView();
    return kit;
  })
  .catch(() => ({ apiFetch: (path) => fetch(`${window.__base}${path}`) }));

async function get(path) {
  const kit = await kitReady;
  const res = await kit.apiFetch(path);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

function App() {
  const [match, setMatch] = useState(null);
  const [sel, setSel] = useState(null);
  const [legal, setLegal] = useState(null);
  const [err, setErr] = useState("");
  const selRef = useRef(null);
  selRef.current = sel;

  // Poll the SAME gated route the 2D board uses. This view computes no rules — it asks.
  useEffect(() => {
    let live = true;
    const tick = async () => {
      try {
        const d = await get("/api/plugins/bloodbowl/game");
        if (!live) return;
        if (d?.match) { setMatch(d.match); setErr(""); }
        else setErr(d?.error || "no match in progress");
      } catch (e) {
        if (live) setErr(String(e?.message || e));
      }
    };
    tick();
    const h = setInterval(tick, 2000);
    return () => { live = false; clearInterval(h); };
  }, []);

  // Legal squares come from the engine, never from here.
  useEffect(() => {
    if (!sel) { setLegal(null); return; }
    let live = true;
    get(`/api/plugins/bloodbowl/game/legal?player=${encodeURIComponent(sel)}`)
      .then((d) => {
        if (!live) return;
        // `squares` is EVERY neighbour with a verdict AND a reason, not a shortlist — the
        // engine answers "what about here?" rather than pre-filtering. Take the yeses.
        setLegal(new Set((d.squares || []).filter((q) => q.legal).map((q) => `${q.x},${q.y}`)));
      })
      .catch(() => live && setLegal(null));
    return () => { live = false; };
  }, [sel, match?.clock?.turn, match?.clock?.active, match?.clock?.half]);

  const players = (match?.players || []).filter((p) => p.place === "pitch");
  const c = match?.clock;
  const hud = err
    ? err
    : c
      ? `H${c.half} T${c.turn} — ${c.active} · ${match.score.home}-${match.score.away}${match.over ? " · FULL TIME" : ""}${sel ? ` · ${sel}` : ""}`
      : "connecting…";

  return (
    <>
      <div id="hud">{hud}</div>
      <Canvas shadows camera={{ position: [0, 23, 18], fov: 40 }}>
        <color attach="background" args={["#0b0f0c"]} />
        <hemisphereLight intensity={0.55} groundColor="#0b0f0c" />
        <directionalLight position={[10, 20, 8]} intensity={1.6} castShadow shadow-mapSize={[2048, 2048]} />
        <Pitch legal={legal} onPick={() => {}} />
        {players.map((p) => (
          <Pawn
            key={p.id}
            p={p}
            selected={sel === p.id}
            carrying={match?.ball?.carrier === p.id}
            onPick={(q) => setSel(q.id === selRef.current ? null : q.id)}
          />
        ))}
        <OrbitControls target={[0, 0, 0]} maxPolarAngle={Math.PI / 2.1} />
      </Canvas>
    </>
  );
}

createRoot(document.getElementById("root")).render(<App />);
