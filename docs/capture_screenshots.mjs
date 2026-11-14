#!/usr/bin/env node
// Regenerates the screenshots in docs/img/ from the demo server. No dependencies: it drives Chrome over the DevTools protocol.
//
//   node docs/capture_screenshots.mjs                 # starts its own demo server and Chrome, writes docs/img/*.png
//   CHROME_PATH=/path/to/chrome node docs/capture_screenshots.mjs
//
// Needs Node 22+ (built-in WebSocket and fetch), Python, and Chrome, Chromium or Edge.
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const OUT = join(ROOT, "docs", "img");
// A neutral folder name, so no screenshot shows the path of the person who makes them.
const DEMO_DIR = process.platform === "win32" ? "C:/chief-demo" : "/tmp/chief-demo";
const SERVER_PORT = 8793, DEBUG_PORT = 9333, WIDTH = 1280, SCALE = 1.5;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function findChrome() {
  const candidates = [process.env.CHROME_PATH,
    "C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"];
  const found = candidates.find((p) => p && existsSync(p));
  if (!found) throw new Error("Chrome, Chromium or Edge not found: set CHROME_PATH");
  return found;
}

async function waitHttp(url, tries = 80) {
  for (let i = 0; i < tries; i++) {
    try { if ((await fetch(url)).ok) return; } catch { /* not up yet */ }
    await sleep(250);
  }
  throw new Error(`${url} did not come up`);
}

class Page {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map();
    ws.onmessage = (e) => { const m = JSON.parse(e.data); const p = this.pending.get(m.id);
      if (p) { this.pending.delete(m.id); m.error ? p.rej(new Error(m.error.message)) : p.res(m.result); } }; }
  send(method, params = {}) { return new Promise((res, rej) => { const id = ++this.id; this.pending.set(id, { res, rej });
    this.ws.send(JSON.stringify({ id, method, params })); }); }
  async eval(expression) { const r = await this.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || "evaluation failed"); return r.result.value; }
  async waitFor(expression, tries = 80) { for (let i = 0; i < tries; i++) { if (await this.eval(expression)) return; await sleep(150); }
    throw new Error(`timed out waiting for: ${expression}`); }
  async shot(name, clip) {
    const { data } = await this.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true, ...(clip ? { clip: { ...clip, scale: 1 } } : {}) });
    writeFileSync(join(OUT, name), Buffer.from(data, "base64")); console.log("wrote", name);
  }
  // Rectangle around everything from the element `jsA` to the element `jsB` (both document-order expressions).
  async rectBetween(jsA, jsB, pad = 8) {
    const r = await this.eval(`(() => { const a = (${jsA}), b = (${jsB}); if (!a || !b) return null;
      const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
      return { x: 0, y: ra.top + scrollY, width: innerWidth, height: rb.bottom - ra.top }; })()`);
    if (!r) throw new Error(`no elements for ${jsA} / ${jsB}`);
    return { x: 0, y: Math.max(0, r.y - pad), width: WIDTH, height: r.height + 2 * pad };
  }
  // Rectangle (document coordinates) around the first element matching `js`, an expression returning an Element, with some padding.
  async rect(js, pad = 6) {
    const r = await this.eval(`(() => { const e = (${js}); if (!e) return null; const b = e.getBoundingClientRect();
      return { x: b.left + scrollX, y: b.top + scrollY, width: b.width, height: b.height }; })()`);
    if (!r) throw new Error(`no element for ${js}`);
    const x = Math.max(0, r.x - pad), y = Math.max(0, r.y - pad);
    return { x, y, width: Math.min(WIDTH - x, r.width + 2 * pad), height: r.height + 2 * pad };
  }
}

// Collapse the long artifact panels so each card shows the decision area, then return the card.
const compact = (title) => `(() => { const c = ${card(title)}; c.querySelectorAll("details[open]").forEach((d) => (d.open = false)); return c; })()`;
const card = (title) => `[...document.querySelectorAll("article.feat")].find(a => a.querySelector("h3")?.textContent.includes(${JSON.stringify(title)}))`;

async function main() {
  mkdirSync(OUT, { recursive: true });
  const profile = mkdtempSync(join(tmpdir(), "chief-shots-"));
  const server = spawn("python", [join(ROOT, "app", "server.py"), "--demo", "--port", String(SERVER_PORT)],
    { stdio: "ignore", env: { ...process.env, CHIEF_DEMO_DIR: DEMO_DIR } });
  const chrome = spawn(findChrome(), ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--hide-scrollbars",
    `--remote-debugging-port=${DEBUG_PORT}`, `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });
  const cleanup = () => { try { chrome.kill(); } catch {} try { server.kill(); } catch {} try { rmSync(profile, { recursive: true, force: true }); } catch {}
    if (existsSync(join(DEMO_DIR, ".chief-demo"))) { try { rmSync(DEMO_DIR, { recursive: true, force: true }); } catch {} } };
  process.on("exit", cleanup);
  try {
    await waitHttp(`http://127.0.0.1:${SERVER_PORT}/`);
    await waitHttp(`http://127.0.0.1:${DEBUG_PORT}/json/version`);
    const target = await (await fetch(`http://127.0.0.1:${DEBUG_PORT}/json/new?about:blank`, { method: "PUT" })).json();
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((r) => (ws.onopen = r));
    const page = new Page(ws);
    await page.send("Page.enable");
    await page.send("Runtime.enable");
    await page.send("Emulation.setDeviceMetricsOverride", { width: WIDTH, height: 900, deviceScaleFactor: SCALE, mobile: false });
    await page.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "dark" }] });
    await page.send("Page.navigate", { url: `http://127.0.0.1:${SERVER_PORT}/?lang=en` });
    await page.waitFor(`document.querySelectorAll("article.feat").length >= 5`);
    await sleep(600);

    // 1. the dashboard, as it opens
    await page.shot("dashboard.png", { x: 0, y: 0, width: WIDTH, height: 1000 });
    // 2-4. one card per stage of the pipeline, with the long artifact panels collapsed so the decision area is what shows
    await page.shot("request-spec.png", await page.rect(compact("Two-factor login")));        // open questions turned into a form
    await page.shot("request-plan.png", await page.rect(compact("Retry failed webhooks")));   // plan checked by an independent validator
    await page.shot("request-review.png", await page.rect(compact("Cart total")));           // reviewed, tests re-run, findings to decide on
    // 5. work in progress
    await page.shot("request-working.png", await page.rect(card("CSV export")));
    // 6. session to-dos and 7. workers: from the section heading to the end of its content
    const heading = (text) => `[...document.querySelectorAll("main h2")].find((h) => h.textContent.includes(${JSON.stringify(text)}))`;
    await page.shot("session-todos.png", await page.rectBetween(heading("Session to-dos"), `document.querySelector("#pends")`));
    await page.shot("workers.png", await page.rectBetween(heading("Workers"), `document.querySelector("#workers")`));
    // 8-9. dialogs (their own box, no padding: they scroll inside)
    await page.eval(`document.querySelector("#newBtn").click()`);
    await sleep(500);
    await page.shot("new-request.png", await page.rect(`document.querySelector("#newF")`, 0));
    await page.eval(`document.querySelector("#newF").close()`);
    await page.eval(`document.querySelector("#setBtn").click()`);
    await page.waitFor(`document.querySelector("#setD").open && document.querySelectorAll("#setBody table tr").length > 3`);
    await sleep(500);
    await page.shot("settings.png", await page.rect(`document.querySelector("#setD")`, 0));
    await page.eval(`document.querySelector("#setD").close()`);
    // 10-11. the session pill opens the sessions and costs dialog: this session, then (scrolled down) the past ones
    await page.eval(`document.querySelector("#sessBtn").click()`);
    await page.waitFor(`document.querySelector("#costD").open && document.querySelectorAll("#costBody .cgrid").length > 0`);
    await sleep(500);
    await page.shot("costs.png", await page.rect(`document.querySelector("#costD")`, 0));
    await page.eval(`(() => { const d = document.querySelector("#costBody details.cl"); if (d) d.open = true; document.querySelector("#pastSessions").scrollIntoView(); })()`);
    await sleep(400);
    await page.shot("costs-past-sessions.png", await page.rect(`document.querySelector("#costD")`, 0));
    await page.eval(`document.querySelector("#costD").close()`);
    // 12-13. the Projects tab: the list, then one project's page (as much of it as fits a tall screen)
    await page.eval(`location.hash = "#/projects"`);
    await page.waitFor(`!document.querySelector("#projectsView").hidden && document.querySelector("#projectsView").textContent.includes("Storefront")`);
    await sleep(900);
    await page.eval(`scrollTo(0, 0)`);
    const tall = async (sel, max) => { const r = await page.rect(sel, 0); return { x: 0, y: 0, width: WIDTH, height: Math.min(max, r.y + r.height + 24) }; };
    await page.shot("projects.png", await tall(`document.querySelector("#projectsView")`, 1000));
    await page.eval(`location.hash = "#/project/Storefront"`);
    await page.waitFor(`!document.querySelector("#projectView").hidden && document.querySelector("#projectView").textContent.length > 400`);
    await sleep(900);
    await page.eval(`scrollTo(0, 0)`);
    await page.shot("project-page.png", await tall(`document.querySelector("#projectView")`, 1500));
    ws.close();
  } finally {
    cleanup();
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
