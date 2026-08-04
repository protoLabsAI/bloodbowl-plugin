// Generate the shipped page from the dev one.
//
// The only difference is how the bundle is addressed. In dev, vite serves index.html and
// resolves `./src/main.jsx` itself. Shipped, the page is served from
// `/plugins/bloodbowl/view3d` while its bundle lives under
// `/plugins/bloodbowl/static/3d/assets/` — different directories, so a RELATIVE script
// src cannot reach it. Addressing it absolutely off the base the page already derives
// keeps one HTML correct at any route, on the host window and through the
// `/agents/<slug>` fleet proxy alike.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";

const src = readFileSync(new URL("./index.html", import.meta.url), "utf8");
const loader = `<script>
      (function () {
        var s = document.createElement("script");
        s.type = "module";
        s.src = window.__base + "/plugins/bloodbowl/static/3d/assets/app.js";
        document.head.appendChild(s);
      })();
    </script>`;
const out = src.replace(
  /<!-- DEV entry only[\s\S]*?<script type="module" src="\.\/src\/main\.jsx"><\/script>/,
  loader,
);
if (out === src) throw new Error("build-page: dev entry script not found — did index.html change shape?");
mkdirSync(new URL("../web/3d/", import.meta.url), { recursive: true });
writeFileSync(new URL("../web/3d/index.html", import.meta.url), out);
console.log("build-page: wrote web/3d/index.html");
