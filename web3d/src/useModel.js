// Load a positional's uploaded mesh, if there is one.
//
// The meshes are GATED (a coach's uploads are their files), so drei's `useGLTF` — which
// takes a bare URL and fetches it itself — cannot reach them: it carries no bearer. We
// fetch through the kit and parse the bytes we get back.
//
// A missing model is the NORMAL case, not an error: a fresh install ships none, and every
// pawn falls back to the primitive. That is the whole contract of this module — it can
// only ever upgrade the board, never break it.

import { useEffect, useState } from "react";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

const loader = new GLTFLoader();
/** slug pair → Promise<Group|null>. One fetch per positional per session, not per pawn:
 *  eleven Linemen share a mesh and would otherwise pull it eleven times. */
const cache = new Map();

const slug = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

function fetchModel(get, team, position) {
  const key = `${slug(team)}/${slug(position)}`;
  if (!cache.has(key)) {
    cache.set(
      key,
      get(`/api/plugins/bloodbowl/models/${key}/file`)
        .then((buf) => new Promise((res, rej) => loader.parse(buf, "", (g) => res(g.scene), rej)))
        .catch(() => null), // 404 = no model uploaded; anything else = fall back too
    );
  }
  return cache.get(key);
}

/** The uploaded mesh for this positional, or null while loading / when there isn't one. */
export function useModel(getBuffer, team, position) {
  const [scene, setScene] = useState(null);
  useEffect(() => {
    if (!team || !position) return;
    let live = true;
    fetchModel(getBuffer, team, position).then((g) => {
      // Clone per pawn: the cached scene is ONE object, and putting the same Object3D at
      // two positions in a graph means the second placement moves the first.
      if (live && g) setScene(g.clone(true));
    });
    return () => { live = false; };
  }, [getBuffer, team, position]);
  return scene;
}
