const API_ENDPOINT = "/paper_reading";
const WORKSPACE_PATH = "/app/paper-reading";
const isDedicatedWorkspace = window.location.pathname === WORKSPACE_PATH;
const STORAGE = {
  session: "paper_reading_session_id",
  paper: "paper_reading_paper_id",
  section: "paper_reading_current_section",
  scroll: "paper_reading_scroll_top",
  copilotWidth: "paper_reading_copilot_width",
  pdfMarks: "paper_reading_pdf_marks",
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

const KG_STAGE_ORDER = ["abstract", "introduction", "method", "experiment", "conclusion", "general"];
const PDFJS_SRC = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js";
const PDFJS_WORKER_SRC = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";

const state = {
  sessionId: "", paperId: "", paper: null, pdfUrl: "", hasPdf: false,
  paperIndex: null, parseQuality: "", textLayerAvailable: false,
  currentSection: "", progress: {}, activeSkills: [], skillOutputs: [],
  revealedKgElements: [], queryKgElements: [], selectedNode: null,
  selectedText: "", selectedPage: null, selectedRect: null, sourceView: "pdf", uploadSummary: null,
  sessionState: "", restored: false, busy: false, kgLayout: "force",
  forks: [], activeFeedId: "main", kgMaxStageIndex: 0,
  readerMode: "pdf", pdfDoc: null, pdfDocUrl: "", pdfRenderedKey: "", pdfjsLoading: null,
  pdfZoom: null, pdfMarks: [],
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
  setupPdfFirstReaderDom();
  if (isDedicatedWorkspace) {
    $("paper-intake").hidden = true;
    $("paper-workbench").hidden = true;
    $("new-paper-button").hidden = false;
    $("workspace-status").textContent = "正在恢复论文…";
  }
  renderSkillControls();
  renderQuickActions();
  renderLegend();
  renderCopilotTabs();
  bindIntake();
  bindWorkbench();
  bindReader();
  bindKg();
  bindFork();
  bindResizeHandle();
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
  $("regenerate-button").addEventListener("click", analyzeCurrentSection);
  $("fullscreen-button").addEventListener("click", toggleFullscreen);
  document.addEventListener("fullscreenchange", syncFullscreenButton);
  $("reading-chat-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("reading-chat-input");
    const question = input.value.trim();
    if (!question) return;
    input.value = "";
    const fork = state.forks.find((item) => item.id === state.activeFeedId);
    if (fork) runForkTurn(fork, question);
    else startReading(question);
  });
  window.addEventListener("beforeunload", saveBeforeUnload);
}

function bindReader() {
  document.querySelectorAll("[data-reader-mode]").forEach((button) => {
    button.addEventListener("click", () => setReaderMode(button.dataset.readerMode));
  });
  $("analyze-section-button").addEventListener("click", analyzeCurrentSection);
  $("fork-explore-button").addEventListener("click", () => openFork(""));
  $("previous-section-button").addEventListener("click", () => moveSection(-1));
  $("next-section-button").addEventListener("click", () => moveSection(1));
  $("pdf-fit-select").addEventListener("change", () => {
    const value = $("pdf-fit-select").value;
    state.pdfZoom = /^\d+$/.test(value) ? Number(value) : null;
    syncPdfZoomInput();
    renderPdf(true);
  });
  $("structured-reader").addEventListener("mouseup", captureSelection);
  $("pdf-reader").addEventListener("mouseup", captureSelection);
  bindScrollSpy();
  $("selection-toolbar").addEventListener("click", handleSelectionAction);
  document.addEventListener("mousedown", (event) => {
    if (!event.target.closest("#selection-toolbar") && !event.target.closest("#structured-reader") && !event.target.closest("#pdf-reader")) {
      $("selection-toolbar").hidden = true;
    }
  });
}

function setupPdfFirstReaderDom() {
  const pdfTab = document.querySelector('[data-reader-mode="pdf"]');
  const indexTab = document.querySelector('[data-reader-mode="structured"]');
  if (pdfTab) {
    pdfTab.textContent = "PDF 原文";
    pdfTab.classList.add("is-active");
  }
  if (indexTab) {
    indexTab.textContent = "智能索引";
    indexTab.classList.remove("is-active");
  }
  const hint = $("pdf-mode-hint");
  if (hint) {
    hint.textContent = "PDF 原文支持直接划选并让右侧 Agent 分析；智能索引只提供章节锚点和摘要，不再作为版面还原视图。";
    hint.hidden = false;
  }
  const reader = $("pdf-reader");
  if (reader && !$("pdf-document")) {
    const frame = $("pdf-frame");
    const empty = $("pdf-empty");
    const tools = createPdfToolbar();
    const documentHost = create("div", "pdf-document");
    documentHost.id = "pdf-document";
    reader.replaceChildren(tools, documentHost);
    if (frame) {
      frame.hidden = true;
      reader.append(frame);
    }
    if (empty) reader.append(empty);
  }
  ensureSelectionPdfActions();
  $("structured-reader").hidden = true;
  $("pdf-reader").hidden = false;
}

function createPdfToolbar() {
  const toolbar = create("div", "pdf-toolbar");
  toolbar.id = "pdf-toolbar";
  const zoomOut = create("button", "pdf-tool-button", "−");
  zoomOut.type = "button";
  zoomOut.title = "缩小";
  zoomOut.addEventListener("click", () => setPdfZoom((state.pdfZoom || currentZoomValue()) - 10));
  const zoomInput = create("input", "pdf-zoom-input");
  zoomInput.id = "pdf-zoom-input";
  zoomInput.type = "number";
  zoomInput.min = "40";
  zoomInput.max = "240";
  zoomInput.step = "5";
  zoomInput.value = String(state.pdfZoom || 100);
  zoomInput.title = "自由缩放百分比";
  zoomInput.addEventListener("change", () => setPdfZoom(Number(zoomInput.value || 100)));
  const zoomUnit = create("span", "pdf-tool-label", "%");
  const zoomIn = create("button", "pdf-tool-button", "+");
  zoomIn.type = "button";
  zoomIn.title = "放大";
  zoomIn.addEventListener("click", () => setPdfZoom((state.pdfZoom || currentZoomValue()) + 10));
  const fitWidth = create("button", "pdf-tool-button", "适宽");
  fitWidth.type = "button";
  fitWidth.title = "适应宽度";
  fitWidth.addEventListener("click", () => setPdfFit("width"));
  const fitPage = create("button", "pdf-tool-button", "整页");
  fitPage.type = "button";
  fitPage.title = "适应整页";
  fitPage.addEventListener("click", () => setPdfFit("page"));
  const save = create("button", "pdf-tool-button", "保存");
  save.type = "button";
  save.title = "下载原始 PDF";
  save.addEventListener("click", downloadCurrentPdf);
  const highlight = create("button", "pdf-tool-button", "高亮");
  highlight.type = "button";
  highlight.title = "高亮当前选区";
  highlight.addEventListener("click", () => addPdfMarkFromSelection("highlight"));
  const note = create("button", "pdf-tool-button", "注释");
  note.type = "button";
  note.title = "给当前选区添加注释";
  note.addEventListener("click", () => addPdfMarkFromSelection("note"));
  toolbar.append(zoomOut, zoomInput, zoomUnit, zoomIn, fitWidth, fitPage, save, highlight, note);
  return toolbar;
}

function ensureSelectionPdfActions() {
  const toolbar = $("selection-toolbar");
  if (!toolbar || toolbar.querySelector('[data-selection-action="highlight"]')) return;
  const highlight = create("button", "", "高亮");
  highlight.type = "button";
  highlight.dataset.selectionAction = "highlight";
  const note = create("button", "", "注释");
  note.type = "button";
  note.dataset.selectionAction = "note";
  toolbar.append(highlight, note);
}

function bindKg() {
  $("kg-query-type").addEventListener("change", () => {
    const type = $("kg-query-type").value;
    $("kg-path-fields").hidden = type !== "path";
    $("kg-question-input").required = type !== "neighbors";
  });
  $("kg-query-form").addEventListener("submit", queryKg);
  $("kg-layout-select").addEventListener("change", () => {
    state.kgLayout = $("kg-layout-select").value;
    renderKg(state.queryKgElements.length ? state.queryKgElements : state.revealedKgElements);
  });
  $("reset-kg-button").addEventListener("click", () => {
    state.queryKgElements = [];
    $("kg-answer").hidden = true;
    $("kg-reasoning").replaceChildren();
    renderKg(state.revealedKgElements);
  });
}

function bindFork() {
  $("fork-create-button").addEventListener("click", createFork);
  $("fork-close-button").addEventListener("click", closeFork);
  document.querySelectorAll("[data-fork-close]").forEach((element) => element.addEventListener("click", closeFork));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("fork-modal").hidden) closeFork();
  });
}

function bindResizeHandle() {
  const handle = $("copilot-resize-handle");
  const grid = $("workbench-grid");
  const saved = Number(localStorage.getItem(STORAGE.copilotWidth));
  if (saved >= 300 && saved <= 760) grid.style.setProperty("--copilot-width", `${saved}px`);

  let dragging = false;
  let startX = 0;
  let startWidth = 0;

  handle.addEventListener("mousedown", (event) => {
    dragging = true;
    startX = event.clientX;
    startWidth = $("copilot-panel").getBoundingClientRect().width;
    handle.classList.add("is-active");
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    event.preventDefault();
  });

  document.addEventListener("mousemove", (event) => {
    if (!dragging) return;
    const next = Math.min(760, Math.max(300, startWidth + (startX - event.clientX)));
    grid.style.setProperty("--copilot-width", `${next}px`);
  });

  document.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    handle.classList.remove("is-active");
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    const width = $("copilot-panel").getBoundingClientRect().width;
    localStorage.setItem(STORAGE.copilotWidth, String(Math.round(width)));
  });
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
    if (paper.citation_count != null) tags.append(create("span", "", `引用 ${paper.citation_count}`));
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
  state.paperIndex = data.paper_index || state.paper?.paper_index || null;
  state.textLayerAvailable = Boolean(data.text_layer_available);
  state.parseQuality = data.parse_quality || "";
  state.pdfUrl = data.pdf_url || "";
  state.hasPdf = Boolean(data.has_pdf && state.pdfUrl);
  loadPdfMarks();
  const initialKg = data.initial_kg || {};
  if (initialKg.cytoscape_elements?.length) {
    state.revealedKgElements = initialKg.cytoscape_elements;
    state.kgMaxStageIndex = Math.max(0, KG_STAGE_ORDER.indexOf(initialKg.current_stage || "abstract"));
    $("kg-stage-copy").textContent = `完整图谱 · ${initialKg.node_count ?? 0} 节点 / ${initialKg.edge_count ?? 0} 关系`;
  }
  if (!state.currentSection) state.currentSection = state.paper?.sections?.[0]?.section_id || "";
  persistState();
}

function renderReadyCard(sourceLabel = "已保存论文") {
  if (!state.paper) return;
  $("ready-title").textContent = state.paper.title || "未命名论文";
  $("ready-authors").textContent = (state.paper.authors || []).join("、") || "作者信息暂无";
  $("ready-abstract").textContent = state.paper.abstract || "解析完成，点击进入工作台查看 PDF 原文与智能索引。";
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
}

function showIntake() {
  window.location.href = "/app?mode=paper_reading";
}

function toggleFullscreen() {
  if (document.fullscreenElement) {
    document.exitFullscreen?.();
  } else {
    document.documentElement.requestFullscreen?.();
  }
}

function syncFullscreenButton() {
  $("fullscreen-button").textContent = document.fullscreenElement ? "退出全屏" : "全屏阅读";
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
  setReaderMode(state.readerMode || "pdf");
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
    button.addEventListener("click", () => selectSection(section.section_id, false));
    container.append(button);
  });
  $("outline-count").textContent = String(sections.length);
}

function renderSections() {
  const reader = $("structured-reader");
  reader.replaceChildren();
  const sections = state.paper?.sections || [];
  if (!sections.length) {
    reader.append(create("div", "empty-state", "没有解析到章节索引，请在 PDF 原文中阅读并划选。"));
    return;
  }
  const indexSections = state.paperIndex?.sections || [];
  sections.forEach((section, index) => {
    const indexed = indexSections.find((item) => item.section_id === section.section_id) || {};
    const level = Math.min(Math.max(Number(section.level) || 1, 1), 6);
    const article = create("section", `paper-section index-section level-${level}${section.section_id === state.currentSection ? " is-current" : ""}`);
    article.id = domSectionId(section.section_id);
    article.dataset.sectionId = section.section_id;
    const meta = create("div", "section-meta");
    meta.append(create("span", "", `Section ${String(index + 1).padStart(2, "0")}`));
    if (section.start_page) meta.append(create("span", "", `Page ${section.start_page}`));
    article.append(meta, create("h2", "", section.title || `Section ${index + 1}`));

    const body = create("div", "paper-section-body");
    const summary = create("p", "index-section-summary", sectionSummaryText(section, indexed));
    body.append(summary);
    const chunks = (indexed.text_chunks || []).slice(0, 3);
    if (chunks.length) {
      const list = create("div", "index-chunk-list");
      chunks.forEach((chunk) => {
        const item = create("p", "index-chunk", truncate(chunk.text || "", 260));
        list.append(item);
      });
      body.append(list);
    }
    const actions = create("div", "index-section-actions");
    const jump = create("button", "figure-source-button", `跳转 PDF 第 ${section.start_page || 1} 页`);
    jump.type = "button";
    jump.addEventListener("click", () => jumpToPdfPage(section.start_page || 1, section.section_id));
    const analyze = create("button", "figure-source-button", "分析本节");
    analyze.type = "button";
    analyze.addEventListener("click", () => {
      state.currentSection = section.section_id;
      startReading(`请分析“${section.title || section.section_id}”，给出核心内容、论证结构、关键证据与需要重点理解的概念。`);
    });
    actions.append(jump, analyze);
    body.append(actions);
    article.append(body);
    reader.append(article);
  });
  restoreReaderPosition(reader);
  reader.addEventListener("scroll", () => localStorage.setItem(STORAGE.scroll, String(reader.scrollTop)), { passive: true });
}

function sectionSummaryText(section, indexed = {}) {
  const pages = section.start_page
    ? `原文页码：${section.start_page}${section.end_page && section.end_page !== section.start_page ? `-${section.end_page}` : ""}。`
    : "";
  const chunks = indexed.text_chunks || [];
  const quality = state.parseQuality ? `解析质量：${state.parseQuality}。` : "";
  if (!chunks.length) return `${pages}${quality}此处是 Agent 索引锚点，请以 PDF 原文为准。`;
  return `${pages}${quality}下方仅展示供 Agent 检索的章节摘要片段，不作为论文版面还原。`;
}

function findFigureInsertionIndex(figure, paragraphs, section, fallbackIndex = 0) {
  if (!paragraphs.length) return 0;
  const number = figureNumber(figure);
  if (number) {
    const pattern = new RegExp(`\\b(?:Figure|Fig\\.?|图)\\s*${escapeRegExp(number)}\\b`, "i");
    const referenced = paragraphs.findIndex((paragraph) => pattern.test(String(paragraph || "")));
    if (referenced >= 0) return referenced;
  }
  if (figure.page && section.start_page && section.end_page && section.end_page > section.start_page) {
    const ratio = (figure.page - section.start_page) / Math.max(1, section.end_page - section.start_page);
    return Math.min(paragraphs.length - 1, Math.max(0, Math.round(ratio * (paragraphs.length - 1))));
  }
  return Math.min(paragraphs.length - 1, Math.max(0, fallbackIndex === 0 ? 1 : 1 + fallbackIndex));
}

function figureNumber(figure) {
  const source = `${figure.figure_id || ""} ${figure.caption || ""}`;
  const match = source.match(/\b(?:fig(?:ure)?[:.\s-]*|figure\s+|图\s*)(\d+[A-Za-z]?)/i);
  return match ? match[1] : "";
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function restoreReaderPosition(reader) {
  requestAnimationFrame(() => {
    if (!state.restored) return;
    if (state.currentSection && document.getElementById(domSectionId(state.currentSection))) {
      scrollReaderToSection(state.currentSection, false);
    } else {
      const savedScroll = Number(localStorage.getItem(STORAGE.scroll) || 0);
      if (savedScroll) reader.scrollTop = savedScroll;
    }
  });
}

function renderReflowParagraph(text) {
  const paragraph = create("p", "reflow-paragraph");
  const value = String(text || "").trim();
  let displayValue = value;
  if (/^[•·]\s*/.test(value)) {
    paragraph.classList.add("is-bullet");
    displayValue = value.replace(/^[•·]\s*/, "");
  }
  const lead = displayValue.match(/^([A-Z][A-Za-z -]{2,48}\.)\s+(.+)$/);
  if (lead) {
    paragraph.append(create("strong", "paragraph-lead", lead[1]), document.createTextNode(` ${lead[2]}`));
  } else {
    paragraph.textContent = displayValue;
  }
  return paragraph;
}

function renderPaperFigure(figure) {
  const card = create("figure", "paper-figure");
  card.id = `paper-${String(figure.figure_id || figure.asset_name || "figure").replace(/[^a-zA-Z0-9_-]/g, "-")}`;
  const image = create("img", "paper-figure-image");
  image.src = figure.image_url || figure.asset_url || "";
  image.alt = figure.caption || `Figure on page ${figure.page || "unknown"}`;
  image.loading = "lazy";
  image.decoding = "async";

  const caption = create("figcaption", "paper-figure-caption");
  const copy = create("span", "figure-caption-copy", figure.caption || "论文配图");
  caption.append(copy);
  appendSourceButton(caption, figure.page);
  card.append(image, caption);
  return card;
}

function appendSourceButton(container, page) {
  if (!page) return;
  const sourceButton = create("button", "figure-source-button", `原文第 ${page} 页`);
  sourceButton.type = "button";
  sourceButton.addEventListener("click", () => jumpToPdfPage(page));
  container.append(sourceButton);
}

function pdfFragment(page, fit) {
  const view = { width: "view=FitH", page: "view=Fit", 100: "zoom=100" }[fit] || "view=FitH";
  return `page=${page || 1}&${view}`;
}

function currentPdfPage() {
  const section = state.paper?.sections?.find((item) => item.section_id === state.currentSection);
  return section?.start_page || 1;
}

async function renderPdf(force = false) {
  setupPdfFirstReaderDom();
  const host = $("pdf-document");
  const fit = state.pdfZoom ? String(state.pdfZoom) : ($("pdf-fit-select").value || "width");
  const baseUrl = state.pdfUrl.split("#", 1)[0];
  $("pdf-empty").hidden = state.hasPdf;
  if (!state.hasPdf || !host) {
    if (host) host.replaceChildren();
    return;
  }
  const renderKey = `${baseUrl}::${fit}::${host.clientWidth}`;
  if (!force && state.pdfRenderedKey === renderKey && host.childElementCount) {
    scrollPdfToPage(currentPdfPage(), false);
    return;
  }
  host.replaceChildren(create("div", "pdf-loading", "正在加载 PDF 原文…"));
  try {
    const pdfjsLib = await ensurePdfJs();
    if (!state.pdfDoc || state.pdfDocUrl !== baseUrl) {
      state.pdfDoc = await pdfjsLib.getDocument(baseUrl).promise;
      state.pdfDocUrl = baseUrl;
    }
    host.replaceChildren();
    for (let pageNumber = 1; pageNumber <= state.pdfDoc.numPages; pageNumber += 1) {
      await renderPdfPage(pdfjsLib, state.pdfDoc, pageNumber, fit, host);
    }
    state.pdfRenderedKey = renderKey;
    $("pdf-frame").hidden = true;
    scrollPdfToPage(currentPdfPage(), false);
  } catch (error) {
    console.warn("PDF.js render failed, fallback to iframe.", error);
    host.replaceChildren(create("div", "pdf-loading", "PDF.js 加载失败，已切换到浏览器原生 PDF 预览。"));
    const nextUrl = `${baseUrl}#${pdfFragment(currentPdfPage(), fit)}`;
    if ($("pdf-frame").getAttribute("src") !== nextUrl) $("pdf-frame").src = nextUrl;
    $("pdf-frame").hidden = false;
  }
}

function setPdfZoom(value) {
  state.pdfZoom = Math.min(240, Math.max(40, Math.round(Number(value) || 100)));
  syncPdfZoomInput();
  renderPdf(true);
}

function setPdfFit(value) {
  state.pdfZoom = null;
  $("pdf-fit-select").value = value;
  syncPdfZoomInput();
  renderPdf(true);
}

function currentZoomValue() {
  if (state.pdfZoom) return state.pdfZoom;
  const value = $("pdf-fit-select")?.value;
  return /^\d+$/.test(value || "") ? Number(value) : 100;
}

function syncPdfZoomInput() {
  const input = $("pdf-zoom-input");
  if (input) input.value = String(state.pdfZoom || currentZoomValue());
}

function downloadCurrentPdf() {
  if (!state.pdfUrl) return toast("当前论文没有可保存的 PDF。", true);
  const link = document.createElement("a");
  link.href = state.pdfUrl.split("#", 1)[0];
  link.download = `${sanitizeFileName(state.paper?.title || "paper")}.pdf`;
  document.body.append(link);
  link.click();
  link.remove();
}

function syncPdfToSection(sectionId) {
  if (!state.hasPdf) return;
  const section = state.paper?.sections?.find((item) => item.section_id === sectionId);
  const page = section?.start_page;
  if (!page) return;
  scrollPdfToPage(page);
  if (!$("pdf-frame").hidden) {
    const fit = $("pdf-fit-select").value || "width";
    const baseUrl = state.pdfUrl.split("#", 1)[0];
    const nextUrl = `${baseUrl}#${pdfFragment(page, fit)}`;
    if ($("pdf-frame").getAttribute("src") !== nextUrl) $("pdf-frame").src = nextUrl;
  }
}

function setReaderMode(mode) {
  const isPdf = mode === "pdf";
  state.readerMode = isPdf ? "pdf" : "structured";
  $("structured-reader").hidden = isPdf;
  $("pdf-reader").hidden = !isPdf;
  $("pdf-fit-control").hidden = !isPdf || !state.hasPdf;
  $("pdf-mode-hint").hidden = false;
  document.querySelectorAll("[data-reader-mode]").forEach((button) => button.classList.toggle("is-active", button.dataset.readerMode === mode));
  if (isPdf) {
    renderPdf();
    syncPdfToSection(state.currentSection);
  }
}

function ensurePdfJs() {
  if (window.pdfjsLib) return Promise.resolve(window.pdfjsLib);
  if (state.pdfjsLoading) return state.pdfjsLoading;
  state.pdfjsLoading = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = PDFJS_SRC;
    script.async = true;
    script.onload = () => {
      if (!window.pdfjsLib) {
        reject(new Error("PDF.js 未暴露 pdfjsLib。"));
        return;
      }
      window.pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS_WORKER_SRC;
      resolve(window.pdfjsLib);
    };
    script.onerror = () => reject(new Error("无法加载 PDF.js。"));
    document.head.append(script);
  });
  return state.pdfjsLoading;
}

async function renderPdfPage(pdfjsLib, pdfDoc, pageNumber, fit, host) {
  const page = await pdfDoc.getPage(pageNumber);
  const baseViewport = page.getViewport({ scale: 1 });
  const availableWidth = Math.max(360, host.clientWidth - 34);
  const widthScale = availableWidth / baseViewport.width;
  const numericZoom = /^\d+$/.test(String(fit)) ? Number(fit) : 0;
  const scale = numericZoom
    ? numericZoom / 100 * 1.35
    : fit === "100"
    ? 1.35
    : fit === "page"
      ? Math.min(widthScale, Math.max(0.7, (host.clientHeight - 42) / baseViewport.height))
      : widthScale;
  const viewport = page.getViewport({ scale });
  const pageShell = create("section", "pdf-page");
  pageShell.dataset.pageNumber = String(pageNumber);
  pageShell.style.width = `${viewport.width}px`;
  pageShell.style.height = `${viewport.height}px`;
  const canvas = create("canvas", "pdf-canvas");
  const context = canvas.getContext("2d");
  const outputScale = window.devicePixelRatio || 1;
  canvas.width = Math.floor(viewport.width * outputScale);
  canvas.height = Math.floor(viewport.height * outputScale);
  canvas.style.width = `${viewport.width}px`;
  canvas.style.height = `${viewport.height}px`;
  const textLayer = create("div", "textLayer");
  textLayer.style.width = `${viewport.width}px`;
  textLayer.style.height = `${viewport.height}px`;
  const markLayer = create("div", "pdf-mark-layer");
  pageShell.append(canvas, textLayer, markLayer);
  host.append(pageShell);
  await page.render({
    canvasContext: context,
    viewport,
    transform: outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : null,
  }).promise;
  const textContent = await page.getTextContent();
  const task = pdfjsLib.renderTextLayer({
    textContentSource: textContent,
    container: textLayer,
    viewport,
    textDivs: [],
    enhanceTextSelection: true,
  });
  await (task.promise || task);
  renderPdfMarks(pageShell, pageNumber);
}

function scrollPdfToPage(page, smooth = true) {
  const host = $("pdf-document");
  const target = host?.querySelector(`[data-page-number="${Number(page) || 1}"]`);
  if (!host || !target) return;
  const top = target.getBoundingClientRect().top - host.getBoundingClientRect().top + host.scrollTop - 10;
  host.scrollTo({ top: Math.max(0, top), behavior: smooth ? "smooth" : "auto" });
}

function jumpToPdfPage(page, sectionId = "") {
  if (sectionId) state.currentSection = sectionId;
  setReaderMode("pdf");
  renderOutline();
  syncComposerContext();
  requestAnimationFrame(() => scrollPdfToPage(page || currentPdfPage()));
}

async function selectSection(sectionId, analyze) {
  state.currentSection = sectionId;
  state.sourceView = state.readerMode || "pdf";
  persistState();
  renderOutline();
  document.querySelectorAll(".paper-section").forEach((section) => section.classList.toggle("is-current", section.dataset.sectionId === sectionId));
  if (state.readerMode === "pdf" || $("structured-reader").hidden) syncPdfToSection(sectionId);
  else scrollReaderToSection(sectionId);
  syncComposerContext();
  if (analyze) await startReading(`请精读“${sectionTitle(sectionId)}”，说明核心内容、论证结构和需要重点理解的概念。`);
}

function moveSection(offset) {
  const sections = state.paper?.sections || [];
  if (!sections.length) return;
  const current = Math.max(0, sections.findIndex((item) => item.section_id === state.currentSection));
  const target = sections[Math.min(sections.length - 1, Math.max(0, current + offset))];
  if (target) selectSection(target.section_id, false);
}

async function startReading(content, sessionId = state.sessionId) {
  if (!state.paperId || state.busy) return;
  state.busy = true;
  const thinking = showThinkingCard(`正在分析 ${sectionTitle(state.currentSection) || "当前内容"}…`);
  try {
    const { payload } = await callPaperReading({
      action: "start_reading", session_id: sessionId || "", paper_id: state.paperId,
      target_section: state.currentSection || "", content,
      metadata: selectionMetadata(),
    });
    thinking.remove();
    applyReadingPayload(payload);
    toast("章节分析已更新。");
    return payload;
  } catch (error) {
    thinking.remove();
    toast(error.message, true);
    return null;
  } finally {
    state.busy = false;
  }
}

function showThinkingCard(detail, target = $("analysis-feed")) {
  const card = create("article", "thinking-card");
  card.append(create("span", "loading-orb"));
  const body = create("div");
  body.append(create("strong", "", "Synapse Copilot 正在精读"));
  body.append(create("p", "", detail || "请稍候…"));
  card.append(body);
  target.append(card);
  target.scrollTop = target.scrollHeight;
  return card;
}

async function analyzeCurrentSection() {
  await startReading(`请分析“${sectionTitle(state.currentSection) || "当前章节"}”，给出核心内容、论证结构、关键证据与需要重点理解的概念。`);
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
  state.revealedKgElements = mergeKgElements(state.revealedKgElements, data.revealed_kg?.cytoscape_elements || []);
  const stageIndex = KG_STAGE_ORDER.indexOf(data.revealed_kg?.current_stage || "general");
  if (stageIndex > state.kgMaxStageIndex) state.kgMaxStageIndex = stageIndex;
  state.queryKgElements = [];
  persistState();
  appendAnalysis(data.agent_response || "后端已完成本次阅读操作。", data);
  renderSkillOutputs(state.skillOutputs, $("analysis-feed"));
  renderProgress();
  renderOutline();
  syncSkillControls();
  updateSessionBadge();
  const revealedNodes = state.revealedKgElements.filter((item) => !item.data?.source).length;
  const revealedEdges = state.revealedKgElements.length - revealedNodes;
  $("kg-stage-copy").textContent = `完整图谱 · ${revealedNodes} 节点 / ${revealedEdges} 关系`;
  renderKg(state.revealedKgElements);
}

function mergeKgElements(existing, incoming) {
  if (!incoming?.length) return existing || [];
  if (!existing?.length) return incoming;
  const seen = new Set(existing.map((item) => item.data?.id));
  const merged = [...existing];
  incoming.forEach((item) => {
    const key = item.data?.id;
    if (key && !seen.has(key)) {
      seen.add(key);
      merged.push(item);
    }
  });
  return merged;
}

function appendAnalysis(text, metadata = {}, target = $("analysis-feed")) {
  const card = create("article", "analysis-card");
  const header = create("header");
  header.append(create("strong", "", "Synapse Copilot"), create("span", "", metadata.duration_ms ? `${Math.round(metadata.duration_ms)} ms` : "Agent"));
  card.append(header, renderMarkdown(text));
  target.append(card);
  target.scrollTop = target.scrollHeight;
}

function renderSkillOutputs(outputs, target) {
  outputs.forEach((output) => {
    const card = create("article", "skill-output-card");
    const header = create("header");
    header.append(create("strong", "", output.skill_name || skillLabel(output.skill_id)), create("span", "", output.output_type || output.parse_status || "Skill"));
    card.append(header);
    const content = output.content;
    const hasStructured = output.parse_status === "parsed" && content && typeof content === "object" && Object.keys(content).length;
    if (hasStructured) card.append(renderSkillContent(output));
    else card.append(renderMarkdown(output.rendered || "Skill 已执行，但没有返回可展示内容。"));
    target.append(card);
  });
}

function renderSkillContent(output) {
  if (output.output_type === "math_derivation") return renderMathTabs(output.content);
  return renderStructuredValue(output.content);
}

function renderMathTabs(content) {
  const tabs = [
    { key: "layer_1_intuition", label: "直觉理解" },
    { key: "layer_2_derivation", label: "逐步推导" },
    { key: "layer_3_numerical_example", label: "数值例子" },
    { key: "detected_gaps", label: "推导跳跃" },
  ].filter((tab) => {
    const value = content?.[tab.key];
    if (value == null) return false;
    if (Array.isArray(value) && !value.length) return false;
    if (typeof value === "object" && !Array.isArray(value) && !Object.keys(value).length) return false;
    return true;
  });
  if (tabs.length < 2) return renderStructuredValue(content);

  const wrap = create("div", "skill-tabs");
  const nav = create("div", "skill-tabs-nav");
  const panels = create("div", "skill-tabs-panels");
  tabs.forEach((tab, index) => {
    const button = create("button", `skill-tab${index === 0 ? " is-active" : ""}`, tab.label);
    button.type = "button";
    const panel = create("div", `skill-tab-panel${index === 0 ? " is-active" : ""}`);
    panel.append(renderTabBody(content[tab.key]));
    button.addEventListener("click", () => {
      nav.querySelectorAll(".skill-tab").forEach((item) => item.classList.remove("is-active"));
      panels.querySelectorAll(".skill-tab-panel").forEach((item) => item.classList.remove("is-active"));
      button.classList.add("is-active");
      panel.classList.add("is-active");
    });
    nav.append(button);
    panels.append(panel);
  });
  wrap.append(nav, panels);
  return wrap;
}

function renderTabBody(value) {
  if (Array.isArray(value)) {
    const list = create("ul", "tab-list");
    value.forEach((entry) => {
      const li = create("li");
      if (entry && typeof entry === "object") li.append(renderStructuredValue(entry));
      else li.textContent = String(entry);
      list.append(li);
    });
    return list;
  }
  if (value && typeof value === "object") return renderStructuredValue(value);
  const paragraph = create("p", "", String(value));
  return paragraph;
}

function renderStructuredValue(value, depth = 0) {
  const container = create("div", "structured-output");
  if (depth > 4) {
    container.append(create("pre", "analysis-text", JSON.stringify(value, null, 2)));
    return container;
  }
  Object.entries(value || {}).forEach(([key, item]) => {
    const block = create("section", "value-block");
    block.append(create("h4", "", humanizeKey(key)));
    if (Array.isArray(item)) {
      if (!item.length) block.append(create("p", "", "暂无"));
      else block.append(renderStructuredArray(item, depth + 1));
    } else if (item && typeof item === "object") {
      block.append(renderCompactObject(item, depth + 1));
    } else {
      block.append(create("p", "", item == null || item === "" ? "暂无" : String(item)));
    }
    container.append(block);
  });
  return container;
}

function renderStructuredArray(items, depth = 0) {
  if (!items.some((entry) => entry && typeof entry === "object")) {
    const list = create("ul", "compact-list");
    items.forEach((entry) => list.append(create("li", "", String(entry))));
    return list;
  }
  const wrap = create("div", "compact-object-list");
  items.forEach((entry, index) => {
    if (!entry || typeof entry !== "object") {
      wrap.append(create("p", "compact-text", String(entry)));
      return;
    }
    const item = create("article", "compact-object");
    const title = entry.name || entry.title || entry.label || entry.step_id || entry.id || `Item ${index + 1}`;
    item.append(create("h5", "", String(title)));
    item.append(renderCompactObject(entry, depth + 1, new Set(["name", "title", "label"])));
    wrap.append(item);
  });
  return wrap;
}

function renderCompactObject(value, depth = 0, hiddenKeys = new Set()) {
  const grid = create("dl", "compact-fields");
  Object.entries(value || {}).forEach(([key, item]) => {
    if (hiddenKeys.has(key)) return;
    const dt = create("dt", "", humanizeKey(key));
    const dd = create("dd");
    if (Array.isArray(item)) {
      dd.append(item.length ? renderStructuredArray(item, depth + 1) : create("span", "", "暂无"));
    } else if (item && typeof item === "object") {
      dd.append(depth > 3 ? create("pre", "analysis-text", JSON.stringify(item, null, 2)) : renderCompactObject(item, depth + 1));
    } else {
      dd.textContent = item == null || item === "" ? "暂无" : String(item);
    }
    grid.append(dt, dd);
  });
  return grid;
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
  // Skill routing is handled by the backend. The frontend only renders returned outputs.
}

function renderQuickActions() {}

function syncSkillControls() {
  // No frontend skill controls are rendered; skill choice is handled server-side.
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
    jumpToSection(state.currentSection);
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
    state.selectedText = "";
    state.selectedPage = null;
    state.selectedRect = null;
    $("selection-toolbar").hidden = true;
    return;
  }
  const anchor = selection.anchorNode?.parentElement;
  const structured = anchor?.closest("#structured-reader");
  const pdfPage = anchor?.closest(".pdf-page");
  if (!structured && !pdfPage) return;
  state.selectedText = text.slice(0, 6000);
  state.sourceView = pdfPage ? "pdf" : "index";
  state.selectedPage = pdfPage ? Number(pdfPage.dataset.pageNumber || 0) : null;
  state.selectedRect = selectionRectForMetadata(selection, pdfPage);
  const section = anchor.closest(".paper-section");
  if (section?.dataset.sectionId) state.currentSection = section.dataset.sectionId;
  else if (state.selectedPage) state.currentSection = sectionForPage(state.selectedPage)?.section_id || state.currentSection;
  renderOutline();
  syncComposerContext();
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
  if (action === "concept") await startReading(`请解释选中概念的定义、前置知识和领域脉络。${quoted}`);
  if (action === "formula") openFork("请对选中公式做直觉、逐步推导和数值例子三层分析。");
  if (action === "fork") openFork("请围绕选中内容进行深入探索。");
  if (action === "highlight") addPdfMarkFromSelection("highlight");
  if (action === "note") addPdfMarkFromSelection("note");
}

function addPdfMarkFromSelection(type) {
  const selection = window.getSelection();
  if (!selection?.rangeCount || !state.selectedText) return toast("请先在 PDF 原文中划选内容。", true);
  const page = selection.anchorNode?.parentElement?.closest(".pdf-page");
  if (!page) return toast("高亮和注释目前只支持 PDF 原文选区。", true);
  const pageRect = page.getBoundingClientRect();
  const rects = Array.from(selection.getRangeAt(0).getClientRects())
    .filter((rect) => rect.width > 1 && rect.height > 1)
    .map((rect) => ({
      left: round2((rect.left - pageRect.left) / pageRect.width),
      top: round2((rect.top - pageRect.top) / pageRect.height),
      width: round2(rect.width / pageRect.width),
      height: round2(rect.height / pageRect.height),
    }));
  if (!rects.length) return;
  const note = type === "note" ? window.prompt("添加注释", "") || "" : "";
  const mark = {
    id: `mark-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    type,
    page: Number(page.dataset.pageNumber || 1),
    rects,
    text: state.selectedText,
    note,
    section_id: state.currentSection,
    created_at: new Date().toISOString(),
  };
  state.pdfMarks.push(mark);
  persistPdfMarks();
  renderPdfMarks(page, mark.page);
  selection.removeAllRanges();
  $("selection-toolbar").hidden = true;
  toast(type === "note" ? "注释已添加。" : "高亮已添加。");
}

function renderPdfMarks(pageShell, pageNumber) {
  const layer = pageShell.querySelector(".pdf-mark-layer");
  if (!layer) return;
  layer.replaceChildren();
  state.pdfMarks
    .filter((mark) => Number(mark.page) === Number(pageNumber))
    .forEach((mark) => {
      mark.rects.forEach((rect) => {
        const item = create("button", `pdf-mark pdf-mark-${mark.type}`);
        item.type = "button";
        item.style.left = `${rect.left * 100}%`;
        item.style.top = `${rect.top * 100}%`;
        item.style.width = `${rect.width * 100}%`;
        item.style.height = `${rect.height * 100}%`;
        item.title = mark.type === "note" && mark.note ? mark.note : mark.text;
        item.addEventListener("click", () => showPdfMark(mark));
        layer.append(item);
      });
    });
}

function showPdfMark(mark) {
  const detail = mark.type === "note" && mark.note
    ? `注释：${mark.note}\n\n原文：${mark.text}`
    : `高亮原文：${mark.text}`;
  toast(detail);
}

function persistPdfMarks() {
  if (!state.paperId) return;
  const all = loadAllPdfMarks();
  all[state.paperId] = state.pdfMarks;
  localStorage.setItem(STORAGE.pdfMarks, JSON.stringify(all));
}

function loadPdfMarks() {
  const all = loadAllPdfMarks();
  state.pdfMarks = Array.isArray(all[state.paperId]) ? all[state.paperId] : [];
}

function loadAllPdfMarks() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE.pdfMarks) || "{}") || {};
  } catch {
    return {};
  }
}

function selectionMetadata() {
  return {
    viewport_section: state.currentSection,
    selected_text: state.selectedText,
    selected_page: state.selectedPage,
    selected_rect: state.selectedRect,
    source_view: state.sourceView || (state.readerMode === "pdf" ? "pdf" : "index"),
    source_section_id: state.currentSection,
  };
}

function selectionRectForMetadata(selection, pdfPage) {
  if (!selection?.rangeCount) return null;
  const rect = selection.getRangeAt(0).getBoundingClientRect();
  if (!pdfPage) {
    return {
      left: Math.round(rect.left),
      top: Math.round(rect.top),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
    };
  }
  const pageRect = pdfPage.getBoundingClientRect();
  return {
    left: round2((rect.left - pageRect.left) / pageRect.width),
    top: round2((rect.top - pageRect.top) / pageRect.height),
    width: round2(rect.width / pageRect.width),
    height: round2(rect.height / pageRect.height),
  };
}

function openFork(question) {
  if (!state.sessionId) return toast("请先开始章节阅读，再创建 Fork。", true);
  $("fork-context-input").value = state.selectedText || sectionTitle(state.currentSection);
  $("fork-question-input").value = question || "请深入分析这段内容。";
  $("fork-modal").hidden = false;
  $("fork-question-input").focus();
}

function closeFork() {
  $("fork-modal").hidden = true;
}

async function createFork() {
  const context = $("fork-context-input").value.trim();
  const question = $("fork-question-input").value.trim() || "请深入分析这段内容。";
  closeFork();

  const fork = {
    id: `fork-${Date.now()}`,
    sessionId: "",
    skillId: "",
    label: `Fork ${state.forks.length + 1}`,
    feedEl: null,
  };
  addForkTab(fork);
  switchFeed(fork.id);

  const thinking = showThinkingCard(`正在创建 ${fork.label} 分支并分析…`, fork.feedEl);
  try {
    const { payload } = await callPaperReading({
      action: "fork", session_id: state.sessionId, paper_id: state.paperId,
      fork_context: context, fork_question: question, fork_skills: [],
      metadata: { selected_text: context, source_section_id: state.currentSection },
    });
    fork.sessionId = payload.data?.fork_session_id || "";
    if (!fork.sessionId) throw new Error("Fork 响应缺少 fork_session_id。");
    const result = await callPaperReading({
      action: "start_reading", session_id: fork.sessionId, paper_id: state.paperId,
      target_section: state.currentSection, content: `${question}\n\n上下文：${context}`,
    });
    thinking.remove();
    appendAnalysis(result.payload.data?.agent_response || "分支分析完成。", {}, fork.feedEl);
    renderSkillOutputs(result.payload.skill_outputs || [], fork.feedEl);
    toast("Fork 分支已创建，可在此选项卡继续追问。");
  } catch (error) {
    thinking.remove();
    toast(error.message, true);
  }
}

async function runForkTurn(fork, question) {
  if (!fork.sessionId) return toast("分支会话尚未就绪，请稍候再试。", true);
  const thinking = showThinkingCard(`正在追问 ${fork.label}…`, fork.feedEl);
  try {
    const { payload } = await callPaperReading({
      action: "start_reading", session_id: fork.sessionId, paper_id: state.paperId,
      target_section: state.currentSection, content: question,
    });
    thinking.remove();
    appendAnalysis(payload.data?.agent_response || "分支已完成本次回答。", {}, fork.feedEl);
    renderSkillOutputs(payload.skill_outputs || [], fork.feedEl);
  } catch (error) {
    thinking.remove();
    toast(error.message, true);
  }
}

async function mergeFork(fork) {
  if (!fork?.sessionId) return;
  setBusy(true, "正在合并探索结论", "把分支成果带回主阅读流…");
  try {
    const { payload } = await callPaperReading({
      action: "merge", session_id: state.sessionId, merge_session_id: fork.sessionId,
    });
    const data = payload.data || {};
    state.activeSkills = [...new Set([...state.activeSkills, ...(data.merged_skills || [])])];
    const card = create("article", "fork-summary");
    card.append(create("strong", "", `Fork 结论已合并 · ${fork.label}`));
    (data.key_findings || []).forEach((finding) => card.append(create("p", "", finding)));
    $("analysis-feed").append(card);
    syncSkillControls();
    removeForkTab(fork.id);
    switchFeed("main");
    toast(data.message || "分支已合并。");
  } catch (error) {
    toast(error.message, true);
  } finally {
    setBusy(false);
  }
}

function addForkTab(fork) {
  const feed = create("div", "analysis-feed copilot-feed fork-feed");
  feed.dataset.feedId = fork.id;
  const toolbar = create("div", "fork-feed-toolbar");
  toolbar.append(create("span", "fork-feed-label", `⑂ ${fork.label}`));
  const mergeButton = create("button", "mini-button is-accent", "合并回主流程");
  mergeButton.type = "button";
  mergeButton.addEventListener("click", () => mergeFork(fork));
  const closeButton = create("button", "mini-button", "关闭分支");
  closeButton.type = "button";
  closeButton.addEventListener("click", () => closeForkTab(fork.id));
  toolbar.append(mergeButton, closeButton);
  feed.append(toolbar);
  $("copilot-feeds").append(feed);
  fork.feedEl = feed;
  state.forks.push(fork);
  renderCopilotTabs();
}

function closeForkTab(forkId) {
  const wasActive = state.activeFeedId === forkId;
  removeForkTab(forkId);
  if (wasActive) switchFeed("main");
}

function removeForkTab(forkId) {
  const index = state.forks.findIndex((item) => item.id === forkId);
  if (index === -1) return;
  state.forks[index].feedEl?.remove();
  state.forks.splice(index, 1);
  renderCopilotTabs();
}

function renderCopilotTabs() {
  const bar = $("copilot-tabs");
  bar.replaceChildren();
  const mainTab = create("button", `copilot-tab${state.activeFeedId === "main" ? " is-active" : ""}`, "主流程");
  mainTab.type = "button";
  mainTab.addEventListener("click", () => switchFeed("main"));
  bar.append(mainTab);
  state.forks.forEach((fork) => {
    const tab = create("button", `copilot-tab${state.activeFeedId === fork.id ? " is-active" : ""}`);
    tab.type = "button";
    tab.append(document.createTextNode(`⑂ ${fork.label}`));
    const close = create("span", "copilot-tab-close", "×");
    close.addEventListener("click", (event) => {
      event.stopPropagation();
      closeForkTab(fork.id);
    });
    tab.append(close);
    tab.addEventListener("click", () => switchFeed(fork.id));
    bar.append(tab);
  });
}

function switchFeed(feedId) {
  state.activeFeedId = feedId;
  document.querySelectorAll(".copilot-feed").forEach((feed) => {
    feed.classList.toggle("is-active", feed.dataset.feedId === feedId);
  });
  renderCopilotTabs();
  syncComposerContext();
}

function syncComposerContext() {
  const fork = state.forks.find((item) => item.id === state.activeFeedId);
  $("composer-context").textContent = fork ? `Fork：${fork.label}` : `当前：${sectionTitle(state.currentSection) || "全文"}`;
}

function skillShort(id) {
  const skill = SKILLS.find((item) => item.id === id);
  return skill ? skill.short : "探索";
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

function computeKgLayout(nodes, edges, layout) {
  const ids = nodes.map((item) => item.data.id || item.data.node_id);
  if (layout === "grid") return layoutGrid(ids);
  if (layout === "grouped") return layoutGrouped(nodes);
  if (layout === "circle") return layoutCircle(ids);
  return layoutForce(ids, edges);
}

function layoutCircle(ids) {
  const positions = new Map();
  const centerX = 450, centerY = 175;
  const radiusX = Math.min(360, 110 + ids.length * 18);
  const radiusY = Math.min(120, 60 + ids.length * 6);
  ids.forEach((id, index) => {
    const angle = -Math.PI / 2 + index * Math.PI * 2 / ids.length;
    positions.set(id, { x: centerX + Math.cos(angle) * radiusX, y: centerY + Math.sin(angle) * radiusY });
  });
  return positions;
}

function layoutGrid(ids) {
  const positions = new Map();
  const cols = Math.max(1, Math.ceil(Math.sqrt(ids.length)));
  const rows = Math.max(1, Math.ceil(ids.length / cols));
  const left = 70, right = 830, top = 45, bottom = 290;
  const stepX = cols > 1 ? (right - left) / (cols - 1) : 0;
  const stepY = rows > 1 ? (bottom - top) / (rows - 1) : 0;
  ids.forEach((id, index) => {
    const col = index % cols, row = Math.floor(index / cols);
    positions.set(id, { x: cols > 1 ? left + col * stepX : 450, y: rows > 1 ? top + row * stepY : 175 });
  });
  return positions;
}

function layoutGrouped(nodes) {
  const positions = new Map();
  const groups = new Map();
  nodes.forEach((item) => {
    const type = item.data.node_type || "Concept";
    if (!groups.has(type)) groups.set(type, []);
    groups.get(type).push(item.data.id || item.data.node_id);
  });
  const types = Array.from(groups.keys());
  const left = 70, right = 830, top = 50, bottom = 285;
  const colW = types.length > 1 ? (right - left) / (types.length - 1) : 0;
  types.forEach((type, colIndex) => {
    const members = groups.get(type);
    const x = types.length > 1 ? left + colIndex * colW : 450;
    const stepY = members.length > 1 ? (bottom - top) / (members.length - 1) : 0;
    members.forEach((id, rowIndex) => {
      positions.set(id, { x, y: members.length > 1 ? top + rowIndex * stepY : 170 });
    });
  });
  return positions;
}

function layoutForce(ids, edges) {
  const n = ids.length;
  const positions = new Map();
  if (!n) return positions;
  const width = 820, height = 260, left = 70, top = 50;
  const index = new Map(ids.map((id, i) => [id, i]));
  const px = new Array(n), py = new Array(n);
  ids.forEach((id, i) => {
    const angle = -Math.PI / 2 + i * Math.PI * 2 / n;
    px[i] = left + width / 2 + Math.cos(angle) * width * 0.32;
    py[i] = top + height / 2 + Math.sin(angle) * height * 0.36;
  });
  const links = edges
    .map((edge) => [index.get(edge.data.source), index.get(edge.data.target)])
    .filter(([a, b]) => a != null && b != null && a !== b);
  const k = Math.sqrt((width * height) / n) * 0.62;
  let temperature = width * 0.09;
  const iterations = 160;
  for (let iter = 0; iter < iterations; iter += 1) {
    const dx = new Array(n).fill(0), dy = new Array(n).fill(0);
    for (let i = 0; i < n; i += 1) {
      for (let j = i + 1; j < n; j += 1) {
        let vx = px[i] - px[j], vy = py[i] - py[j];
        let dist = Math.hypot(vx, vy) || 0.01;
        const repel = (k * k) / dist;
        vx = vx / dist * repel; vy = vy / dist * repel;
        dx[i] += vx; dy[i] += vy; dx[j] -= vx; dy[j] -= vy;
      }
    }
    for (const [a, b] of links) {
      let vx = px[a] - px[b], vy = py[a] - py[b];
      const dist = Math.hypot(vx, vy) || 0.01;
      const attract = (dist * dist) / k;
      vx = vx / dist * attract; vy = vy / dist * attract;
      dx[a] -= vx; dy[a] -= vy; dx[b] += vx; dy[b] += vy;
    }
    for (let i = 0; i < n; i += 1) {
      const disp = Math.hypot(dx[i], dy[i]) || 0.01;
      const scale = Math.min(disp, temperature) / disp;
      px[i] += dx[i] * scale; py[i] += dy[i] * scale;
      px[i] = Math.max(left, Math.min(left + width, px[i]));
      py[i] = Math.max(top, Math.min(top + height, py[i]));
    }
    temperature *= 0.965;
  }
  ids.forEach((id, i) => positions.set(id, { x: px[i], y: py[i] }));
  return positions;
}

let kgSim = null;

function stopKgSim() {
  if (kgSim?.rafId) cancelAnimationFrame(kgSim.rafId);
  kgSim = null;
}

function renderKg(elements) {
  stopKgSim();
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

  const nodeEls = new Map();
  const edgeEls = [];
  edges.forEach((item) => {
    const line = svgNode("line", { class: "kg-edge", "data-source": item.data.source, "data-target": item.data.target, "marker-end": "url(#kg-arrow)" });
    const label = svgNode("text", { class: "kg-edge-label" });
    label.textContent = truncate(item.data.label || item.data.edge_type || "", 14);
    svg.append(line, label);
    edgeEls.push({ line, label, source: item.data.source, target: item.data.target });
  });
  nodes.forEach((item) => {
    const data = { ...item.data, id: item.data.id || item.data.node_id };
    const group = svgNode("g", {
      class: `kg-node${state.selectedNode?.id === data.id ? " is-selected" : ""}`,
      "data-node-id": data.id, tabindex: "0", role: "button",
    });
    group.append(svgNode("circle", { r: "24", fill: NODE_COLORS[data.node_type] || "#87a7ff" }));
    const label = svgNode("text", { y: "39" });
    label.textContent = truncate(data.label || data.node_type || "Node", 18);
    group.append(label);
    group.addEventListener("click", () => selectKgNode(data));
    group.addEventListener("keydown", (event) => { if (event.key === "Enter") selectKgNode(data); });
    svg.append(group);
    nodeEls.set(data.id, group);
  });

  if (state.kgLayout === "force") {
    startKgSim(nodes, edgeEls, nodeEls);
  } else {
    applyKgPositions(computeKgLayout(nodes, edges, state.kgLayout), nodeEls, edgeEls);
  }
}

function applyKgPositions(positions, nodeEls, edgeEls) {
  nodeEls.forEach((group, id) => {
    const p = positions.get(id);
    if (p) group.setAttribute("transform", `translate(${p.x} ${p.y})`);
  });
  edgeEls.forEach(({ line, label, source, target }) => {
    const s = positions.get(source), t = positions.get(target);
    if (!s || !t) return;
    line.setAttribute("x1", s.x); line.setAttribute("y1", s.y);
    line.setAttribute("x2", t.x); line.setAttribute("y2", t.y);
    label.setAttribute("x", (s.x + t.x) / 2);
    label.setAttribute("y", (s.y + t.y) / 2 - 5);
  });
}

function startKgSim(nodes, edgeEls, nodeEls) {
  const count = nodes.length;
  const simNodes = nodes.map((item, i) => {
    const angle = -Math.PI / 2 + i * 2 * Math.PI / count;
    return {
      id: item.data.id || item.data.node_id,
      x: 450 + Math.cos(angle) * 300,
      y: 175 + Math.sin(angle) * 110,
      vx: 0, vy: 0, fixed: false,
    };
  });
  const index = new Map(simNodes.map((n) => [n.id, n]));
  const links = edgeEls
    .map((e) => ({ s: index.get(e.source), t: index.get(e.target) }))
    .filter((l) => l.s && l.t && l.s !== l.t);
  kgSim = { nodes: simNodes, links, nodeEls, edgeEls, alpha: 1, rafId: null, dragging: null };
  simNodes.forEach((n) => {
    nodeEls.get(n.id).addEventListener("mousedown", (event) => startNodeDrag(event, n));
  });
  kgSim.rafId = requestAnimationFrame(tickKgSim);
}

function tickKgSim() {
  const sim = kgSim;
  if (!sim) return;
  stepKgSim(sim);
  applyKgPositions(new Map(sim.nodes.map((n) => [n.id, n])), sim.nodeEls, sim.edgeEls);
  sim.alpha *= 0.985;
  if (sim.alpha > 0.02 || sim.dragging) {
    sim.rafId = requestAnimationFrame(tickKgSim);
  } else {
    sim.rafId = null;
  }
}

function stepKgSim(sim) {
  const { nodes, links } = sim;
  const n = nodes.length;
  const alpha = sim.dragging ? Math.max(sim.alpha, 0.35) : sim.alpha;
  if (alpha <= 0.001) return;
  const k = Math.sqrt((820 * 260) / Math.max(n, 1)) * 0.62;
  for (let i = 0; i < n; i += 1) {
    for (let j = i + 1; j < n; j += 1) {
      const a = nodes[i], b = nodes[j];
      let dx = a.x - b.x, dy = a.y - b.y;
      let d2 = dx * dx + dy * dy;
      if (d2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1; }
      const d = Math.sqrt(d2);
      const f = (k * k) / d * alpha;
      const fx = dx / d * f, fy = dy / d * f;
      a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
    }
  }
  links.forEach(({ s, t }) => {
    const dx = s.x - t.x, dy = s.y - t.y;
    const d = Math.hypot(dx, dy) || 0.01;
    const f = (d * d) / k * alpha * 0.1;
    const fx = dx / d * f, fy = dy / d * f;
    s.vx -= fx; s.vy -= fy; t.vx += fx; t.vy += fy;
  });
  nodes.forEach((node) => {
    if (node.fixed) { node.vx = 0; node.vy = 0; return; }
    node.vx *= 0.32; node.vy *= 0.32;
    node.x += node.vx; node.y += node.vy;
    node.x = Math.max(70, Math.min(830, node.x));
    node.y = Math.max(50, Math.min(310, node.y));
  });
}

function startNodeDrag(event, node) {
  if (state.kgLayout !== "force" || !kgSim) return;
  event.preventDefault();
  const sim = kgSim;
  const svg = $("kg-graph");
  node.fixed = true;
  sim.dragging = node;
  sim.alpha = Math.max(sim.alpha, 0.2);
  if (!sim.rafId) sim.rafId = requestAnimationFrame(tickKgSim);
  const onMove = (e) => {
    const p = svgPoint(svg, e.clientX, e.clientY);
    node.x = Math.max(70, Math.min(830, p.x));
    node.y = Math.max(50, Math.min(310, p.y));
    node.vx = 0; node.vy = 0;
    sim.alpha = Math.max(sim.alpha, 0.35);
  };
  const onUp = () => {
    node.fixed = false;
    sim.dragging = null;
    sim.alpha = Math.min(sim.alpha, 0.04);
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
  };
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}

function svgPoint(svg, clientX, clientY) {
  const pt = svg.createSVGPoint();
  pt.x = clientX; pt.y = clientY;
  const ctm = svg.getScreenCTM();
  return ctm ? pt.matrixTransform(ctm.inverse()) : { x: clientX, y: clientY };
}

function selectKgNode(data) {
  state.selectedNode = data;
  document.querySelectorAll(".kg-node").forEach((node) => {
    node.classList.toggle("is-selected", node.dataset.nodeId === data.id);
  });
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

function scrollReaderToSection(sectionId, smooth = true) {
  const reader = $("structured-reader");
  const target = document.getElementById(domSectionId(sectionId));
  if (!reader || !target) return;
  const top = target.getBoundingClientRect().top - reader.getBoundingClientRect().top + reader.scrollTop - 12;
  reader.scrollTo({ top: Math.max(0, top), behavior: smooth ? "smooth" : "auto" });
}

function bindScrollSpy() {
  const reader = $("structured-reader");
  let ticking = false;
  reader.addEventListener("scroll", () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => {
      ticking = false;
      updateCurrentSectionFromScroll();
    });
  }, { passive: true });
}

function updateCurrentSectionFromScroll() {
  if ($("structured-reader").hidden) return;
  const reader = $("structured-reader");
  const sections = Array.from(reader.querySelectorAll(".paper-section"));
  if (!sections.length) return;
  const readerTop = reader.getBoundingClientRect().top;
  let current = sections[0].dataset.sectionId;
  for (const section of sections) {
    if (section.getBoundingClientRect().top - readerTop <= 110) current = section.dataset.sectionId;
    else break;
  }
  if (current === state.currentSection) return;
  state.currentSection = current;
  persistState();
  document.querySelectorAll(".outline-item").forEach((item, index) => {
    item.classList.toggle("is-active", (state.paper?.sections?.[index]?.section_id) === current);
  });
  document.querySelectorAll(".paper-section").forEach((section) => section.classList.toggle("is-current", section.dataset.sectionId === current));
  syncComposerContext();
}

function jumpToSection(sectionId) {
  if (!sectionId) return;
  state.currentSection = sectionId;
  renderOutline();
  const section = state.paper?.sections?.find((item) => item.section_id === sectionId);
  jumpToPdfPage(section?.start_page || 1, sectionId);
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
function sectionForPage(page) {
  const value = Number(page) || 0;
  if (!value) return null;
  return (state.paper?.sections || []).find((section) => {
    const start = Number(section.start_page || 0);
    const end = Number(section.end_page || start || 0);
    return start && value >= start && value <= Math.max(start, end);
  }) || null;
}
function domSectionId(id) { return `paper-section-${String(id || "").replace(/[^a-zA-Z0-9_-]/g, "-")}`; }
function sectionTitle(id) { return state.paper?.sections?.find((item) => item.section_id === id)?.title || id || ""; }
function skillLabel(id) { return SKILLS.find((item) => item.id === id)?.label || id || "Skill"; }
function humanizeKey(key) { return String(key).replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase()); }
function truncate(value, length) { const text = String(value || ""); return text.length > length ? `${text.slice(0, length - 1)}…` : text; }
function round2(value) { return Math.round(Number(value || 0) * 10000) / 10000; }
function sanitizeFileName(value) { return String(value || "paper").replace(/[\\/:*?"<>|]+/g, "_").slice(0, 120) || "paper"; }
function formatBytes(bytes) { return bytes < 1024 * 1024 ? `${Math.ceil(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`; }
function delay(ms) { return new Promise((resolve) => window.setTimeout(resolve, ms)); }
