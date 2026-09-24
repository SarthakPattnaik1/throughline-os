import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

export default defineConfig([
  ...nextVitals,
  ...nextTs,
  globalIgnores([
    "node_modules/**",
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // Vendored at install time by scripts/vendor-hand-model.mjs — MediaPipe's
    // own WASM glue, minified and generated. Linting somebody else's build
    // output produces noise that buries this project's own findings.
    "public/mediapipe/**",
    // Vendored at install time by scripts/vendor-pdf-worker.mjs — PDF.js's
    // own worker, minified and generated.
    "public/pdfjs/**",
  ]),
]);
