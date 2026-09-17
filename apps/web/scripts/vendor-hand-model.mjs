/**
 * Put the hand-tracking model and its WASM on this machine, once, at install time.
 *
 * MediaPipe's own examples pass a CDN URL to `FilesetResolver` and let the
 * browser fetch the runtime and the model on first use. That is the wrong
 * default for this product and it is not a close call: the feature's entire
 * claim is that camera frames never leave the machine, and a *silent network
 * request made at the moment the researcher enables it* undermines that claim on
 * the exact interaction where it matters. It also means the feature stops
 * working on a plane, on a locked-down university network, or the day the CDN
 * path moves.
 *
 * So both halves are vendored into `public/mediapipe/` and served by this
 * application:
 *
 *   - the WASM runtime, copied out of the npm package that is already a
 *     dependency, so it is pinned by `package-lock.json` like everything else;
 *   - the `.task` model, downloaded once from Google's model store and checked
 *     against a known hash.
 *
 * Downloading at *install* time is a different thing from fetching at *use*
 * time. Installing software fetches things; that is what installing is. The
 * promise is about what happens once it is running.
 *
 * The model is not committed: it is 7.5MB of binary that would sit in every
 * clone's history forever. `.gitignore` excludes it, and the tracker refuses
 * with an actionable message when it is absent rather than failing obscurely —
 * so a fresh clone that skipped this step is told exactly what to run.
 */

import { createHash } from "node:crypto";
import { copyFile, mkdir, mkdtemp, open, readFile, rename, rm } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB = join(HERE, "..");
const OUT = join(WEB, "public", "mediapipe");

/**
 * float16 rather than float32: half the bytes for accuracy differences that do
 * not survive the smoothing filter downstream, and this runs on laptops.
 */
const MODEL_URL =
  "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
  + "hand_landmarker/float16/1/hand_landmarker.task";

/**
 * Pinned, so a silently substituted model is a failure rather than a surprise.
 * A model file is executable behaviour in every sense that matters here.
 */
const MODEL_SHA256 =
  "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1";

/**
 * Both SIMD and non-SIMD runtimes.
 *
 * MediaPipe picks between them at load time. Copying only the SIMD build works
 * on every machine I could test and fails on older ones, which is the worst
 * possible distribution of outcomes for a bug.
 */
const WASM_FILES = [
  "vision_wasm_internal.js",
  "vision_wasm_internal.wasm",
  "vision_wasm_nosimd_internal.js",
  "vision_wasm_nosimd_internal.wasm",
];

async function vendorWasm() {
  const from = join(WEB, "node_modules", "@mediapipe", "tasks-vision", "wasm");
  try {
    await readFile(join(from, WASM_FILES[0]));
  } catch {
    throw new Error(
      "@mediapipe/tasks-vision is not installed. Run `npm install` first.");
  }
  await mkdir(join(OUT, "wasm"), { recursive: true });
  for (const file of WASM_FILES) {
    await copyFile(join(from, file), join(OUT, "wasm", file));
  }
  console.log(`wasm: ${WASM_FILES.length} files copied from node_modules`);
}

async function vendorModel() {
  const target = join(OUT, "hand_landmarker.task");

  try {
    const existing = await readFile(target);
    const hash = createHash("sha256").update(existing).digest("hex");
    if (hash === MODEL_SHA256) {
      console.log("model: already present and matches the expected hash");
      return;
    }
    console.log("model: present but does not match the expected hash, replacing");
  } catch (error) {
    if (!(error && typeof error === "object" && "code" in error && error.code === "ENOENT")) {
      throw error;
    }
  }

  console.log("model: downloading (7.5MB, once)…");
  const response = await fetch(MODEL_URL);
  if (!response.ok) {
    throw new Error(`model download failed: ${response.status} ${response.statusText}`);
  }
  const bytes = Buffer.from(await response.arrayBuffer());

  const hash = createHash("sha256").update(bytes).digest("hex");
  if (hash !== MODEL_SHA256) {
    // Refuse rather than warn. A model that is not the model this was built
    // against is a behaviour change nobody asked for, arriving over a network.
    throw new Error(
      `model hash mismatch.\n  expected ${MODEL_SHA256}\n  got      ${hash}\n`
      + "Refusing to install it.");
  }

  await mkdir(OUT, { recursive: true });
  const stagingDir = await mkdtemp(join(OUT, ".hand-model-"));
  const staging = join(stagingDir, "hand_landmarker.task");
  try {
    const handle = await open(staging, "wx", 0o600);
    try {
      // The bytes crossed the network, but are written only after their pinned
      // SHA-256 matched MODEL_SHA256 above. A substituted response never reaches disk.
      await handle.writeFile(bytes);
      await handle.sync();
    } finally {
      await handle.close();
    }
    await rename(staging, target);
  } finally {
    await rm(stagingDir, { recursive: true, force: true });
  }
  console.log(`model: installed at public/mediapipe/hand_landmarker.task`);
}

await vendorWasm();
await vendorModel();
console.log("Hand tracking assets are local. Nothing is fetched at run time.");
