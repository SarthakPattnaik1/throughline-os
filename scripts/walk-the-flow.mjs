/**
 * Walk the product as a first-time tester, and check the hand-offs between
 * screens rather than merely record what they show.
 *
 * `scripts/measure-provenance.mjs` is the precedent this follows: drive the
 * running product with Playwright and report what it finds, against a
 * scratch instance only. This walks landing -> the sign-in gate -> the
 * first-project screen -> the worked example -> the screens a first-time
 * researcher reaches from there, and turns six defects a plain walkthrough
 * found (TASKS.md rows D194-D199) into PASS/FAIL checks that encode the
 * *intended* behaviour, so the fixes for those rows can be proven rather
 * than eyeballed:
 *
 *   C0  the landing page shows its call to action without scrolling
 *   C1  the overview catches up on its own once the example's pipeline
 *       finishes, with no click and no reload
 *   C2  a finding's "Computations behind it" link opens the analysis, not
 *       the Findings list it started from
 *   C3  the browser's Back from that analysis returns to the finding or the
 *       list it came from (either is acceptable; the report says which)
 *   C4  recording a finding from a connection lands on the finding, not the
 *       connection it was recorded from
 *   C5  a newly created project becomes the current one, and survives a
 *       reload
 *   C6  switching back to another project and reloading keeps that project,
 *       even from deep inside a section
 *   C7  the machine pages (/gesture-check, /air-ink) link back to /workspace
 *   C9  a hit on Search sources opens the source the passage came from
 *   C10 every value in the connections table sits under its own heading
 *   C11 every section carries the step strip, its action inside the fold
 *   C12 the connection screen offers record and report on arrival
 *   C13 the Overview's current step is a button that opens its object
 *   C14 a board card's relations can be followed (passes with a note when the board has no cards)
 *   C15 a finding offers to publish a figure or draft a report
 *   C17 a search says where it looked; no bare retrieval id outside a control
 *   C18 the palette reaches the machine pages and an analysis by its id
 *   C19 the camera controls are on the first screen of /gesture-check
 *   C20 a fresh browser is offered account creation first, as a button
 *   C21 Reports offers the take-away exports without opening a document
 *   C22 Discovery shows the ledger, offers a hypothesis, explains the correction first
 *   C23 the notebook's check says what it checks
 *   C25 a new project can be started without opening the menu
 *   C26 the rail's five headings and the open group's entries fit a 900 px laptop;
 *       one group open, none numbered
 *   C27 every screen shows one thing at a time: prose words and visible controls
 *       under the cap, never more than one filled primary (T139)
 *   C8  informational only: distinct console errors, page errors and HTTP
 *       >= 400 responses seen along the way -- this one never fails the run
 *
 * Needs the full stack, and it signs up a brand-new account and creates
 * projects on it -- NEVER point it at a real corpus. Run it against a
 * scratch instance:
 *
 *   THROUGHLINE_HOME=/tmp/walk .venv/bin/python scripts/manage.py dev \
 *     --api-port 8099 --web-port 3100
 *
 * then, in another terminal (Playwright itself comes from `npm install` in
 * apps/web -- there is nothing further to install at the repo root; Chromium
 * is expected to already be downloaded to ~/.cache/ms-playwright):
 *
 *   node scripts/walk-the-flow.mjs
 *
 * Environment variables:
 *   WALK_URL       the running web app                default http://localhost:3100
 *   WALK_OUT       dir for screenshots + report.txt    default <os.tmpdir()>/throughline-walk
 *   WALK_EMAIL     account to sign up                  default walk-<timestamp>@local.test
 *   WALK_PASSWORD  its password                        default a long throwaway
 *   WALK_ONLY      comma-separated check ids to run    default all
 *
 * A fresh, unique WALK_EMAIL on every run matters, not just for hygiene: the
 * gate only shows the first-project screen (and so the worked example) to an
 * account with no projects yet, and re-running against an account that
 * already has one would skip past everything C1 checks.
 *
 * Every check (C1-C7) is wrapped so one failure does not abort the rest: on
 * a failure the script catches it, records a FAIL with the observed values,
 * and navigates back to `${WALK_URL}/workspace` before continuing. C8 never
 * fails the run. The process exits 1 if any non-informational check failed
 * (or if the walkthrough could not get far enough to run them at all).
 *
 * Other sessions may be editing the web app while this runs, and the dev
 * server hot-reloads -- a transient Next.js compile-error overlay is
 * possible on a fresh navigation. `gotoSafe` below waits five seconds and
 * retries once if it sees one.
 */

import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const { chromium } = await import(
  new URL("../apps/web/node_modules/playwright/index.mjs", import.meta.url).href
);

const WALK_URL = process.env.WALK_URL ?? "http://localhost:3100";
const WALK_OUT = process.env.WALK_OUT ?? mkdtempSync(join(tmpdir(), "throughline-walk-"));
const WALK_EMAIL = process.env.WALK_EMAIL ?? `walk-${Date.now()}@local.test`;
const WALK_PASSWORD = process.env.WALK_PASSWORD ?? "walk-only-local-throwaway-2026";

mkdirSync(WALK_OUT, { recursive: true });

const out = [];
const log = (...a) => { const s = a.join(" "); out.push(s); console.log(s); };
let n = 0;

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
page.setDefaultNavigationTimeout(120000);
page.setDefaultTimeout(30000);

const consoleIssues = [];
page.on("console", (m) => {
  if (m.type() === "error" || m.type() === "warning") {
    consoleIssues.push(`[${m.type()}] ${m.text().slice(0, 300)}`);
  }
});
page.on("pageerror", (e) => consoleIssues.push(`[pageerror] ${String(e).slice(0, 300)}`));
page.on("response", (r) => {
  if (r.status() >= 400) consoleIssues.push(`[http ${r.status()}] ${r.request().method()} ${r.url()}`);
});

const shot = async (name) => {
  const file = join(WALK_OUT, `${String(n++).padStart(2, "0")}-${name}.png`);
  await page.screenshot({ path: file }).catch((e) => log(`   (screenshot failed: ${e.message})`));
  return file;
};

/** Is a Next.js dev-mode compile-error overlay currently on screen? */
const hasCompileErrorOverlay = () => page.evaluate(() => {
  return !!document.querySelector(
    "[data-nextjs-dialog-overlay], #nextjs__container_errors_label, nextjs-portal");
}).catch(() => false);

/** `page.goto`, but retries once after 5s if a hot-reload compile error is showing. */
async function gotoSafe(url) {
  await page.goto(url, { waitUntil: "domcontentloaded" });
  if (await hasCompileErrorOverlay()) {
    log(`   compile-error overlay at ${url} -- waiting 5s and retrying once`);
    await page.waitForTimeout(5000);
    await page.goto(url, { waitUntil: "domcontentloaded" });
  }
}

const state = async () => page.evaluate(() => {
  const t = (el) => (el?.textContent ?? "").trim().replace(/\s+/g, " ");
  return {
    url: location.href.replace(location.origin, ""),
    h1: [...document.querySelectorAll("h1")].map(t).slice(0, 3),
    crumbs: [...document.querySelectorAll("nav.crumbs .crumb")].map(t),
    project: t(document.querySelector(".pm-name")) || null,
    railCurrent: t(document.querySelector(".rail-item[aria-current='true']")) || null,
    // The Overview's counts are one line (T139); each number carries the
    // plural it counts, so this reads like the old "6Analyses" cells did.
    meters: [...document.querySelectorAll(".totals [data-count-of]")]
      .map((n) => `${n.textContent.trim()}${n.dataset.countOf}`),
    steps: [...document.querySelectorAll("ol.steps li")].map((li) =>
      `${t(li.querySelector("b"))} = ${t(li.querySelector(".step-state"))}`),
    railCounts: [...document.querySelectorAll(".rail-item")]
      .filter((b) => b.querySelector(".rail-count"))
      .map((b) => t(b)),
    mainButtons: [...(document.querySelector("main") ?? document.body)
      .querySelectorAll("button, a")].map(t).filter(Boolean).slice(0, 40),
  };
});

const say = async (label) => {
  const s = await state();
  log(`\n## ${label}`);
  log(`   url=${s.url}`);
  log(`   project=${s.project}  rail=${s.railCurrent}  h1=${JSON.stringify(s.h1)}`);
  log(`   crumbs=${JSON.stringify(s.crumbs)}`);
  if (s.meters.length) log(`   meters=${JSON.stringify(s.meters)}`);
  if (s.steps.length) log(`   steps=${JSON.stringify(s.steps)}`);
  if (s.railCounts.length) log(`   railCounts=${JSON.stringify(s.railCounts)}`);
  return s;
};

const settle = (ms) => page.waitForTimeout(ms);

/**
 * The rail shows one stage at a time (T139): a closed group's entries are not
 * in the DOM. So when the entry is not there, press headings until it appears.
 * Pressing the heading of the group that is already open is a no-op, so this
 * loop cannot close the group it wanted and always terminates.
 */
const railClick = async (prefix) => {
  const row = page.locator("button.rail-item").filter({ hasText: new RegExp(`^${prefix}`) });
  if (!(await row.count())) {
    const heads = page.locator("button.rail-heading");
    const n = await heads.count();
    for (let i = 0; i < n; i++) {
      await heads.nth(i).click();
      if (await row.count()) break;
    }
  }
  await row.first().click();
};

/** The leading integer off a meter/rail string like "6Analyses" or "0Contradictions". */
function meterCount(strings, prefix) {
  for (const m of strings) {
    const match = /^(\d+)(\D.*)$/.exec(m);
    if (match && match[2].toLowerCase().startsWith(prefix.toLowerCase())) {
      return parseInt(match[1], 10);
    }
  }
  return 0;
}

/** h1[0] is present and is not `forbidden` -- guards against an empty h1 counting as "not X". */
const h1IsNot = (s, forbidden) => s.h1.length > 0 && s.h1[0] !== forbidden;

/**
 * Poll `probe()` (which returns `{ ok, ...details }`) until it says `ok` or
 * `timeoutMs` elapses, whichever comes first. Always probes at least once.
 */
async function pollUntil(probe, timeoutMs, intervalMs = 500) {
  const start = Date.now();
  for (;;) {
    const result = await probe();
    const elapsedMs = Date.now() - start;
    if (result.ok || elapsedMs >= timeoutMs) return { ...result, elapsedMs };
    await page.waitForTimeout(intervalMs);
  }
}

const results = [];

/** Run one check; a thrown error becomes a FAIL, and either way we recover to /workspace. */
/** `WALK_ONLY=C13,C27` runs just those checks; the rest are recorded as SKIP. */
const ONLY = (process.env.WALK_ONLY ?? "").split(",").map((x) => x.trim()).filter(Boolean);

async function check(id, description, fn) {
  if (ONLY.length && !ONLY.includes(id)) {
    results.push({ id, description, status: "SKIP", detail: "not in WALK_ONLY" });
    return;
  }
  try {
    const detail = await fn();
    results.push({ id, description, status: "PASS", detail: detail ?? "" });
    log(`\nPASS ${id} -- ${description}\n   ${detail ?? ""}`);
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    results.push({ id, description, status: "FAIL", detail: msg });
    log(`\nFAIL ${id} -- ${description}\n   ${msg}`);
    try {
      await gotoSafe(`${WALK_URL}/workspace`);
      await settle(1000);
    } catch (recoverErr) {
      log(`   (recovery navigation also failed: ${recoverErr.message})`);
    }
  }
}

let lastFindingTitle = null;
let fatal = null;

try {
  // ---------------------------------------------------------------- 1. landing
  await gotoSafe(`${WALK_URL}/`);
  // Let the reveal animation finish before measuring or photographing.
  await settle(3000);
  await shot("landing");
  const ctas = await page.evaluate(() =>
    [...document.querySelectorAll("a.l-btn")].map((a) => `${a.textContent.trim()} -> ${a.getAttribute("href")}`));
  log("landing CTAs:", JSON.stringify(ctas));

  // ---------------------------------------------------------------- C0
  // Measured here rather than through check(): a failure must not navigate
  // away, because the next step is to press the very control being measured.
  {
    const cta = await page.evaluate(() => {
      const el = document.querySelector(".l-hero .l-cta-row");
      if (!el) return null;
      const box = el.getBoundingClientRect();
      return { bottom: Math.round(box.bottom), viewport: window.innerHeight };
    });
    const ok = Boolean(cta && cta.bottom <= cta.viewport);
    const detail = cta
      ? `call to action ends at ${cta.bottom}px in a ${cta.viewport}px viewport`
      : "no call to action found in the hero";
    results.push({ id: "C0", description: "the landing page shows its way in without scrolling (D200)", status: ok ? "PASS" : "FAIL", detail });
    log(`\n${ok ? "PASS" : "FAIL"} C0 -- the landing page shows its way in without scrolling\n   ${detail}`);
  }
  await page.locator("a.l-btn-primary").first().click();
  await page.waitForSelector(".gate, .shell, .first", { timeout: 30000 });
  await say("after clicking Open the workspace");
  await shot("gate");

  // ---------------------------------------------------------------- 2. gate (three modes)
  let gateH1 = (await page.locator(".gate h1").textContent()).trim();
  log("gate heading:", gateH1);
  // ---------------------------------------------------------------- C20
  // Measured in place (not via check()), because a failure must not navigate
  // away from the gate the walk is about to go through.
  {
    const pressed = await page.evaluate(() => {
      const el = document.querySelector('.gate [aria-pressed="true"]');
      return el ? el.textContent.trim() : null;
    });
    const ok = pressed === "Create an account" && /Create your account/i.test(gateH1);
    const detail = `leading choice: ${JSON.stringify(pressed)}; heading: "${gateH1}"`;
    results.push({ id: "C20", description: "a fresh browser is offered account creation first, as a button", status: ok ? "PASS" : "FAIL", detail });
    log(`\n${ok ? "PASS" : "FAIL"} C20 -- a fresh browser is offered account creation first\n   ${detail}`);
  }
  if (/Welcome back/i.test(gateH1)) {
    await page.locator(".gate").getByRole("button", { name: "Create an account" }).click();
    gateH1 = (await page.locator(".gate h1").textContent()).trim();
    log("gate heading after switching to sign-up:", gateH1);
  }
  if (await page.locator(".gate input[type=text]").count()) {
    await page.locator(".gate input[type=text]").fill("Walk Tester");
  }
  await page.locator(".gate input[type=email]").fill(WALK_EMAIL);
  await page.locator(".gate input[type=password]").fill(WALK_PASSWORD);
  await page.locator(".gate button[type=submit]").click();
  await page.waitForSelector(".shell, .first", { timeout: 30000 });
  await say("after creating the account");
  await shot("first-project");

  // ---------------------------------------------------------------- 3. worked example
  const t0 = Date.now();
  await page.getByRole("button", { name: /Open a worked example/ }).click();
  await page.waitForSelector(".shell", { timeout: 60000 });
  log(`shell appeared ${Date.now() - t0}ms after clicking the example`);
  await say("immediately after the example opens");
  await shot("after-example-0s");

  // ---------------------------------------------------------------- C1
  await check("C1", "the overview catches up on its own", async () => {
    const start = Date.now();
    const result = await pollUntil(async () => {
      const s = await state();
      const ok = meterCount(s.meters, "dataset") >= 1
        && meterCount(s.meters, "analys") >= 1
        && meterCount(s.meters, "finding") >= 1;
      return { ok, meters: s.meters };
    }, 90000, 3000);
    await shot(result.ok ? "overview-caught-up" : "overview-still-stale");
    if (!result.ok) {
      throw new Error(`meters never caught up within 90s (no click, no reload): last seen ${JSON.stringify(result.meters)}`);
    }
    return `caught up after ~${Math.round((Date.now() - start) / 1000)}s: meters=${JSON.stringify(result.meters)}`;
  });

  // ---------------------------------------------------------------- C2
  await check("C2", "a finding's computation opens the analysis", async () => {
    await railClick("Findings");
    await settle(1000);
    await shot("findings-list");
    const card = page.locator("main .card").first();
    if (!(await card.count())) throw new Error("no finding card in the Findings list");
    await card.click();
    await settle(1000);
    const findingState = await say("finding detail, before opening its analysis");
    lastFindingTitle = findingState.h1[0] ?? null;
    await shot("finding-detail");

    const methodButton = page.locator("main .card-tight button").first();
    if (!(await methodButton.count())) {
      throw new Error("no computation button under \"Computations behind it\" on the finding detail");
    }
    const label = (await methodButton.textContent()).trim();
    await methodButton.click();
    await settle(1500);
    const s = await say(`after clicking the computation "${label}" on the finding`);
    await shot("finding-open-analysis");

    const pass = (s.railCurrent ?? "").startsWith("Analyses")
      && s.url.includes("section=analyses")
      && h1IsNot(s, "Findings");
    const detail = `rail=${s.railCurrent} url=${s.url} h1=${JSON.stringify(s.h1)}`;
    if (!pass) throw new Error(detail);
    return detail;
  });

  // ---------------------------------------------------------------- C3
  await check("C3", "back from a detail returns to the list", async () => {
    await page.goBack();
    // Polled rather than read once: Back changes the address at once, but the
    // finding's detail is fetched before it has a heading, and on a loaded
    // machine that gap is long enough to read an empty screen as a failure.
    const where = (s) =>
      lastFindingTitle && s.h1[0] === lastFindingTitle
        ? `returned to the finding detail ("${lastFindingTitle}")`
        : s.h1[0] === "Findings" ? "returned to the Findings list" : null;
    const outcome = await pollUntil(async () => {
      const s = await state();
      return { ok: where(s) !== null, s };
    }, 15000);
    const s = await say("after page.goBack() from the analysis detail");
    await shot("back-from-analysis");
    if (!outcome.ok) {
      throw new Error(`neither the finding detail nor the Findings list -- h1=${JSON.stringify(s.h1)} url=${s.url}`);
    }
    return `${where(outcome.s)} after ~${outcome.elapsedMs}ms`;
  });
  // Back to a clean, known screen before the next check, regardless of C3's outcome.
  await railClick("Overview").catch((e) => log(`   (returning to Overview via the rail failed: ${e.message})`));
  await settle(500);

  // ---------------------------------------------------------------- C4
  await check("C4", "recording a finding shows the finding", async () => {
    await railClick("Connections");
    await settle(1000);
    await shot("connections-list");
    const row = page.locator("main tbody tr").first();
    if (!(await row.count())) throw new Error("no connection row in the Connections list");
    const rowButton = row.locator("button").first();
    if (await rowButton.count()) await rowButton.click(); else await row.click();
    await settle(1000);
    await say("connection detail");
    await shot("connection-detail");

    const recordButton = page.getByRole("button", { name: "Record a finding", exact: true });
    if (!(await recordButton.count())) throw new Error("no \"Record a finding\" button on the connection detail");
    await recordButton.click();
    await settle(500);
    await shot("record-finding-form");

    const textarea = page.locator("main textarea").last();
    if (!(await textarea.count())) throw new Error("no textarea in the record-a-finding form");
    await textarea.fill("Recorded by the walk-the-flow harness.");
    const submit = page.locator("main button").filter({ hasText: /record it/i }).last();
    if (!(await submit.count())) throw new Error("no submit button matching /record it/i");
    await submit.click();

    const result = await pollUntil(async () => {
      const s = await state();
      const ok = (s.railCurrent ?? "").startsWith("Findings")
        && s.url.includes("section=findings")
        && s.url.includes("item=")
        && h1IsNot(s, "Connections");
      return { ok, s };
    }, 5000, 300);
    await shot("after-record-finding");
    const detail = `rail=${result.s.railCurrent} url=${result.s.url} h1=${JSON.stringify(result.s.h1)}`;
    if (!result.ok) throw new Error(detail);
    return detail;
  });

  // ---------------------------------------------------------------- C9
  await check("C9", "a search hit opens its source (D203)", async () => {
    await railClick("Search sources");
    await settle(800);
    await page.getByLabel("Search the sources in this project").fill("resistance");
    await page.getByRole("button", { name: "Search", exact: true }).click();
    await page.locator("main .card-tight").first().waitFor({ timeout: 30000 });
    await shot("search-results");
    await page.getByRole("button", { name: /open the source/i }).first().click();
    const outcome = await pollUntil(async () => {
      const s = await state();
      return { ok: /^Sources/.test(s.railCurrent ?? "") && /section=sources/.test(s.url) && /item=src_/.test(s.url), s };
    }, 15000);
    await say("after opening a search hit's source");
    await shot("search-hit-source");
    if (!outcome.ok) throw new Error(`did not land on the source: rail=${outcome.s.railCurrent} url=${outcome.s.url}`);
    return `rail=${outcome.s.railCurrent} url=${outcome.s.url} h1=${JSON.stringify(outcome.s.h1)}`;
  });

  // ---------------------------------------------------------------- C10
  await check("C10", "every connections-table value sits under its own heading (D209)", async () => {
    await railClick("Connections");
    await page.locator("main tbody tr").first().waitFor({ timeout: 30000 });
    const shape = await page.evaluate(() => {
      const headers = [...document.querySelectorAll("main thead th")].map((th) => th.textContent.trim());
      const cells = [...document.querySelector("main tbody tr").querySelectorAll("td")].map((td) => td.textContent.trim());
      return { headers, cells };
    });
    const q = shape.cells[shape.headers.indexOf("q-value")];
    const ok = shape.headers.length === shape.cells.length && q !== undefined && /^\d|^—|e-/.test(q) && Number(q.replace("—", "0")) <= 1;
    if (!ok) throw new Error(`headers=${JSON.stringify(shape.headers)} cells=${JSON.stringify(shape.cells)}`);
    return `${shape.headers.length} headers, ${shape.cells.length} cells; under "q-value": ${q}`;
  });

  // ---------------------------------------------------------------- C11
  await check("C11", "every section names the loop's step and keeps its action inside the fold", async () => {
    const project = new URL(page.url()).searchParams.get("project");
    const ids = ["board", "overview", "sources", "variables", "search", "discover", "compare",
      "patterns", "connections", "findings", "analyses", "graph", "embedding", "reports",
      "figures", "gallery", "notebook", "journal", "activity", "literature", "datasearch",
      "readfigure", "settings"];
    const bad = [];
    let stripOnOverview = "";
    let nextOnOverview = "";
    for (const id of ids) {
      await gotoSafe(`${WALK_URL}/workspace?project=${project}&section=${id}`);
      await page.locator(".step-strip").waitFor({ timeout: 30000 });
      const m = await page.evaluate(() => {
        const strip = document.querySelector(".step-strip");
        const button = strip ? strip.querySelector(".btn-primary") : null;
        const box = button ? button.getBoundingClientRect() : null;
        return {
          text: (strip ? strip.textContent : "").trim().replace(/\s+/g, " "),
          hasButton: Boolean(button),
          bottom: box ? Math.round(box.bottom) : null,
          viewport: window.innerHeight,
          next: (document.querySelector('.steps li[data-next="true"] b') || {}).textContent || "",
        };
      });
      if (!/step \d of \d|you are here|every step is done|working/i.test(m.text)) bad.push(`${id}: strip says "${m.text}"`);
      if (m.hasButton && m.bottom > m.viewport) bad.push(`${id}: action ends at ${m.bottom}px in ${m.viewport}px`);
      if (id === "overview") { stripOnOverview = m.text; nextOnOverview = m.next.trim(); }
    }
    if (nextOnOverview && !stripOnOverview.includes(nextOnOverview)) {
      bad.push(`overview: strip "${stripOnOverview}" does not name the row marked next ("${nextOnOverview}")`);
    }
    await shot("step-strip");
    if (bad.length) throw new Error(bad.join("; "));
    return `${ids.length} sections carry the strip; on Overview it names "${nextOnOverview}"`;
  });

  // ---------------------------------------------------------------- C26
  await check("C26", "the rail's five stage headings and the open group's entries fit a 900 px laptop", async () => {
    await railClick("Overview");
    await settle(500);
    const m = await page.evaluate(() => {
      const body = document.querySelector("nav.rail");
      const heads = [...document.querySelectorAll("button.rail-heading")];
      const open = heads.filter((h) => h.getAttribute("aria-expanded") === "true");
      const items = [...document.querySelectorAll(".rail-item")];
      const off = [...heads, ...items].filter((el) => {
        const b = el.getBoundingClientRect();
        return b.bottom > window.innerHeight + 1 || b.top < 52;
      }).map((el) => el.textContent.trim().slice(0, 20));
      const numbered = heads.map((h) => (h.querySelector("span")?.textContent ?? "").trim()).filter((n) => /^\d/.test(n));
      return { heads: heads.length, open: open.map((h) => h.querySelector("span")?.textContent.trim()),
               items: items.length, overflow: body.scrollHeight - body.clientHeight, off, numbered };
    });
    if (m.heads !== 5) throw new Error(`${m.heads} rail headings, expected the five stages`);
    if (m.open.length !== 1) throw new Error(`${m.open.length} groups open: ${JSON.stringify(m.open)}`);
    if (m.numbered.length) throw new Error(`numbered headings: ${JSON.stringify(m.numbered)} (plan rule 3: named, never numbered)`);
    if (m.overflow > 2 || m.off.length) throw new Error(`rail overflows by ${m.overflow}px; off-screen: ${JSON.stringify(m.off)}`);
    return `5 headings, "${m.open[0]}" open with ${m.items} entries, overflow ${m.overflow}px`;
  });

  // ---------------------------------------------------------------- C27
  await check("C27", "every screen shows one thing at a time: words and controls under the cap, one primary at most", async () => {
    // The caps are the hard ones from T139's contract (target 180 words / 12
    // controls; hard 220 / 15). Prose is what the owner said a reader glances
    // past, so table cells, code and figure text are data and do not count;
    // text inside a closed fold has no box and does not count either, which
    // is the point of the fold (see visible() below for how that is asked).
    const project = new URL(page.url()).searchParams.get("project");
    const ids = ["overview", "sources", "connections", "findings", "discover", "reports",
      "figures", "analyses", "search", "variables"];
    const WORDS = 220, CONTROLS = 15;
    const bad = [], seen = [];
    for (const id of ids) {
      await gotoSafe(`${WALK_URL}/workspace?project=${project}&section=${id}`);
      await page.locator(".step-strip").waitFor({ timeout: 30000 });
      await settle(800);
      const m = await page.evaluate(() => {
        const visible = (el) => {
          // A closed <details> keeps its layout box in Chromium (its content
          // is content-visibility: hidden, geometry preserved), so a rect test
          // alone would count exactly the prose the fold was built to hide.
          if (el.checkVisibility && !el.checkVisibility({
            checkVisibilityCSS: true, contentVisibilityAuto: true, opacityProperty: true,
          })) return false;
          const r = el.getBoundingClientRect();
          const cs = getComputedStyle(el);
          return r.width > 0 && r.height > 0 && cs.visibility !== "hidden"
            && r.top < window.innerHeight && r.bottom > 0;
        };
        const main = document.querySelector("main");
        const inspector = document.querySelector(".inspector");
        const roots = [main, inspector].filter(Boolean);
        const controls = roots
          .flatMap((r) => [...r.querySelectorAll("button, a[href], input, select, textarea, summary")])
          .filter(visible);
        let words = 0;
        if (main) {
          const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
          let node;
          while ((node = walker.nextNode())) {
            const text = node.textContent.trim();
            if (!text) continue;
            const el = node.parentElement;
            if (!el || !visible(el) || el.closest("table, pre, code, svg, kbd")) continue;
            const range = document.createRange();
            range.selectNodeContents(node);
            const r = range.getBoundingClientRect();
            if (r.top >= window.innerHeight || r.bottom <= 0) continue;
            words += text.split(/\s+/).length;
          }
        }
        const primaries = [...document.querySelectorAll(".btn-primary")].filter(visible).length;
        return { controls: controls.length, words, primaries,
                 inspector: Boolean(inspector && visible(inspector)) };
      });
      seen.push(`${id}: ${m.words} words, ${m.controls} controls, ${m.primaries} primary${m.inspector ? ", inspector" : ""}`);
      if (m.words > WORDS) bad.push(`${id}: ${m.words} prose words above the fold (cap ${WORDS})`);
      if (m.controls > CONTROLS) bad.push(`${id}: ${m.controls} visible controls (cap ${CONTROLS})`);
      if (m.primaries > 1) bad.push(`${id}: ${m.primaries} filled primaries on one screen`);
    }
    await shot("density");
    if (bad.length) throw new Error(bad.join("; "));
    return seen.join("; ");
  });

  // ---------------------------------------------------------------- C12
  await check("C12", "the connection screen offers record and report on arrival", async () => {
    await railClick("Connections");
    await page.locator("main tbody tr").first().waitFor({ timeout: 30000 });
    const row = page.locator("main tbody tr").first();
    if (await row.locator("button").count()) await row.locator("button").first().click(); else await row.click();
    await page.locator("main .oa").waitFor({ timeout: 30000 });
    await settle(800);
    await shot("connection-actions");
    const m = await page.evaluate(() => {
      const main = document.querySelector("main");
      const controls = [...main.querySelectorAll("button, a, .oa-note")];
      const record = controls.find((c) => /record .*finding/i.test(c.textContent || ""));
      const report = controls.find((c) => /report/i.test(c.textContent || ""));
      const box = record ? record.getBoundingClientRect() : null;
      return { record: record ? record.textContent.trim() : null, recordBottom: box ? Math.round(box.bottom) : null,
               report: report ? report.textContent.trim().slice(0, 80) : null, viewport: window.innerHeight };
    });
    if (!m.record) throw new Error("no record-a-finding control on the connection screen");
    if (m.recordBottom > m.viewport) throw new Error(`record control ends at ${m.recordBottom}px in ${m.viewport}px`);
    if (!m.report) throw new Error("no report control or stated reason on the connection screen");
    await page.getByRole("button", { name: /record this as a finding/i }).first().click();
    await settle(600);
    const focused = await page.evaluate(() => {
      const el = document.activeElement;
      return el && el !== document.body ? `${el.tagName.toLowerCase()} "${(el.textContent || "").trim().slice(0, 40)}"` : null;
    });
    if (!focused) throw new Error("pressing the record control moved focus nowhere");
    return `record control ends at ${m.recordBottom}px; report control: "${m.report}"; focus moved to ${focused}`;
  });

  // ---------------------------------------------------------------- C13
  await check("C13", "the Overview's next step is a button that opens the object it names", async () => {
    await railClick("Overview");
    await page.locator(".steps").waitFor({ timeout: 30000 });
    await settle(800);
    await shot("overview-next-step");
    // The row's control is a button at text weight: the strip above carries
    // the screen's one filled primary (T139), and the row names the same act.
    const next = page.locator('.steps li[data-next="true"] .step-action button');
    if (!(await next.count())) throw new Error("the current loop row carries no control");
    const label = (await next.textContent()).trim();
    await next.click();
    const outcome = await pollUntil(async () => {
      const s = await state();
      return { ok: s.h1.some((h) => / and |×/.test(h)) || /item=/.test(s.url), s };
    }, 15000);
    if (!outcome.ok) throw new Error(`pressing "${label}" landed on h1=${JSON.stringify(outcome.s.h1)} url=${outcome.s.url}`);
    return `"${label}" opened ${JSON.stringify(outcome.s.h1)} at ${outcome.s.url}`;
  });

  // ---------------------------------------------------------------- C14
  await check("C14", "a board card's relations can be followed", async () => {
    await railClick("Workboard");
    await settle(1500);
    const cards = page.locator(".board-card");
    if (!(await cards.count())) return "no card on this board to open (the example places none)";
    const opener = cards.first().locator("button").first();
    await opener.click();
    await page.locator("#built-on-heading").waitFor({ timeout: 15000 });
    const first = page.locator("#built-on-heading ~ * button.pick, .board-detail button.pick").first();
    if (!(await first.count())) return "card opened; it has no dependents to follow";
    const label = (await first.textContent()).trim();
    await first.click();
    const outcome = await pollUntil(async () => {
      const s = await state();
      return { ok: /item=/.test(s.url) && !/^Workboard/.test(s.railCurrent ?? ""), s };
    }, 10000);
    if (!outcome.ok) throw new Error(`following "${label}" left rail=${outcome.s.railCurrent} url=${outcome.s.url}`);
    return `followed "${label}" to ${outcome.s.url}`;
  });

  // ---------------------------------------------------------------- C15
  await check("C15", "a finding offers to publish a figure or draft a report", async () => {
    await railClick("Findings");
    await page.locator("main .card").first().waitFor({ timeout: 15000 });
    await page.locator("main .card").first().click();
    const found = await pollUntil(async () => {
      const t = await page.locator("main").textContent();
      return { ok: /Export for publication|Draft a report from this finding|nothing yet for a report|no run behind/i.test(t ?? ""), t: (t ?? "").slice(0, 80) };
    }, 15000);
    await shot("finding-take-it-further");
    if (!found.ok) throw new Error("neither a figure control nor a report control (nor a stated reason) on the finding");
    const controls = await page.evaluate(() => [...document.querySelectorAll("main button")].map((b) => b.textContent.trim()).filter((t) => /Export for publication|Draft a report from this finding/i.test(t)));
    return `controls: ${JSON.stringify(controls)}`;
  });

  // ---------------------------------------------------------------- C17
  await check("C17", "a search says where it looked, and nothing is a bare id", async () => {
    await railClick("Search sources");
    await settle(600);
    await page.getByLabel("Search the sources in this project").fill("resistance");
    await page.getByRole("button", { name: "Search", exact: true }).click();
    await page.locator("main .card-tight").first().waitFor({ timeout: 30000 });
    const summary = page.getByText(/which passages this search was built from/i).first();
    if (!(await summary.count())) throw new Error("no 'which passages' disclosure");
    const bare = await page.evaluate(() => {
      const main = document.querySelector("main");
      const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
      const hits = [];
      let n; while ((n = walker.nextNode())) {
        if (/\bret_[a-z0-9]+/i.test(n.textContent) && !n.parentElement.closest("details, button")) hits.push(n.textContent.trim().slice(0, 60));
      }
      return hits;
    });
    if (bare.length) throw new Error(`bare retrieval id outside a control: ${JSON.stringify(bare)}`);
    await summary.click();
    await settle(1500);
    await shot("search-disclosure");
    return "disclosure present; no bare retrieval id in the results";
  });

  // ---------------------------------------------------------------- C18
  await check("C18", "the palette reaches the machine pages and an analysis by id", async () => {
    await railClick("Overview");
    await settle(500);
    await page.keyboard.press("Control+k");
    await page.locator("[role=dialog] input, .palette input").first().waitFor({ timeout: 10000 });
    await page.keyboard.type("air");
    await settle(400);
    const air = await page.getByText(/draw in the air/i).first().count();
    await page.keyboard.press("Escape");
    const project = new URL(page.url()).searchParams.get("project");
    const runs = await (await page.request.get(`${WALK_URL}/api/projects/${project}/analyses?limit=1`)).json();
    const runId = runs[0]?.id;
    await page.keyboard.press("Control+k");
    await page.locator("[role=dialog] input, .palette input").first().waitFor({ timeout: 10000 });
    await page.keyboard.type(runId ?? "arun_");
    await settle(400);
    // Whatever method the first run used, the id must bring up that run and
    // nothing else: one option, in the Analysis group.
    const offered = await page.evaluate(() => [...document.querySelectorAll("[role=option]")]
      .map((o) => o.textContent.trim().replace(/\s+/g, " ")));
    await shot("palette-by-id");
    await page.keyboard.press("Escape");
    if (!air) throw new Error("typing 'air' offered no 'Draw in the air'");
    if (offered.length !== 1 || !/Analysis$/.test(offered[0])) throw new Error(`typing ${runId} offered ${JSON.stringify(offered)}`);
    return `'air' → Draw in the air; ${runId} → "${offered[0]}"`;
  });

  // ---------------------------------------------------------------- C21
  await check("C21", "Reports offers the take-away exports without opening a document", async () => {
    await railClick("Reports");
    await page.getByRole("heading", { name: /take this away/i }).waitFor({ timeout: 15000 });
    const csv = await page.locator('a[href$="results.csv"]').count();
    const zip = await page.locator('a[href$="snapshot.zip"]').count();
    await shot("reports-take-away");
    if (!csv || !zip) throw new Error(`results.csv link: ${csv}; snapshot.zip link: ${zip}`);
    return "results.csv and snapshot.zip are on the Reports screen";
  });

  // ---------------------------------------------------------------- C22
  await check("C22", "Discovery shows the ledger, offers a hypothesis, and explains the correction first", async () => {
    await railClick("Discovery");
    await page.getByRole("heading", { name: /this line of enquiry/i }).waitFor({ timeout: 15000 });
    const register = await page.getByRole("button", { name: /register a hypothesis/i }).count();
    const order = await page.evaluate(() => {
      const main = document.querySelector("main");
      const table = main.querySelector("table");
      const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
      let n; while ((n = walker.nextNode())) {
        if (/corrected for how many tests ran/i.test(n.textContent)) {
          return table ? Boolean(n.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING) : "no-table";
        }
      }
      return "no-caption";
    });
    await shot("discovery");
    if (!register) throw new Error("no 'Register a hypothesis' control on Discovery");
    if (order !== true && order !== "no-table") throw new Error(`the correction caption is ${order === "no-caption" ? "missing" : "below the table"}`);
    return `ledger heading present; register control present; caption ${order === "no-table" ? "present (no table yet)" : "precedes the table"}`;
  });

  // ---------------------------------------------------------------- C23
  await check("C23", "the notebook's check says what it checks", async () => {
    await railClick("Notebook");
    const button = page.locator("main .nb-lint button").first();
    await button.waitFor({ timeout: 15000 });
    const name = (await button.textContent()).trim();
    if (name.split(/\s+/).length < 4 || !/stale|link|page|source|evidence|check/i.test(name)) throw new Error(`the check's label says only "${name}"`);
    return `check control reads "${name}"`;
  });

  // ---------------------------------------------------------------- C25
  await check("C25", "a new project can be started without opening the menu", async () => {
    const btn = page.getByRole("button", { name: /new project/i }).first();
    if (!(await btn.count())) throw new Error("no 'New project' control outside the menu");
    const inMenu = await btn.evaluate((el) => Boolean(el.closest("[role=menu]")));
    if (inMenu) throw new Error("'New project' is only inside the menu");
    return "visible in the topbar";
  });

  // ---------------------------------------------------------------- C5
  let newProjectName = null;
  await check("C5", "a new project becomes the current one", async () => {
    await page.locator(".pm-trigger").click();
    await settle(500);
    await shot("project-menu");
    await page.getByRole("menuitem", { name: /New project/ }).click();
    await settle(500);
    await shot("new-project-form");

    const question = `Does the walk-the-flow harness's new project become current, at ${Date.now()}?`;
    newProjectName = question.slice(0, 60);
    await page.locator("textarea").fill(question);
    await page.getByRole("button", { name: /Create project/ }).click();

    const result = await pollUntil(async () => {
      const s = await state();
      return { ok: s.project === newProjectName && s.url.includes("project="), s };
    }, 5000, 300);
    await shot("after-new-project");
    const detail1 = `project=${result.s.project} url=${result.s.url} (expected "${newProjectName}")`;
    if (!result.ok) throw new Error(`part 1 (immediately after creating): ${detail1}`);

    await page.reload({ waitUntil: "domcontentloaded" });
    await settle(1500);
    const s2 = await say("after reloading the new project");
    await shot("after-new-project-reload");
    if (s2.project !== newProjectName) {
      throw new Error(`part 1 passed (${detail1}) but part 2 (after reload) failed: project=${s2.project}`);
    }
    return `part 1: ${detail1}; part 2 (after reload): project=${s2.project}`;
  });

  // ---------------------------------------------------------------- C6
  await check("C6", "the project survives a reload from deep inside", async () => {
    await page.locator(".pm-trigger").click();
    await settle(500);
    await shot("project-menu-switch-back");
    const exampleItem = page.getByRole("menuitemradio", { name: /Example/i }).first();
    if (!(await exampleItem.count())) throw new Error("no menuitemradio matching /Example/i in the project menu");
    await exampleItem.click();
    await settle(1000);
    await railClick("Findings");
    await settle(1000);
    await say("back in the example project, on Findings (before reload)");
    await shot("example-findings-before-reload");

    await page.reload({ waitUntil: "domcontentloaded" });
    await settle(1500);
    const after = await say("after reloading from deep inside the example project");
    await shot("example-findings-after-reload");

    const pass = /Example/i.test(after.project ?? "") && (after.railCurrent ?? "").startsWith("Findings");
    const detail = `project=${after.project} rail=${after.railCurrent} url=${after.url}`;
    if (!pass) throw new Error(detail);
    return detail;
  });

  // ---------------------------------------------------------------- C7
  await check("C7", "the machine pages lead back", async () => {
    const missing = [];
    for (const path of ["/gesture-check", "/air-ink"]) {
      await gotoSafe(`${WALK_URL}${path}`);
      await settle(1000);
      await shot(path.slice(1));
      const hasLink = await page.locator('a[href="/workspace"]').count();
      log(`   ${path}: a[href="/workspace"] count=${hasLink}`);
      if (!hasLink) missing.push(path);
      if (path === "/gesture-check") {
        // C19, measured in place: the banner names the camera buttons, so
        // they must be on the first screen, not two chart-heights down.
        const box = await page.evaluate(() => {
          const el = [...document.querySelectorAll("button")].find((b) => /try hand gestures/i.test(b.textContent || ""));
          return el ? Math.round(el.getBoundingClientRect().bottom) : null;
        });
        const ok = box !== null && box <= 900;
        const detail = box === null ? "no 'Try hand gestures' control found" : `control ends at ${box}px of 900`;
        results.push({ id: "C19", description: "the camera controls are on the first screen of /gesture-check", status: ok ? "PASS" : "FAIL", detail });
        log(`\n${ok ? "PASS" : "FAIL"} C19 -- the camera controls are on the first screen of /gesture-check\n   ${detail}`);
      }
    }
    if (missing.length) throw new Error(`no a[href="/workspace"] found on: ${missing.join(", ")}`);
    return "both machine pages link back to /workspace";
  });
} catch (err) {
  fatal = err;
  log(`\nFATAL -- the walkthrough could not continue: ${err && err.stack ? err.stack : err}`);
}

// ---------------------------------------------------------------- C8 (informational; never fails)
log("\n## C8 -- console / network issues seen (informational only, does not affect pass/fail)");
const distinctIssues = [...new Set(consoleIssues)];
for (const line of distinctIssues) log("   " + line);
results.push({
  id: "C8",
  description: "console/network issues seen",
  status: "INFO",
  detail: `${distinctIssues.length} distinct issue(s)`,
});

// ---------------------------------------------------------------- summary
log("\n## Summary");
for (const r of results) {
  log(`${r.status.padEnd(4)} ${r.id.padEnd(3)} ${r.description}${r.detail ? `  (${r.detail})` : ""}`);
}
if (fatal) log(`\nFATAL ${String(fatal.message ?? fatal)}`);

const anyFail = fatal != null || results.some((r) => r.status === "FAIL");

const reportPath = join(WALK_OUT, "report.txt");
writeFileSync(reportPath, out.join("\n"));
console.log(`\nFull report written to ${reportPath}`);

await browser.close();
process.exit(anyFail ? 1 : 0);
