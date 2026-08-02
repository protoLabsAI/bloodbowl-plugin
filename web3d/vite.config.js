import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Built output lands in the plugin's own `web/3d/`, which the EXISTING static route
// already serves — so the 3D view adds no new FastAPI route, and therefore does not need
// a process restart to appear (a new route would: FastAPI cannot swap a mounted router,
// which is documented at length in docs/HANDOFF.md).
//
// `base: "./"` is load-bearing. The page is served at
// `/plugins/bloodbowl/static/3d/index.html` on the host window and at
// `/agents/<slug>/plugins/bloodbowl/static/3d/index.html` through the fleet proxy;
// RELATIVE asset URLs resolve correctly under both, and an absolute base would break the
// proxy — which is the guide's rule 3 (never hardcode a base).
export default defineConfig({
  base: "./",
  plugins: [react()],
  build: { outDir: "../web/3d", emptyOutDir: true },
  server: { port: 5199, proxy: { "/api": { target: "http://127.0.0.1:7878", changeOrigin: true } } },
});
