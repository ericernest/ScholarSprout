import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import test from "node:test";

const root = resolve(import.meta.dirname, "../..");
const staticRoot = join(root, "gateway", "static");

const contracts = {
  "index.html": ["hero-card", "开始体验", "/library", "/settings"],
  "chat.html": ["chat-form", "mode-menu", "paper-mode-input", "discussion-context-bar", "/static/app.js"],
  "domain-onboarding/index.html": ["section-nav", "topbar-retry-button", "cancel-button", "sidebar-resizer", "/static/domain-onboarding/app.js"],
  "library/index.html": ["library-nav", "paper-import", "folder-tree", "paper-note-dialog", "/static/library/app.js"],
  "paper-reading/index.html": ["paper-intake", "paper-workbench", "reading-chat-form", "fork-create-button", "paper-note-button", "/static/paper-reading/app.js"],
  "settings/index.html": ["settings-form", "embedding-model-name", "mineru-base-url", "data-dir", "/static/settings/app.js"]
};

test("every Vue surface maps to a preserved v1 functional contract", () => {
  const registry = readFileSync(join(root, "webui", "src", "config", "surfaces.ts"), "utf8");
  for (const relativePath of Object.keys(contracts)) {
    const publicPath = relativePath === "index.html" ? "/static/index.html" : `/static/${relativePath}`;
    assert.match(registry, new RegExp(publicPath.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
});

for (const [relativePath, markers] of Object.entries(contracts)) {
  test(`${relativePath} keeps its required controls and scripts`, () => {
    const html = readFileSync(join(staticRoot, ...relativePath.split("/")), "utf8");
    for (const marker of markers) assert.ok(html.includes(marker), `${relativePath} is missing ${marker}`);
    assert.ok(!html.includes("NoviceSynapse"), `${relativePath} still exposes the retired brand`);
  });
}

test("gateway keeps stable API routes while serving the Vue build", () => {
  const app = readFileSync(join(root, "gateway", "app.py"), "utf8");
  for (const route of ["/app", "/settings", "/library", "/app/paper-reading", "/app/domain-onboarding"]) {
    assert.ok(app.includes(`@app.get("${route}")`), `missing route ${route}`);
  }
  assert.ok(app.includes('FRONTEND_DIR = STATIC_DIR / "app-v2"'));
  assert.ok(app.includes("legacy_path"), "source-checkout fallback was removed");
});

test("Vue compatibility layer preserves the previous surface design", () => {
  const overrides = readFileSync(join(root, "webui", "public", "styles", "legacy-overrides.css"), "utf8");
  const guide = readFileSync(join(root, "webui", "src", "components", "ProductGuide.vue"), "utf8");
  const home = readFileSync(join(root, "gateway", "static", "index.html"), "utf8");
  assert.ok(home.includes("研见 · SeeFurther"));
  assert.ok(home.includes("See Further into Research."));
  assert.ok(!overrides.includes(".chat-page { max-width"));
  assert.ok(!overrides.includes("focus-within"));
  assert.ok(!overrides.includes("outline: 3px"));
  assert.ok(!guide.includes("本地版不会额外启动前端服务"));
});
