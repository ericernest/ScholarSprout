const API_ENDPOINT = "/paper_reading";
const WORKSPACE_PATH = "/app/paper-reading";
const isDedicatedWorkspace = window.location.pathname === WORKSPACE_PATH;
const STORAGE = {
  session: "paper_reading_session_id",
  paper: "paper_reading_paper_id",
  section: "paper_reading_current_section",
  scroll: "paper_reading_scroll_top",
};

const SKILLS = [
  { id: "reading.method_analyst", label: "Method Analyst", short: "方法拆解", prompt: "请拆解当前章节的问题定义、方法 Pipeline、每一步动机和依赖关系。" },
  { id: "reading.critique_agent", label: "Critique Agent", short: "批判审稿", prompt: "请以 Peer Review 标准评价实验、基线、消融和论断支撑情况。" },
  { id: "reading.math_verifier", label: "Math Verifier", short: "公式验证", prompt: "请检查当前章节的数学推导，给出直觉、逐步推导、数值例子和可能的跳跃。" },
  { id: "reading.code_reviewer", label: "Code Reviewer", short: "复现审查", prompt: "请检查实现描述与可复现性，评估复现难度并列出潜在坑点。" },
  { id: "reading.domain_expert", label: "Domain Expert", short: "领域定位", prompt: "请把当前内容放到领域发展脉络中，解释相关概念、技术路线和承袭关系。" },
  { id: "reading.writing_coach", label: "Writing Coach", short: "写作教练", prompt: "请分析论文的写作结构、论证逻辑和可复用写作模板。" },
  { id: "reading.idea_generator", label: "Idea Generator", short: "创新想法", prompt: "请基于论文局限生成 3-5 个后续研究想法，并说明动机、难度和预期贡献。" },
  { id: "reading.cross_paper_linker", label: "Cross Paper Linker", short: "跨论文连接", prompt: "请联系相关论文，指出继承、互补、冲突和潜在研究路线。" },
];

const NODE_COLORS = {
  Problem: "#ff7f88", Method: "#66f5d6", Module: "#74d59c", Baseline: "#ffbb6e",
  Metric: "#8bd0ff", Dataset: "#b493ff", Experiment: "#48d8dc", Figure: "#91a6b0",
  Concept: "#87a7ff", Limitation: "#ff738f", Claim: "#ffe082", RelatedWork: "#c39bff", Insight: "#ffd35c",
};

const state = {
  sessionId: "", paperId: "", paper: null, pdfUrl: "", hasPdf: false,
  currentSection: "", progress: {}, activeSkills: [], skillOutputs: [],
  revealedKgElements: [], queryKgElements: [], selectedNode: null,
  selectedText: "", activeForkSessionId: "", uploadSummary: null,
  sessionState: "", restored: false, busy: false,
};

const $ = (id) => document.getElementById(id);
const create = (tag, className = "", text = "") => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
};

boot();

function boot() {
  if (isDedicatedWorkspace) {
    $("paper-intake").hidden = true;
    $("paper-workbench").hidden = true;
    $("new-paper-button").hidden = false;
    $("workspace-status").textContent = "正在恢复论文…";
  }
  renderSkillControls();
  renderQuickActions();
  renderLegend();
  bindIntake();
  bindWorkbench();
  bindReader();
  bindKg();
  bindFork();
  restoreLocalState();
}

function bindIntake() {
  $("pdf-file-input").addEventListener("change", (event) => {
    const file = event.target.files?.[0];
    if (file) importLocalPdf(file);
  });
  const dropzone = $("upload-dropzone");
  ["dragenter", "dragover"].forEach((name) => dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    dropzone.classList.add("is-dragging");
  }));
  ["dragleave", "drop"].forEach((name) => dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    dropzone.classList.remove("is-dragging");
  }));
  dropzone.addEventListener("drop", (event) => {
    const file = event.dataTransfer?.files?.[0];
    if (file) importLocalPdf(file);
  });
  $("url-import-form").addEventListener("submit", (event) => {
    event.preventDefault();
    importPdfUrl($("paper-url-input").value);
  });
  $("paper-search-form").addEventListener("submit", searchPapers);
  $("paper-ready-card").addEventListener("click", enterWorkbench);
  $("paper-ready-card").addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      enterWorkbench();
    }
  });
}

function bindWorkbench() {
  $("new-paper-button").addEventListener("click", showIntake);
  $("refresh-session-button").addEventListener("click", refreshSessionState);
  $("pause-button").addEventListener("click", pauseReading);
  $("resume-button").addEventListener("click", resumeReading);
  $("progress-button").addEventListener("click", refreshProgress);
  $("reading-chat-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("reading-chat-input");
    const question = input.value.trim();
    if (!question) return;
    input.value = "";
    startReading(question);
  });
  window.addEventListener("beforeunload", saveBeforeUnload);
}

function bindReader() {
  document.querySelectorAll("[data-reader-mode]").forEach((button) => {
    button.addEventListener("click", () => setReaderMode(button.dataset.readerMode));
  });
  $("analyze-section-button").addEventListener("click", () => startReading("请深入分析当前章节的核心问题、方法、证据和潜在局限。"));
  $("previous-section-button").addEventListener("click", () => moveSection(-1));
  $("next-section-button").addEventListener("click", () => moveSection(1));
  $("pdf-fit-select").addEventListener("change", renderPdf);
  $("structured-reader").addEventListener("mouseup", captureSelection);
  $("selection-toolbar").addEventListener("click", handleSelectionAction);
  document.addEventListener("mousedown", (event) => {
    if (!event.target.closest("#selection-toolbar") && !event.target.closest("#structured-reader")) {
      $("selection-toolbar").hidden = true;
    }
  });
}

function bindKg() {
  $("kg-query-type").addEventListener("change", () => {
    const type = $("kg-query-type").value;
    $("kg-path-fields").hidden = type !== "path";
    $("kg-question-input").required = type !== "neighbors";
  });
  $("kg-query-form").addEventListener("submit", queryKg);
  $("reset-kg-button").addEventListener("click", () => {
    state.queryKgElements = [];
    $("kg-answer").hidden = true;
    $("kg-reasoning").replaceChildren();
    renderKg(state.revealedKgElements);
  });
}

function bindFork() {
  SKILLS.forEach((skill) => {
    const option = create("option", "", skill.label);
    option.value = skill.id;
    $("fork-skill-select").append(option);
  });
  $("fork-skill-select").value = "reading.math_verifier";
  $("fork-create-button").addEventListener("click", createFork);
  $("fork-merge-button").addEventListener("click", mergeFork);
  $("fork-close-button").addEventListener("click", closeFork);
}

async function callPaperReading(body, options = {}) {
  const response = await fetch(API_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: body.session_id ?? state.sessionId ?? "",
      paper_id: body.paper_id ?? state.paperId ?? "",
      content: body.content ?? "",
      metadata: body.metadata ?? {},
      ...body,
    }),
    ...options,
  });
  let envelope;
  try {
    envelope = await response.json();
  } catch {
    throw new Error(`后端返回了无法解析的响应（HTTP ${response.status}）`);
  }
  if (!response.ok) {
    throw new Error(envelope?.detail || `请求失败（HTTP ${response.status}）`);
  }
  let payload = envelope?.content ?? envelope;
  if (typeof payload === "string") {
    try { payload = JSON.parse(payload); } catch { throw new Error(payload); }
  }
  if (!payload || typeof payload !== "object") throw new Error("后端没有返回论文精读数据。");
  if (payload.status === "error") throw new Error(payload.message || payload.error || `${body.action} 执行失败`);
  return { envelope, payload };
}

async function searchPapers(event) {
  event.preventDefault();
  const query = $("paper-search-input").value.trim();
  if (!query) return;
  setBusy(true, "正在检索论文", "正在查询 arXiv、Semantic Scholar 与降级来源…");
  try {
    const { payload } = await callPaperReading({
      action: "search_paper",
      search_query: query,
      search_source: $("paper-search-source").value,
      search_max_results: Number($("paper-search-limit").value) || 6,
    });
    renderSearchResults(payload.data?.papers || []);
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(false);
  }
}

function renderSearchResults(papers) {
  const container = $("search-results");
  container.replaceChildren();
  papers.forEach((paper) => {
    const card = create("article", "paper-result");
    const tags = create("div", "tag-row");
    [paper.source, paper.year, paper.venue].filter(Boolean).forEach((value) => tags.append(create("span", "", String(value))));
    card.append(tags, create("h3", "", paper.title || "未命名论文"));
    card.append(create("p", "", (paper.authors || []).join("、") || "作者信息暂无"));
    card.append(create("p", "", truncate(paper.abstract || "暂无摘要", 230)));
    const actions = create("div", "paper-result-actions");
    const importButton = create("button", "", paper.pdf_url ? "导入精读" : "缺少 PDF");
    importButton.type = "button";
    importButton.disabled = !paper.pdf_url;
    importButton.addEventListener("click", () => importPdfUrl(paper.pdf_url));
    actions.append(importButton);
    if (paper.url || paper.pdf_url) {
      const link = create("a", "", "查看来源");
      link.href = paper.url || paper.pdf_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      actions.append(link);
    }
    card.append(actions);
    container.append(card);
  });
  $("search-result-count").textContent = `${papers.length} 篇`;
  $("search-results-section").hidden = false;
  $("search-results-section").scrollIntoView({ behavior: "smooth", block: "start" });
  if (!papers.length) toast("没有找到可展示的论文，可以尝试 arXiv ID 或直接上传 PDF。");
}

async function importLocalPdf(file) {
  if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
    toast("请选择 PDF 文件。", true);
    return;
  }
  $("upload-file-label").textContent = `${file.name} · ${formatBytes(file.size)}`;
  setBusy(true, "正在解析 PDF", "上传、章节重排与完整知识图谱构建可能需要一些时间…");
  try {
    const pdfData = await fileToBase64(file);
    const { payload } = await callPaperReading({
      action: "upload_paper",
      session_id: "",
      paper_id: "",
      pdf_data: pdfData,
      metadata: { original_filename: file.name, size_bytes: file.size },
    });
    await acceptUploadedPaper(payload, "本地 PDF");
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function importPdfUrl(rawUrl) {
  const pdfUrl = normalizePdfUrl(rawUrl);
  if (!pdfUrl) {
    toast("请输入有效的 PDF 或 arXiv 链接。", true);
    return;
  }
  setBusy(true, "正在导入在线论文", "下载 PDF、解析章节并构建知识图谱…");
  try {
    const { payload } = await callPaperReading({
      action: "upload_paper", session_id: "", paper_id: "", pdf_url: pdfUrl,
    });
    await acceptUploadedPaper(payload, "在线 PDF");
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function acceptUploadedPaper(payload, sourceLabel) {
  const data = payload.data || {};
  localStorage.removeItem(STORAGE.session);
  localStorage.removeItem(STORAGE.section);
  localStorage.removeItem(STORAGE.scroll);
  state.paperId = data.paper_id || "";
  state.sessionId = "";
  state.currentSection = data.sections?.[0]?.section_id || "";
  state.activeSkills = [];
  state.progress = {};
  state.uploadSummary = data.kg_build || {};
  state.restored = false;
  if (!state.paperId) throw new Error("上传成功响应中缺少 paper_id。");
  persistState();
  await loadPaperDetail();
  renderReadyCard(sourceLabel);
  $("search-results-section").hidden = true;
  $("paper-ready-section").hidden = false;
  $("paper-ready-section").scrollIntoView({ behavior: "smooth", block: "center" });
}

async function loadPaperDetail() {
  const { payload } = await callPaperReading({ action: "get_paper_detail", paper_id: state.paperId });
  const data = payload.data || {};
  state.paper = data.paper || null;
  state.pdfUrl = data.pdf_url || "";
  state.hasPdf = Boolean(data.has_pdf && state.pdfUrl);
  const initialKg = data.initial_kg || {};
  if (initialKg.cytoscape_elements?.length) {
    state.revealedKgElements = initialKg.cytoscape_elements;
    $("kg-stage-copy").textContent = `已恢复图谱：${initialKg.current_stage || "abstract"} · ${initialKg.node_count ?? 0} 节点 / ${initialKg.edge_count ?? 0} 关系`;
  }
  if (!state.currentSection) state.currentSection = state.paper?.sections?.[0]?.section_id || "";
  persistState();
}

function renderReadyCard(sourceLabel = "已保存论文") {
  if (!state.paper) return;
  $("ready-title").textContent = state.paper.title || "未命名论文";
  $("ready-authors").textContent = (state.paper.authors || []).join("、") || "作者信息暂无";
  $("ready-abstract").textContent = state.paper.abstract || "解析完成，点击进入工作台查看结构化正文。";
  $("ready-sections").textContent = state.paper.sections?.length || 0;
  $("ready-nodes").textContent = state.uploadSummary?.new_nodes ?? "—";
  $("ready-edges").textContent = state.uploadSummary?.new_edges ?? "—";
  $("ready-source").textContent = state.restored ? "继续阅读" : sourceLabel;
}

async function enterWorkbench() {
  if (!state.paper) return;
  if (!$("paper-intake").hidden) {
    $("paper-ready-card").classList.add("is-entering");
    await delay(320);
  }
  $("paper-intake").hidden = true;
  $("paper-workbench").hidden = false;
  $("new-paper-button").hidden = false;
  $("workspace-status").textContent = "论文精读 · 阅读中";
  $("paper-ready-card").classList.remove("is-entering");
  renderPaperWorkspace();
  window.scrollTo({ top: 0, behavior: "auto" });
  if (state.restored && state.sessionId) {
    if (state.sessionState === "paused") await resumeReading(false);
    await startReading("请继续上次的阅读，并概括当前章节接下来的理解重点。");
  } else if (!state.sessionId) {
    await startReading("请先给出这篇论文的阅读导览，并分析当前章节的核心结构。");
  }
}

function showIntake() {
  window.location.href = "/app?mode=paper_reading";
}

function renderPaperWorkspace() {
  const paper = state.paper || {};
  $("paper-ribbon-title").textContent = paper.title || "未命名论文";
  $("paper-ribbon-meta").textContent = `${paper.source || "upload"} · ${paper.year || "年份未知"} · ${paper.sections?.length || 0} sections`;
  $("side-paper-title").textContent = paper.title || "未命名论文";
  $("side-paper-authors").textContent = (paper.authors || []).join("、") || "作者信息暂无";
  const tags = $("side-paper-tags");
  tags.replaceChildren();
  [...(paper.categories || []).slice(0, 3), paper.venue].filter(Boolean).forEach((tag) => tags.append(create("span", "", tag)));
  renderOutline();
  renderSections();
  renderProgress();
  syncSkillControls();
  renderPdf();
  renderKg(state.revealedKgElements);
  updateSessionBadge();
}

function renderOutline() {
  const container = $("paper-outline");
  container.replaceChildren();
  const sections = state.paper?.sections || [];
  const statuses = state.progress?.section_statuses || {};
  sections.forEach((section, index) => {
    const button = create("button", `outline-item${section.section_id === state.currentSection ? " is-active" : ""}`);
    button.type = "button";
    button.style.paddingLeft = `${Math.min(Math.max(section.level || 1, 1), 4) * 0.45}rem`;
    const icon = create("span", "outline-state", statuses[section.section_id] === "completed" ? "●" : String(index + 1).padStart(2, "0"));
    button.append(icon, create("span", "outline-title", section.title || `Section ${index + 1}`));
    button.addEventListener("click", () => selectSection(section.section_id, true));
    container.append(button);
  });
  $("outline-count").textContent = String(sections.length);
}

function renderSections() {
  const reader = $("structured-reader");
  reader.replaceChildren();
  const sections = state.paper?.sections || [];
  if (!sections.length) {
    reader.append(create("div", "empty-state", "没有解析到结构化章节，可切换到 PDF 原文。"));
    return;
  }
  sections.forEach((section, index) => {
    const article = create("section", `paper-section${section.section_id === state.currentSection ? " is-current" : ""}`);
    article.id = domSectionId(section.section_id);
    article.dataset.sectionId = section.section_id;
    const meta = create("div", "section-meta");
    meta.append(create("span", "", `Section ${String(index + 1).padStart(2, "0")}`));
    if (section.start_page) meta.append(create("span", "", `Page ${section.start_page}`));
    article.append(meta, create("h2", "", section.title || `Section ${index + 1}`));
    const paragraphs = section.paragraphs?.length ? section.paragraphs : splitParagraphs(section.content || "");
    paragraphs.forEach((paragraph) => article.append(create("p", "", paragraph)));
    reader.append(article);
  });
  requestAnimationFrame(() => {
    const savedScroll = Number(localStorage.getItem(STORAGE.scroll) || 0);
    if (state.restored && savedScroll) reader.scrollTop = savedScroll;
  });
  reader.addEventListener("scroll", () => localStorage.setItem(STORAGE.scroll, String(reader.scrollTop)), { passive: true });
}

function renderPdf() {
  const fit = $("pdf-fit-select").value || "width";
  const fragments = {
    width: "page=1&zoom=75",
    page: "page=1&zoom=55",
    100: "page=1&zoom=100",
  };
  const baseUrl = state.pdfUrl.split("#", 1)[0];
  const nextUrl = state.hasPdf ? `${baseUrl}#${fragments[fit] || fragments.width}` : "about:blank";
  if ($("pdf-frame").getAttribute("src") !== nextUrl) $("pdf-frame").src = nextUrl;
  $("pdf-frame").hidden = !state.hasPdf;
  $("pdf-empty").hidden = state.hasPdf;
}

function setReaderMode(mode) {
  const isPdf = mode === "pdf";
  $("workbench-grid").classList.toggle("is-pdf-mode", isPdf);
  $("structured-reader").hidden = isPdf;
  $("pdf-reader").hidden = !isPdf;
  $("pdf-fit-control").hidden = !isPdf || !state.hasPdf;
  $("previous-section-button").hidden = isPdf;
  $("analyze-section-button").hidden = isPdf;
  $("next-section-button").hidden = isPdf;
  document.querySelectorAll("[data-reader-mode]").forEach((button) => button.classList.toggle("is-active", button.dataset.readerMode === mode));
}

async function selectSection(sectionId, analyze) {
  state.currentSection = sectionId;
  persistState();
  renderOutline();
  document.querySelectorAll(".paper-section").forEach((section) => section.classList.toggle("is-current", section.dataset.sectionId === sectionId));
  document.getElementById(domSectionId(sectionId))?.scrollIntoView({ behavior: "smooth", block: "start" });
  $("composer-context").textContent = `当前：${sectionTitle(sectionId)}`;
  if (analyze) await startReading(`请精读“${sectionTitle(sectionId)}”，说明核心内容、论证结构和需要重点理解的概念。`);
}

function moveSection(offset) {
  const sections = state.paper?.sections || [];
  if (!sections.length) return;
  const current = Math.max(0, sections.findIndex((item) => item.section_id === state.currentSection));
  const target = sections[Math.min(sections.length - 1, Math.max(0, current + offset))];
  if (target) selectSection(target.section_id, true);
}

async function startReading(content, sessionId = state.sessionId) {
  if (!state.paperId || state.busy) return;
  setBusy(true, "AI 正在精读", `正在分析 ${sectionTitle(state.currentSection) || "当前内容"}…`);
  try {
    const { payload } = await callPaperReading({
      action: "start_reading", session_id: sessionId || "", paper_id: state.paperId,
      target_section: state.currentSection || "", content,
      metadata: { viewport_section: state.currentSection, selected_text: state.selectedText },
    });
    applyReadingPayload(payload);
    toast("章节分析已更新。");
    return payload;
  } catch (error) {
    toast(error.message, true);
    return null;
  } finally {
    setBusy(false);
  }
}

function applyReadingPayload(payload) {
  const data = payload.data || {};
  const session = payload.session || {};
  state.sessionId = session.session_id || data.session_id || state.sessionId;
  state.sessionState = session.state || "active";
  state.currentSection = data.current_section || session.current_section || state.currentSection;
  state.activeSkills = session.active_skills || state.activeSkills;
  state.progress = payload.progress || state.progress;
  state.skillOutputs = payload.skill_outputs || [];
  state.revealedKgElements = data.revealed_kg?.cytoscape_elements || state.revealedKgElements;
  state.queryKgElements = [];
  persistState();
  appendAnalysis(data.agent_response || "后端已完成本次阅读操作。", data);
  renderSkillOutputs(state.skillOutputs, $("analysis-feed"));
  renderProgress();
  renderOutline();
  syncSkillControls();
  updateSessionBadge();
  $("kg-stage-copy").textContent = `当前展开阶段：${data.revealed_kg?.current_stage || "general"} · ${data.revealed_kg?.node_count ?? 0} 节点 / ${data.revealed_kg?.edge_count ?? 0} 关系`;
  renderKg(state.revealedKgElements);
}

function appendAnalysis(text, metadata = {}) {
  const card = create("article", "analysis-card");
  const header = create("header");
  header.append(create("strong", "", "Synapse Copilot"), create("span", "", metadata.duration_ms ? `${Math.round(metadata.duration_ms)} ms` : "Agent"));
  card.append(header, renderMarkdown(text));
  $("analysis-feed").append(card);
  $("analysis-feed").scrollTop = $("analysis-feed").scrollHeight;
}

function renderSkillOutputs(outputs, target) {
  outputs.forEach((output) => {
    const card = create("article", "skill-output-card");
    const header = create("header");
    header.append(create("strong", "", output.skill_name || skillLabel(output.skill_id)), create("span", "", output.output_type || output.parse_status || "Skill"));
    card.append(header);
    if (output.content && Object.keys(output.content).length) card.append(renderStructuredValue(output.content));
    else card.append(renderMarkdown(output.rendered || "Skill 已执行，但没有返回可展示内容。"));
    const candidates = output.kg_candidates;
    if (candidates?.nodes?.length || candidates?.edges?.length) {
      card.append(create("span", "count-pill", `${candidates.nodes?.length || 0} 个 KG 候选`));
    }
    target.append(card);
  });
}

function renderStructuredValue(value, depth = 0) {
  const container = create("div", "structured-output");
  if (depth > 4) {
    container.append(create("pre", "analysis-text", JSON.stringify(value, null, 2)));
    return container;
  }
  Object.entries(value || {}).forEach(([key, item]) => {
    const block = create("div", "value-block");
    block.append(create("strong", "", humanizeKey(key)));
    if (Array.isArray(item)) {
      if (!item.length) block.append(create("p", "", "暂无"));
      else {
        const list = create("ul");
        item.forEach((entry) => {
          const li = create("li");
          if (entry && typeof entry === "object") li.append(renderStructuredValue(entry, depth + 1));
          else li.textContent = String(entry);
          list.append(li);
        });
        block.append(list);
      }
    } else if (item && typeof item === "object") {
      block.append(renderStructuredValue(item, depth + 1));
    } else {
      block.append(create("p", "", item == null || item === "" ? "暂无" : String(item)));
    }
    container.append(block);
  });
  return container;
}

// Render the Markdown subset used by model answers without injecting raw HTML.
function renderMarkdown(source) {
  const root = create("div", "markdown-content");
  const lines = String(source || "").replace(/\r\n?/g, "\n").split("\n");
  let paragraph = [];
  let list = null;
  let listType = "";
  let code = null;

  const flushParagraph = () => {
    if (!paragraph.length) return;
    const node = create("p");
    appendInlineMarkdown(node, paragraph.join(" ").trim());
    root.append(node);
    paragraph = [];
  };
  const flushList = () => {
    if (list) root.append(list);
    list = null;
    listType = "";
  };
  const flushCode = () => {
    if (!code) return;
    const pre = create("pre", "markdown-code");
    pre.append(create("code", "", code.lines.join("\n")));
    root.append(pre);
    code = null;
  };

  lines.forEach((rawLine) => {
    const line = rawLine.trimEnd();
    if (line.trim().startsWith("```")) {
      flushParagraph();
      flushList();
      if (code) flushCode();
      else code = { lines: [] };
      return;
    }
    if (code) {
      code.lines.push(rawLine);
      return;
    }
    if (!line.trim()) {
      flushParagraph();
      flushList();
      return;
    }
    if (/^\s*(?:\*{3,}|-{3,}|_{3,})\s*$/.test(line)) {
      flushParagraph();
      flushList();
      root.append(create("hr"));
      return;
    }
    const heading = line.match(/^\s*(#{1,4})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      const node = create(`h${Math.min(heading[1].length + 2, 6)}`);
      appendInlineMarkdown(node, heading[2]);
      root.append(node);
      return;
    }
    const quote = line.match(/^\s*>\s?(.*)$/);
    if (quote) {
      flushParagraph();
      flushList();
      const node = create("blockquote");
      appendInlineMarkdown(node, quote[1]);
      root.append(node);
      return;
    }
    const bullet = line.match(/^\s*[-+*]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (bullet || ordered) {
      flushParagraph();
      const nextType = ordered ? "ol" : "ul";
      if (!list || listType !== nextType) {
        flushList();
        list = create(nextType);
        listType = nextType;
      }
      const item = create("li");
      appendInlineMarkdown(item, (bullet || ordered)[1]);
      list.append(item);
      return;
    }
    paragraph.push(line.trim());
  });
  flushParagraph();
  flushList();
  flushCode();
  if (!root.childNodes.length) root.append(create("p", "", "暂无内容。"));
  return root;
}

function appendInlineMarkdown(target, text) {
  const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|_[^_\n]+_)/g;
  let cursor = 0;
  for (const match of String(text || "").matchAll(pattern)) {
    if (match.index > cursor) target.append(document.createTextNode(text.slice(cursor, match.index)));
    const token = match[0];
    if (token.startsWith("`")) target.append(create("code", "", token.slice(1, -1)));
    else if (token.startsWith("**") || token.startsWith("__")) target.append(create("strong", "", token.slice(2, -2)));
    else target.append(create("em", "", token.slice(1, -1)));
    cursor = match.index + token.length;
  }
  if (cursor < text.length) target.append(document.createTextNode(text.slice(cursor)));
}

function renderSkillControls() {
  const switches = $("skill-switches");
  const quick = $("quick-actions");
  SKILLS.forEach((skill) => {
    const label = create("label", "skill-toggle");
    label.append(create("span", "", skill.label));
    const input = create("input");
    input.type = "checkbox";
    input.dataset.skillId = skill.id;
    input.addEventListener("change", () => toggleSkill(skill.id, input.checked, input));
    label.append(input, create("span", "toggle-ui"));
    switches.append(label);

    const button = create("button", "quick-action", skill.short);
    button.type = "button";
    button.addEventListener("click", () => runSkill(skill));
    quick.append(button);
  });
}

function renderQuickActions() {}

async function toggleSkill(skillId, active, input) {
  if (!state.sessionId) {
    input.checked = false;
    toast("首次章节分析完成后才能加载 Skill。", true);
    return;
  }
  setBusy(true, active ? "正在加载专家" : "正在卸载专家", skillLabel(skillId));
  try {
    const { payload } = await callPaperReading({
      action: active ? "load_skill" : "unload_skill", session_id: state.sessionId, skill_ids: [skillId],
    });
    state.activeSkills = payload.data?.active_skills || [];
    syncSkillControls();
    toast(payload.data?.message || "Skill 状态已更新。");
  } catch (error) {
    input.checked = !active;
    toast(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function runSkill(skill) {
  if (!state.sessionId) {
    toast("正在先初始化阅读会话，请稍候。");
    await startReading("请初始化本篇论文的阅读上下文。");
  }
  if (!state.sessionId) return;
  if (!state.activeSkills.includes(skill.id)) {
    try {
      const { payload } = await callPaperReading({ action: "load_skill", session_id: state.sessionId, skill_ids: [skill.id] });
      state.activeSkills = payload.data?.active_skills || state.activeSkills;
      syncSkillControls();
    } catch (error) {
      toast(error.message, true);
      return;
    }
  }
  await startReading(skill.prompt);
}

function syncSkillControls() {
  document.querySelectorAll("[data-skill-id]").forEach((input) => {
    input.checked = state.activeSkills.includes(input.dataset.skillId);
  });
  $("active-skill-count").textContent = `${state.activeSkills.length} Skills`;
}

async function pauseReading(showToast = true) {
  if (!state.sessionId) return toast("当前还没有可暂停的阅读会话。", true);
  setBusy(true, "正在保存阅读进度", "创建 checkpoint…");
  try {
    const { payload } = await callPaperReading({
      action: "pause_reading", session_id: state.sessionId,
      metadata: { viewport_section: state.currentSection, scroll_top: $("structured-reader").scrollTop, selected_node_id: state.selectedNode?.id || "" },
    });
    state.sessionState = "paused";
    updateSessionBadge();
    if (showToast) toast(payload.data?.message || "阅读进度已保存。");
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function resumeReading(showToast = true) {
  if (!state.sessionId) return toast("没有找到可恢复的阅读会话。", true);
  setBusy(true, "正在恢复会话", "同步章节、Skill 与 checkpoint…");
  try {
    const { payload } = await callPaperReading({ action: "resume_reading", session_id: state.sessionId });
    state.sessionState = "active";
    state.currentSection = payload.data?.current_section || state.currentSection;
    state.activeSkills = payload.data?.active_skills || state.activeSkills;
    await refreshProgress(false);
    renderOutline();
    syncSkillControls();
    updateSessionBadge();
    if (showToast) toast(payload.data?.message || "阅读已恢复。");
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function refreshSessionState(showToast = true) {
  if (!state.sessionId) return;
  try {
    const { payload } = await callPaperReading({ action: "get_session_state", session_id: state.sessionId });
    const data = payload.data || {};
    state.paperId = data.paper_id || state.paperId;
    state.sessionState = data.state || state.sessionState;
    state.activeSkills = data.active_skills || state.activeSkills;
    state.progress = payload.progress || state.progress;
    state.currentSection = state.progress?.current_position?.section_id || state.currentSection;
    persistState();
    renderProgress();
    renderOutline();
    syncSkillControls();
    updateSessionBadge();
    if (showToast) toast("会话状态已同步。");
  } catch (error) {
    if (showToast) toast(error.message, true);
    throw error;
  }
}

async function refreshProgress(showToast = true) {
  if (!state.sessionId) return;
  try {
    const { payload } = await callPaperReading({ action: "get_progress", session_id: state.sessionId });
    state.progress = payload.data?.progress || state.progress;
    state.currentSection = state.progress?.current_position?.section_id || state.currentSection;
    persistState();
    renderProgress();
    renderOutline();
    if (showToast) toast(payload.data?.formatted || "进度已刷新。");
  } catch (error) {
    if (showToast) toast(error.message, true);
  }
}

function renderProgress() {
  const progress = state.progress || {};
  const total = Number(progress.total_sections || state.paper?.sections?.length || 0);
  const completed = Array.isArray(progress.completed_sections) ? progress.completed_sections.length : Number(progress.completed_sections || 0);
  const percentage = Number(progress.percentage ?? (total ? completed / total * 100 : 0));
  $("progress-percentage").textContent = `${Math.round(percentage)}%`;
  $("progress-bar").style.width = `${Math.min(100, Math.max(0, percentage))}%`;
  $("progress-copy").textContent = `${completed}/${total} 章节 · ${sectionTitle(progress.current_position?.section_id || state.currentSection) || "尚未定位"}`;
}

function updateSessionBadge() {
  const labels = { active: "阅读中", paused: "已暂停", completed: "已完成" };
  $("session-state-badge").textContent = labels[state.sessionState] || (state.sessionId ? "已创建" : "未开始");
}

function captureSelection() {
  const selection = window.getSelection();
  const text = selection?.toString().trim();
  if (!text || text.length < 2) {
    $("selection-toolbar").hidden = true;
    return;
  }
  const anchor = selection.anchorNode?.parentElement;
  if (!anchor?.closest("#structured-reader")) return;
  state.selectedText = text.slice(0, 6000);
  const section = anchor.closest(".paper-section");
  if (section?.dataset.sectionId) state.currentSection = section.dataset.sectionId;
  const rect = selection.getRangeAt(0).getBoundingClientRect();
  const toolbar = $("selection-toolbar");
  toolbar.style.left = `${Math.max(8, Math.min(window.innerWidth - 430, rect.left))}px`;
  toolbar.style.top = `${Math.max(72, rect.top - 52)}px`;
  toolbar.hidden = false;
}

async function handleSelectionAction(event) {
  const action = event.target.closest("[data-selection-action]")?.dataset.selectionAction;
  if (!action) return;
  $("selection-toolbar").hidden = true;
  const quoted = `\n\n选中内容：\n${state.selectedText}`;
  if (action === "explain") await startReading(`请解释这段内容的直觉、上下文和关键假设。${quoted}`);
  if (action === "concept") await runSkill({ ...SKILLS.find((item) => item.id === "reading.domain_expert"), prompt: `请解释选中概念的定义、前置知识和领域脉络。${quoted}` });
  if (action === "formula") openFork("reading.math_verifier", "请对选中公式做直觉、逐步推导和数值例子三层分析。");
  if (action === "fork") openFork("reading.domain_expert", "请围绕选中内容进行深入探索。");
  if (action === "candidate") toast("已加入本页 KG 候选区；后端暂未提供确认写入 action。");
}

function openFork(skillId, question) {
  if (!state.sessionId) return toast("请先开始章节阅读，再创建 Fork。", true);
  $("fork-context-input").value = state.selectedText || sectionTitle(state.currentSection);
  $("fork-question-input").value = question || "请深入分析这段内容。";
  $("fork-skill-select").value = skillId || "reading.math_verifier";
  $("fork-output").hidden = true;
  $("fork-output").replaceChildren();
  $("fork-merge-button").hidden = true;
  state.activeForkSessionId = "";
  $("fork-panel").hidden = false;
  $("fork-question-input").focus();
  $("fork-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function closeFork() {
  $("fork-panel").hidden = true;
}

async function createFork() {
  const context = $("fork-context-input").value.trim();
  const question = $("fork-question-input").value.trim() || "请深入分析这段内容。";
  const skillId = $("fork-skill-select").value;
  setBusy(true, "正在创建探索分支", skillLabel(skillId));
  try {
    const { payload } = await callPaperReading({
      action: "fork", session_id: state.sessionId, paper_id: state.paperId,
      fork_context: context, fork_question: question, fork_skills: [skillId],
      metadata: { selected_text: context, source_section_id: state.currentSection },
    });
    state.activeForkSessionId = payload.data?.fork_session_id || "";
    if (!state.activeForkSessionId) throw new Error("Fork 响应缺少 fork_session_id。");
    const result = await callPaperReading({
      action: "start_reading", session_id: state.activeForkSessionId, paper_id: state.paperId,
      target_section: state.currentSection, content: `${question}\n\n上下文：${context}`,
    });
    const output = $("fork-output");
    output.replaceChildren(renderMarkdown(result.payload.data?.agent_response || "分支分析完成。"));
    renderSkillOutputs(result.payload.skill_outputs || [], output);
    output.hidden = false;
    $("fork-merge-button").hidden = false;
    toast("Fork 分支分析已完成。");
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function mergeFork() {
  if (!state.activeForkSessionId) return;
  setBusy(true, "正在合并探索结论", "把分支成果带回主阅读流…");
  try {
    const { payload } = await callPaperReading({
      action: "merge", session_id: state.sessionId, merge_session_id: state.activeForkSessionId,
    });
    const data = payload.data || {};
    state.activeSkills = [...new Set([...state.activeSkills, ...(data.merged_skills || [])])];
    const card = create("article", "fork-summary");
    card.append(create("strong", "", "Fork 结论已合并"));
    (data.key_findings || []).forEach((finding) => card.append(create("p", "", finding)));
    $("analysis-feed").append(card);
    syncSkillControls();
    closeFork();
    toast(data.message || "分支已合并。");
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function queryKg(event) {
  event?.preventDefault();
  if (!state.paperId) return;
  const type = $("kg-query-type").value;
  const question = $("kg-question-input").value.trim() || (type === "neighbors" ? "查看选中节点的邻域" : "");
  if (type === "neighbors" && !state.selectedNode?.id) return toast("请先选择一个图谱节点。", true);
  if (type === "path" && (!$("kg-source-input").value.trim() || !$("kg-target-input").value.trim())) return toast("路径查询需要起点和终点标签。", true);
  setBusy(true, "正在查询知识图谱", question || "提取关联证据…");
  try {
    const { payload } = await callPaperReading({
      action: "kg_query", session_id: state.sessionId, paper_id: state.paperId,
      kg_question: question, kg_query_type: type,
      kg_node_id: state.selectedNode?.id || "",
      kg_source_label: $("kg-source-input").value.trim(),
      kg_target_label: $("kg-target-input").value.trim(),
    });
    renderKgQueryResult(payload.data || {});
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(false);
  }
}

function renderKgQueryResult(data) {
  state.queryKgElements = data.cytoscape_elements || [];
  if (state.queryKgElements.length) renderKg(state.queryKgElements);
  const answer = $("kg-answer");
  answer.replaceChildren(create("h3", "", "KG 回答"), renderMarkdown(data.answer || "当前图谱没有足够证据。"));
  answer.hidden = false;
  const reasoning = $("kg-reasoning");
  reasoning.replaceChildren();
  (data.reasoning_paths || []).forEach((path) => {
    const card = create("button", "path-card", `${path.source_label || "?"} —${path.relation_label || path.relation || "关联"}→ ${path.target_label || "?"}`);
    card.type = "button";
    card.addEventListener("click", () => highlightPath(path));
    reasoning.append(card);
  });
  (data.evidence || []).forEach((item) => {
    const card = create("button", "evidence-card", `${item.label || "证据"} · ${item.section_id || "未知章节"}`);
    card.type = "button";
    card.addEventListener("click", () => jumpToSection(item.section_id));
    reasoning.append(card);
  });
}

function renderLegend() {
  const legend = $("kg-legend");
  ["Problem", "Method", "Module", "Dataset", "Experiment", "Limitation", "Insight"].forEach((type) => {
    const item = create("span");
    const dot = create("i", "legend-dot");
    dot.style.background = NODE_COLORS[type];
    item.append(dot, document.createTextNode(type));
    legend.append(item);
  });
}

function renderKg(elements) {
  const svg = $("kg-graph");
  svg.replaceChildren();
  const nodes = elements.filter((item) => !item.data?.source && (item.data?.id || item.data?.node_id));
  const edges = elements.filter((item) => item.data?.source && item.data?.target);
  $("kg-empty").hidden = nodes.length > 0;
  if (!nodes.length) return;
  const defs = svgNode("defs");
  const marker = svgNode("marker", { id: "kg-arrow", viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "5", markerHeight: "5", orient: "auto-start-reverse" });
  marker.append(svgNode("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "rgba(154,194,183,.55)" }));
  defs.append(marker);
  svg.append(defs);
  const positions = new Map();
  const centerX = 450, centerY = 190, radiusX = Math.min(350, 110 + nodes.length * 18), radiusY = Math.min(125, 70 + nodes.length * 6);
  nodes.forEach((item, index) => {
    const angle = -Math.PI / 2 + index * Math.PI * 2 / nodes.length;
    positions.set(item.data.id || item.data.node_id, { x: centerX + Math.cos(angle) * radiusX, y: centerY + Math.sin(angle) * radiusY });
  });
  edges.forEach((item) => {
    const source = positions.get(item.data.source), target = positions.get(item.data.target);
    if (!source || !target) return;
    svg.append(svgNode("line", {
      class: "kg-edge", x1: source.x, y1: source.y, x2: target.x, y2: target.y,
      "data-source": item.data.source, "data-target": item.data.target, "marker-end": "url(#kg-arrow)",
    }));
    const text = svgNode("text", { class: "kg-edge-label", x: (source.x + target.x) / 2, y: (source.y + target.y) / 2 - 5 });
    text.textContent = truncate(item.data.label || item.data.edge_type || "", 14);
    svg.append(text);
  });
  nodes.forEach((item) => {
    const data = { ...item.data, id: item.data.id || item.data.node_id };
    const position = positions.get(data.id);
    const group = svgNode("g", {
      class: `kg-node${state.selectedNode?.id === data.id ? " is-selected" : ""}`,
      transform: `translate(${position.x} ${position.y})`, "data-node-id": data.id, tabindex: "0", role: "button",
    });
    group.append(svgNode("circle", { r: "24", fill: NODE_COLORS[data.node_type] || "#87a7ff" }));
    const label = svgNode("text", { y: "39" });
    label.textContent = truncate(data.label || data.node_type || "Node", 18);
    group.append(label);
    group.addEventListener("click", () => selectKgNode(data));
    group.addEventListener("keydown", (event) => { if (event.key === "Enter") selectKgNode(data); });
    svg.append(group);
  });
}

function selectKgNode(data) {
  state.selectedNode = data;
  renderKg(state.queryKgElements.length ? state.queryKgElements : state.revealedKgElements);
  const detail = $("kg-node-detail");
  detail.replaceChildren(create("p", "panel-label", data.node_type || "Node"), create("h3", "", data.label || data.id));
  if (data.summary) detail.append(create("p", "muted-copy", data.summary));
  const properties = create("div", "node-properties");
  Object.entries(data.properties || {}).forEach(([key, value]) => properties.append(create("div", "", `${humanizeKey(key)}：${String(value)}`)));
  detail.append(properties);
  const actions = create("div", "paper-result-actions");
  const neighbors = create("button", "", "查看邻域");
  neighbors.type = "button";
  neighbors.addEventListener("click", () => { $("kg-query-type").value = "neighbors"; $("kg-question-input").value = `解释 ${data.label || "该节点"} 周围的关系`; queryKg(); });
  actions.append(neighbors);
  if (data.section_id) {
    const jump = create("button", "", "跳转正文");
    jump.type = "button";
    jump.addEventListener("click", () => jumpToSection(data.section_id));
    actions.append(jump);
  }
  detail.append(actions);
}

function highlightPath(path) {
  const ids = [path.source_id, path.target_id].filter(Boolean);
  document.querySelectorAll(".kg-node").forEach((node) => {
    node.style.opacity = !ids.length || ids.includes(node.dataset.nodeId) ? "1" : ".22";
  });
  document.querySelectorAll(".kg-edge").forEach((edge) => {
    const connected = ids.includes(edge.dataset.source) && ids.includes(edge.dataset.target);
    edge.style.opacity = !ids.length || connected ? "1" : ".12";
  });
  toast(`${path.source_label || "起点"} → ${path.target_label || "终点"}`);
}

function jumpToSection(sectionId) {
  if (!sectionId) return;
  setReaderMode("structured");
  state.currentSection = sectionId;
  renderOutline();
  document.getElementById(domSectionId(sectionId))?.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function restoreLocalState() {
  state.sessionId = localStorage.getItem(STORAGE.session) || "";
  state.paperId = localStorage.getItem(STORAGE.paper) || "";
  state.currentSection = localStorage.getItem(STORAGE.section) || "";
  if (!state.paperId && !state.sessionId) {
    if (isDedicatedWorkspace) window.location.replace("/app?mode=paper_reading");
    return;
  }
  setBusy(true, "正在检查上次阅读", "恢复论文与会话索引…");
  try {
    if (state.sessionId) await refreshSessionState(false);
    if (state.paperId) {
      await loadPaperDetail();
      state.restored = true;
      renderReadyCard("继续阅读");
      $("paper-ready-section").hidden = false;
      toast("已找到上次的阅读记录。");
    }
  } catch {
    localStorage.removeItem(STORAGE.session);
    if (!state.paperId) localStorage.removeItem(STORAGE.paper);
  } finally {
    setBusy(false);
  }
  if (isDedicatedWorkspace && state.paper) {
    await enterWorkbench();
  }
}

function persistState() {
  if (state.sessionId) localStorage.setItem(STORAGE.session, state.sessionId);
  else localStorage.removeItem(STORAGE.session);
  if (state.paperId) localStorage.setItem(STORAGE.paper, state.paperId);
  else localStorage.removeItem(STORAGE.paper);
  if (state.currentSection) localStorage.setItem(STORAGE.section, state.currentSection);
  else localStorage.removeItem(STORAGE.section);
}

function saveBeforeUnload() {
  persistState();
  if (!state.sessionId || state.sessionState === "paused") return;
  const body = JSON.stringify({
    action: "pause_reading", session_id: state.sessionId, paper_id: state.paperId,
    metadata: { viewport_section: state.currentSection, scroll_top: $("structured-reader")?.scrollTop || 0 },
  });
  navigator.sendBeacon?.(API_ENDPOINT, new Blob([body], { type: "application/json" }));
}

function setBusy(active, title = "", detail = "") {
  state.busy = active;
  $("global-loading").hidden = !active;
  if (title) $("loading-title").textContent = title;
  if (detail) $("loading-detail").textContent = detail;
  document.querySelectorAll(".accent-button").forEach((button) => { button.disabled = active; });
}

function toast(message, isError = false) {
  if (!message) return;
  const item = create("div", `toast${isError ? " is-error" : ""}`, String(message));
  $("toast-region").append(item);
  window.setTimeout(() => item.remove(), 4800);
}

function normalizePdfUrl(raw) {
  const value = String(raw || "").trim();
  if (!value) return "";
  const id = value.match(/^(?:arxiv:\s*)?(\d{4}\.\d{4,5})(?:v\d+)?$/i)?.[1];
  if (id) return `https://arxiv.org/pdf/${id}.pdf`;
  try {
    const url = new URL(value);
    if (/(^|\.)arxiv\.org$/i.test(url.hostname)) {
      url.hostname = "arxiv.org";
      url.pathname = url.pathname.replace(/^\/abs\//, "/pdf/");
      if (url.pathname.startsWith("/pdf/") && !url.pathname.toLowerCase().endsWith(".pdf")) url.pathname += ".pdf";
      url.search = "";
      url.hash = "";
    }
    return url.toString();
  } catch {
    return "";
  }
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.onerror = () => reject(new Error("读取 PDF 文件失败。"));
    reader.readAsDataURL(file);
  });
}

function svgNode(tag, attributes = {}) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
  return node;
}

function splitParagraphs(content) {
  return String(content || "").split(/\n\s*\n|\n(?=[A-Z0-9])/).map((item) => item.trim()).filter(Boolean);
}
function domSectionId(id) { return `paper-section-${String(id || "").replace(/[^a-zA-Z0-9_-]/g, "-")}`; }
function sectionTitle(id) { return state.paper?.sections?.find((item) => item.section_id === id)?.title || id || ""; }
function skillLabel(id) { return SKILLS.find((item) => item.id === id)?.label || id || "Skill"; }
function humanizeKey(key) { return String(key).replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase()); }
function truncate(value, length) { const text = String(value || ""); return text.length > length ? `${text.slice(0, length - 1)}…` : text; }
function formatBytes(bytes) { return bytes < 1024 * 1024 ? `${Math.ceil(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`; }
function delay(ms) { return new Promise((resolve) => window.setTimeout(resolve, ms)); }
