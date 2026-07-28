const API_ENDPOINT = "/domain_onboarding";
const PAPER_ENDPOINT = "/paper_reading";
const STORAGE_KEY = "domain_onboarding_workspace_v1_3";
const DRAFT_KEY = "domain_onboarding_draft_query";
const STATUS_LABELS = {
  ok: ["质量通过", "passed"],
  quality_warning: ["可用 · 建议复核", "warning"],
  quality_failed: ["质量门槛未通过", "failed"],
};
const PRIORITY_LABELS = { core: "核心", recommended: "推荐", optional: "可选", extended: "拓展" };
const ROLE_LABELS = { survey: "综述", foundational: "奠基", method: "方法", evaluation: "评测", application: "应用", frontier: "前沿", other: "补充" };
const DIMENSION_LABELS = { structure: "结构", paper_validity: "论文真实性", paper_relevance: "论文相关性", evidence_grounding: "证据支撑", topic_coverage: "领域覆盖", development_coherence: "发展脉络", learning_path: "学习路线", goal_alignment: "目标匹配" };

const state = { result: null, indexes: null, activePriority: "all", request: null, loadingTimer: null };
const $ = (id) => document.getElementById(id);
const create = (tag, className = "", text = "") => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== "") node.textContent = String(text);
  return node;
};

boot();

function boot() {
  $("onboarding-form").addEventListener("submit", submitOnboarding);
  $("new-plan-button").addEventListener("click", showIntake);
  $("retry-button").addEventListener("click", showIntake);
  $("restore-button").addEventListener("click", restoreSavedResult);
  const query = new URLSearchParams(window.location.search).get("query") || localStorage.getItem(DRAFT_KEY) || "";
  $("domain-query").value = query;
  localStorage.removeItem(DRAFT_KEY);
  showRestoreOffer();
  if (query) window.setTimeout(() => $("onboarding-form").requestSubmit(), 80);
}

function buildRequest() {
  const query = $("domain-query").value.trim();
  const background = splitValues($("profile-background").value);
  const goal = $("profile-goal").value.trim();
  const weeks = Number($("profile-weeks").value);
  const metadata = { preference: $("profile-preference").value };
  if (background.length) {
    metadata.background = background;
    metadata.known_concepts = background;
  }
  if (goal) metadata.goal = goal;
  if (Number.isInteger(weeks) && weeks > 0) metadata.time_budget_weeks = weeks;
  return {
    session_id: getSessionId(),
    content: query,
    user_id: "local-web",
    metadata,
  };
}

async function submitOnboarding(event) {
  event.preventDefault();
  const request = buildRequest();
  if (!request.content) return;
  state.request = request;
  showView("loading-view");
  startLoadingProgress();
  try {
    const response = await fetch(API_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
    const envelope = await parseResponse(response);
    let payload = envelope?.content ?? envelope;
    if (typeof payload === "string") payload = JSON.parse(payload);
    if (!payload || typeof payload !== "object") throw new Error("后端未返回可识别的领域入门结果。");
    if (!payload.domain || !payload.learning_path) {
      throw new Error(payload.error || statusError(payload.status));
    }
    state.result = payload;
    state.indexes = buildIndexes(payload);
    saveWorkspace();
    renderWorkbench();
  } catch (error) {
    showError(error.message || String(error));
  } finally {
    stopLoadingProgress();
  }
}

async function parseResponse(response) {
  let payload;
  try { payload = await response.json(); }
  catch { throw new Error(`后端返回了无法解析的响应（HTTP ${response.status}）`); }
  if (!response.ok) throw new Error(payload?.detail || `请求失败（HTTP ${response.status}）`);
  return payload;
}

function buildIndexes(result) {
  const indexes = {
    papersById: new Map((result.papers || []).map((paper) => [paper.paper_id, paper])),
    stagesByPaper: new Map(), stepsByPaper: new Map(), evidenceByPaper: new Map(), prerequisitesByPaper: new Map(),
  };
  addReverseLinks(indexes.stagesByPaper, result.development_stages, "related_paper_ids");
  addReverseLinks(indexes.stepsByPaper, result.learning_path, "paper_ids");
  addReverseLinks(indexes.evidenceByPaper, result.evidence_claims, "supporting_paper_ids");
  addReverseLinks(indexes.prerequisitesByPaper, result.prerequisites, "related_paper_ids");
  return indexes;
}

function addReverseLinks(index, items = [], key) {
  items.forEach((item, position) => (item[key] || []).forEach((paperId) => {
    if (!index.has(paperId)) index.set(paperId, []);
    index.get(paperId).push({ item, position });
  }));
}

function renderWorkbench() {
  const result = state.result;
  showView("workbench-view");
  $("workspace-status").textContent = `${result.domain} · 学习中`;
  $("result-domain").textContent = result.domain;
  $("result-meta").textContent = `${result.policy_version || "policy unknown"} · ${result.papers?.length || 0} papers`;
  $("result-summary").textContent = result.text || "";
  renderQualityBadge();
  renderProfile();
  renderNavigation();
  renderPrerequisites();
  renderLandscape();
  renderStages();
  renderLearningPath();
  renderPaperFilters();
  renderPapers();
  renderEvidence();
  renderQualityDetails();
  window.scrollTo({ top: 0, behavior: "auto" });
}

function renderQualityBadge() {
  const quality = state.result.quality || {};
  const [label, className] = STATUS_LABELS[state.result.status] || [state.result.status || "未知状态", "warning"];
  const badge = $("quality-badge");
  badge.className = `quality-badge ${className}`;
  badge.replaceChildren(create("span", "", label), create("strong", "", quality.score == null ? "—" : `${Math.round(quality.score * 100)}%`), create("small", "", `阈值 ${Math.round((quality.threshold || 0) * 100)}%`));
}

function renderProfile() {
  const profile = state.result.learner_profile || {};
  const container = $("profile-summary");
  container.replaceChildren();
  [...(profile.background || []), PRIORITY_LABELS[profile.preference] || preferenceLabel(profile.preference)].filter(Boolean).forEach((value) => container.append(create("span", "profile-chip", value)));
  if (profile.goal) container.append(create("p", "", profile.goal));
  if (profile.time_budget_weeks) container.append(create("p", "", `${profile.time_budget_weeks} 周学习预算`));
}

function renderNavigation() {
  const nav = $("section-navigation");
  nav.replaceChildren();
  [["overview-section", "领域全景"], ["stages-section", "发展阶段"], ["path-section", "学习路线"], ["quality-section", "质量与修复"]].forEach(([id, label]) => {
    const button = create("button", "", label);
    button.type = "button";
    button.addEventListener("click", () => $(id).scrollIntoView({ behavior: "smooth", block: "start" }));
    nav.append(button);
  });
}

function renderPrerequisites() {
  const container = $("prerequisite-list");
  container.replaceChildren();
  (state.result.prerequisites || []).forEach((item) => {
    const card = create("article", "prerequisite");
    card.append(create("strong", "", item.name), create("p", "", item.why_needed || ""));
    const tags = create("div", "mini-list");
    (item.key_points || []).forEach((value) => tags.append(create("span", "tag", value)));
    card.append(tags); container.append(card);
  });
}

function renderLandscape() {
  const landscape = state.result.current_landscape || {};
  const grid = create("div", "landscape-grid");
  grid.append(listCard("当前问题", landscape.problems), listCard("主要子方向", landscape.subdirections));
  $("landscape-content").replaceChildren(grid);
}

function listCard(title, values = []) {
  const card = create("article", "landscape-card");
  card.append(create("h3", "", title));
  const list = create("ul"); values.forEach((value) => list.append(create("li", "", value))); card.append(list); return card;
}

function renderStages() {
  const container = $("stage-timeline"); container.replaceChildren();
  (state.result.development_stages || []).forEach((stage, index) => {
    const card = create("article", "timeline-card");
    card.append(create("p", "panel-label", `Stage ${index + 1}`), create("h3", "", stage.name), create("p", "", stage.summary));
    if (stage.motivation) card.append(create("p", "", `动机：${stage.motivation}`));
    const tags = create("div", "mini-list");
    [...(stage.core_concepts || []), ...(stage.main_techniques || [])].forEach((value) => tags.append(create("span", "tag", value)));
    card.append(tags, paperLinkRow(stage.related_paper_ids)); container.append(card);
  });
}

function renderLearningPath() {
  const container = $("learning-path"); container.replaceChildren();
  (state.result.learning_path || []).forEach((step) => {
    const card = create("article", "step-card"); card.id = `learning-step-${step.step}`;
    card.append(create("div", "step-number", step.step));
    const body = create("div"); body.append(create("h3", "", step.goal));
    const tags = create("div", "mini-list"); (step.topics || []).forEach((value) => tags.append(create("span", "tag", value))); body.append(tags);
    const columns = create("div", "step-columns"); columns.append(listBlock("学习活动", step.activities), listBlock("完成标准", step.completion_criteria));
    body.append(columns, create("p", "", `预期产出：${step.expected_outcome || "—"}`), paperLinkRow(step.paper_ids)); card.append(body); container.append(card);
  });
}

function listBlock(title, values = []) { const box = create("div"); box.append(create("strong", "", title)); const list = create("ul"); values.forEach((v) => list.append(create("li", "", v))); box.append(list); return box; }

function paperLinkRow(ids = []) {
  const row = create("div", "mini-list");
  ids.forEach((paperId) => {
    const paper = state.indexes.papersById.get(paperId); if (!paper) return;
    const button = create("button", "tag", `论文·${truncate(paper.title, 28)}`); button.type = "button";
    button.addEventListener("click", () => focusPaper(paperId)); row.append(button);
  });
  return row;
}

function renderPaperFilters() {
  const priorities = ["all", "core", "recommended", "optional", "extended"];
  const labels = { all: "全部", ...PRIORITY_LABELS };
  const row = $("paper-filters"); row.replaceChildren();
  priorities.forEach((priority) => {
    const button = create("button", priority === state.activePriority ? "active" : "", labels[priority]); button.type = "button";
    button.addEventListener("click", () => { state.activePriority = priority; renderPaperFilters(); renderPapers(); }); row.append(button);
  });
}

function renderPapers() {
  const papers = (state.result.papers || []).filter((paper) => state.activePriority === "all" || paper.reading_priority === state.activePriority);
  $("paper-count").textContent = `${papers.length} 篇`;
  const container = $("paper-list"); container.replaceChildren();
  papers.forEach((paper) => {
    const card = create("article", "paper-card"); card.id = `paper-${safeId(paper.paper_id)}`;
    const meta = create("div", "paper-meta");
    [PRIORITY_LABELS[paper.reading_priority], ROLE_LABELS[paper.paper_role], paper.year, paper.is_canonical ? "Canonical" : ""].filter(Boolean).forEach((value) => meta.append(create("span", "tag", value)));
    card.append(meta, create("h3", "", paper.title), create("p", "", (paper.authors || []).join("、")));
    const references = collectPaperReferences(paper.paper_id);
    const guided = references.find((item) => item.contribution || item.reading_focus?.length);
    if (guided?.contribution) card.append(create("p", "", guided.contribution));
    if (guided?.reading_focus?.length) { const list = create("ul", "reading-focus"); guided.reading_focus.forEach((value) => list.append(create("li", "", value))); card.append(list); }
    const actions = create("div", "paper-actions");
    const source = create("a", "", "查看来源"); source.href = paper.url; source.target = "_blank"; source.rel = "noopener noreferrer"; actions.append(source);
    const read = create("button", "", "导入论文精读"); read.type = "button"; read.disabled = !paperPdfUrl(paper); read.addEventListener("click", () => importPaperForReading(paper)); actions.append(read);
    card.append(actions); container.append(card);
  });
}

function collectPaperReferences(paperId) {
  return [...(state.result.development_stages || []).flatMap((stage) => stage.representative_papers || []), ...(state.result.learning_path || []).flatMap((step) => step.papers || [])].filter((paper) => paper.paper_id === paperId);
}

function renderEvidence() {
  const container = $("evidence-list"); container.replaceChildren();
  (state.result.evidence_claims || []).forEach((claim) => {
    const card = create("article", "evidence-card"); card.append(create("p", "", claim.claim), create("small", "", evidenceLabel(claim.support_type)));
    const row = create("div", "mini-list"); (claim.supporting_paper_ids || []).forEach((id) => { const paper = state.indexes.papersById.get(id); const button = create("button", "", paper ? truncate(paper.title, 24) : id); button.type = "button"; button.addEventListener("click", () => focusPaper(id)); row.append(button); });
    card.append(row); container.append(card);
  });
}

function renderQualityDetails() {
  const quality = state.result.quality || {};
  $("quality-summary").textContent = `${Math.round((quality.score || 0) * 100)}% · ${quality.retry_status || "not_needed"}`;
  const container = $("quality-content"); container.replaceChildren();
  const grid = create("div", "quality-grid"); Object.entries(quality.dimensions || {}).forEach(([key, value]) => { const item = create("div", "metric"); item.append(create("span", "", DIMENSION_LABELS[key] || key), create("strong", "", `${Math.round(value * 100)}%`)); grid.append(item); }); container.append(grid);
  (quality.hard_gates || []).forEach((gate) => { const item = create("div", "issue", `${gate.gate}：${gate.status}${gate.score == null ? "" : ` · ${Math.round(gate.score * 100)}% / ${Math.round((gate.threshold || 0) * 100)}%`}`); container.append(item); });
  (quality.issues || []).forEach((issue) => { const item = create("div", "issue"); item.append(create("strong", "", `${issue.severity.toUpperCase()} · ${DIMENSION_LABELS[issue.dimension] || issue.dimension}`), create("p", "", issue.message), create("small", "", `位置：${issue.target_path} · 建议：${issue.recommended_action}`)); container.append(item); });
  const attempts = state.result.quality_attempts || [];
  if (attempts.length > 1) {
    container.append(create("h3", "", "质量尝试"));
    attempts.forEach((attempt) => container.append(create("div", "issue", `#${attempt.attempt_number} ${attempt.source} · ${Math.round((attempt.quality?.score || 0) * 100)}% · ${Math.round(attempt.duration_ms || 0)} ms`)));
  }
  const repair = state.result.repair_record;
  if (repair?.triggered) { const title = create("h3", "", "修复记录"); container.append(title); (repair.actions || []).forEach((action) => container.append(create("div", "issue", `${action.action_type} · ${action.status} · ${action.changed_paths?.join(", ") || "无字段变更"}`))); if (repair.decision) container.append(create("p", "", `选择结果：${repair.decision.decision}，分数变化 ${repair.decision.score_delta >= 0 ? "+" : ""}${repair.decision.score_delta.toFixed(3)}`)); }
}

function focusPaper(paperId) {
  if (state.activePriority !== "all") { state.activePriority = "all"; renderPaperFilters(); renderPapers(); }
  document.querySelectorAll(".paper-card").forEach((node) => node.classList.remove("is-highlighted"));
  const target = $(`paper-${safeId(paperId)}`); if (!target) return; target.classList.add("is-highlighted"); target.scrollIntoView({ behavior: "smooth", block: "center" });
}

async function importPaperForReading(paper) {
  const pdfUrl = paperPdfUrl(paper); if (!pdfUrl) return;
  toast(`正在导入《${truncate(paper.title, 30)}》…`);
  try {
    const response = await fetch(PAPER_ENDPOINT, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "upload_paper", session_id: "", paper_id: "", content: "", pdf_url: pdfUrl, metadata: { source: "domain_onboarding", domain: state.result.domain, source_paper_id: paper.paper_id } }) });
    const envelope = await parseResponse(response); let payload = envelope?.content ?? envelope; if (typeof payload === "string") payload = JSON.parse(payload);
    const paperId = payload?.data?.paper_id; if (!paperId) throw new Error(payload?.message || "精读接口未返回 paper_id。");
    localStorage.removeItem("paper_reading_session_id"); localStorage.setItem("paper_reading_paper_id", paperId); window.location.href = "/app/paper-reading";
  } catch (error) { toast(`论文导入失败：${error.message}`, true); }
}

function paperPdfUrl(paper) {
  if (paper.arxiv_id) return `https://arxiv.org/pdf/${paper.arxiv_id}.pdf`;
  const value = String(paper.url || ""); if (!/arxiv\.org/i.test(value)) return "";
  return value.replace("/abs/", "/pdf/").replace(/(?<!\.pdf)$/i, ".pdf");
}

function showRestoreOffer() {
  const saved = loadWorkspace(); if (!saved?.result?.domain) return;
  $("restore-copy").textContent = `${saved.result.domain} · ${new Date(saved.saved_at).toLocaleString("zh-CN")}`; $("restore-card").hidden = false;
}

function restoreSavedResult() {
  const saved = loadWorkspace(); if (!saved?.result) return;
  state.result = saved.result; state.request = saved.request || null; state.indexes = buildIndexes(state.result); renderWorkbench();
}

function saveWorkspace() { localStorage.setItem(STORAGE_KEY, JSON.stringify({ schema_version: "1.3", saved_at: new Date().toISOString(), request: state.request, result: state.result })); }
function loadWorkspace() { try { const value = JSON.parse(localStorage.getItem(STORAGE_KEY)); return value?.schema_version === "1.3" ? value : null; } catch { return null; } }
function showIntake() { showView("intake-view"); $("workspace-status").textContent = "领域学习工作台"; $("domain-query").focus(); }
function showError(message) { $("error-message").textContent = message; showView("error-view"); $("workspace-status").textContent = "生成失败"; }
function showView(id) { ["intake-view", "loading-view", "error-view", "workbench-view"].forEach((name) => { $(name).hidden = name !== id; }); }

function startLoadingProgress() {
  let index = 0; const titles = ["正在解析需求与学习者画像", "正在从多视角规划领域", "正在检索并验证代表论文", "正在生成受论文约束的学习内容", "正在检查质量并尝试定向修复"];
  const advance = () => { const items = [...$("loading-steps").children]; items.forEach((item, i) => item.classList.toggle("active", i === index)); $("loading-title").textContent = titles[index]; index = Math.min(index + 1, items.length - 1); };
  advance(); state.loadingTimer = window.setInterval(advance, 5200);
}
function stopLoadingProgress() { if (state.loadingTimer) window.clearInterval(state.loadingTimer); state.loadingTimer = null; }
function statusError(status) { return ({ invalid_input: "请输入有效领域。", planning_failed: "领域规划失败，请稍后重试。", retrieval_failed: "没有检索到可验证的论文。", generation_failed: "内容生成失败。", timeout: "请求超时，可尝试缩小领域范围。", cancelled: "请求已取消。" })[status] || `领域入门失败：${status || "unknown"}`; }
function evidenceLabel(type) { return ({ abstract_explicit: "摘要直接支持", metadata_inference: "元数据推断", background_synthesis: "背景综合" })[type] || type; }
function preferenceLabel(value) { return ({ balanced: "平衡学习", theory_first: "理论优先", experiment_first: "实验优先" })[value] || value; }
function splitValues(value) { return String(value || "").split(/[,，;；\n]/).map((item) => item.trim()).filter(Boolean); }
function safeId(value) { return String(value || "").replace(/[^a-zA-Z0-9_-]/g, "-"); }
function truncate(value, length) { const text = String(value || ""); return text.length > length ? `${text.slice(0, length - 1)}…` : text; }
function toast(message, isError = false) { const item = create("div", `toast${isError ? " error" : ""}`, message); $("toast-region").append(item); window.setTimeout(() => item.remove(), 4800); }
function getSessionId() { let value = localStorage.getItem("novicesynapse_session_id"); if (!value) { value = globalThis.crypto?.randomUUID?.() || `web-${Date.now()}`; localStorage.setItem("novicesynapse_session_id", value); } return value; }
