const API_ENDPOINT = "/paper_reading";
const WORKSPACE_PATH = "/app/paper-reading";
const isDedicatedWorkspace = window.location.pathname === WORKSPACE_PATH;
const STORAGE = {
  session: "paper_reading_session_id",
  paper: "paper_reading_paper_id",
  section: "paper_reading_current_section",
  scroll: "paper_reading_scroll_top",
  copilotWidth: "paper_reading_copilot_width",
  navigatorWidth: "paper_reading_navigator_width",
  noteHeight: "paper_reading_note_height_ratio",
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

const PDFJS_SRC = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js";
const PDFJS_WORKER_SRC = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
const PDF_CACHE_NAME = "novicesynapse-paper-pdf-v1";
const READING_MAP_STALE_AFTER_MS = 180000;

const state = {
  sessionId: "", paperId: "", paper: null, pdfUrl: "", hasPdf: false,
  paperIndex: null, parseQuality: "", textLayerAvailable: false,
  sectionExtractionSource: "", sectionExtractionStatus: "", sectionExtractionMessage: "", outlineEntriesCount: 0,
  parseStatus: "", readingMapStatus: "", readingMapPhase: "", readingMapProgress: 0, readingMapError: "", readingMapCardProgress: null, readingMap: null, readingMapStartedAt: 0, readingMapHeartbeatAt: "", parsePollTimer: null,
  currentSection: "", progress: {}, activeSkills: [], skillOutputs: [],
  selectedText: "", selectedPage: null, selectedRect: null, sourceView: "pdf", uploadSummary: null,
  sessionState: "", restored: false, busy: false,
  readingMapExpanded: false,
  forks: [], activeFeedId: "main",
  readerMode: "pdf", readerModeChosen: false, pdfDoc: null, pdfDocUrl: "", pdfRenderedKey: "", pdfjsLoading: null,
  pdfZoom: null, pdfMarks: [], pdfMarkColor: "yellow", pdfMarkHistory: [],
  pendingPdfPage: null, pdfRenderGeneration: 0, pdfRenderingKey: "",
  pendingPdfNoteMark: null, editingPdfNoteId: "",
  activeResponseController: null,
  paperNoteLoadedFor: "", paperNoteDirty: false, paperNoteSaveTimer: null, paperNoteMode: "normal", paperNoteEditor: null,
  historyLoadedFor: "",
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
  syncReturnChatLink();
  setupPdfFirstReaderDom();
  if (isDedicatedWorkspace) {
    $("paper-intake").hidden = true;
    $("paper-workbench").hidden = true;
    $("workspace-status").textContent = "正在恢复论文…";
  }
  renderSkillControls();
  renderQuickActions();
  renderCopilotTabs();
  bindIntake();
  bindWorkbench();
  bindPaperNote();
  bindReader();
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
  $("regenerate-button").addEventListener("click", analyzeCurrentSection);
  $("regenerate-reading-map-button")?.addEventListener("click", regenerateReadingMap);
  $("reparse-paper-button")?.addEventListener("click", reparsePaper);
  $("reading-map-toggle-button")?.addEventListener("click", () => toggleReadingMapPanel());
  syncReadingMapPanelState();
  $("fullscreen-button").addEventListener("click", toggleFullscreen);
  document.addEventListener("fullscreenchange", syncFullscreenButton);
  $("reading-chat-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("reading-chat-input");
    const question = input.value.trim();
    if (!question) return;
    input.value = "";
    const fork = state.forks.find((item) => item.id === state.activeFeedId);
    if (fork) {
      appendUserQuestion(question, fork.feedEl);
      runForkTurn(fork, question);
    } else {
      appendUserQuestion(question);
      startReading(question);
    }
  });
  $("reading-chat-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      $("reading-chat-form").requestSubmit();
    }
  });
  $("reading-stop-button").addEventListener("click", interruptReadingResponse);
  window.addEventListener("beforeunload", saveBeforeUnload);
}

function bindReader() {
  document.querySelectorAll("[data-reader-mode]").forEach((button) => {
    button.addEventListener("click", () => setReaderMode(button.dataset.readerMode, { userInitiated: true }));
  });
  $("analyze-section-button").addEventListener("click", analyzeCurrentSection);
  $("fork-explore-button").addEventListener("click", () => openFork(""));
  $("previous-section-button").addEventListener("click", () => moveSection(-1));
  $("next-section-button").addEventListener("click", () => moveSection(1));
  $("pdf-fit-select").addEventListener("change", () => {
    const value = $("pdf-fit-select").value;
    state.pdfZoom = /^\d+$/.test(value) ? Number(value) : null;
    syncPdfZoomInput();
    renderPdf(true, { preserveViewport: true });
  });
  $("structured-reader").addEventListener("mouseup", captureSelection);
  $("reflow-reader").addEventListener("mouseup", captureSelection);
  $("pdf-reader").addEventListener("mouseup", captureSelection);
  bindScrollSpy();
  $("selection-toolbar").addEventListener("click", handleSelectionAction);
  $("note-save-button").addEventListener("click", savePdfNote);
  $("note-close-button").addEventListener("click", closePdfNoteModal);
  document.querySelectorAll("[data-note-close]").forEach((element) => element.addEventListener("click", closePdfNoteModal));
  document.addEventListener("mousedown", (event) => {
    if (!event.target.closest("#selection-toolbar") && !event.target.closest("#structured-reader") && !event.target.closest("#reflow-reader") && !event.target.closest("#pdf-reader")) {
      $("selection-toolbar").hidden = true;
    }
  });
}

function setupPdfFirstReaderDom() {
  const pdfTab = document.querySelector('[data-reader-mode="pdf"]');
  const indexTab = document.querySelector('[data-reader-mode="structured"]');
  const reflowTab = document.querySelector('[data-reader-mode="reflow"]');
  if (pdfTab) {
    pdfTab.textContent = "PDF 原文";
  }
  if (indexTab) {
    indexTab.textContent = "智能索引";
  }
  if (reflowTab) {
    reflowTab.textContent = "AI 论文重排";
    reflowTab.hidden = !usesMineruReflow();
  }
  updateReaderModeHint();
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
  const selectionToolbar = $("selection-toolbar");
  if (selectionToolbar && selectionToolbar.parentElement !== document.body) document.body.append(selectionToolbar);
  const isPdf = state.readerMode === "pdf";
  const isReflow = state.readerMode === "reflow" && usesMineruReflow();
  $("structured-reader").hidden = isPdf || isReflow;
  $("reflow-reader").hidden = !isReflow;
  $("pdf-reader").hidden = !isPdf;
  document.querySelectorAll("[data-reader-mode]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.readerMode === state.readerMode);
  });
}

function usesMineruReflow() {
  return state.sectionExtractionSource === "mineru_markdown"
    || state.paper?.section_extraction_source === "mineru_markdown";
}

function updateReaderModeHint() {
  const hint = $("pdf-mode-hint");
  if (!hint) return;
  hint.textContent = state.readerMode === "reflow"
    ? "AI 论文重排展示 MinerU Markdown 正文；缺失的图片资源请切换 PDF 原文核对。"
    : state.readerMode === "pdf"
      ? "PDF 原文支持直接划选并让右侧智能体分析。"
      : "智能索引提供面向科研新手的章节导读，不与 AI 论文重排混合。";
  hint.hidden = false;
}

function createPdfToolbar() {
  const toolbar = create("div", "pdf-toolbar");
  toolbar.id = "pdf-toolbar";
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
  const divider = create("span", "pdf-toolbar-divider");
  const colorLabel = create("span", "pdf-tool-label", "高亮");
  const colors = createPdfColorPicker();
  const undo = create("button", "pdf-tool-button", "撤销标注");
  undo.type = "button";
  undo.title = "撤销最近一次高亮或注释";
  undo.addEventListener("click", undoLastPdfMark);
  toolbar.append(zoomInput, zoomUnit, fitWidth, fitPage, save, divider, colorLabel, colors, undo);
  return toolbar;
}

function createPdfColorPicker() {
  const colors = create("div", "pdf-color-picker");
  [
    ["yellow", "黄"],
    ["green", "绿"],
    ["blue", "蓝"],
    ["pink", "粉"],
  ].forEach(([color, label]) => {
    const swatch = create("button", `pdf-color-swatch pdf-color-${color}${state.pdfMarkColor === color ? " is-active" : ""}`);
    swatch.type = "button";
    swatch.setAttribute("aria-label", `使用${label}色标注`);
    swatch.addEventListener("click", () => setPdfMarkColor(color));
    colors.append(swatch);
  });
  return colors;
}

function ensureSelectionPdfActions() {
  const toolbar = $("selection-toolbar");
  if (!toolbar || toolbar.querySelector('[data-selection-action="highlight"]')) return;
  toolbar.append(create("span", "selection-toolbar-divider"));
  const highlight = create("button", "", "高亮");
  highlight.type = "button";
  highlight.dataset.selectionAction = "highlight";
  const note = create("button", "", "注释");
  note.type = "button";
  note.dataset.selectionAction = "note";
  toolbar.append(highlight, note);
}

function bindFork() {
  $("fork-create-button").addEventListener("click", createFork);
  $("fork-close-button").addEventListener("click", closeFork);
  document.querySelectorAll("[data-fork-close]").forEach((element) => element.addEventListener("click", closeFork));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("fork-modal").hidden) closeFork();
    if (event.key === "Escape" && !$("note-modal").hidden) closePdfNoteModal();
  });
}

function bindResizeHandle() {
  const handle = $("copilot-resize-handle");
  const grid = $("workbench-grid");
  const saved = Number(localStorage.getItem(STORAGE.copilotWidth));
  if (saved >= 300) setCopilotWidth(saved, false);
  const savedNavigator = Number(localStorage.getItem(STORAGE.navigatorWidth));
  if (savedNavigator >= 190) setNavigatorWidth(savedNavigator, false);

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
    setCopilotWidth(startWidth + (startX - event.clientX), false);
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

  const navigatorHandle = $("navigator-resize-handle");
  let navigatorDragging = false;
  let navigatorStartX = 0;
  let navigatorStartWidth = 0;
  navigatorHandle.addEventListener("mousedown", (event) => {
    navigatorDragging = true;
    navigatorStartX = event.clientX;
    navigatorStartWidth = document.querySelector(".navigator-panel").getBoundingClientRect().width;
    navigatorHandle.classList.add("is-active");
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    event.preventDefault();
  });
  document.addEventListener("mousemove", (event) => {
    if (navigatorDragging) setNavigatorWidth(navigatorStartWidth + event.clientX - navigatorStartX, false);
  });
  document.addEventListener("mouseup", () => {
    if (!navigatorDragging) return;
    navigatorDragging = false;
    navigatorHandle.classList.remove("is-active");
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    const width = document.querySelector(".navigator-panel").getBoundingClientRect().width;
    localStorage.setItem(STORAGE.navigatorWidth, String(Math.round(width)));
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
    const authors = (paper.authors || []).filter(Boolean);
    if (authors.length) card.append(create("p", "", authors.join("、")));
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
  setBusy(true, "正在解析 PDF", "上传、章节重排与阅读地图生成可能需要一些时间…");
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
  setBusy(true, "正在导入在线论文", "下载 PDF、解析章节并生成阅读地图…");
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
  state.readerMode = "pdf";
  state.readerModeChosen = false;
  state.currentSection = data.sections?.[0]?.section_id || "";
  state.parseStatus = data.parse_status || "";
  state.sectionExtractionSource = data.section_extraction_source || "";
  state.sectionExtractionStatus = data.section_extraction_status || "";
  state.sectionExtractionMessage = data.section_extraction_message || "";
  state.outlineEntriesCount = Number(data.outline_entries_count || 0);
  state.readingMapStatus = data.reading_map_status || "";
  state.readingMapPhase = data.reading_map_phase || "";
  state.readingMapProgress = Number(data.reading_map_progress || 0);
  state.readingMapError = data.reading_map_error || "";
  state.readingMapCardProgress = data.reading_map_card_progress || null;
  state.readingMapHeartbeatAt = data.reading_map_heartbeat_at || "";
  state.readingMapStartedAt = state.readingMapStatus === "llm_running" ? Date.now() : 0;
  state.readingMap = null;
  state.activeSkills = [];
  state.progress = {};
  state.uploadSummary = {};
  state.restored = false;
  if (!state.paperId) throw new Error("上传成功响应中缺少 paper_id。");
  persistState();
  await loadPaperDetail();
  $("search-results-section").hidden = true;
  $("paper-ready-section").hidden = true;
  await enterWorkbench();
  startParsePolling();
}

async function loadPaperDetail() {
  const { payload } = await callPaperReading({ action: "get_paper_detail", paper_id: state.paperId });
  const data = payload.data || {};
  state.paper = data.paper || null;
  state.paperIndex = data.paper_index || state.paper?.paper_index || null;
  state.readingMap = data.reading_map || state.paper?.reading_map || null;
  state.parseStatus = data.parse_status || state.paper?.parse_status || "";
  state.sectionExtractionSource = data.section_extraction_source || state.paper?.section_extraction_source || "";
  state.sectionExtractionStatus = data.section_extraction_status || state.paper?.section_extraction_status || "";
  state.sectionExtractionMessage = data.section_extraction_message || state.paper?.section_extraction_message || "";
  state.outlineEntriesCount = Number(data.outline_entries_count || state.paper?.outline_entries_count || 0);
  state.readingMapStatus = data.reading_map_status || state.paper?.reading_map_status || state.readingMap?.status || "";
  state.readingMapPhase = data.reading_map_phase || state.paper?.reading_map_phase || "";
  state.readingMapProgress = Number(data.reading_map_progress || state.paper?.reading_map_progress || 0);
  state.readingMapError = data.reading_map_error || state.paper?.reading_map_error || state.readingMap?.error || "";
  state.readingMapCardProgress = data.reading_map_card_progress || state.paper?.reading_map_card_progress || null;
  state.readingMapHeartbeatAt = data.reading_map_heartbeat_at || state.paper?.reading_map_heartbeat_at || "";
  if (state.readingMapStatus === "llm_running" && !state.readingMapStartedAt) state.readingMapStartedAt = Date.now();
  if (state.readingMapStatus !== "llm_running") state.readingMapStartedAt = 0;
  state.textLayerAvailable = Boolean(data.text_layer_available);
  state.parseQuality = data.parse_quality || "";
  state.pdfUrl = data.pdf_url || "";
  state.hasPdf = Boolean(data.has_pdf && state.pdfUrl);
  if (!state.readerModeChosen) {
    state.readerMode = usesMineruReflow() ? "reflow" : "pdf";
  }
  setupPdfFirstReaderDom();
  await loadPdfMarks();
  if (!state.currentSection) state.currentSection = state.paper?.sections?.[0]?.section_id || "";
  persistState();
}

async function regenerateReadingMap() {
  if (!state.paperId) {
    toast("请先上传或打开一篇论文。", true);
    return;
  }
  const button = $("regenerate-reading-map-button");
  if (button) button.disabled = true;
  state.readingMapStatus = "llm_running";
  state.readingMapPhase = "queued";
  state.readingMapProgress = 0;
  state.readingMapError = "";
  state.readingMapCardProgress = null;
  state.readingMapHeartbeatAt = new Date().toISOString();
  state.readingMapStartedAt = Date.now();
  state.readingMap = { version: "novice-reading-map-v2", status: "llm_running", paper_type: "unknown", map_variant: "research", prerequisite_card: {}, research_map: {}, survey_map: {}, section_guides: [] };
  renderSections();
  renderReadingMap();
  try {
    const { payload } = await callPaperReading({
      action: "regenerate_reading_map",
      paper_id: state.paperId,
      session_id: state.sessionId || "",
    });
    const data = payload.data || {};
    state.readingMap = data.reading_map || state.readingMap;
    state.readingMapStatus = data.reading_map_status || state.readingMap?.status || "llm_running";
    state.readingMapPhase = data.reading_map_phase || "queued";
    state.readingMapProgress = Number(data.reading_map_progress || 0);
    state.readingMapError = data.reading_map_error || "";
    state.readingMapCardProgress = data.reading_map_card_progress || null;
    state.readingMapHeartbeatAt = data.reading_map_heartbeat_at || state.readingMapHeartbeatAt;
    state.readingMapStartedAt = state.readingMapStatus === "llm_running" ? Date.now() : 0;
    toast(data.message || "已重新提交导读地图生成。");
    startParsePolling();
  } catch (error) {
    state.readingMapStatus = "failed";
    state.readingMapPhase = "failed";
    state.readingMapProgress = 0;
    state.readingMapError = error.message || "重新生成失败。";
    state.readingMapCardProgress = null;
    state.readingMapHeartbeatAt = "";
    state.readingMapStartedAt = 0;
    state.readingMap = { version: "novice-reading-map-v2", status: "failed", error: error.message || "重新生成失败。", prerequisite_card: {}, research_map: {}, survey_map: {}, section_guides: [] };
    toast(error.message || "重新生成失败。", true);
  } finally {
    if (button) button.disabled = false;
    renderSections();
    renderReadingMap();
  }
}

function startParsePolling() {
  stopParsePolling();
  if (!state.paperId || !shouldPollPaperDetail()) return;
  state.parsePollTimer = window.setInterval(async () => {
    try {
      await loadPaperDetail();
      if (!$("paper-workbench").hidden) {
        renderPaperMetadata();
        renderOutline();
        renderSections();
        renderReflowSections();
        renderProgress();
        renderReadingMap();
        if (!state.currentSection) {
          state.currentSection = state.paper?.sections?.[0]?.section_id || "";
          syncComposerContext();
        }
      }
      if (!shouldPollPaperDetail()) stopParsePolling();
    } catch (error) {
      console.warn("Parse polling failed", error);
    }
  }, 2500);
}

function shouldPollPaperDetail() {
  return ["queued", "pending", "parsing"].includes(state.parseStatus)
    || state.readingMapStatus === "llm_running";
}

function stopParsePolling() {
  if (state.parsePollTimer) window.clearInterval(state.parsePollTimer);
  state.parsePollTimer = null;
}

function renderReadyCard(sourceLabel = "已保存论文") {
  if (!state.paper) return;
  $("ready-title").textContent = state.paper.title || "未命名论文";
  const authors = (state.paper.authors || []).filter(Boolean);
  $("ready-authors").textContent = authors.join("、");
  $("ready-authors").hidden = !authors.length;
  $("ready-abstract").textContent = state.paper.abstract || (shouldPollPaperDetail()
    ? "正在解析论文信息，完成后会自动补充摘要与章节结构。"
    : "点击进入工作台查看 PDF 原文与智能索引。");
  $("ready-source").textContent = state.restored ? "继续阅读" : sourceLabel;
}

async function enterWorkbench() {
  if (!state.paper) return;
  if (!await ensureReadingSession()) return;
  if (!$("paper-intake").hidden) {
    $("paper-ready-card").classList.add("is-entering");
    await delay(320);
  }
  $("paper-intake").hidden = true;
  $("paper-workbench").hidden = false;
  state.readingMapExpanded = false;
  $("workspace-status").textContent = "论文精读 · 阅读中";
  $("paper-ready-card").classList.remove("is-entering");
  renderPaperWorkspace();
  syncPaperNoteDrawerBounds();
  $("paper-note-button").hidden = !state.paperId;
  if (state.paperId && state.paperNoteLoadedFor !== state.paperId) void loadPaperNote();
  $("paper-boot").hidden = true;
  document.body.classList.remove("is-booting");
  window.scrollTo({ top: 0, behavior: "auto" });
}

async function reparsePaper() {
  if (!state.paperId) {
    toast("请先上传或打开一篇论文。", true);
    return;
  }
  const button = $("reparse-paper-button");
  if (button) button.disabled = true;
  try {
    const { payload } = await callPaperReading({
      action: "reparse_paper",
      paper_id: state.paperId,
      session_id: state.sessionId || "",
    });
    const data = payload.data || {};
    state.parseStatus = data.parse_status || "parsing";
    if (state.paper) state.paper.parse_status = state.parseStatus;
    toast(data.message || "已重新提交论文解析。");
    renderPaperMetadata();
    renderOutlineSourceWarning();
    startParsePolling();
  } catch (error) {
    toast(error.message || "重新解析论文失败。", true);
  } finally {
    if (button) button.disabled = false;
  }
}

async function ensureReadingSession() {
  if (state.sessionId) return true;
  if (!state.paperId) return false;
  try {
    const { payload } = await callPaperReading({
      action: "create_session",
      session_id: "",
      paper_id: state.paperId,
    });
    state.sessionId = payload.data?.session_id || payload.session?.session_id || "";
    state.sessionState = payload.session?.state || state.sessionState;
    state.progress = payload.progress || state.progress;
    if (!state.sessionId) throw new Error("创建阅读会话后未返回 session_id。");
    persistState();
    return true;
  } catch (error) {
    toast(`无法创建阅读会话：${error.message}`, true);
    return false;
  }
}

function bindPaperNote() {
  const input = $("paper-note-input");
  const reader = document.querySelector(".reader-panel");
  const drawer = $("paper-note-drawer");
  if (reader && drawer.parentElement !== reader) reader.append(drawer);
  state.paperNoteEditor = new window.PaperMarkdownEditor({
    source: input,
    normal: $("paper-note-normal"),
    onChange: markPaperNoteChanged,
    onModeChange: syncPaperNoteModeButtons,
  });
  $("paper-note-button").addEventListener("click", togglePaperNoteDrawer);
  $("paper-note-close-button").addEventListener("click", closePaperNoteDrawer);
  bindPaperNoteResize();
  $("paper-note-save-button").addEventListener("click", () => savePaperNote());
  $("paper-note-mode").addEventListener("click", (event) => {
    const button = event.target.closest("[data-paper-note-mode]");
    if (button) setPaperNoteMode(button.dataset.paperNoteMode);
  });
  $("paper-note-toolbar").addEventListener("mousedown", (event) => {
    if (event.target.closest("[data-markdown-action]")) event.preventDefault();
  });
  $("paper-note-toolbar").addEventListener("click", (event) => {
    const button = event.target.closest("[data-markdown-action]");
    if (button) applyMarkdownAction(button.dataset.markdownAction);
  });
  [input, $("paper-note-normal")].forEach((editor) => editor.addEventListener("blur", () => {
    if (state.paperNoteDirty) void savePaperNote({ quiet: true });
  }));
  window.addEventListener("resize", syncPaperNoteDrawerBounds);
  if (typeof ResizeObserver === "function") {
    const observer = new ResizeObserver(syncPaperNoteDrawerBounds);
    observer.observe(document.querySelector(".reader-panel"));
  }
  document.addEventListener("keydown", (event) => {
    const drawerOpen = $("paper-note-drawer").classList.contains("is-open");
    if (event.key === "Escape" && drawerOpen) {
      event.preventDefault();
      closePaperNoteDrawer();
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s" && drawerOpen) {
      event.preventDefault();
      void savePaperNote();
    }
  });
}

async function loadPaperNote() {
  if (!state.paperId) return;
  const paperId = state.paperId;
  setPaperNoteStatus("正在读取…");
  try {
    const note = await fetchResearchJson(`/api/research/papers/${encodeURIComponent(paperId)}/note`);
    if (paperId !== state.paperId) return;
    state.paperNoteEditor.setMarkdown(note.content_markdown || "");
    $("paper-note-title").textContent = `${state.paper?.title || note.paper_title || "论文"} · 笔记`;
    state.paperNoteLoadedFor = paperId;
    state.paperNoteDirty = false;
    updatePaperNoteCount();
    setPaperNoteStatus(note.updated_at ? "已保存" : "空白笔记");
  } catch (error) {
    setPaperNoteStatus("读取失败", true);
    toast(error.message || "论文笔记读取失败。", true);
  }
}

async function savePaperNote({ quiet = false } = {}) {
  if (!state.paperId || state.paperNoteLoadedFor !== state.paperId) return;
  if (state.paperNoteSaveTimer) window.clearTimeout(state.paperNoteSaveTimer);
  state.paperNoteSaveTimer = null;
  const paperId = state.paperId;
  const content = state.paperNoteEditor.getMarkdown();
  setPaperNoteStatus("正在保存…");
  try {
    const note = await fetchResearchJson(`/api/research/papers/${encodeURIComponent(paperId)}/note`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content_markdown: content }),
    });
    if (paperId !== state.paperId || content !== state.paperNoteEditor.getMarkdown()) return;
    state.paperNoteDirty = false;
    const savedAt = note.updated_at ? new Date(note.updated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
    setPaperNoteStatus(savedAt ? `已保存 ${savedAt}` : "已保存");
    if (!quiet) toast("论文笔记已保存。");
  } catch (error) {
    state.paperNoteDirty = true;
    setPaperNoteStatus("保存失败", true);
    if (!quiet) toast(error.message || "论文笔记保存失败。", true);
  }
}

async function openPaperNoteDrawer() {
  if (!state.paperId) return;
  if (state.paperNoteLoadedFor !== state.paperId) await loadPaperNote();
  if (state.paperNoteLoadedFor !== state.paperId) return;
  setPaperNoteMode("normal");
  const drawer = $("paper-note-drawer");
  syncPaperNoteDrawerBounds();
  drawer.classList.add("is-open");
  drawer.setAttribute("aria-hidden", "false");
  $("paper-note-button").classList.add("is-active");
  window.setTimeout(() => state.paperNoteEditor.focus(), 180);
}

function closePaperNoteDrawer() {
  const drawer = $("paper-note-drawer");
  drawer.classList.remove("is-open");
  drawer.setAttribute("aria-hidden", "true");
  $("paper-note-button").classList.remove("is-active");
  if (state.paperNoteDirty) void savePaperNote({ quiet: true });
}

function togglePaperNoteDrawer() {
  if ($("paper-note-drawer").classList.contains("is-open")) closePaperNoteDrawer();
  else void openPaperNoteDrawer();
}

function updatePaperNoteCount() {
  const content = state.paperNoteEditor?.getMarkdown() || "";
  const characters = Array.from(content).length;
  const lines = content ? content.split(/\r?\n/).length : 0;
  $("paper-note-count").textContent = `${characters} 字 · ${lines} 行`;
}

function setPaperNoteStatus(message, isError = false) {
  const status = $("paper-note-save-status");
  status.textContent = message;
  status.classList.toggle("is-error", isError);
}

function setPaperNoteMode(mode) {
  state.paperNoteEditor?.setMode(mode);
}

function syncPaperNoteModeButtons(mode) {
  state.paperNoteMode = mode;
  $("paper-note-mode").querySelectorAll("[data-paper-note-mode]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.paperNoteMode === mode);
  });
}

function markPaperNoteChanged() {
  state.paperNoteDirty = true;
  updatePaperNoteCount();
  setPaperNoteStatus("等待保存…");
  if (state.paperNoteSaveTimer) window.clearTimeout(state.paperNoteSaveTimer);
  state.paperNoteSaveTimer = window.setTimeout(() => savePaperNote({ quiet: true }), 700);
}

function applyMarkdownAction(action) {
  state.paperNoteEditor?.apply(action);
}

function syncPaperNoteDrawerBounds() {
  const reader = document.querySelector(".reader-panel");
  const drawer = $("paper-note-drawer");
  if (!reader || !drawer) return;
  const savedRatio = Number(localStorage.getItem(STORAGE.noteHeight));
  const ratio = savedRatio >= 0.25 && savedRatio <= 0.75 ? savedRatio : 1 / 3;
  drawer.style.setProperty("--paper-note-height", `${ratio * 100}%`);
}

function setCopilotWidth(width, persist = true) {
  const grid = $("workbench-grid");
  const maxWidth = Math.max(360, Math.min(960, window.innerWidth - 810));
  const next = Math.min(maxWidth, Math.max(300, Number(width) || 400));
  grid.style.setProperty("--copilot-width", `${Math.round(next)}px`);
  if (persist) localStorage.setItem(STORAGE.copilotWidth, String(Math.round(next)));
}

function bindPaperNoteResize() {
  const handle = $("paper-note-drag-handle");
  const drawer = $("paper-note-drawer");
  const reader = document.querySelector(".reader-panel");
  if (!handle || !drawer || !reader) return;
  let startY = 0;
  let startHeight = 0;

  const finish = (event) => {
    if (!handle.classList.contains("is-dragging")) return;
    handle.classList.remove("is-dragging");
    if (handle.hasPointerCapture?.(event.pointerId)) handle.releasePointerCapture(event.pointerId);
    const readerHeight = reader.getBoundingClientRect().height || 1;
    const ratio = drawer.getBoundingClientRect().height / readerHeight;
    localStorage.setItem(STORAGE.noteHeight, String(Math.min(0.75, Math.max(0.25, ratio))));
  };

  handle.addEventListener("pointerdown", (event) => {
    if (!drawer.classList.contains("is-open")) return;
    startY = event.clientY;
    startHeight = drawer.getBoundingClientRect().height;
    handle.classList.add("is-dragging");
    handle.setPointerCapture?.(event.pointerId);
    event.preventDefault();
  });
  handle.addEventListener("pointermove", (event) => {
    if (!handle.classList.contains("is-dragging")) return;
    const readerHeight = reader.getBoundingClientRect().height;
    const next = Math.min(readerHeight * 0.75, Math.max(readerHeight * 0.25, startHeight + startY - event.clientY));
    drawer.style.setProperty("--paper-note-height", `${next}px`);
  });
  handle.addEventListener("pointerup", finish);
  handle.addEventListener("pointercancel", finish);
}

async function fetchResearchJson(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `请求失败（HTTP ${response.status}）`);
  return data;
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
  renderPaperMetadata();
  const paper = state.paper || {};
  const tags = $("side-paper-tags");
  tags.replaceChildren();
  [...(paper.categories || []).slice(0, 3), paper.venue].filter(Boolean).forEach((tag) => tags.append(create("span", "", tag)));
  renderOutline();
  renderSections();
  renderReflowSections();
  renderProgress();
  syncSkillControls();
  setReaderMode(state.readerMode || "pdf");
  renderReadingMap();
  startParsePolling();
  updateSessionBadge();
}

function renderPaperMetadata() {
  const paper = state.paper || {};
  const parsing = ["queued", "pending", "parsing"].includes(paper.parse_status || state.parseStatus);
  const sections = Array.isArray(paper.sections) ? paper.sections : [];
  const meta = [paperSourceLabel(paper.source)];
  if (parsing) meta.push("正在解析论文信息");
  else {
    if (paper.year) meta.push(String(paper.year));
    if (sections.length) meta.push(`${sections.length} 个章节`);
  }
  const reparseButton = $("reparse-paper-button");
  if (reparseButton) {
    reparseButton.disabled = parsing || !state.paperId;
    reparseButton.textContent = parsing ? "正在解析…" : "重新解析论文";
  }
  $("paper-ribbon-title").textContent = paper.title || "论文已上传";
  $("paper-ribbon-meta").textContent = meta.join(" · ");
  $("side-paper-title").textContent = paper.title || "论文已上传";
  const authors = (paper.authors || []).filter(Boolean);
  $("side-paper-authors").textContent = authors.join("、");
  $("side-paper-authors").hidden = !authors.length;
}

function renderOutline() {
  const container = $("paper-outline");
  container.replaceChildren();
  renderOutlineSourceWarning();
  const sections = state.paper?.sections || [];
  if (!sections.length && ["queued", "pending", "parsing"].includes(state.parseStatus)) {
    const pending = create("div", "outline-pending", "正在生成章节索引…");
    container.append(pending);
    $("outline-count").textContent = "";
    $("outline-count").hidden = true;
    return;
  }
  const statuses = state.progress?.section_statuses || {};
  sections.forEach((section, index) => {
    const isCompleted = statuses[section.section_id] === "completed";
    const label = outlineLabel(section);
    const button = create("button", `outline-item${section.section_id === state.currentSection ? " is-active" : ""}${isCompleted ? " is-completed" : ""}`);
    button.type = "button";
    button.style.paddingLeft = `${Math.min(Math.max(section.level || 1, 1), 4) * 0.45}rem`;
    const icon = create("span", "outline-state", label);
    button.append(icon, create("span", "outline-title", outlineTitle(section, label) || section.title || "未命名章节"));
    button.addEventListener("click", () => {
      if (isReferenceSection(section)) jumpToPdfPage(section.start_page || 1, section.section_id);
      else selectSection(section.section_id, false);
    });
    container.append(button);
  });
  $("outline-count").textContent = sections.length ? String(sections.length) : "";
  $("outline-count").hidden = !sections.length;
}

function outlineLabel(section = {}) {
  const explicit = String(section.outline_label || section.display_label || section.number || "").trim();
  if (explicit) return explicit;
  const title = String(section.title || "").trim();
  const numeric = title.match(/^(\d+(?:\.\d+)*)(?:\.|\s)+/);
  if (numeric) return numeric[1];
  const appendix = title.match(/^(Appendix\s+[A-Z]|[A-Z])(?:\.|\s)+/i);
  if (appendix && /^appendix\b/i.test(title)) return appendix[1].replace(/\s+/, " ");
  const lower = title.toLowerCase();
  if (lower === "abstract") return "Abs";
  if (lower === "contents" || lower === "table of contents") return "TOC";
  if (lower === "references" || lower === "bibliography") return "Ref";
  const sectionId = String(section.section_id || "");
  const idNumber = sectionId.match(/^sec:(\d+(?:\.\d+)*)\b/);
  if (idNumber) return idNumber[1];
  if (/^sec:abstract\b/.test(sectionId)) return "Abs";
  if (/^sec:references\b/.test(sectionId)) return "Ref";
  return "•";
}

function outlineTitle(section = {}, label = "") {
  const title = String(section.title || "").trim();
  if (!title || !label || ["Abs", "TOC", "Ref", "•"].includes(label)) return title;
  const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+");
  return title.replace(new RegExp(`^${escaped}(?:\\.|\\s)+`), "").trim();
}

function renderOutlineSourceWarning() {
  const warning = $("outline-source-warning");
  if (!warning) return;
  const status = state.sectionExtractionStatus || state.paper?.section_extraction_status || "";
  const source = state.sectionExtractionSource || state.paper?.section_extraction_source || "";
  const message = state.sectionExtractionMessage || state.paper?.section_extraction_message || "";
  warning.classList.remove("is-error", "is-ok", "is-info", "is-pending");
  if (["queued", "pending", "parsing"].includes(state.parseStatus)) {
    warning.textContent = "正在检测 PDF 内置目录…";
    warning.classList.add("is-pending");
    warning.hidden = false;
    return;
  }
  if (source === "pdf_outline" || status === "outline_used") {
    warning.textContent = `已使用 PDF 内置目录${state.outlineEntriesCount ? ` · ${state.outlineEntriesCount} 项` : ""}`;
    warning.classList.add("is-ok");
    warning.hidden = false;
    return;
  }
  if (source === "heuristic" || status.includes("fallback")) {
    warning.textContent = message || "PDF 未提供内置目录，已根据正文标题生成章节索引。";
    warning.classList.add("is-info");
    warning.hidden = false;
    return;
  }
  warning.hidden = true;
}

function readingMapGenerationStatus() {
  const map = state.readingMap || state.paper?.reading_map || {};
  return state.readingMapStatus === "llm_running" ? "llm_running" : (map.status || state.parseStatus || "pending");
}

function toggleReadingMapPanel(forceExpanded) {
  state.readingMapExpanded = typeof forceExpanded === "boolean" ? forceExpanded : !state.readingMapExpanded;
  syncReadingMapPanelState();
  if (state.readingMapExpanded) $("reading-map-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function syncReadingMapPanelState() {
  const panel = $("reading-map-panel");
  if (!panel) return;
  const map = state.readingMap || state.paper?.reading_map || {};
  const isSurvey = map.map_variant === "survey";
  const expanded = Boolean(state.readingMapExpanded);
  const label = isSurvey ? "综述地图" : "研究总览";
  panel.classList.toggle("is-collapsed", !expanded);
  const panelButton = $("reading-map-toggle-button");
  if (panelButton) {
    panelButton.textContent = expanded ? "收起" : "展开";
    panelButton.setAttribute("aria-expanded", String(expanded));
  }
}

function isReadingMapReady() {
  const map = state.readingMap || state.paper?.reading_map || {};
  return readingMapGenerationStatus() === "llm_done" && !missingRequiredSurveyMapGroups(map).length;
}

function isReadingMapFailed() {
  const status = readingMapGenerationStatus();
  const map = state.readingMap || state.paper?.reading_map || {};
  return ["failed", "failed_partial"].includes(status)
    || (status === "llm_done" && Boolean(missingRequiredSurveyMapGroups(map).length));
}

function missingRequiredSurveyMapGroups(map = state.readingMap || state.paper?.reading_map || {}) {
  if (!map || map.map_variant !== "survey") return [];
  return surveyReadingMapGroups(map)
    .filter((group) => !(group.items || []).some((item) => isRenderableReadingMapItem(item, group.key)))
    .map((group) => group.title);
}

function hasPartialReadingMapContent(map = state.readingMap || state.paper?.reading_map || {}) {
  if (!map || map.map_variant !== "survey" || !map.partial) return false;
  const survey = map.survey_map || {};
  const hasMapItems = Boolean(survey.field_overview && Object.keys(survey.field_overview).length)
    || ["development_timeline", "pain_points", "taxonomy", "technical_routes", "representative_methods", "datasets", "evaluation_protocols", "applications", "open_challenges"]
      .some((key) => Array.isArray(survey[key]) && survey[key].length);
  const prerequisite = map.prerequisite_card || {};
  const hasPrerequisite = Boolean(Object.values(prerequisite).some((value) => Array.isArray(value) ? value.length : Boolean(value)));
  return hasPrerequisite || hasMapItems || Boolean((map.section_guides || []).some((guide) => (guide.cards || []).length));
}

function isReadingMapDisplayable() {
  return isReadingMapReady() || hasPartialReadingMapContent();
}

function isReadingMapTimedOut() {
  const heartbeatAt = Date.parse(state.readingMapHeartbeatAt || "");
  const lastActivityAt = Number.isFinite(heartbeatAt) ? heartbeatAt : state.readingMapStartedAt;
  return readingMapGenerationStatus() === "llm_running"
    && lastActivityAt
    && Date.now() - lastActivityAt > READING_MAP_STALE_AFTER_MS;
}

function readingMapPhaseText() {
  const labels = {
    queued: "已进入生成队列",
    planning_sections: "正在规划综述卡片",
    generating_cards: "正在生成综述卡片",
    extracting_sections: "正在全文抽取章节事实",
    merging_facts: "正在聚合综述事实",
    finalizing_map: "正在生成最终导读地图",
    llm_done: "生成完成",
    failed: "生成失败",
    failed_partial: "部分生成失败",
  };
  const base = labels[state.readingMapPhase] || "正在生成导读地图与智能索引";
  const percentText = state.readingMapProgress ? `${state.readingMapProgress}%` : "";
  const cardText = readingMapCardProgressText();
  return [base, percentText, cardText].filter(Boolean).join(" · ");
}

function readingMapCardProgressText() {
  const progress = state.readingMapCardProgress || state.paper?.reading_map_card_progress || {};
  const total = Number(progress.total || 0);
  if (!total) return "";
  const completed = Number(progress.completed || 0);
  const failed = Number(progress.failed || 0);
  const current = progress.current_title ? `当前：${progress.current_title}` : "";
  const failedText = failed ? `失败 ${failed}` : "";
  return [`${completed}/${total} 卡片`, failedText, current].filter(Boolean).join(" · ");
}

function renderSections() {
  const reader = $("structured-reader");
  reader.replaceChildren();
  const sections = (state.paper?.sections || []).filter((section) => !isReferenceSection(section));
  if (!sections.length) {
    reader.append(create("div", "empty-state", "没有解析到章节索引，请在 PDF 原文中阅读并划选。"));
    return;
  }
  const prerequisite = isReadingMapDisplayable() ? renderPrerequisiteCard(state.readingMap?.prerequisite_card || state.paper?.reading_map?.prerequisite_card) : null;
  if (prerequisite) reader.append(prerequisite);
  const indexSections = state.paperIndex?.sections || [];
  sections.forEach((section, index) => {
    const indexed = indexSections.find((item) => item.section_id === section.section_id) || {};
    const guide = sectionGuide(section.section_id);
    const level = Math.min(Math.max(Number(section.level) || 1, 1), 6);
    const article = create("section", `paper-section index-section level-${level}${section.section_id === state.currentSection ? " is-current" : ""}`);
    article.id = domSectionId(section.section_id);
    article.dataset.sectionId = section.section_id;
    const meta = create("div", "section-meta");
    meta.append(create("span", "", `章节 ${String(index + 1).padStart(2, "0")}`));
    article.append(meta, create("h2", "", section.title || `Section ${index + 1}`));

    const body = create("div", "paper-section-body");
    const summary = create("p", "index-section-summary", sectionSummaryText(section, indexed, guide));
    body.append(summary);
    const renderedGuide = renderSectionGuide(guide, indexed);
    if (renderedGuide) body.append(renderedGuide);
    const actions = create("div", "index-section-actions");
    const jump = create("button", "figure-source-button", section.start_page ? `跳转 PDF 第 ${section.start_page} 页` : "查看 PDF 原文");
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

function renderReflowSections() {
  const reader = $("reflow-reader");
  reader.replaceChildren();
  if (!usesMineruReflow()) {
    reader.append(create("div", "empty-state", "当前论文没有可用的 MinerU AI 重排结果。"));
    return;
  }
  const sections = (state.paper?.sections || []).filter((section) => !isReferenceSection(section));
  const contentSections = sections.filter((section) => strHasContent(section.content));
  if (!contentSections.length) {
    reader.append(create("div", "empty-state", "MinerU 已返回章节索引，但没有可展示的重排正文。"));
    return;
  }
  contentSections.forEach((section, index) => {
    const level = Math.min(Math.max(Number(section.level) || 1, 1), 6);
    const article = create("section", `paper-section ai-reflow-section level-${level}${section.section_id === state.currentSection ? " is-current" : ""}`);
    article.dataset.sectionId = section.section_id;
    const meta = create("div", "section-meta");
    meta.append(create("span", "", `AI 重排 ${String(index + 1).padStart(2, "0")}`));
    article.append(meta, create("h2", "", section.title || `Section ${index + 1}`));
    const body = create("div", "paper-section-body ai-reflow-content");
    body.append(renderMineruMarkdown(section.content));
    article.append(body);
    reader.append(article);
  });
  restoreReaderPosition(reader);
}

function renderMineruMarkdown(source) {
  const root = create("div", "mineru-markdown");
  const cleaned = cleanMineruMarkdown(source);
  const segments = cleaned
    .split(/(<table\b[\s\S]*?<\/table>|!\[[^\]]*\]\([^)]+\))/giu)
    .filter(Boolean);
  segments.forEach((segment) => {
    const trimmed = segment.trim();
    if (/^<table\b/iu.test(trimmed)) {
      const table = renderSafeHtmlTable(segment);
      if (table) root.append(table);
      return;
    }
    if (/^!\[[^\]]*\]\([^)]+\)$/u.test(trimmed)) {
      const image = renderMineruImage(trimmed);
      if (image) root.append(image);
      return;
    }
    const rendered = renderMarkdown(segment);
    if (rendered.textContent?.trim() || rendered.querySelector("img,table")) root.append(rendered);
  });
  return root;
}

function renderMineruImage(source) {
  const match = source.match(/^!\[([^\]]*)\]\(([^)]+)\)$/u);
  if (!match) return null;
  let url;
  try {
    url = new URL(match[2], window.location.origin);
  } catch (_) {
    return null;
  }
  if (url.origin !== window.location.origin || !url.pathname.startsWith("/paper_reading/figures/")) {
    return null;
  }
  const figure = create("figure", "mineru-figure");
  const image = create("img");
  image.src = `${url.pathname}${url.search}`;
  image.alt = match[1].trim() || "论文图表";
  image.loading = "lazy";
  image.decoding = "async";
  figure.append(image);
  if (match[1].trim()) figure.append(create("figcaption", "", match[1].trim()));
  return figure;
}

function cleanMineruMarkdown(source) {
  return String(source || "")
    .replace(/<details>\s*<summary>line<\/summary>[\s\S]*?<\/details>/giu, "")
    .replace(/!\[[^\]]*\]\(images\/[^)]+\)/giu, "\n\n> 该图的图片资源未随 MinerU Markdown 返回，请切换到 PDF 原文查看。\n\n")
    .replace(/^Received\s+month\s+dd,\s*yyyy;.*$/gimu, "")
    .replace(/^E-?mail\s*:.*$/gimu, "")
    .replace(/^\\?\*?Both authors contribute equally to this paper\.?$/gimu, "")
    .replace(/\b([A-Za-z]{2,4})-[ \t]*\n+[ \t]*([a-z]{2,})(?![-A-Za-z])/gu, "$1$2")
    .replace(/([A-Za-z])-[ \t]*\n+[ \t]*(?=[a-z])/gu, "$1-")
    .replace(/\n{3,}/gu, "\n\n")
    .trim();
}

function renderSafeHtmlTable(source) {
  const parsed = new DOMParser().parseFromString(source, "text/html");
  const original = parsed.querySelector("table");
  if (!original) return null;
  const wrapper = create("div", "markdown-table-wrap mineru-table-wrap");
  const table = create("table", "mineru-table");
  original.querySelectorAll("tr").forEach((row) => {
    const safeRow = document.createElement("tr");
    row.querySelectorAll(":scope > th, :scope > td").forEach((cell) => {
      const safeCell = document.createElement(cell.tagName.toLowerCase() === "th" ? "th" : "td");
      safeCell.textContent = cell.textContent?.trim() || "";
      ["rowspan", "colspan"].forEach((attribute) => {
        const value = Number(cell.getAttribute(attribute) || 0);
        if (value > 1 && value <= 50) safeCell.setAttribute(attribute, String(value));
      });
      safeRow.append(safeCell);
    });
    if (safeRow.childElementCount) table.append(safeRow);
  });
  if (!table.childElementCount) return null;
  wrapper.append(table);
  return wrapper;
}

function sectionSummaryText(section, indexed = {}, guide = null) {
  if (isReferenceSection(section)) {
    return "参考文献属于检索型补充内容，因此不单独生成导读，请直接查看 PDF 原文。";
  }
  if (!isReadingMapDisplayable()) {
    return isReadingMapFailed()
      ? "智能索引生成失败，请点击“重新生成”。"
      : isReadingMapTimedOut()
        ? "智能索引生成已超时，可以点击“重新生成”。"
      : "智能索引正在生成中，完成前不展示临时 fallback 内容。";
  }
  return guide
    ? "下方是面向科研新手的章节导读。"
    : strHasContent(section.content)
      ? "系统根据该小节与论文主线的关联度，将其归为补充阅读，因此未单独生成导读，可查看 PDF 原文或直接分析本节。"
      : "该条目主要承担章节组织作用，因此未单独生成导读，可查看子章节、PDF 原文或直接分析本节。";
}

function sectionGuide(sectionId) {
  if (!isReadingMapDisplayable()) return null;
  const guides = state.readingMap?.section_guides || state.paper?.reading_map?.section_guides || [];
  const guide = guides.find((item) => item.section_id === sectionId) || null;
  return guide && Array.isArray(guide.cards) && guide.cards.length ? guide : null;
}

function renderSectionGuide(guide, indexed = {}) {
  if (!guide || !Array.isArray(guide.cards) || !guide.cards.length) return null;
  const wrap = create("div", "section-guide");
  guide.cards.slice(0, 6).forEach((card) => wrap.append(renderGuideCard(card)));
  return wrap;
}

function isReferenceSection(section = {}) {
  const title = String(section.title || "").toLowerCase();
  const sectionId = String(section.section_id || "").toLowerCase();
  return title.includes("reference")
    || title.includes("bibliography")
    || title.includes("参考文献")
    || /^sec:(references|bibliography)\b/.test(sectionId);
}

function strHasContent(value) {
  return Boolean(String(value || "").trim());
}

function renderPrerequisiteCard(card) {
  if (!card || typeof card !== "object") return null;
  const anchorWorks = card.anchor_works || card.baseline_papers || [];
  const hasContent = (card.concepts || []).length
    || (card.field_questions || []).length
    || anchorWorks.length
    || (card.reading_order || []).length
    || (card.common_confusions || []).length;
  if (!hasContent) return null;
  const article = create("section", "paper-section index-section prerequisite-section");
  article.id = "paper-section-prerequisites";
  article.append(create("div", "section-meta", "Before Reading"), create("h2", "", "前置知识"));
  const body = create("div", "paper-section-body");
  const guide = create("div", "section-guide prerequisite-guide");
  (card.concepts || []).slice(0, 8).forEach((concept) => {
    guide.append(renderGuideCard({
      card_type: "prerequisite_concept",
      title: concept.name || "前置概念",
      content: {
        why_needed: concept.why_needed,
        learn_first: concept.learn_first,
        difficulty: concept.difficulty,
        evidence: concept.evidence,
        /*
        "为什么需要": concept.why_needed,
        "先学": concept.learn_first,
        "难度": concept.difficulty,
        */
      },
    }));
  });
  if ((card.baseline_papers || []).length) {
    guide.append(renderGuideCard({
      card_type: "baseline_papers",
      title: "相关论文",
      content: { papers: card.baseline_papers.slice(0, 8) },
    }));
  }
  if ((card.field_questions || []).length) {
    guide.append(renderGuideCard({
      card_type: "field_questions",
      title: "Field Questions",
      content: { questions: card.field_questions.slice(0, 8) },
    }));
  }
  if (anchorWorks.length && !(card.baseline_papers || []).length) {
    guide.append(renderGuideCard({
      card_type: "anchor_works",
      title: "Anchor Works",
      content: { works: anchorWorks.slice(0, 8) },
    }));
  }
  if ((card.reading_order || []).length) {
    guide.append(renderGuideCard({
      card_type: "reading_route",
      title: "建议阅读顺序",
      content: { steps: card.reading_order },
    }));
  }
  if ((card.common_confusions || []).length) {
    guide.append(renderGuideCard({
      card_type: "common_confusions",
      title: "Common Confusions",
      content: { confusions: card.common_confusions.slice(0, 8) },
    }));
  }
  body.append(guide);
  article.append(body);
  return article;
}

function legacyGuideCards(guide) {
  const cards = [];
  if (guide.main_content || guide.novice_summary) {
    cards.push({ card_type: "reading_route", title: "本节怎么读", content: { summary: guide.novice_summary || guide.main_content, focus: guide.novice_focus } });
  }
  if (guide.technical_route || guide.implementation_plan) {
    cards.push({ card_type: "method_architecture", title: "技术路线", content: { technical_route: guide.technical_route, implementation_plan: guide.implementation_plan } });
  }
  if ((guide.datasets || []).length || (guide.baselines || []).length || (guide.metrics || []).length) {
    cards.push({ card_type: "experiment_dataset", title: "实验与数据", content: { datasets: guide.datasets, baselines: guide.baselines, metrics: guide.metrics, protocol: guide.experiment_protocol } });
  }
  return cards;
}

function renderGuideCard(card) {
  const type = String(card?.card_type || "reading_route");
  const item = create("section", `section-guide-item guide-card guide-card-${type}`);
  const title = card?.title || guideCardLabel(type);
  item.append(create("strong", "", title));
  item.append(renderReadableValue(card?.content ?? card, "guide-card-body", guideCardFieldOrder(type)));
  const sources = Array.isArray(card?.source_sections) ? card.source_sections : [];
  const sourceText = sources.map((entry) => entry.title || entry.section_id || (entry.page ? `Page ${entry.page}` : "")).filter(Boolean).slice(0, 2).join(" · ");
  if (sourceText) item.append(create("small", "guide-card-source", sourceText));
  return item;
}

function renderReadableValue(value, className = "", preferredKeys = []) {
  const wrap = create("div", className);
  if (value == null || value === "") {
    wrap.append(create("p", "", "暂无"));
    return wrap;
  }
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    wrap.append(create("p", "", String(value)));
    return wrap;
  }
  if (Array.isArray(value)) {
    if (!value.length) {
      wrap.append(create("p", "", "暂无"));
      return wrap;
    }
    const list = create("ul", "compact-list");
    value.slice(0, 12).forEach((entry) => {
      const li = create("li");
      if (entry && typeof entry === "object") li.append(renderReadableObjectLine(entry));
      else li.textContent = String(entry);
      list.append(li);
    });
    wrap.append(list);
    return wrap;
  }
  orderedReadableEntries(value || {}, preferredKeys).forEach(([key, item]) => {
    if (item == null || item === "" || (Array.isArray(item) && !item.length)) return;
    const block = create("div", "readable-field");
    block.append(create("span", "", humanizeCardKey(key)));
    block.append(renderReadableValue(item, "readable-field-value"));
    wrap.append(block);
  });
  if (!wrap.childNodes.length) wrap.append(create("p", "", "暂无"));
  return wrap;
}

function orderedReadableEntries(value, preferredKeys = []) {
  const entries = Object.entries(value || {});
  const used = new Set();
  const ordered = [];
  preferredKeys.forEach((key) => {
    if (Object.prototype.hasOwnProperty.call(value, key)) {
      ordered.push([key, value[key]]);
      used.add(key);
    }
  });
  entries.forEach((entry) => {
    if (!used.has(entry[0])) ordered.push(entry);
  });
  return ordered;
}

function guideCardFieldOrder(type) {
  const common = ["core_message", "why_it_matters", "key_points", "connections", "next_reading", "summary", "focus"];
  const orders = {
    reading_route: ["core_message", "summary", "read_priority", "why_it_matters", "key_points", "next_reading", "quality_note"],
    field_timeline: ["stage", "time_range", "key_change", "representative_works", "why_it_matters", ...common],
    taxonomy_node: ["category", "basis", "typical_methods", "solved_problems", "limitations", ...common],
    route_comparison: ["name", "core_mechanism", "typical_pipeline", "strengths", "weaknesses", "representative_methods", ...common],
    paper_method_table: ["paper_title", "year", "method_name", "route", "method_summary", "specific_solution", "improves_on", "remaining_limits", ...common],
    dataset_catalog: ["name", "task", "content", "structure", "scale", "metrics", ...common],
    benchmark_protocol: ["protocol", "task", "metrics", "setting", "what_it_tests", ...common],
    challenge_card: ["challenge", "problem", "why_hard", "why_it_matters", "current_bottleneck", "future_direction", ...common],
    application_landscape: ["application", "scenario", "why_suitable", "typical_methods", "constraints", ...common],
    future_direction: ["direction", "challenge", "why_it_matters", "next_reading", ...common],
  };
  return orders[type] || common;
}

function renderReadableObjectLine(value) {
  const line = create("span", "readable-object-line");
  const title = value.title || value.paper_title || value.method_name || value.name || value.stage || value.time_range || value.problem || value.category || value.route || value.protocol || value.application || value.challenge || "";
  const details = value.why_read || value.method_summary || value.specific_solution || value.key_change || value.core_mechanism || value.summary || value.core_idea || value.relationship || value.task || value.structure || value.metrics || "";
  if (title) {
    if (value.url) {
      const link = create("a", "", String(title));
      link.href = value.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      line.append(link);
    } else {
      line.append(create("strong", "", String(title)));
    }
    if (details) line.append(document.createTextNode(`：${details}`));
  } else {
    line.textContent = Object.values(value).filter((item) => typeof item !== "object").slice(0, 3).join(" · ") || "条目";
  }
  return line;
}

function guideCardLabel(type) {
  return {
    abstract_takeaway: "摘要速读",
    intro_insight: "引言洞察",
    problem_formulation: "问题定义",
    method_architecture: "方法结构",
    algorithm_steps: "算法步骤",
    innovation_detail: "改进细节",
    experiment_dataset: "数据集信息",
    experiment_design: "实验设计",
    result_interpretation: "结果解读",
    limitation_reflection: "局限反思",
    field_timeline: "发展脉络",
    taxonomy_node: "分类体系",
    route_comparison: "技术路线",
    paper_method_table: "代表论文方法",
    dataset_catalog: "数据集目录",
    benchmark_protocol: "评测协议",
    challenge_card: "难点痛点",
    application_landscape: "应用场景",
    future_direction: "未来方向",
    reading_route: "阅读路线",
    prerequisite_concept: "前置概念",
    baseline_papers: "相关论文",
  }[type] || "阅读卡片";
}

function humanizeCardKey(key) {
  const labels = {
    core_message: "核心信息",
    why_it_matters: "为什么重要",
    key_points: "关键点",
    connections: "关联章节/路线",
    next_reading: "后续阅读",
    quality_note: "质量提示",
    summary: "摘要",
    focus: "阅读重点",
    read_priority: "阅读优先级",
    steps: "步骤",
    papers: "论文",
    datasets: "数据集",
    baselines: "Baseline",
    metrics: "指标",
    protocol: "协议",
    task: "任务",
    content: "内容",
    structure: "结构",
    scale: "规模",
    setting: "设置",
    what_it_tests: "评测目标",
    technical_route: "技术路线",
    implementation_plan: "实现方案",
    main_point: "核心内容",
    details: "展开说明",
    terms: "关键词",
    category: "类别",
    basis: "分类依据",
    typical_methods: "典型方法",
    solved_problems: "适合问题",
    limitations: "局限",
    core_mechanism: "核心机制",
    typical_pipeline: "典型流程",
    strengths: "优点",
    weaknesses: "局限",
    paper_title: "论文标题",
    method_name: "方法名称",
    method_summary: "方法摘要",
    specific_solution: "具体方案",
    improves_on: "改进对象",
    remaining_limits: "剩余局限",
    time_range: "时间范围",
    key_change: "关键变化",
    representative_works: "代表工作",
    why_hard: "难点原因",
    impact: "影响",
    existing_attempts: "已有尝试",
    unresolved_part: "未解决部分",
    current_bottleneck: "当前瓶颈",
    future_direction: "未来方向",
  };
  return labels[key] || humanizeKey(key);
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
    if (state.currentSection && reader.querySelector(`[data-section-id="${cssEscape(state.currentSection)}"]`)) {
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

async function renderPdf(force = false, options = {}) {
  setupPdfFirstReaderDom();
  const host = $("pdf-document");
  const fit = state.pdfZoom ? String(state.pdfZoom) : ($("pdf-fit-select").value || "width");
  const baseUrl = state.pdfUrl.split("#", 1)[0];
  const anchor = options.preserveViewport ? capturePdfViewportAnchor() : null;
  const preserveViewport = Boolean(options.preserveViewport && anchor);
  $("pdf-empty").hidden = state.hasPdf;
  if (!state.hasPdf || !host) {
    if (host) host.replaceChildren();
    return;
  }
  const renderKey = `${baseUrl}::${fit}::${host.clientWidth}`;
  if (!force && state.pdfRenderedKey === renderKey && host.childElementCount) {
    if (state.pendingPdfPage) scrollPdfToPage(state.pendingPdfPage, false);
    state.pendingPdfPage = null;
    return;
  }
  if (!force && state.pdfRenderingKey === renderKey) {
    requestAnimationFrame(() => scrollPdfToPage(state.pendingPdfPage || currentPdfPage(), false));
    return;
  }
  const generation = state.pdfRenderGeneration + 1;
  state.pdfRenderGeneration = generation;
  state.pdfRenderingKey = renderKey;
  if (!preserveViewport) host.replaceChildren(create("div", "pdf-loading", "正在加载 PDF 原文…"));
  try {
    const pdfjsLib = await ensurePdfJs();
    if (!state.pdfDoc || state.pdfDocUrl !== baseUrl) {
      const source = await loadCachedPdfSource(baseUrl);
      state.pdfDoc = await pdfjsLib.getDocument(source).promise;
      state.pdfDocUrl = baseUrl;
    }
    if (generation !== state.pdfRenderGeneration) return;

    const firstPage = await state.pdfDoc.getPage(1);
    const fallbackViewport = pdfViewportForPage(firstPage, fit, host);
    const placeholders = [];
    for (let pageNumber = 1; pageNumber <= state.pdfDoc.numPages; pageNumber += 1) {
      const placeholder = create("section", "pdf-page pdf-page-placeholder");
      placeholder.dataset.pageNumber = String(pageNumber);
      placeholder.style.width = `${fallbackViewport.width}px`;
      placeholder.style.height = `${fallbackViewport.height}px`;
      placeholder.append(create("span", "pdf-page-placeholder-label", `第 ${pageNumber} 页`));
      placeholders.push(placeholder);
    }
    host.replaceChildren(...placeholders);
    const targetPage = Number(state.pendingPdfPage || anchor?.page || currentPdfPage()) || 1;
    scrollPdfToPage(targetPage, false);
    const pageOrder = Array.from({ length: state.pdfDoc.numPages }, (_, index) => index + 1)
      .sort((left, right) => Math.abs(left - targetPage) - Math.abs(right - targetPage));
    for (const pageNumber of pageOrder) {
      if (generation !== state.pdfRenderGeneration) return;
      const placeholder = host.querySelector(`[data-page-number="${pageNumber}"]`);
      await renderPdfPage(pdfjsLib, state.pdfDoc, pageNumber, fit, host, host, placeholder);
      if (pageNumber === targetPage) scrollPdfToPage(targetPage, false);
    }
    state.pdfRenderedKey = renderKey;
    state.pdfRenderingKey = "";
    $("pdf-frame").hidden = true;
    if (preserveViewport) restorePdfViewportAnchor(anchor);
    else scrollPdfToPage(targetPage, false);
    state.pendingPdfPage = null;
  } catch (error) {
    if (generation !== state.pdfRenderGeneration) return;
    state.pdfRenderingKey = "";
    console.warn("PDF.js render failed, fallback to iframe.", error);
    host.replaceChildren(create("div", "pdf-loading", "PDF.js 加载失败，已切换到浏览器原生 PDF 预览。"));
    const nextUrl = `${baseUrl}#${pdfFragment(currentPdfPage(), fit)}`;
    if ($("pdf-frame").getAttribute("src") !== nextUrl) $("pdf-frame").src = nextUrl;
    $("pdf-frame").hidden = false;
  }
}

function paperSourceLabel(source) {
  const labels = {
    upload: "本地上传",
    arxiv: "arXiv",
    semantic_scholar: "Semantic Scholar",
    dblp: "DBLP",
    openalex: "OpenAlex",
  };
  return labels[source] || source || "论文";
}

function paperYearLabel(paper) {
  if (paper?.year) return String(paper.year);
  return ["queued", "pending", "parsing"].includes(paper?.parse_status || state.parseStatus)
    ? "年份解析中"
    : "";
}

async function loadCachedPdfSource(baseUrl) {
  if (!window.caches || !window.fetch) return baseUrl;
  try {
    const request = new Request(baseUrl, { credentials: "same-origin" });
    const cache = await window.caches.open(PDF_CACHE_NAME);
    let response = await cache.match(request);
    if (!response) {
      response = await fetch(request, { cache: "force-cache" });
      if (!response.ok) throw new Error(`PDF 请求失败（HTTP ${response.status}）`);
      await cache.put(request, response.clone());
    }
    return { data: new Uint8Array(await response.arrayBuffer()) };
  } catch (error) {
    console.warn("PDF cache unavailable; loading from URL.", error);
    return baseUrl;
  }
}

function setPdfZoom(value) {
  state.pdfZoom = Math.min(240, Math.max(40, Math.round(Number(value) || 100)));
  syncPdfZoomInput();
  renderPdf(true, { preserveViewport: true });
}

function setPdfFit(value) {
  state.pdfZoom = null;
  $("pdf-fit-select").value = value;
  syncPdfZoomInput();
  renderPdf(true, { preserveViewport: true });
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

function capturePdfViewportAnchor() {
  const host = $("pdf-document");
  if (!host) return null;
  const hostRect = host.getBoundingClientRect();
  const pages = Array.from(host.querySelectorAll(".pdf-page"));
  let best = null;
  let bestDistance = Number.POSITIVE_INFINITY;
  pages.forEach((page) => {
    const rect = page.getBoundingClientRect();
    const distance = Math.abs(rect.top - hostRect.top);
    if (rect.bottom >= hostRect.top && rect.top <= hostRect.bottom && distance < bestDistance) {
      bestDistance = distance;
      best = page;
    }
  });
  if (!best) return { page: currentPdfPage(), ratio: 0 };
  const rect = best.getBoundingClientRect();
  return {
    page: Number(best.dataset.pageNumber || 1),
    ratio: Math.min(1, Math.max(0, (hostRect.top - rect.top) / Math.max(1, rect.height))),
  };
}

function restorePdfViewportAnchor(anchor) {
  const host = $("pdf-document");
  const page = host?.querySelector(`[data-page-number="${Number(anchor?.page) || 1}"]`);
  if (!host || !page) return;
  const pageTop = page.offsetTop;
  host.scrollTo({ top: Math.max(0, pageTop + page.offsetHeight * (anchor.ratio || 0)), behavior: "auto" });
}

function visiblePdfPage() {
  return capturePdfViewportAnchor()?.page || currentPdfPage();
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
  state.pendingPdfPage = page;
  scrollPdfToPage(page);
  if (!$("pdf-frame").hidden) {
    const fit = $("pdf-fit-select").value || "width";
    const baseUrl = state.pdfUrl.split("#", 1)[0];
    const nextUrl = `${baseUrl}#${pdfFragment(page, fit)}`;
    if ($("pdf-frame").getAttribute("src") !== nextUrl) $("pdf-frame").src = nextUrl;
  }
}

function setReaderMode(mode, options = {}) {
  const requestedMode = mode === "reflow" ? (usesMineruReflow() ? "reflow" : "structured") : mode;
  const normalizedMode = ["pdf", "structured", "reflow"].includes(requestedMode) ? requestedMode : "structured";
  const isPdf = normalizedMode === "pdf";
  const isReflow = normalizedMode === "reflow";
  if (options.userInitiated) state.readerModeChosen = true;
  state.readerMode = normalizedMode;
  updateReaderModeHint();
  $("structured-reader").hidden = isPdf || isReflow;
  $("reflow-reader").hidden = !isReflow;
  $("pdf-reader").hidden = !isPdf;
  $("pdf-fit-control").hidden = !isPdf || !state.hasPdf;
  $("pdf-mode-hint").hidden = false;
  document.querySelectorAll("[data-reader-mode]").forEach((button) => button.classList.toggle("is-active", button.dataset.readerMode === normalizedMode));
  if (isPdf) {
    renderPdf();
    syncPdfToSection(state.currentSection);
  } else {
    requestAnimationFrame(() => scrollReaderToSection(state.currentSection, false));
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

function pdfViewportForPage(page, fit, measureHost) {
  const baseViewport = page.getViewport({ scale: 1 });
  const availableWidth = Math.max(360, measureHost.clientWidth - 34);
  const widthScale = availableWidth / baseViewport.width;
  const numericZoom = /^\d+$/.test(String(fit)) ? Number(fit) : 0;
  const scale = numericZoom
    ? numericZoom / 100 * 1.35
    : fit === "page"
      ? Math.min(widthScale, Math.max(0.7, (measureHost.clientHeight - 42) / baseViewport.height))
      : widthScale;
  return page.getViewport({ scale });
}

async function renderPdfPage(pdfjsLib, pdfDoc, pageNumber, fit, measureHost, appendTarget = measureHost, placeholder = null) {
  const page = await pdfDoc.getPage(pageNumber);
  const viewport = pdfViewportForPage(page, fit, measureHost);
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
  textLayer.style.setProperty("--scale-factor", String(viewport.scale));
  const markLayer = create("div", "pdf-mark-layer");
  pageShell.append(canvas, textLayer, markLayer);
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
  if (placeholder?.isConnected) placeholder.replaceWith(pageShell);
  else appendTarget.append(pageShell);
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
  state.pendingPdfPage = Number(page) || currentPdfPage();
  setReaderMode("pdf");
  renderOutline();
  syncComposerContext();
  scrollPageToPdfReader();
  requestAnimationFrame(() => {
    scrollPageToPdfReader();
    scrollPdfToPage(state.pendingPdfPage || currentPdfPage());
  });
  window.setTimeout(scrollPageToPdfReader, 120);
  window.setTimeout(scrollPageToPdfReader, 360);
}

function scrollPageToPdfReader() {
  // Source jumps should return to the top workbench area so the PDF reader is
  // visible even when the overview panel is expanded above it.
  const target = $("workbench-grid") || $("pdf-reader");
  const scrollRoots = [
    document.scrollingElement,
    document.documentElement,
    document.body,
    document.fullscreenElement,
  ].filter(Boolean);
  scrollRoots.forEach((root) => {
    root.scrollTop = 0;
    if (typeof root.scrollTo === "function") root.scrollTo({ top: 0, left: 0, behavior: "auto" });
  });
  window.scrollTo({ top: 0, left: 0, behavior: "auto" });
  target?.scrollIntoView({ behavior: "auto", block: "start", inline: "nearest" });
}

async function selectSection(sectionId, analyze) {
  state.currentSection = sectionId;
  state.sourceView = state.readerMode || "pdf";
  persistState();
  renderOutline();
  document.querySelectorAll(".paper-section").forEach((section) => section.classList.toggle("is-current", section.dataset.sectionId === sectionId));
  if (state.readerMode === "pdf") syncPdfToSection(sectionId);
  else scrollReaderToSection(sectionId);
  syncComposerContext();
  if (analyze) await startReading(
    `请精读“${sectionTitle(sectionId)}”，说明核心内容、论证结构和需要重点理解的概念。`,
    state.sessionId,
    { sectionAnalysis: true },
  );
}

function moveSection(offset) {
  const allSections = state.paper?.sections || [];
  const sections = state.readerMode === "structured"
    ? allSections.filter((section) => !isReferenceSection(section))
    : allSections;
  if (!sections.length) return;
  const current = Math.max(0, sections.findIndex((item) => item.section_id === state.currentSection));
  const target = sections[Math.min(sections.length - 1, Math.max(0, current + offset))];
  if (target) selectSection(target.section_id, false);
}

async function startReading(content, sessionId = state.sessionId, options = {}) {
  if (!state.paperId || state.busy) return;
  state.busy = true;
  const interactionType = options.sectionAnalysis ? "section_analysis" : "";
  const detail = options.sectionAnalysis
    ? `正在分析 ${sectionTitle(state.currentSection) || "当前章节"}…`
    : "正在分析…";
  const streaming = showStreamingAnalysisCard(detail, { preferSkillOutput: options.sectionAnalysis });
  const controller = new AbortController();
  state.activeResponseController = controller;
  $("reading-stop-button").hidden = false;
  try {
    const response = await fetch("/paper_reading/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action: "start_reading", session_id: sessionId || "", paper_id: state.paperId,
        target_section: state.currentSection || "", content,
        metadata: { ...selectionMetadata(), interaction_type: interactionType },
      }),
      signal: controller.signal,
    });
    const payload = await window.streamSseJson(
      response,
      (delta) => streaming.append(delta),
      (delta) => streaming.appendReasoning(delta),
    );
    const responseText = payload?.data?.agent_response || "";
    streaming.finish(responseText, payload?.data?.reasoning || "", payload?.skill_outputs || []);
    const visibleResponseText = extractReadableSkillBlocks(responseText).text;
    applyReadingPayload(payload, {
      appendAgent: false,
      appendSkills: !options.sectionAnalysis && !parseStructuredAgentResponse(visibleResponseText),
    });
    toast("章节分析已更新。");
    return payload;
  } catch (error) {
    if (error.name === "AbortError") {
      streaming.interrupt();
      toast("回答已中断。");
    } else {
      streaming.remove();
      toast(error.message, true);
    }
    return null;
  } finally {
    if (state.activeResponseController === controller) state.activeResponseController = null;
    $("reading-stop-button").hidden = true;
    state.busy = false;
  }
}

function interruptReadingResponse() {
  state.activeResponseController?.abort();
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
  await startReading(
    `请分析“${sectionTitle(state.currentSection) || "当前章节"}”，给出核心内容、论证结构、关键证据与需要重点理解的概念。`,
    state.sessionId,
    { sectionAnalysis: true },
  );
}

function showStreamingAnalysisCard(detail, options = {}, target = $("analysis-feed")) {
  if (options instanceof Element) {
    target = options;
    options = {};
  }
  const card = create("article", "analysis-card streaming-analysis-card");
  const header = create("header");
  header.append(create("strong", "", "Synapse Copilot"), create("span", "", detail || "正在分析…"));
  const body = create("div", "streaming-analysis-body");
  body.append(create("p", "thinking-status", "正在思考…"));
  card.append(header, body);
  target.append(card);
  target.scrollTop = target.scrollHeight;
  let text = "";
  let reasoning = "";
  let streaming = true;
  let mergedSkillOutputs = [];
  const render = () => {
    const inline = window.splitVisibleThinking?.(text) || { reasoning: "", answer: text };
    const visibleReasoning = [reasoning, inline.reasoning].filter(Boolean).join("\n\n");
    body.replaceChildren();
    if (visibleReasoning && typeof window.createThinkingDetails === "function") {
      body.append(window.createThinkingDetails(visibleReasoning, streaming));
    } else if (streaming && !inline.answer) {
      body.append(create("p", "thinking-status", detail || "正在思考…"));
    }
    if (options.preferSkillOutput && !streaming) {
      const methodOutput = mergedSkillOutputs.find((output) => output.skill_id === "reading.method_analyst");
      if (inline.answer) {
        body.append(renderAgentResponse(inline.answer, { allowStructured: true }));
      } else if (methodOutput?.rendered) {
        body.append(renderMarkdown(methodOutput.rendered));
      }
    } else if (inline.answer) {
      body.append(renderAgentResponse(inline.answer, { allowStructured: !streaming }));
    }
    target.scrollTop = target.scrollHeight;
  };
  return {
    append(delta) { text += String(delta || ""); render(); },
    appendReasoning(delta) { reasoning += String(delta || ""); render(); },
    finish(finalText, finalReasoning = "", skillOutputs = []) {
      text = String(finalText || text || "后端没有返回内容。");
      reasoning = String(finalReasoning || reasoning || "");
      mergedSkillOutputs = Array.isArray(skillOutputs) ? skillOutputs : [];
      streaming = false;
      card.classList.remove("streaming-analysis-card");
      header.lastElementChild.textContent = options.preferSkillOutput ? "章节分析" : "Agent";
      render();
      requestAnimationFrame(() => scrollAnalysisCardToTop(target, card));
    },
    interrupt() {
      text = text ? `${text}\n\n_回答已中断。_` : "回答已中断。";
      streaming = false;
      card.classList.remove("streaming-analysis-card");
      header.lastElementChild.textContent = "已中断";
      render();
    },
    remove() { card.remove(); },
  };
}

function scrollAnalysisCardToTop(target, card) {
  if (!target || !card?.isConnected) return;
  const top = target.scrollTop
    + card.getBoundingClientRect().top
    - target.getBoundingClientRect().top
    - 8;
  target.scrollTo({ top: Math.max(0, top), behavior: "auto" });
}

function applyReadingPayload(payload, options = {}) {
  const data = payload.data || {};
  const session = payload.session || {};
  state.sessionId = session.session_id || data.session_id || state.sessionId;
  state.sessionState = session.state || "active";
  state.currentSection = data.current_section || session.current_section || state.currentSection;
  state.activeSkills = session.active_skills || state.activeSkills;
  state.progress = payload.progress || state.progress;
  state.skillOutputs = payload.skill_outputs || [];
  persistState();
  if (options.appendAgent !== false) appendAnalysis(data.agent_response || "后端已完成本次阅读操作。", data);
  if (options.appendSkills !== false) renderSkillOutputs(state.skillOutputs, $("analysis-feed"));
  renderProgress();
  renderOutline();
  syncSkillControls();
  updateSessionBadge();
  renderReadingMap();
}

function appendAnalysis(text, metadata = {}, target = $("analysis-feed")) {
  const card = create("article", "analysis-card");
  const header = create("header");
  header.append(create("strong", "", "Synapse Copilot"), create("span", "", metadata.duration_ms ? `${Math.round(metadata.duration_ms)} ms` : "Agent"));
  card.append(header, renderAgentResponse(text));
  target.append(card);
  target.scrollTop = target.scrollHeight;
}

function appendReadingWelcome(target = $("analysis-feed")) {
  const card = create("article", "welcome-card");
  card.append(create("span", "assistant-avatar", "N"));
  const body = create("div");
  body.append(create("strong", "", "继续当前论文精读"));
  body.append(create("p", "", "下面已恢复本次精读的提问与分析记录；你可以继续针对当前章节提问。"));
  card.append(body);
  target.append(card);
}

async function restoreReadingConversationHistory() {
  if (!state.sessionId || state.historyLoadedFor === state.sessionId) return;
  try {
    const response = await fetch(`/api/research/conversations/${encodeURIComponent(state.sessionId)}`, { cache: "no-store" });
    if (!response.ok) return;
    const conversation = await response.json();
    const history = (conversation.messages || []).filter((message) => ["user", "assistant"].includes(message.role));
    const feed = $("analysis-feed");
    if (!feed) return;
    feed.replaceChildren();
    appendReadingWelcome(feed);
    history.forEach((message) => {
      if (message.role === "user") appendUserQuestion(message.content, feed);
      else appendAnalysis(message.content, { restored: true }, feed);
    });
    feed.scrollTop = 0;
    state.historyLoadedFor = state.sessionId;
  } catch {
    // Keep the built-in welcome card when persisted history is temporarily unavailable.
  }
}

function renderSkillOutputs(outputs, target) {
  dedupeSkillOutputs(outputs).forEach((output) => {
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

function dedupeSkillOutputs(outputs = []) {
  const seen = new Set();
  return (outputs || []).filter((output) => {
    const key = output?.skill_id || output?.output_type || "";
    if (!key) return true;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function renderSkillContent(output) {
  const content = output.output_type === "method_pipeline"
      ? renderMethodPipeline(output.content)
      : output.output_type === "math_derivation"
      ? renderMathTabs(output.content)
      : renderStructuredValue(output.content);
  typesetResponseMath(content);
  return content;
}

function renderMethodPipeline(content = {}) {
  const wrap = create("div", "method-pipeline-view");
  const problem = content.problem_formulation || {};
  const innovation = content.core_innovation_analysis || {};

  if (Object.keys(problem).length) {
    const section = create("section", "method-panel");
    section.append(create("h4", "", "问题定义"));
    const fields = create("dl", "method-fields");
    [
      ["输入", problem.input],
      ["输出", problem.output],
      ["约束", problem.constraints],
      ["形式化定义", problem.formal_definition],
    ].forEach(([label, value]) => appendMethodField(fields, label, value));
    section.append(fields);
    wrap.append(section);
  }

  const steps = Array.isArray(content.pipeline) ? content.pipeline : [];
  if (steps.length) {
    const section = create("section", "method-panel");
    section.append(create("h4", "", "方法 Pipeline"));
    const list = create("div", "method-step-list");
    steps.forEach((step, index) => {
      const item = create("article", `method-step${step.is_core_innovation ? " is-core" : ""}`);
      const header = create("div", "method-step-header");
      header.append(create("span", "method-step-index", String(index + 1).padStart(2, "0")));
      header.append(create("strong", "", step.name || step.step_id || `Step ${index + 1}`));
      if (step.is_core_innovation) header.append(create("em", "", "核心创新"));
      item.append(header);
      if (step.description) item.append(create("p", "", step.description));
      if (step.motivation) item.append(methodInlineBlock("动机", step.motivation));
      if (Array.isArray(step.connects_to) && step.connects_to.length) {
        item.append(methodInlineBlock("连接到", step.connects_to.join("、")));
      }
      list.append(item);
    });
    section.append(list);
    wrap.append(section);
  }

  if (Object.keys(innovation).length) {
    const section = create("section", "method-panel");
    section.append(create("h4", "", "核心创新"));
    const fields = create("dl", "method-fields");
    [
      ["是什么", innovation.what],
      ["与前人差异", innovation.difference_from_prior],
      ["为什么有效", innovation.why_it_works],
    ].forEach(([label, value]) => appendMethodField(fields, label, value));
    section.append(fields);
    wrap.append(section);
  }

  const checkpoints = Array.isArray(content.novice_checkpoints) ? content.novice_checkpoints : [];
  if (checkpoints.length) {
    const section = create("section", "method-panel");
    section.append(create("h4", "", "理解检查"));
    const list = create("ul", "compact-list");
    checkpoints.forEach((item) => list.append(create("li", "", String(item))));
    section.append(list);
    wrap.append(section);
  }

  if (!wrap.childNodes.length) wrap.append(renderStructuredValue(content));
  return wrap;
}

function appendMethodField(fields, label, value) {
  if (value == null || value === "" || (Array.isArray(value) && !value.length)) return;
  fields.append(create("dt", "", label));
  const dd = create("dd");
  if (Array.isArray(value)) {
    const list = create("ul", "compact-list");
    value.forEach((item) => list.append(create("li", "", String(item))));
    dd.append(list);
  } else {
    dd.textContent = String(value);
  }
  fields.append(dd);
}

function methodInlineBlock(label, value) {
  const block = create("p", "method-inline");
  block.append(create("span", "", `${label}：`), document.createTextNode(String(value)));
  return block;
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
    container.append(create("p", "analysis-text", structuredValueText(value)));
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
      dd.append(depth > 3 ? create("p", "analysis-text", structuredValueText(item)) : renderCompactObject(item, depth + 1));
    } else {
      dd.textContent = item == null || item === "" ? "暂无" : String(item);
    }
    grid.append(dt, dd);
  });
  return grid;
}

function structuredValueText(value) {
  if (Array.isArray(value)) return value.map(structuredValueText).filter(Boolean).join("；");
  if (value && typeof value === "object") {
    return Object.values(value).map(structuredValueText).filter(Boolean).join("；");
  }
  return value == null ? "" : String(value);
}

function renderMarkdown(source) {
  source = cleanRepeatedMarkdown(source);
  let content;
  if (typeof window.renderSafeMarkdown === "function") {
    content = window.renderSafeMarkdown(source, "markdown-content");
  } else {
    content = create("div", "markdown-content", String(source || "暂无内容。"));
  }
  typesetResponseMath(content);
  return content;
}

function cleanRepeatedMarkdown(source) {
  const output = [];
  let previousText = "";
  String(source || "").split("\n").forEach((rawLine) => {
    let line = rawLine;
    const dotted = line.split(/\s*·\s*/u).filter(Boolean);
    const wasFragmented = dotted.length > 3 && dotted.every((item) => [...item.trim()].length <= 2);
    if (wasFragmented) line = dotted.join("");
    const comparable = comparableMapText(line);
    if (comparable && (comparable === previousText || (wasFragmented && previousText.includes(comparable)))) return;
    output.push(line);
    if (comparable) previousText = comparable;
  });
  return output.join("\n").replace(/\n{3,}/gu, "\n\n").trim();
}

function typesetResponseMath(root) {
  if (!root || !window.katex?.render || typeof document.createTreeWalker !== "function") return root;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (!node.parentElement?.closest("code,pre,script,style,textarea,.katex")) nodes.push(node);
  }
  const pattern = /(\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\$([^$\n]+?)\$)/g;
  nodes.forEach((node) => {
    const text = node.nodeValue || "";
    pattern.lastIndex = 0;
    let match;
    let cursor = 0;
    const fragment = document.createDocumentFragment();
    let changed = false;
    while ((match = pattern.exec(text))) {
      if (match.index > 0 && text[match.index - 1] === "\\") continue;
      changed = true;
      if (match.index > cursor) fragment.append(document.createTextNode(text.slice(cursor, match.index)));
      const displayMode = Boolean(match[2] != null || match[3] != null);
      const latex = match[2] ?? match[3] ?? match[4] ?? match[5] ?? "";
      const span = create("span", displayMode ? "response-math-block" : "response-math-inline");
      window.katex.render(latex.trim(), span, { displayMode, throwOnError: false, strict: "ignore", trust: false });
      fragment.append(span);
      cursor = pattern.lastIndex;
    }
    if (!changed) return;
    if (cursor < text.length) fragment.append(document.createTextNode(text.slice(cursor)));
    node.replaceWith(fragment);
  });
  return root;
}

function parseStructuredAgentResponse(source) {
  const raw = String(source || "").trim();
  if (!raw) return null;
  const fenced = raw.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  const candidate = fenced ? fenced[1] : raw;
  if (!candidate.startsWith("{") && !candidate.startsWith("[")) return null;
  try {
    const parsed = JSON.parse(candidate);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

function extractReadableSkillBlocks(source) {
  let text = String(source || "");
  const outputs = [];
  const extracted = extractSkillJsonObjects(text);
  text = extracted.text;
  outputs.push(...extracted.outputs);
  const titlePattern = /(?:^|\n)[^\n]*Method Analyst\s*结构化(?:输出|分析)[^\n]*(?:\n|$)/i;
  let match = titlePattern.exec(text);
  while (match) {
    let removeStart = match.index;
    if (text[removeStart] === "\n") removeStart += 1;
    const beforeTitle = text.slice(0, match.index);
    const hrMatch = beforeTitle.match(/(?:^|\n)[ \t]*[-*_]{3,}[ \t]*\n[ \t]*$/);
    if (hrMatch) removeStart = beforeTitle.length - hrMatch[0].length + (hrMatch[0].startsWith("\n") ? 1 : 0);

    let cursor = match.index + match[0].length;
    while (cursor < text.length && /\s/.test(text[cursor])) cursor += 1;
    let removeEnd = cursor;
    if (text.startsWith("```", cursor)) {
      const fenceEnd = text.indexOf("```", cursor + 3);
      removeEnd = fenceEnd >= 0 ? fenceEnd + 3 : text.length;
    } else {
      const objectStart = text.indexOf("{", cursor);
      if (objectStart >= 0) {
        removeEnd = findBalancedJsonObjectEnd(text, objectStart);
        const output = skillOutputFromJson(text.slice(objectStart, removeEnd));
        if (output) outputs.push(output);
      }
    }
    text = `${text.slice(0, removeStart)}\n${text.slice(removeEnd)}`;
    match = titlePattern.exec(text);
  }
  return {
    text: text.replace(/\n{3,}/g, "\n\n").trim(),
    outputs: dedupeSkillOutputs(outputs),
  };
}

function extractSkillJsonObjects(source) {
  let text = String(source || "");
  const outputs = [];
  let cursor = 0;
  while (cursor < text.length) {
    const start = text.indexOf("{", cursor);
    if (start < 0) break;
    const end = findBalancedJsonObjectEnd(text, start);
    if (end <= start || end > text.length) break;
    const candidate = text.slice(start, end);
    const output = skillOutputFromJson(candidate);
    if (output) {
      outputs.push(output);
      const lineStart = text.lastIndexOf("\n", start - 1) + 1;
      const prefix = text.slice(lineStart, start);
      const removeStart = /^[\s>]*$/.test(prefix) ? lineStart : start;
      let removeEnd = end;
      while (removeEnd < text.length && /[ \t]/.test(text[removeEnd])) removeEnd += 1;
      if (text[removeEnd] === "\n") removeEnd += 1;
      text = `${text.slice(0, removeStart)}\n${text.slice(removeEnd)}`;
      cursor = Math.max(0, removeStart - 1);
    } else {
      cursor = end;
    }
  }
  return { text, outputs: dedupeSkillOutputs(outputs) };
}

function skillOutputFromJson(candidate) {
  try {
    const parsed = JSON.parse(candidate);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    const isMethodAnalyst = parsed.problem_formulation && Array.isArray(parsed.pipeline);
    const isMathVerifier = parsed.formula
      && parsed.layer_1_intuition
      && Array.isArray(parsed.layer_2_derivation);
    if (isMethodAnalyst) {
      return {
        skill_id: "reading.method_analyst",
        skill_name: "方法论拆解",
        output_type: "method_pipeline",
        parse_status: "parsed",
        content: parsed,
      };
    }
    if (isMathVerifier) {
      return {
        skill_id: "reading.math_verifier",
        skill_name: "公式推导验证者",
        output_type: "math_derivation",
        parse_status: "parsed",
        content: parsed,
      };
    }
    return null;
  } catch {
    return null;
  }
}

function findBalancedJsonObjectEnd(text, start) {
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let index = start; index < text.length; index += 1) {
    const char = text[index];
    if (inString) {
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === "\"") inString = false;
      continue;
    }
    if (char === "\"") inString = true;
    else if (char === "{") depth += 1;
    else if (char === "}") {
      depth -= 1;
      if (depth === 0) return index + 1;
    }
  }
  return text.length;
}

function renderAgentResponse(source, options = {}) {
  const extracted = extractReadableSkillBlocks(source);
  source = extracted.text;
  const structured = options.allowStructured === false
    ? null
    : parseStructuredAgentResponse(source);
  const wrap = create("div", "agent-response-content");
  const appendExtracted = () => {
    extracted.outputs.forEach((output) => {
      if (output.skill_id === "reading.method_analyst") return;
      const card = create("article", "skill-output-card inline-skill-output-card");
      const header = create("header");
      header.append(create("strong", "", output.skill_name || skillLabel(output.skill_id)), create("span", "", output.output_type || "Skill"));
      card.append(header, renderSkillContent(output));
      wrap.append(card);
    });
  };
  if (Array.isArray(structured)) {
    const content = renderStructuredArray(structured);
    typesetResponseMath(content);
    wrap.append(content);
    appendExtracted();
    return wrap;
  }
  if (structured) {
    const content = renderStructuredValue(structured);
    typesetResponseMath(content);
    wrap.append(content);
    appendExtracted();
    return wrap;
  }
  if (source.trim()) wrap.append(renderMarkdown(source));
  appendExtracted();
  if (!wrap.childNodes.length) wrap.append(renderMarkdown("暂无可展示内容。"));
  return wrap;
}

function renderSkillControls() {
  // Skill routing is handled by the backend. The frontend only renders returned outputs.
}

function renderQuickActions() {}

function syncSkillControls() {
  // No frontend skill controls are rendered; skill choice is handled server-side.
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

function captureSelection(event) {
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
  const toolbar = $("selection-toolbar");
  toolbar.hidden = false;
  const readerBounds = (pdfPage || structured)?.closest(".reader-panel")?.getBoundingClientRect();
  const rangeRect = selection.getRangeAt(0).getBoundingClientRect();
  const pointer = {
    x: Number.isFinite(event?.clientX) && event.clientX > 0 ? event.clientX : rangeRect.right,
    y: Number.isFinite(event?.clientY) && event.clientY > 0 ? event.clientY : rangeRect.bottom,
  };
  requestAnimationFrame(() => positionSelectionToolbar(toolbar, pointer, readerBounds));
}

function positionSelectionToolbar(toolbar, pointer, readerBounds) {
  const gap = 6;
  const minX = Math.max(8, (readerBounds?.left ?? 0) + 6);
  const maxX = Math.min(window.innerWidth - 8, (readerBounds?.right ?? window.innerWidth) - 6);
  const availableWidth = Math.max(0, maxX - minX);
  toolbar.style.maxWidth = `${availableWidth}px`;
  const toolbarRect = toolbar.getBoundingClientRect();
  const toolbarWidth = Math.min(toolbarRect.width || toolbar.offsetWidth || 1, availableWidth);
  const toolbarHeight = toolbarRect.height || toolbar.offsetHeight || 1;
  const rightX = pointer.x + gap;
  const leftX = pointer.x - toolbarWidth - gap;
  const useRight = rightX + toolbarWidth <= maxX;
  const preferredX = useRight ? rightX : leftX;
  toolbar.style.left = `${Math.max(minX, Math.min(maxX - toolbarWidth, preferredX))}px`;
  toolbar.style.top = `${Math.max(8, Math.min(window.innerHeight - toolbarHeight - 8, pointer.y - toolbarHeight / 2))}px`;
  toolbar.dataset.side = useRight ? "right" : "left";
}

async function handleSelectionAction(event) {
  const action = event.target.closest("[data-selection-action]")?.dataset.selectionAction;
  if (!action) return;
  $("selection-toolbar").hidden = true;
  const quoted = `\n\n选中内容：\n${state.selectedText}`;
  if (action === "explain") await startReading(`请解释这段内容的直觉、上下文和关键假设。${quoted}`);
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
  const rects = normalizePdfMarkRects(selection.getRangeAt(0), pageRect);
  if (!rects.length) return;
  const mark = {
    id: `mark-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    type,
    color: state.pdfMarkColor || "yellow",
    page: Number(page.dataset.pageNumber || 1),
    rects,
    text: state.selectedText,
    note: "",
    section_id: state.currentSection,
    created_at: new Date().toISOString(),
  };

  $("selection-toolbar").hidden = true;
  if (type === "note") {
    openPdfNoteModal(mark);
    return;
  }
  commitPdfMark(mark);
}

function normalizePdfMarkRects(range, pageRect) {
  const clamp = (value) => Math.min(1, Math.max(0, value));
  const raw = Array.from(range.getClientRects())
    .filter((rect) => rect.width > 1 && rect.height > 1)
    .map((rect) => {
      const left = clamp((rect.left - pageRect.left) / pageRect.width);
      const top = clamp((rect.top - pageRect.top) / pageRect.height);
      const right = clamp((rect.right - pageRect.left) / pageRect.width);
      const bottom = clamp((rect.bottom - pageRect.top) / pageRect.height);
      return { left, top, width: right - left, height: bottom - top };
    })
    .filter((rect) => rect.width > 0 && rect.height > 0)
    .sort((left, right) => left.top - right.top || left.left - right.left);

  const merged = [];
  raw.forEach((rect) => {
    const previous = merged.at(-1);
    if (!previous) {
      merged.push({ ...rect });
      return;
    }
    const overlap = Math.min(previous.top + previous.height, rect.top + rect.height) - Math.max(previous.top, rect.top);
    const sameLine = overlap >= Math.min(previous.height, rect.height) * 0.55;
    const horizontalGap = rect.left - (previous.left + previous.width);
    if (sameLine && horizontalGap <= 0.012) {
      const right = Math.max(previous.left + previous.width, rect.left + rect.width);
      const bottom = Math.max(previous.top + previous.height, rect.top + rect.height);
      previous.left = Math.min(previous.left, rect.left);
      previous.top = Math.min(previous.top, rect.top);
      previous.width = right - previous.left;
      previous.height = bottom - previous.top;
    } else {
      merged.push({ ...rect });
    }
  });

  return merged.map((rect) => ({
    left: round2(rect.left),
    top: round2(rect.top + rect.height * 0.14),
    width: round2(rect.width),
    height: round2(rect.height * 0.72),
  }));
}

function commitPdfMark(mark, editingId = "") {
  if (editingId) {
    const index = state.pdfMarks.findIndex((item) => item.id === editingId);
    if (index >= 0) state.pdfMarks[index] = mark;
  } else {
    state.pdfMarks.push(mark);
    state.pdfMarkHistory.push(mark.id);
  }
  persistPdfMarks();
  void savePdfMarkRemote(mark);
  const page = $("pdf-document")?.querySelector(`[data-page-number="${Number(mark.page) || 1}"]`);
  renderPdfMarks(page, mark.page);
  window.getSelection()?.removeAllRanges();
  toast(mark.type === "note" ? (editingId ? "注释已更新。" : "注释已添加。") : "高亮已添加。");
}

function openPdfNoteModal(mark, editing = false) {
  state.pendingPdfNoteMark = { ...mark };
  state.editingPdfNoteId = editing ? mark.id : "";
  $("note-modal-title").textContent = editing ? "编辑注释" : "添加注释";
  $("note-selection-copy").textContent = truncate(mark.text || "未记录选中原文", 420);
  $("note-text-input").value = mark.note || "";
  $("note-modal").hidden = false;
  requestAnimationFrame(() => $("note-text-input").focus());
}

function closePdfNoteModal() {
  $("note-modal").hidden = true;
  state.pendingPdfNoteMark = null;
  state.editingPdfNoteId = "";
}

function savePdfNote() {
  if (!state.pendingPdfNoteMark) return;
  const note = $("note-text-input").value.trim();
  if (!note) return toast("请先填写注释内容。", true);
  const mark = { ...state.pendingPdfNoteMark, note, updated_at: new Date().toISOString() };
  const editingId = state.editingPdfNoteId;
  closePdfNoteModal();
  commitPdfMark(mark, editingId);
}

function setPdfMarkColor(color) {
  state.pdfMarkColor = color || "yellow";
  document.querySelectorAll(".pdf-color-swatch").forEach((item) => {
    item.classList.toggle("is-active", item.classList.contains(`pdf-color-${state.pdfMarkColor}`));
  });
}

function undoLastPdfMark() {
  if (!state.pdfMarks.length) return toast("没有可撤销的高亮或注释。", true);
  let removed = null;
  while (state.pdfMarkHistory.length && !removed) {
    const markId = state.pdfMarkHistory.pop();
    const index = state.pdfMarks.findIndex((mark) => mark.id === markId);
    if (index >= 0) removed = state.pdfMarks.splice(index, 1)[0];
  }
  if (!removed) removed = state.pdfMarks.pop();
  persistPdfMarks();
  void deletePdfMarkRemote(removed.id);
  const page = $("pdf-document")?.querySelector(`[data-page-number="${Number(removed.page) || 1}"]`);
  if (page) renderPdfMarks(page, removed.page);
  toast("已撤销最近一次标注。");
}

function renderPdfMarks(pageShell, pageNumber) {
  if (!pageShell) return;
  const layer = pageShell.querySelector(".pdf-mark-layer");
  if (!layer) return;
  layer.replaceChildren();
  state.pdfMarks
    .filter((mark) => Number(mark.page) === Number(pageNumber))
    .forEach((mark) => {
      mark.rects.forEach((rect) => {
        const item = create("button", `pdf-mark pdf-mark-${mark.type} pdf-mark-${mark.color || "yellow"}`);
        item.type = "button";
        item.style.left = `${rect.left * 100}%`;
        item.style.top = `${rect.top * 100}%`;
        item.style.width = `${rect.width * 100}%`;
        item.style.height = `${rect.height * 100}%`;
        item.setAttribute("aria-label", mark.type === "note" ? "编辑此处注释" : "PDF 高亮");
        if (mark.type === "note") item.addEventListener("click", () => openPdfNoteModal(mark, true));
        else item.tabIndex = -1;
        layer.append(item);
      });
    });
}

function persistPdfMarks() {
  if (!state.paperId) return;
  const all = loadAllPdfMarks();
  all[state.paperId] = state.pdfMarks;
  localStorage.setItem(STORAGE.pdfMarks, JSON.stringify(all));
}

async function loadPdfMarks() {
  const all = loadAllPdfMarks();
  const cached = Array.isArray(all[state.paperId]) ? all[state.paperId] : [];
  try {
    const query = state.sessionId ? `?reading_session_id=${encodeURIComponent(state.sessionId)}` : "";
    const response = await fetch(`/api/research/papers/${encodeURIComponent(state.paperId)}/annotations${query}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const remote = (await response.json()).map(annotationToPdfMark);
    const merged = new Map(cached.map((mark) => [mark.id, mark]));
    remote.forEach((mark) => merged.set(mark.id, mark));
    state.pdfMarks = [...merged.values()];
    const remoteIds = new Set(remote.map((mark) => mark.id));
    cached.filter((mark) => !remoteIds.has(mark.id)).forEach((mark) => void savePdfMarkRemote(mark));
    persistPdfMarks();
  } catch {
    state.pdfMarks = cached;
  }
  state.pdfMarkHistory = state.pdfMarks.map((mark) => mark.id).filter(Boolean);
}

function appendUserQuestion(text, target = $("analysis-feed")) {
  const card = create("article", "user-question-card");
  const header = create("header");
  header.append(create("strong", "", "你"), create("span", "", "提问"));
  card.append(header, create("p", "", text));
  target.append(card);
  target.scrollTop = target.scrollHeight;
}

function annotationToPdfMark(annotation) {
  return {
    id: annotation.annotation_id,
    type: annotation.annotation_type,
    color: annotation.color,
    page: annotation.page_number,
    rects: annotation.rects || [],
    text: annotation.selected_text || "",
    note: annotation.note_text || "",
    section_id: annotation.section_id || "",
    created_at: annotation.created_at,
    updated_at: annotation.updated_at,
  };
}

async function savePdfMarkRemote(mark) {
  if (!state.paperId || !mark?.id) return;
  try {
    const response = await fetch(`/api/research/papers/${encodeURIComponent(state.paperId)}/annotations/${encodeURIComponent(mark.id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        reading_session_id: state.sessionId || null,
        annotation_type: mark.type,
        color: mark.color || "yellow",
        page_number: Number(mark.page) || 1,
        section_id: mark.section_id || null,
        selected_text: mark.text || "未记录原文",
        rects: mark.rects || [],
        note_text: mark.note || "",
      }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
  } catch {
    toast("标注已保存在本机缓存，暂未同步到资料库。", true);
  }
}

async function deletePdfMarkRemote(markId) {
  if (!state.paperId || !markId) return;
  try {
    const response = await fetch(`/api/research/papers/${encodeURIComponent(state.paperId)}/annotations/${encodeURIComponent(markId)}`, { method: "DELETE" });
    if (!response.ok && response.status !== 404) throw new Error(`HTTP ${response.status}`);
  } catch {
    toast("本机标注已撤销，但资料库同步失败。", true);
  }
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
  $("fork-question-input").value = question || defaultForkQuestion(state.selectedText);
  $("fork-modal").hidden = false;
  $("fork-question-input").focus();
}

function defaultForkQuestion(context = "") {
  return looksLikeFormula(context)
    ? "请对选中公式做直觉、逐步推导和数值例子三层分析。"
    : "请围绕选中内容进行深入探索。";
}

function looksLikeFormula(text = "") {
  const value = String(text || "");
  if (!value.trim()) return false;
  const mathTokens = (value.match(/[=∑∏∫√∞≈≤≥≠→←↔∀∃∈∉⊂⊆⊕⊗πθλμστΩαβγΔ_{}^\\]/g) || []).length;
  return mathTokens >= 2 || /\b(?:argmax|argmin|softmax|log|exp|Pr|p\(|E\[|R\(|\\frac|\\sum|\\prod|\\theta|\\pi)\b/.test(value);
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
    containerEl: null,
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
    thinking.remove();
    const result = await streamPaperTurn({
      action: "start_reading", session_id: fork.sessionId, paper_id: state.paperId,
      target_section: state.currentSection, content: `${question}\n\n上下文：${context}`,
    }, fork.feedEl, `正在分析 ${fork.label}…`);
    if (!result) return;
    renderSkillOutputs(unrenderedSkillOutputs(result.skill_outputs || [], result.data?.agent_response || ""), fork.feedEl);
    toast("Fork 分支已创建，可在此选项卡继续追问。");
  } catch (error) {
    thinking.remove();
    toast(error.message, true);
  }
}

async function runForkTurn(fork, question) {
  if (!fork.sessionId) return toast("分支会话尚未就绪，请稍候再试。", true);
  const payload = await streamPaperTurn({
    action: "start_reading", session_id: fork.sessionId, paper_id: state.paperId,
    target_section: state.currentSection, content: question,
  }, fork.feedEl, `正在追问 ${fork.label}…`);
  if (payload) renderSkillOutputs(unrenderedSkillOutputs(payload.skill_outputs || [], payload.data?.agent_response || ""), fork.feedEl);
}

function unrenderedSkillOutputs(outputs = [], agentResponse = "") {
  const inlineIds = new Set(extractReadableSkillBlocks(agentResponse).outputs.map((output) => output.skill_id));
  return (outputs || []).filter((output) => !inlineIds.has(output.skill_id));
}

async function streamPaperTurn(body, target, detail) {
  const streaming = showStreamingAnalysisCard(detail, target);
  const controller = new AbortController();
  state.activeResponseController = controller;
  $("reading-stop-button").hidden = false;
  try {
    const response = await fetch("/paper_reading/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    const payload = await window.streamSseJson(
      response,
      (delta) => streaming.append(delta),
      (delta) => streaming.appendReasoning(delta),
    );
    streaming.finish(payload?.data?.agent_response || "", payload?.data?.reasoning || "");
    return payload;
  } catch (error) {
    if (error.name === "AbortError") {
      streaming.interrupt();
      toast("回答已中断。");
    } else {
      streaming.remove();
      toast(error.message, true);
    }
    return null;
  } finally {
    if (state.activeResponseController === controller) state.activeResponseController = null;
    $("reading-stop-button").hidden = true;
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
  const container = create("div", "copilot-feed fork-feed");
  container.dataset.feedId = fork.id;
  const toolbar = create("div", "fork-feed-toolbar");
  toolbar.append(create("span", "fork-feed-label", `⑂ ${fork.label}`));
  const mergeButton = create("button", "mini-button is-accent", "合并回主流程");
  mergeButton.type = "button";
  mergeButton.addEventListener("click", () => mergeFork(fork));
  const closeButton = create("button", "mini-button", "关闭分支");
  closeButton.type = "button";
  closeButton.addEventListener("click", () => closeForkTab(fork.id));
  toolbar.append(mergeButton, closeButton);
  const feed = create("div", "analysis-feed fork-feed-content");
  container.append(toolbar, feed);
  $("copilot-feeds").append(container);
  fork.feedEl = feed;
  fork.containerEl = container;
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
  state.forks[index].containerEl?.remove();
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

function renderReadingMapLegacy() {
  const map = state.readingMap || state.paper?.reading_map || {};
  const grid = $("reading-map-grid");
  const empty = $("reading-map-empty");
  const detail = $("reading-map-detail");
  if (!grid) return;
  grid.replaceChildren();
  if (detail) {
    detail.replaceChildren(
      create("p", "panel-label", "导读地图"),
      create("h3", "", "论文阅读地图"),
      create("p", "muted-copy", map.map_variant === "survey" ? "综述论文会按发展脉络、技术路线、数据集和开放问题展开。" : "点击卡片可以跳转原文，或让右侧智能体解释这一段。")
    );
  }

  const status = state.readingMapStatus === "llm_running" ? "llm_running" : (map.status || state.parseStatus || "pending");
  const mapReady = ["done", "llm_done", "heuristic_done"].includes(status);
  $("reading-map-status-copy").textContent = status === "llm_done"
    ? "深度阅读地图已生成"
    : status === "llm_running"
      ? "基础阅读地图已生成，正在深化导读…"
    : mapReady
      ? (map.map_variant === "survey" ? "综述型阅读地图已生成" : "研究型阅读地图已生成")
    : status === "failed"
      ? "阅读地图生成失败，可先使用 PDF 原文和章节索引阅读"
      : "正在生成章节索引与阅读地图…";

  const groups = map.map_variant === "survey" ? surveyReadingMapGroups(map) : researchReadingMapGroups(map);

  const hasContent = groups.some((group) => group.items.some((item) => item && Object.keys(item).length));
  if (empty) empty.hidden = hasContent || !mapReady;
  if (!hasContent) {
    grid.append(create("div", "reading-map-pending", status === "failed" ? "解析失败，暂时无法生成阅读地图。" : "正在解析 PDF。你可以先阅读原文、划选内容并让智能体分析。"));
    return;
  }

  groups.forEach((group, groupIndex) => {
    const column = create("section", "reading-map-column");
    column.append(create("h3", "", group.title));
    const items = group.items.filter((item) => item && Object.keys(item).length);
    if (!items.length) {
      column.append(create("p", "muted-copy", "暂无明确内容"));
    }
    items.slice(0, 5).forEach((item, index) => {
      column.append(renderReadingMapCard(item, group.key, groupIndex, index));
    });
    grid.append(column);
  });
}

function renderReadingMap() {
  const map = state.readingMap || state.paper?.reading_map || {};
  const grid = $("reading-map-grid");
  const empty = $("reading-map-empty");
  const detail = $("reading-map-detail");
  const isSurvey = map.map_variant === "survey";
  const taskLabel = isSurvey ? "综述导读地图与智能索引" : "研究总览与智能索引";
  if (!grid) return;
  grid.classList.toggle("is-survey-vertical", isSurvey);
  grid.replaceChildren();
  if ($("reading-map-kicker")) $("reading-map-kicker").textContent = isSurvey ? "综述导读" : "研究总览";
  if ($("reading-map-title")) $("reading-map-title").textContent = isSurvey ? "综述导读地图" : "研究总览";
  syncReadingMapPanelState();
  if (detail) {
    detail.replaceChildren(
      create("p", "panel-label", isSurvey ? "综述导读" : "研究总览"),
      create("h3", "", isSurvey ? "综述导读地图" : "研究总览"),
      create("p", "muted-copy", isSurvey ? "综述论文会按发展脉络、技术路线、数据集和开放问题展开。" : "这里汇总研究问题、核心方法、方法步骤、实验支撑与局限追问；点击卡片可跳转原文或让右侧智能体解释。")
    );
  }

  const status = state.readingMapStatus === "llm_running" ? "llm_running" : (map.status || state.parseStatus || "pending");
  const groups = (isSurvey ? surveyReadingMapGroups(map) : researchReadingMapGroups(map))
    .map((group) => ({
      ...group,
      items: (group.items || []).filter((item) => isRenderableReadingMapItem(item, group.key)),
    }));
  const missingSurveyGroups = isSurvey
    ? groups.filter((group) => !group.items.length).map((group) => group.title)
    : [];
  const staleIncompleteSurvey = status === "llm_done" && Boolean(missingSurveyGroups.length);
  const mapReady = status === "llm_done" && !staleIncompleteSurvey;
  const mapFailed = ["failed", "failed_partial"].includes(status) || staleIncompleteSurvey;
  const mapDisplayable = mapReady || staleIncompleteSurvey || hasPartialReadingMapContent(map);
  const regenerating = status === "llm_running" || ["queued", "pending", "parsing"].includes(status);
  const timedOut = isReadingMapTimedOut();
  const regenerateButton = $("regenerate-reading-map-button");
  if (regenerateButton) regenerateButton.disabled = (regenerating && !timedOut) || !state.paperId;
  const failureText = staleIncompleteSurvey
    ? `旧版生成结果缺少固定分区：${missingSurveyGroups.join("、")}。请点击“重新生成”，系统会重新读取论文正文补齐。`
    : readingMapFailureText(map);
  $("reading-map-status-copy").textContent = mapReady
    ? (map.partial && map.generation_warning ? `${taskLabel}已生成；${map.generation_warning}` : `${taskLabel}已生成`)
    : mapFailed
      ? `${taskLabel}生成失败：${failureText}`
      : timedOut
        ? `${taskLabel}生成已超时，可以点击“重新生成”。`
        : `正在并行生成${taskLabel}，请稍候。`;

  if (!mapReady && !mapFailed && !timedOut) {
    $("reading-map-status-copy").textContent = readingMapPhaseText();
  }

  if (!mapDisplayable) {
    if (empty) empty.hidden = false;
    grid.append(create(
      "div",
      `reading-map-pending${mapFailed ? " is-error" : ""}`,
      mapFailed
        ? `生成失败：${failureText}`
        : timedOut
          ? "生成等待时间过长，可以点击“重新生成”再次提交请求。"
          : "导读地图与智能索引正在生成中，完成前不会展示临时 fallback 内容。"
    ));
    return;
  }

  const hasContent = groups.some((group) => group.items.some((item) => item && Object.keys(item).length));
  if (empty) empty.hidden = hasContent;
  if (!mapReady) {
    grid.append(create(
      "div",
      `reading-map-pending${mapFailed ? " is-error" : ""}`,
      mapFailed
        ? `部分生成失败：${failureText}${readingMapCardProgressText() ? `（${readingMapCardProgressText()}）` : ""}`
        : `正在生成，已完成的 LLM 卡片会先显示。${readingMapCardProgressText() ? `（${readingMapCardProgressText()}）` : ""}`
    ));
  }
  if (!hasContent) {
    grid.append(create("div", "reading-map-pending is-error", "生成结果为空，请点击重新生成。"));
    return;
  }

  groups.forEach((group, groupIndex) => {
    const column = create("section", "reading-map-column");
    column.append(create("h3", "", group.title));
    const items = group.items.filter((item) => item && Object.keys(item).length);
    if (!items.length) {
      column.append(create(
        "p",
        "muted-copy",
        mapFailed ? "该固定分区生成失败，请重新生成研究总览。" : "正在生成该固定分区…",
      ));
    }
    items.slice(0, 5).forEach((item, index) => {
      column.append(renderReadingMapCard(item, group.key, groupIndex, index));
    });
    grid.append(column);
  });
}

function readingMapFailureText(map = {}) {
  return map.error
    || map.llm_error
    || state.readingMapError
    || state.paper?.reading_map_error
    || "API 错误、余额不足或超时都可能导致失败，请点击重新生成。";
}

function researchReadingMapGroups(map) {
  const research = map.research_map || {};
  return [
    { key: "research_problem", title: "研究问题", items: (map.research_problem || research.research_problem) ? [map.research_problem || research.research_problem] : [] },
    { key: "core_method", title: "核心方法", items: (map.core_method || research.core_method) ? [map.core_method || research.core_method] : [] },
    { key: "method_steps", title: "方法步骤", items: map.method_steps || research.method_steps || [] },
    { key: "experimental_support", title: "实验支撑", items: map.experimental_support || research.experimental_support || [] },
    { key: "limitations_and_questions", title: "局限追问", items: map.limitations_and_questions || research.limitations_and_questions || [] },
  ];
}

function surveyReadingMapGroups(map) {
  const survey = map.survey_map || {};
  return [
    { key: "field_overview", title: "领域概览", items: survey.field_overview ? [survey.field_overview] : [] },
    { key: "development_timeline", title: "发展历程", items: survey.development_timeline || [] },
    { key: "pain_points", title: "难点痛点", items: survey.pain_points || [] },
    { key: "taxonomy", title: "分类体系", items: survey.taxonomy || [] },
    { key: "technical_routes", title: "技术路线", items: survey.technical_routes || [] },
    { key: "representative_methods", title: "代表论文方法", items: survey.representative_methods || [] },
    { key: "datasets", title: "公开数据集", items: survey.datasets || [] },
    { key: "evaluation_protocols", title: "评测方式", items: survey.evaluation_protocols || [] },
    { key: "applications", title: "应用场景", items: survey.applications || [] },
    { key: "open_challenges", title: "开放问题", items: survey.open_challenges || [] },
  ].map((group) => ({ ...group, items: prepareSurveyMapItems(group.key, group.items) }));
}

function renderReadingMapCard(item, groupKey, groupIndex, index) {
  const title = truncate(readingMapCardTitle(item, groupKey), 96);
  const summaryCandidate = readingMapCardSummary(item, groupKey);
  const summary = sameMapText(summaryCandidate, title) ? "" : summaryCandidate;
  const whyCandidate = readingMapCardWhy(item, groupKey, summary);
  const why = sameMapText(whyCandidate, title) || sameMapText(whyCandidate, summary) ? "" : whyCandidate;
  const sources = Array.isArray(item.source_sections) ? item.source_sections : [];
  const source = sources.find((entry) => entry?.page || entry?.section_id) || {};
  const card = create("article", `reading-map-card reading-map-${groupKey}`);
  card.append(create("strong", "", title));
  const fields = readingMapCardFields(item, groupKey, title);
  if (fields.length) {
    const list = create("dl", "reading-map-fields");
    fields.forEach(([label, value]) => {
      list.append(create("dt", "", label), create("dd", "", value));
    });
    card.append(list);
  } else {
    if (summary) card.append(create("p", "", summary));
    if (why && why !== summary) card.append(create("p", "reading-map-why", why));
  }

  if (["experimental_support"].includes(groupKey)) {
    const meta = [
      ...mapArray(item.datasets), ...mapArray(item.metrics), ...mapArray(item.figures_or_tables),
      ...mapArray(item.strengths), ...mapArray(item.mentioned_terms), item.year, item.url,
    ].map(readableInlineText).filter(Boolean);
    if (meta.length) card.append(create("small", "", meta.slice(0, 8).join(" · ")));
  }

  const actions = create("div", "reading-map-actions");
  const jump = create("button", "mini-button", "跳转 PDF");
  jump.type = "button";
  jump.disabled = !source.page && !source.section_id;
  jump.addEventListener("click", () => {
    const section = state.paper?.sections?.find((item) => item.section_id === source.section_id);
    jumpToPdfPage(source.page || section?.start_page || 1, source.section_id || "");
  });
  const ask = create("button", "mini-button is-accent", "让智能体解释");
  ask.type = "button";
  ask.addEventListener("click", () => {
    if (source.section_id) state.currentSection = source.section_id;
    startReading(`请面向科研新手解释阅读地图中的“${title}”：说明它是什么、为什么重要、和论文主线的关系。`);
  });
  actions.append(jump, ask);
  card.append(actions);
  if (sources.length) {
    const hiddenRouteLabels = new Set(["edgeandtopologyevolution", "nodeandfeatureevolution"]);
    const sourceText = sources
      .map((entry) => entry.title || entry.section_id || (entry.page ? `Page ${entry.page}` : ""))
      .filter((value) => value && !(groupKey === "representative_methods" && hiddenRouteLabels.has(comparableMapText(value).toLowerCase())))
      .slice(0, 3).join(" · ");
    if (sourceText) card.append(create("small", "reading-map-source", sourceText));
  }
  return card;
}

function prepareSurveyMapItems(groupKey, rawItems) {
  const blockedMethods = new Set(["edgeandtopologyevolution", "nodeandfeatureevolution"]);
  const items = (Array.isArray(rawItems) ? rawItems : []).filter((item) => item && Object.keys(item).length);
  const seenTitles = new Set();
  const seenMechanisms = new Set();
  return items.filter((item) => {
    if (!isRenderableReadingMapItem(item, groupKey)) return false;
    const title = comparableMapText(readingMapCardTitle(item, groupKey));
    if (groupKey === "representative_methods" && blockedMethods.has(title.toLowerCase())) return false;
    const mechanism = groupKey === "technical_routes"
      ? comparableMapText(firstUsefulField(item, ["core_mechanism", "typical_pipeline", "typical_flow", "core_idea"]))
      : "";
    if ((title && seenTitles.has(title)) || (mechanism.length >= 12 && seenMechanisms.has(mechanism))) return false;
    if (title) seenTitles.add(title);
    if (mechanism.length >= 12) seenMechanisms.add(mechanism);
    return true;
  });
}

function isRenderableReadingMapItem(item, groupKey) {
  if (!item || typeof item !== "object" || Array.isArray(item)) return false;
  if (["object", "json_object", "array"].includes(String(item.type || "").toLowerCase()) && item.properties && typeof item.properties === "object") return false;
  if (item.$schema || item.definitions || item.$defs) return false;
  const title = readingMapCardTitle(item, groupKey);
  const summary = readingMapCardSummary(item, groupKey);
  const why = readingMapCardWhy(item, groupKey, summary);
  return Boolean(readingMapCardFields(item, groupKey, title).length || summary || why);
}

function readingMapCardFields(item, groupKey, title) {
  const specs = {
    field_overview: [["领域范围", ["field_scope", "field"]], ["核心任务", ["core_tasks", "core_task"]], ["当前价值", ["why_now"]], ["新手结论", ["novice_takeaway"]], ["常见误解", ["common_misunderstanding"]]],
    development_timeline: [["时间范围", ["time_range"]], ["关键变化", ["key_change"]], ["代表工作", ["representative_work", "representative_works"]], ["阶段意义", ["why_important", "why_it_matters"]]],
    pain_points: [["为什么难", ["why_hard"]], ["实际影响", ["impact"]], ["已有尝试", ["existing_attempts"]], ["未解决部分", ["unresolved_part"]]],
    taxonomy: [["分类依据", ["basis"]], ["典型方法", ["typical_methods"]], ["适合问题", ["problem_fit", "solved_problems"]], ["局限", ["limitations"]]],
    technical_routes: [["核心机制", ["core_mechanism", "core_idea"]], ["典型流程", ["typical_flow", "typical_pipeline"]], ["优势", ["strengths"]], ["局限", ["limitations", "weaknesses"]], ["代表方法", ["representative_methods", "representative_method_ids"]]],
    representative_methods: [["论文", ["paper_title"]], ["年份", ["year"]], ["解决问题", ["problem_addressed"]], ["方法简介", ["method_summary", "core_mechanism"]], ["具体方案", ["specific_solution"]], ["改进对象", ["improves_on"]], ["剩余局限", ["remaining_limits", "limitations"]]],
    datasets: [["数据集类型", ["dataset_type", "task"]], ["一句话介绍", ["one_sentence_intro", "content", "description"]], ["数据结构", ["structure"]], ["规模", ["scale"]], ["评测指标", ["metrics"]], ["论文中的具体例子", ["paper_examples", "examples_in_paper"]], ["使用方法", ["used_by_methods"]]],
    evaluation_protocols: [["评测任务", ["task"]], ["评测指标", ["metrics", "metric"]], ["实验设置", ["setting"]], ["验证内容", ["what_it_tests"]]],
    applications: [["应用场景", ["scenario"]], ["适用原因", ["why_suitable"]], ["典型方法", ["typical_methods"]], ["落地限制", ["constraints"]]],
    open_challenges: [["为什么重要", ["why_it_matters"]], ["当前瓶颈", ["current_bottleneck", "why_hard"]], ["已有尝试", ["existing_attempts"]], ["可能方向", ["future_direction", "possible_directions"]]],
  };
  const seen = new Set([comparableMapText(title)]);
  return (specs[groupKey] || []).flatMap(([label, keys]) => {
    const value = firstUsefulField(item, keys);
    const key = comparableMapText(value);
    if (!value || !key || seen.has(key)) return [];
    seen.add(key);
    return [[label, value]];
  });
}

function readingMapCardTitle(item, groupKey) {
  const priorities = {
    field_overview: ["field", "core_task", "title", "name"],
    development_timeline: ["stage", "time_range", "key_change", "title"],
    pain_points: ["problem", "challenge", "title"],
    taxonomy: ["category", "name", "basis", "title"],
    technical_routes: ["route_name", "name", "route", "route_id", "core_mechanism", "title"],
    representative_methods: ["paper_title", "method_name", "name", "title"],
    datasets: ["name", "dataset", "task", "title"],
    evaluation_protocols: ["protocol", "task", "metric", "title"],
    applications: ["application", "scenario", "title"],
    open_challenges: ["challenge", "problem", "future_direction", "title"],
    research_problem: ["title", "problem", "one_sentence"],
    core_method: ["name", "title", "one_sentence"],
    method_steps: ["name", "goal", "title"],
    experimental_support: ["claim", "dataset", "title"],
    limitations_and_questions: ["limitation", "novice_question", "title"],
  };
  const value = firstUsefulField(item, priorities[groupKey] || ["title", "name"]);
  return value || readingMapGroupLabel(groupKey);
}

function readingMapCardSummary(item, groupKey) {
  const priorities = {
    development_timeline: ["key_change", "why_it_matters", "evidence", "summary"],
    pain_points: ["why_hard", "impact", "unresolved_part", "evidence", "summary"],
    taxonomy: ["basis", "solved_problems", "summary", "evidence"],
    technical_routes: ["core_mechanism", "typical_flow", "typical_pipeline", "summary", "evidence"],
    representative_methods: ["method_summary", "specific_solution", "improves_on", "evidence"],
    datasets: ["task", "content", "structure", "scale", "evidence"],
    evaluation_protocols: ["what_it_tests", "setting", "metrics", "evidence"],
    applications: ["why_suitable", "scenario", "constraints", "evidence"],
    open_challenges: ["why_it_matters", "current_bottleneck", "future_direction", "evidence"],
  };
  return firstUsefulField(item, priorities[groupKey] || ["one_sentence", "main_idea", "goal", "summary", "core_idea", "content", "description", "evidence", "novice_takeaway"]);
}

function readingMapCardWhy(item, groupKey, summary) {
  const priorities = {
    field_overview: ["why_now", "novice_takeaway"],
    development_timeline: ["representative_works", "why_it_matters"],
    pain_points: ["existing_attempts", "unresolved_part"],
    taxonomy: ["typical_methods", "limitations"],
    technical_routes: ["strengths", "weaknesses"],
    representative_methods: ["remaining_limits", "route"],
    datasets: ["metrics", "url"],
    open_challenges: ["future_direction"],
  };
  const value = firstUsefulField(item, priorities[groupKey] || ["why_it_matters", "why_needed", "operation", "novice_question", "why_hard", "impact", "specific_solution", "remaining_limits"]);
  return value && value !== summary ? value : "";
}

function firstUsefulField(item, keys) {
  for (const key of keys) {
    const value = item?.[key];
    const text = readableInlineText(value);
    if (text && !isLowValueMapText(text)) return text;
  }
  return "";
}

function readableInlineText(value) {
  if (value == null || value === "") return "";
  if (Array.isArray(value)) {
    const items = value.map(readableInlineText).filter(Boolean);
    if (items.length > 3 && items.every((item) => [...item].length <= 2)) return cleanMapText(items.join(""));
    return cleanMapText([...new Set(items)].slice(0, 4).join(" · "));
  }
  if (typeof value === "object") {
    return cleanMapText([...new Set(Object.values(value).map(readableInlineText).filter(Boolean))].slice(0, 3).join(" · "));
  }
  return cleanMapText(value);
}

function mapArray(value) {
  if (value == null || value === "") return [];
  return Array.isArray(value) ? value : [value];
}

function cleanMapText(value) {
  let text = String(value || "").trim();
  text = text.replace(/^\*{1,2}([\s\S]*?)\*{1,2}$/u, "$1").trim();
  const dotted = text.split(/\s*·\s*/u).filter(Boolean);
  if (dotted.length > 3 && dotted.every((item) => [...item].length <= 2)) text = dotted.join("");
  const lines = text.split(/\n+/u).map((line) => line.trim()).filter(Boolean);
  const unique = [];
  const seen = new Set();
  lines.forEach((line) => {
    const key = comparableMapText(line);
    if (key && !seen.has(key)) {
      seen.add(key);
      unique.push(line);
    }
  });
  return unique.join("\n");
}

function comparableMapText(value) {
  return String(value || "")
    .replace(/\*{1,2}/gu, "")
    .replace(/[\s\p{P}\p{S}]+/gu, "")
    .toLowerCase();
}

function sameMapText(left, right) {
  const a = comparableMapText(left);
  const b = comparableMapText(right);
  return Boolean(a && b && (a === b || (a.length > 24 && b.length > 24 && (a.includes(b) || b.includes(a)))));
}

function isLowValueMapText(text) {
  const normalized = String(text || "").trim();
  if (!normalized) return true;
  if (/^(item|point)\s*\d+$/i.test(normalized)) return true;
  if (/^(front|comput)\.$/i.test(normalized)) return true;
  if (normalized.length <= 8 && /^[A-Za-z]+\.$/.test(normalized)) return true;
  return false;
}

function readingMapGroupLabel(groupKey) {
  return {
    field_overview: "领域概览",
    development_timeline: "发展历程",
    pain_points: "难点痛点",
    taxonomy: "分类体系",
    technical_routes: "技术路线",
    representative_methods: "代表论文方法",
    datasets: "公开数据集",
    evaluation_protocols: "评测方式",
    applications: "应用场景",
    open_challenges: "开放问题",
    research_problem: "研究问题",
    core_method: "核心方法",
    method_steps: "方法步骤",
    experimental_support: "实验支撑",
    limitations_and_questions: "局限追问",
  }[groupKey] || "阅读地图";
}

function scrollReaderToSection(sectionId, smooth = true) {
  const reader = activeTextReader();
  const target = reader?.querySelector(`[data-section-id="${cssEscape(sectionId)}"]`);
  if (!reader || !target) return;
  const top = target.getBoundingClientRect().top - reader.getBoundingClientRect().top + reader.scrollTop - 12;
  reader.scrollTo({ top: Math.max(0, top), behavior: smooth ? "smooth" : "auto" });
}

function bindScrollSpy() {
  [$("structured-reader"), $("reflow-reader")].forEach((reader) => {
    let ticking = false;
    reader.addEventListener("scroll", () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => {
        ticking = false;
        updateCurrentSectionFromScroll(reader);
      });
    }, { passive: true });
  });

  const pdfHost = $("pdf-document");
  let pdfTicking = false;
  pdfHost?.addEventListener("scroll", () => {
    if (pdfTicking) return;
    pdfTicking = true;
    requestAnimationFrame(() => {
      pdfTicking = false;
      updateCurrentSectionFromPdfScroll();
    });
  }, { passive: true });
}

function activeTextReader() {
  return state.readerMode === "reflow" ? $("reflow-reader") : $("structured-reader");
}

function updateCurrentSectionFromScroll(reader = activeTextReader()) {
  if (!reader || reader.hidden || state.readerMode === "pdf") return;
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

function updateCurrentSectionFromPdfScroll() {
  if ($("pdf-reader").hidden) return;
  const page = visiblePdfPage();
  const section = sectionForPage(page);
  const current = section?.section_id || "";
  if (!current || current === state.currentSection) return;
  state.currentSection = current;
  persistState();
  renderOutline();
  document.querySelectorAll(".paper-section").forEach((item) => {
    item.classList.toggle("is-current", item.dataset.sectionId === current);
  });
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
  const params = new URLSearchParams(window.location.search);
  const requestedPaper = params.get("paper_id");
  const requestedSession = params.get("session_id");
  state.paperId = requestedPaper || localStorage.getItem(STORAGE.paper) || "";
  state.sessionId = requestedSession !== null
    ? requestedSession
    : (requestedPaper ? "" : localStorage.getItem(STORAGE.session) || "");
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
      startParsePolling();
      syncReturnChatLink();
    }
    if (state.sessionId) await restoreReadingConversationHistory();
  } catch {
    localStorage.removeItem(STORAGE.session);
    if (!state.paperId) localStorage.removeItem(STORAGE.paper);
  } finally {
    setBusy(false);
  }
  if (isDedicatedWorkspace && state.paper) {
    await enterWorkbench();
  } else if (isDedicatedWorkspace) {
    window.location.replace("/app?mode=paper_reading");
  }
}

function persistState() {
  syncReturnChatLink();
  if (state.sessionId) localStorage.setItem(STORAGE.session, state.sessionId);
  else localStorage.removeItem(STORAGE.session);
  if (state.paperId) localStorage.setItem(STORAGE.paper, state.paperId);
  else localStorage.removeItem(STORAGE.paper);
  if (state.currentSection) localStorage.setItem(STORAGE.section, state.currentSection);
  else localStorage.removeItem(STORAGE.section);
}

function setNavigatorWidth(width, persist = true) {
  const next = Math.min(460, Math.max(190, Number(width) || 270));
  $("workbench-grid").style.setProperty("--navigator-width", `${Math.round(next)}px`);
  if (persist) localStorage.setItem(STORAGE.navigatorWidth, String(Math.round(next)));
}

function syncReturnChatLink() {
  const link = $("return-chat-link");
  if (!link) return;
  link.href = state.sessionId
    ? `/app?conversation_id=${encodeURIComponent(state.sessionId)}`
    : "/app?mode=paper_reading";
}

function saveBeforeUnload() {
  persistState();
  if (state.paperNoteDirty && state.paperId && state.paperNoteLoadedFor === state.paperId) {
    fetch(`/api/research/papers/${encodeURIComponent(state.paperId)}/note`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content_markdown: state.paperNoteEditor?.getMarkdown() || "" }),
      keepalive: true,
    }).catch(() => {});
  }
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
function cssEscape(value) { return window.CSS?.escape ? window.CSS.escape(String(value || "")) : String(value || "").replace(/["\\]/g, "\\$&"); }
function sectionTitle(id) { return state.paper?.sections?.find((item) => item.section_id === id)?.title || id || ""; }
function skillLabel(id) { return SKILLS.find((item) => item.id === id)?.label || id || "Skill"; }
function humanizeKey(key) { return String(key).replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase()); }
function truncate(value, length) { const text = String(value || ""); return text.length > length ? `${text.slice(0, length - 1)}…` : text; }
function round2(value) { return Math.round(Number(value || 0) * 10000) / 10000; }
function sanitizeFileName(value) { return String(value || "paper").replace(/[\\/:*?"<>|]+/g, "_").slice(0, 120) || "paper"; }
function formatBytes(bytes) { return bytes < 1024 * 1024 ? `${Math.ceil(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`; }
function delay(ms) { return new Promise((resolve) => window.setTimeout(resolve, ms)); }
