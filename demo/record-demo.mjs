/**
 * Deterministic browser-only capture skeleton for AI Project Finder demo mode.
 *
 * Prerequisites:
 *   python3 app.py --demo
 *   npm install --no-save playwright
 *
 * This script never starts the server and expects the isolated demo endpoint.
 * It records only the browser viewport; post-production and final export are
 * intentionally handled separately.
 */

import { copyFile, mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const baseUrl = process.env.APF_DEMO_URL || "http://127.0.0.1:4390";
const locale = String(process.env.APF_DEMO_LOCALE || "en").toLowerCase().startsWith("zh")
  ? "zh-CN"
  : "en";
const localeLabel = locale === "zh-CN" ? "ZH" : "EN";
const searchTerm = locale === "zh-CN" ? "阿特拉斯 发布" : "atlas launch";
const outputDir =
  process.env.APF_DEMO_OUTPUT ||
  path.join(os.tmpdir(), "ai-project-finder-demo-capture");
const namedOutput = path.join(outputDir, `AI_Project_Finder_Demo_${localeLabel}_raw.webm`);

await mkdir(outputDir, { recursive: true });

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1600, height: 900 },
  deviceScaleFactor: 1,
  reducedMotion: "no-preference",
  recordVideo: {
    dir: outputDir,
    size: { width: 1600, height: 900 },
  },
});

const page = await context.newPage();
const video = page.video();

await page.route("**/*", async (route) => {
  const url = new URL(route.request().url());
  if (["127.0.0.1", "localhost"].includes(url.hostname)) {
    await route.continue();
  } else {
    await route.abort();
  }
});

await page.goto(`${baseUrl}/?theme=light&lang=${encodeURIComponent(locale)}`, { waitUntil: "networkidle" });
await page.waitForFunction(() => document.body.dataset.demo === "true");
await page.waitForFunction((expected) => document.documentElement.lang === expected, locale);
await page.evaluate(() => {
  const cursor = document.createElement("div");
  cursor.id = "demoCursor";
  cursor.setAttribute("aria-hidden", "true");
  cursor.style.cssText = [
    "position:fixed",
    "left:0",
    "top:0",
    "z-index:9999",
    "width:22px",
    "height:22px",
    "border:1px solid currentColor",
    "border-radius:50%",
    "pointer-events:none",
    "mix-blend-mode:difference",
    "color:white",
    "transform:translate(-40px,-40px)",
    "transition:transform .46s cubic-bezier(.22,.8,.24,1),width .15s ease,height .15s ease",
  ].join(";");
  document.body.appendChild(cursor);
});

async function moveTo(locator) {
  const box = await locator.boundingBox();
  if (!box) throw new Error("Demo target is not visible");
  await page.evaluate(
    ({ x, y }) => {
      const cursor = document.querySelector("#demoCursor");
      cursor.style.transform = `translate(${x - 11}px,${y - 11}px)`;
    },
    { x: box.x + box.width / 2, y: box.y + box.height / 2 },
  );
  await page.waitForTimeout(560);
}

async function demoClick(locator) {
  await moveTo(locator);
  await locator.click();
  await page.waitForTimeout(220);
}

await page.waitForTimeout(1800);

const search = page.locator("#search");
await demoClick(search);
await search.pressSequentially(searchTerm, { delay: 110 });
await page.waitForTimeout(850);
await demoClick(page.locator("#searchSubmit"));
await page.waitForTimeout(2300);

await demoClick(page.locator("#themeBtn"));
await page.waitForTimeout(1900);

await demoClick(page.locator('[data-view="projects"]'));
await page.waitForTimeout(1900);

const claudeFilter = page.locator('[data-source="claude"]');
await demoClick(claudeFilter);
await page.waitForTimeout(1700);

const firstOpenButton = page.locator('#resultList [data-open-action="session"]').first();
await firstOpenButton.scrollIntoViewIfNeeded();
await page.waitForTimeout(600);
await demoClick(firstOpenButton);
await page.locator("#toast").waitFor({ state: "visible" });
await page.waitForTimeout(2600);

await page.evaluate((activeLocale) => {
  const localized = activeLocale === "zh-CN"
    ? {
        eyebrow: "本地 · 私有 · 跨 AI",
        headline: "AI PROJECT<br>FINDER.",
        description: "从一个本地索引中搜索 AI 项目历史。"
      }
    : {
        eyebrow: "Local · Private · Cross-AI",
        headline: "AI PROJECT<br>FINDER.",
        description: "Search local AI project history from one index."
      };
  const endCard = document.createElement("div");
  endCard.style.cssText = [
    "position:fixed",
    "inset:0",
    "z-index:9998",
    "display:grid",
    "place-items:center",
    "background:rgba(11,11,11,.94)",
    "color:#f1f1ef",
    "font-family:Geist,system-ui,sans-serif",
    "opacity:0",
    "transition:opacity .7s ease",
  ].join(";");
  endCard.innerHTML = `
    <div style="width:min(820px,78vw);border-top:1px solid rgba(255,255,255,.25);padding-top:28px">
      <div style="font:500 11px/1.4 'Geist Mono',monospace;letter-spacing:.18em;color:#ff3b24;text-transform:uppercase">${localized.eyebrow}</div>
      <div style="margin-top:18px;font-size:66px;font-weight:800;line-height:.92;letter-spacing:-.055em">${localized.headline}</div>
      <div style="margin-top:28px;font:400 15px/1.7 'Geist Mono',monospace;color:#a6a6a0">${localized.description}</div>
      <div style="margin-top:34px;font:500 12px/1.4 'Geist Mono',monospace;letter-spacing:.08em">github.com/stevensilu/ai-project-finder</div>
    </div>`;
  document.body.appendChild(endCard);
  requestAnimationFrame(() => {
    endCard.style.opacity = "1";
  });
}, locale);
await page.waitForTimeout(2800);

await context.close();
await browser.close();

const rawPath = await video.path();
await copyFile(rawPath, namedOutput);
console.log(`Raw Playwright capture: ${namedOutput}`);
