# Vendored third-party

| File | Version | Licence | Why it is here |
|---|---|---|---|
| `three.min.js` | three.js r160.1 (classic/UMD build) | MIT | WebGL rendering for the CGI product pipeline. The UMD build is used deliberately: ES modules cannot be loaded from a `file://` URL without relaxing Chrome's security flags, and every template in this plugin must be self-contained and renderable offline. |
| `GLTFLoader.esm.js` | three.js r160.1 examples | MIT | Loads `.glb` / `.gltf` product models. ESM source, kept for reference and for the loader shim; see `templates/cgi/README.md`. |

Vendored on 2026-07-30 from unpkg. Both carry the MIT licence of the three.js project.
Do not fetch these at render time -- a CGI scene must render with no network access.
