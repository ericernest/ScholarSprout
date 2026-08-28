import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import test from "node:test";

const root = resolve(import.meta.dirname, "../..");
const staticRoot = join(root, "gateway", "static");

const contracts = {
  "index.html": ["hero-card", "开始体验", "/library", "进入教程", "/app?tutorial=1"],
  "chat.html": ["chat-form", "mode-menu", "paper-mode-input", "discussion-context-bar", "/static/app.js"],
  "domain-onboarding/index.html": ["section-nav", "topbar-retry-button", "cancel-button", "sidebar-resizer", "/static/domain-onboarding/app.js"],
  "library/index.html": ["library-nav", "paper-import", "folder-tree", "paper-note-dialog", "/static/library/app.js"],
  "paper-reading/index.html": ["paper-intake", "paper-workbench", "reading-chat-form", "fork-create-button", "paper-note-button", "/static/paper-reading/app.js"],
  "settings/index.html": ["settings-form", "embedding-model-name", "data-dir", "/static/settings/app.js"]
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

test("first-use tutorial overlays the real surfaces with synchronized detail context", () => {
  const tutorial = readFileSync(join(staticRoot, "tutorial-runtime.js"), "utf8");
  const paperApp = readFileSync(join(staticRoot, "paper-reading", "app.js"), "utf8");
  const chat = readFileSync(join(staticRoot, "app.js"), "utf8");
  const home = readFileSync(join(staticRoot, "index.html"), "utf8");
  for (const forbidden of ["预定义", "预制", "固定", "预生成"]) {
    assert.ok(!tutorial.includes(forbidden), `tutorial still contains ${forbidden}`);
  }
  assert.ok(!tutorial.includes("不会写入业务数据库"));
  assert.ok(tutorial.includes("跳过教程"));
  assert.ok(tutorial.includes("智能体 Agent"));
  assert.ok(tutorial.includes("这个综述认为的未来可做的有哪些"));
  assert.ok(tutorial.includes("sf-tour-spotlight"));
  assert.ok(tutorial.includes("demoDomainSnapshot"));
  assert.ok(tutorial.includes("demoReadingPaper"));
  assert.ok(tutorial.includes('companion: ".inspector"'));
  assert.ok(tutorial.includes("selectTutorialReaderText"));
  assert.ok(tutorial.includes('id: "reading-note"'));
  assert.ok(tutorial.includes('target: "#paper-note-button"'));
  assert.ok(tutorial.includes('companion: "#paper-note-drawer"'));
  assert.ok(tutorial.indexOf('id: "reading-selection"') < tutorial.indexOf('id: "reading-note"'));
  assert.ok(tutorial.indexOf('id: "reading-note"') < tutorial.indexOf('id: "domain-mode"'));
  assert.ok(tutorial.includes("data-tour-anchor='reading-map-explain'"));
  assert.ok(tutorial.includes("openReadingMap?.()"));
  assert.ok(paperApp.includes("window.SeeFurtherTutorial.openReadingMap"));
  assert.ok(tutorial.indexOf("上传一篇论文") < tutorial.indexOf("提出一个领域"));
  assert.ok(tutorial.includes("prepareTutorialPaperUpload"));
  assert.ok(tutorial.includes("tour-paper-card"));
  assert.ok(tutorial.includes("也可以从领域入门的论文详情下载 PDF，并从这里进入论文精读"));
  assert.ok(chat.includes('fetch("/api/tutorial/status"'));
  assert.ok(chat.includes('window.location.replace("/app?tutorial=1")'));
  assert.ok(home.includes('id="start-experience"'));
  assert.ok(home.includes('id="open-tutorial"'));
  assert.ok(home.includes('href="/app?new=1"'));
  assert.ok(home.includes('/app?tutorial=1'));
  for (const retiredLabel of ["Domain Onboarding", "Paper Reading", "Research Chat", "配置向导"]) {
    assert.ok(!home.includes(retiredLabel));
  }
  assert.ok(!readFileSync(join(root, "webui", "vite.config.ts"), "utf8").includes('tutorial: page("tutorial")'));
});

test("model operations share the local configuration guard", () => {
  const guard = readFileSync(join(staticRoot, "model-guard.js"), "utf8");
  const chat = readFileSync(join(staticRoot, "chat.html"), "utf8");
  const paper = readFileSync(join(staticRoot, "paper-reading", "index.html"), "utf8");
  const domain = readFileSync(join(staticRoot, "domain-onboarding", "index.html"), "utf8");
  assert.ok(guard.includes('fetch("/api/config"'));
  assert.ok(guard.includes("setup_complete"));
  assert.ok(guard.includes("先完成模型配置"));
  for (const html of [chat, paper, domain]) assert.ok(html.includes("/static/model-guard.js"));
});

test("every guided surface loads the same tutorial runtime before its app", () => {
  for (const relativePath of ["chat.html", "domain-onboarding/index.html", "library/index.html", "paper-reading/index.html", "settings/index.html"]) {
    const html = readFileSync(join(staticRoot, ...relativePath.split("/")), "utf8");
    const tutorialAt = html.indexOf("/static/tutorial-runtime.js");
    assert.ok(tutorialAt >= 0, `${relativePath} does not load the real-page tutorial runtime`);
    const ownAppAt = html.lastIndexOf("app.js");
    assert.ok(tutorialAt < ownAppAt, `${relativePath} initializes the app before tutorial isolation`);
  }
});

test("chat exposes a dedicated interrupt control and uses the wide workspace", () => {
  const html = readFileSync(join(staticRoot, "chat.html"), "utf8");
  const javascript = readFileSync(join(staticRoot, "app.js"), "utf8");
  const styles = readFileSync(join(staticRoot, "style.css"), "utf8");
  assert.ok(html.includes('id="stop-button"'));
  assert.ok(javascript.includes("async function stopActiveGeneration"));
  assert.ok(javascript.includes("/cancel"));
  assert.ok(styles.includes("width: calc(100% - clamp(24px, 3.2vw, 52px))"));
  assert.ok(styles.includes("max-width: none"));
});

test("external-channel answers refresh incrementally without rebuilding every message", () => {
  const javascript = readFileSync(join(staticRoot, "app.js"), "utf8");
  assert.ok(javascript.includes("window.setInterval(reloadWhenEnteringChat, 650)"));
  assert.ok(javascript.includes("patchPersistedMessages(history)"));
  assert.ok(javascript.includes("data-persisted-message-id") || javascript.includes("persistedMessageId"));
});

test("smart index uses an asymmetric twelve-column card rhythm", () => {
  const javascript = readFileSync(join(staticRoot, "paper-reading", "app.js"), "utf8");
  const styles = readFileSync(join(staticRoot, "paper-reading", "styles.css"), "utf8");
  assert.ok(javascript.includes('classList.toggle("is-featured", index === 0)'));
  assert.ok(javascript.includes('classList.toggle("is-wide-tail"'));
  assert.ok(styles.includes("grid-template-columns: repeat(12,minmax(0,1fr))"));
  assert.ok(styles.includes(".section-guide-item.is-featured"));
});

test("settings explains API key storage concisely", () => {
  const html = readFileSync(join(staticRoot, "settings", "index.html"), "utf8");
  assert.ok(html.includes("API Key 仅保存在本地后端。"));
  assert.ok(!html.includes("API Key 不会在页面加载时回传"));
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
  assert.ok(overrides.includes(".library-shell .brand,"));
  assert.ok(overrides.includes(".library-shell .brand::before,"));
  assert.ok(!guide.includes("本地版不会额外启动前端服务"));
});
