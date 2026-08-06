const modeLabels = {
  chat: "日常聊天",
  domain_onboarding: "领域入门",
  paper_reading: "论文精读",
};

const modeEndpoints = {
  chat: "/chat",
  domain_onboarding: "/domain_onboarding",
  paper_reading: "/paper_reading",
};

const DOMAIN_WORKSPACE_KEY = "domain_onboarding_workspace_v1_5";

let currentMode = "chat";
let isGenerating = false;
let activeResponseController = null;
let selectedPaperFile = null;
const sessionId = getSessionId();

const homePage = document.querySelector("#home-page");
const chatPage = document.querySelector("#chat-page");
const messages = document.querySelector("#messages");
const form = document.querySelector("#chat-form");
const input = document.querySelector("#message-input");
const modeButton = document.querySelector("#mode-button");
const modeMenu = document.querySelector("#mode-menu");
const modePill = document.querySelector("#mode-pill");
const sendButton = document.querySelector("#send-button");
const cursorGlow = document.querySelector("#cursor-glow");
const selectedModeChip = document.querySelector("#selected-mode-chip");
const selectedModeLabel = document.querySelector("#selected-mode-label");
const clearModeButton = document.querySelector("#clear-mode-button");
const paperModeInput = document.querySelector("#paper-mode-input");
const paperFileInput = document.querySelector("#paper-file-input");
const paperFileButton = document.querySelector("#paper-file-button");
const paperFileLabel = document.querySelector("#paper-file-label");
const paperUrlInput = document.querySelector("#paper-url-input");

bindChatPage();
startParticleField();
bindCursorGlow();

// Bind chat page interactions.
function bindChatPage() {
  if (!form) {
    return;
  }

  modeButton.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleModeMenu();
  });

  modeMenu.addEventListener("click", (event) => {
    event.stopPropagation();
    const button = event.target.closest("button[data-mode]");
    if (!button) {
      return;
    }

    setMode(button.dataset.mode);
    focusCurrentInput();
  });

  clearModeButton.addEventListener("click", (event) => {
    event.stopPropagation();
    setMode("chat");
    focusCurrentInput();
  });

  document.addEventListener("click", (event) => {
    if (!event.target.closest(".mode-menu-wrap")) {
      closeModeMenu();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeModeMenu();
    }
  });

  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
  });

  input.addEventListener("keydown", async (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      await sendMessage();
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await sendMessage();
  });

  paperFileButton.addEventListener("click", () => paperFileInput.click());
  paperFileInput.addEventListener("change", () => {
    setSelectedPaperFile(paperFileInput.files?.[0] || null);
  });
  ["dragenter", "dragover"].forEach((eventName) => {
    paperModeInput.addEventListener(eventName, (event) => {
      if (currentMode !== "paper_reading") return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
      paperModeInput.classList.add("is-dragging");
    });
  });
  ["dragleave", "dragend"].forEach((eventName) => {
    paperModeInput.addEventListener(eventName, () => paperModeInput.classList.remove("is-dragging"));
  });
  paperModeInput.addEventListener("drop", (event) => {
    if (currentMode !== "paper_reading") return;
    event.preventDefault();
    paperModeInput.classList.remove("is-dragging");
    setSelectedPaperFile(event.dataTransfer.files?.[0] || null);
  });

  const initialMode = new URLSearchParams(window.location.search).get("mode");
  setMode(initialMode in modeLabels ? initialMode : currentMode);
}

// Close mode menu and sync accessibility state.
function closeModeMenu() {
  modeMenu.hidden = true;
  modeButton.setAttribute("aria-expanded", "false");
}

// Toggle mode menu visibility.
function toggleModeMenu() {
  modeMenu.hidden = !modeMenu.hidden;
  modeButton.setAttribute("aria-expanded", String(!modeMenu.hidden));
}

// Set current mode and update visible labels.
function setMode(mode) {
  currentMode = mode in modeLabels ? mode : "chat";
  const label = modeLabels[currentMode] || modeLabels.chat;
  const isPaperReading = currentMode === "paper_reading";

  modePill.textContent = `当前模式：${label}`;
  selectedModeLabel.textContent = label;
  selectedModeChip.hidden = currentMode === "chat";
  input.hidden = isPaperReading;
  input.required = !isPaperReading;
  paperModeInput.hidden = !isPaperReading;
  sendButton.textContent = isPaperReading ? "解析论文" : "发送";
  closeModeMenu();
}

// Send user message to current backend endpoint.
async function sendMessage() {
  if (isGenerating) {
    activeResponseController?.abort();
    return;
  }

  if (currentMode === "paper_reading") {
    await submitPaper();
    return;
  }

  const content = input.value.trim();
  if (!content) {
    return;
  }

  const requestMode = currentMode;
  const endpoint = modeEndpoints[requestMode];

  appendMessage("user", content);
  input.value = "";
  input.style.height = "auto";
  if (requestMode !== "chat") {
    setMode("chat");
  }
  setLoading(true, requestMode === "chat");

  try {
    if (requestMode === "domain_onboarding") {
      const job = await submitDomainOnboardingJob(content);
      appendDomainOnboardingCard(job, content);
      watchDomainOnboardingCard(job.task_id, job.access_token);
      return;
    }

    const controller = new AbortController();
    activeResponseController = controller;
    const streaming = appendStreamingMessage();
    const response = await fetch(`${endpoint}/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        session_id: sessionId,
        content,
        user_id: "local-web",
        metadata: {},
      }),
      signal: controller.signal,
    });
    const data = await streamSseJson(
      response,
      (delta) => streaming.append(delta),
      (delta) => streaming.appendReasoning(delta),
    );
    streaming.finish(extractReply(data), data?.reasoning || "");
  } catch (error) {
    if (error.name === "AbortError") {
      finishInterruptedMessage();
    } else {
      if (activeStreamingMessage) activeStreamingMessage.finish(`请求失败：${error.message}`);
      else appendMessage("assistant", `请求失败：${error.message}`);
    }
  } finally {
    activeResponseController = null;
    setLoading(false);
  }
}

// Submit domain onboarding as a background job so the chat request never times out.
async function submitDomainOnboardingJob(content) {
  const response = await fetch("/domain_onboarding/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: sessionId,
      content,
      user_id: "local-web",
      metadata: {},
      client_request_id: crypto.randomUUID(),
    }),
  });
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`后端返回了无法解析的响应（HTTP ${response.status}）`);
  }
  if (!response.ok || !payload?.task_id) {
    const detail = typeof payload?.detail === "string" ? payload.detail : "";
    throw new Error(detail || `创建领域入门任务失败（HTTP ${response.status}）`);
  }
  saveDomainWorkspace({
    schema_version: "1.5",
    saved_at: new Date().toISOString(),
    task_id: payload.task_id,
    access_token: payload.access_token,
    request: { query: content, session_id: sessionId, user_id: "local-web", metadata: {} },
    snapshot: { ...payload, progress: 0 },
  });
  return payload;
}

// Append an interactive onboarding card; the full result lives in its workspace.
function appendDomainOnboardingCard(job, query) {
  const item = document.createElement("article");
  item.className = "message assistant domain-card-message";
  item.dataset.taskId = job.task_id;

  const card = document.createElement("button");
  card.type = "button";
  card.className = "domain-chat-card";
  card.innerHTML = `
    <span class="paper-card-kicker">DOMAIN ONBOARDING · 已开始</span>
    <strong>${escapeHtml(query)}</strong>
    <span class="domain-card-copy">正在为你检索真实论文、梳理发展脉络并生成个性化学习路线。</span>
    <span class="domain-card-progress" aria-label="生成进度">
      <span class="domain-card-progress-fill" style="transform:scaleX(0)"></span>
    </span>
    <span class="domain-card-meta">
      <span data-domain-state>任务已接收</span>
      <span data-domain-progress>0%</span>
    </span>
    <span class="paper-card-enter">进入领域学习工作台 <b>↗</b></span>
  `;
  card.addEventListener("click", () => {
    window.location.href = `/app/domain-onboarding?task_id=${encodeURIComponent(job.task_id)}`;
  });
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "domain-card-cancel";
  cancel.textContent = "中断生成";
  cancel.addEventListener("click", async () => {
    cancel.disabled = true;
    try {
      const response = await fetch(`/domain_onboarding/jobs/${encodeURIComponent(job.task_id)}`, {
        method: "DELETE",
        headers: job.access_token ? { Authorization: `Bearer ${job.access_token}` } : {},
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      cancel.textContent = "正在中断";
    } catch (error) {
      cancel.disabled = false;
      cancel.textContent = "重试中断";
    }
  });
  item.append(card, cancel);
  messages.appendChild(item);
  messages.scrollTop = messages.scrollHeight;
}

// Keep the chat card useful while the user decides when to open the workspace.
function watchDomainOnboardingCard(taskId, accessToken) {
  const labels = {
    queued: "等待执行",
    running: "正在生成",
    cancel_requested: "正在取消",
    completed: "学习路线已生成",
    failed: "生成失败，可进入工作台重试",
    cancelled: "任务已取消",
    interrupted: "任务因服务重启中断",
  };
  let attempts = 0;
  const poll = async () => {
    const item = Array.from(document.querySelectorAll(".domain-card-message"))
      .find((node) => node.dataset.taskId === taskId);
    if (!item) return;
    try {
      const response = await fetch(`/domain_onboarding/jobs/${encodeURIComponent(taskId)}`, {
        headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
      });
      if (!response.ok) throw new Error(String(response.status));
      const snapshot = await response.json();
      const progress = Math.max(0, Math.min(1, Number(snapshot.progress) || 0));
      item.querySelector("[data-domain-state]").textContent = labels[snapshot.state] || snapshot.current_stage || "处理中";
      item.querySelector("[data-domain-progress]").textContent = `${Math.round(progress * 100)}%`;
      item.querySelector(".domain-card-progress-fill").style.transform = `scaleX(${progress})`;
      item.querySelector(".paper-card-kicker").textContent =
        snapshot.state === "completed" ? "DOMAIN ONBOARDING · 已完成" : "DOMAIN ONBOARDING · 生成中";
      const cancel = item.querySelector(".domain-card-cancel");
      if (cancel) cancel.hidden = ["completed", "failed", "cancelled", "interrupted"].includes(snapshot.state);
      const saved = loadDomainWorkspace() || {};
      saveDomainWorkspace({
        ...saved,
        saved_at: new Date().toISOString(),
        task_id: taskId,
        access_token: accessToken || saved.access_token || saved.snapshot?.access_token || "",
        snapshot,
      });
      if (!["completed", "failed", "cancelled", "interrupted"].includes(snapshot.state)) {
        window.setTimeout(poll, 1800);
      }
    } catch {
      attempts += 1;
      if (attempts < 4) window.setTimeout(poll, 2500);
    }
  };
  window.setTimeout(poll, 600);
}

function loadDomainWorkspace() {
  try {
    const value = JSON.parse(localStorage.getItem(DOMAIN_WORKSPACE_KEY) || "null");
    return value?.schema_version === "1.5" ? value : null;
  } catch {
    return null;
  }
}

function saveDomainWorkspace(value) {
  localStorage.setItem(DOMAIN_WORKSPACE_KEY, JSON.stringify(value));
}

// Upload a local PDF or import a PDF/arXiv link from the chat composer.
async function submitPaper() {
  const rawUrl = paperUrlInput.value.trim();
  if (!selectedPaperFile && !rawUrl) {
    appendMessage("assistant", "请选择本地 PDF，或粘贴 PDF / arXiv 链接。");
    return;
  }

  if (selectedPaperFile && rawUrl) {
    appendMessage("assistant", "一次请选择一种导入方式：本地 PDF 或在线链接。");
    return;
  }

  setLoading(true);
  try {
    const request = {
      action: "upload_paper",
      session_id: "",
      paper_id: "",
      content: "",
      metadata: {},
    };
    let sourceLabel = "在线 PDF";

    if (selectedPaperFile) {
      appendMessage("user", `上传论文：${selectedPaperFile.name}`);
      request.pdf_data = await fileToBase64(selectedPaperFile);
      request.metadata = {
        original_filename: selectedPaperFile.name,
        size_bytes: selectedPaperFile.size,
      };
      sourceLabel = "本地 PDF";
    } else {
      const pdfUrl = normalizePdfUrl(rawUrl);
      if (!pdfUrl) {
        throw new Error("请输入有效的 PDF、arXiv 链接或 arXiv ID。");
      }
      appendMessage("user", `导入论文：${rawUrl}`);
      request.pdf_url = pdfUrl;
    }

    const upload = await callPaperReading(request);
    const paperId = upload.data?.paper_id || "";
    if (!paperId) {
      throw new Error("上传成功响应中缺少 paper_id。");
    }

    clearPreviousPaperSession();
    localStorage.setItem("paper_reading_paper_id", paperId);
    const detail = await callPaperReading({
      action: "get_paper_detail",
      session_id: "",
      paper_id: paperId,
      content: "",
      metadata: {},
    });
    const paperCard = appendPaperCard(detail.data?.paper, {
      paperId,
      sourceLabel,
      kgBuild: upload.data?.kg_build || {},
    });
    watchPaperCard(paperCard, paperId, sourceLabel);
    resetPaperComposer();
  } catch (error) {
    appendMessage("assistant", `论文解析失败：${error.message}`);
  } finally {
    setLoading(false);
  }
}

// Call the unified paper-reading endpoint and unwrap ChannelMessage content.
async function callPaperReading(body) {
  const response = await fetch("/paper_reading", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
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
    try {
      payload = JSON.parse(payload);
    } catch {
      throw new Error(payload);
    }
  }
  if (!payload || typeof payload !== "object") {
    throw new Error("后端没有返回论文数据。");
  }
  if (payload.status === "error") {
    throw new Error(payload.message || payload.error || `${body.action} 执行失败`);
  }
  return payload;
}

// Append an interactive paper card; navigation only happens after a user click.
function appendPaperCard(paper, context) {
  const item = document.createElement("article");
  item.className = "message assistant paper-card-message";

  const card = document.createElement("button");
  card.type = "button";
  card.className = "paper-chat-card";
  updatePaperCard(card, paper, context);
  card.addEventListener("click", () => {
    localStorage.setItem("paper_reading_paper_id", context.paperId);
    window.location.href = "/app/paper-reading";
  });
  item.appendChild(card);
  messages.appendChild(item);
  messages.scrollTop = messages.scrollHeight;
  return card;
}

function updatePaperCard(card, paper, context) {
  const parseStatus = paper?.parse_status || "";
  const isParsing = ["queued", "pending", "parsing"].includes(parseStatus);
  const failed = parseStatus === "failed";
  const authors = Array.isArray(paper?.authors) ? paper.authors.filter(Boolean) : [];
  const sections = Array.isArray(paper?.sections) ? paper.sections : [];
  const metadata = [];
  if (authors.length) metadata.push(`<span class="paper-card-authors">${escapeHtml(authors.join("、"))}</span>`);
  if (!isParsing && paper?.abstract) metadata.push(`<span class="paper-card-abstract">${escapeHtml(paper.abstract)}</span>`);
  if (!isParsing && sections.length) {
    metadata.push(`<span class="paper-card-stats"><span>${sections.length} 章节</span></span>`);
  }
  card.innerHTML = `
    <span class="paper-card-kicker">${escapeHtml(context.sourceLabel)} · ${failed ? "解析失败" : (isParsing ? "正在解析" : "解析完成")}</span>
    <strong>${escapeHtml(paper?.title || "论文已上传")}</strong>
    ${metadata.join("")}
    ${isParsing ? '<span class="paper-card-abstract">正在提取作者、摘要和章节结构，完成后会自动显示。</span>' : ""}
    ${failed ? '<span class="paper-card-abstract">论文结构解析失败，可进入精读工作台查看原因或重新导入。</span>' : ""}
    <span class="paper-card-enter">进入论文精读 <b>↗</b></span>
  `;
}

function watchPaperCard(card, paperId, sourceLabel = "论文") {
  let attempts = 0;
  const poll = async () => {
    if (!card?.isConnected || attempts >= 120) return;
    attempts += 1;
    try {
      const detail = await callPaperReading({
        action: "get_paper_detail", session_id: "", paper_id: paperId, content: "", metadata: {},
      });
      const paper = detail.data?.paper || null;
      updatePaperCard(card, paper, { paperId, sourceLabel, kgBuild: {} });
      if (["queued", "pending", "parsing"].includes(paper?.parse_status || "")) {
        window.setTimeout(poll, 1800);
      }
    } catch {
      window.setTimeout(poll, 2500);
    }
  };
  window.setTimeout(poll, 900);
}

function clearPreviousPaperSession() {
  localStorage.removeItem("paper_reading_session_id");
  localStorage.removeItem("paper_reading_current_section");
  localStorage.removeItem("paper_reading_scroll_top");
}

function resetPaperComposer() {
  setSelectedPaperFile(null);
  paperFileInput.value = "";
  paperUrlInput.value = "";
}

function setSelectedPaperFile(file) {
  if (file && file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
    appendMessage("assistant", "拖入的文件不是 PDF，请重新选择。");
    paperFileInput.value = "";
    selectedPaperFile = null;
    paperFileLabel.textContent = "选择 PDF";
    paperFileButton.classList.remove("has-file");
    return;
  }
  selectedPaperFile = file;
  paperFileLabel.textContent = file ? `${file.name} · ${formatBytes(file.size)}` : "选择 PDF";
  paperFileButton.classList.toggle("has-file", Boolean(file));
}

function focusCurrentInput() {
  if (currentMode === "paper_reading") {
    paperUrlInput.focus();
  } else {
    input.focus();
  }
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
      if (url.pathname.startsWith("/pdf/") && !url.pathname.toLowerCase().endsWith(".pdf")) {
        url.pathname += ".pdf";
      }
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

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / (1024 ** index)).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function escapeHtml(value) {
  const span = document.createElement("span");
  span.textContent = String(value ?? "");
  return span.innerHTML;
}

// Render the Markdown subset used by model answers with DOM nodes only.
// Raw HTML is intentionally treated as text so model output cannot inject markup.
function renderSafeMarkdown(source, className = "markdown-content") {
  const root = document.createElement("div");
  root.className = className;
  const lines = String(source || "").replace(/\r\n?/g, "\n").split("\n");
  let paragraph = [];
  let list = null;
  let listType = "";
  let code = null;

  const flushParagraph = () => {
    if (!paragraph.length) return;
    const node = document.createElement("p");
    appendSafeInlineMarkdown(node, paragraph.join(" ").trim());
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
    const pre = document.createElement("pre");
    pre.className = "markdown-code";
    const codeNode = document.createElement("code");
    codeNode.textContent = code.lines.join("\n");
    pre.append(codeNode);
    root.append(pre);
    code = null;
  };

  for (let index = 0; index < lines.length; index += 1) {
    const rawLine = lines[index];
    const line = rawLine.trimEnd();
    if (line.trim().startsWith("```")) {
      flushParagraph();
      flushList();
      if (code) flushCode();
      else code = { lines: [] };
      continue;
    }
    if (code) {
      code.lines.push(rawLine);
      continue;
    }
    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }

    const nextLine = lines[index + 1] || "";
    if (line.includes("|") && isMarkdownTableDivider(nextLine)) {
      flushParagraph();
      flushList();
      const headers = splitMarkdownTableRow(line);
      const table = document.createElement("table");
      const thead = document.createElement("thead");
      const headerRow = document.createElement("tr");
      headers.forEach((value) => {
        const cell = document.createElement("th");
        appendSafeInlineMarkdown(cell, value);
        headerRow.append(cell);
      });
      thead.append(headerRow);
      const tbody = document.createElement("tbody");
      index += 2;
      while (index < lines.length && lines[index].trim() && lines[index].includes("|")) {
        const row = document.createElement("tr");
        const values = splitMarkdownTableRow(lines[index]);
        headers.forEach((_, cellIndex) => {
          const cell = document.createElement("td");
          appendSafeInlineMarkdown(cell, values[cellIndex] || "");
          row.append(cell);
        });
        tbody.append(row);
        index += 1;
      }
      index -= 1;
      table.append(thead, tbody);
      const wrapper = document.createElement("div");
      wrapper.className = "markdown-table-wrap";
      wrapper.append(table);
      root.append(wrapper);
      continue;
    }
    if (/^\s*(?:\*{3,}|-{3,}|_{3,})\s*$/.test(line)) {
      flushParagraph();
      flushList();
      root.append(document.createElement("hr"));
      continue;
    }
    const heading = line.match(/^\s*(#{1,4})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      const node = document.createElement(`h${Math.min(heading[1].length + 2, 6)}`);
      appendSafeInlineMarkdown(node, heading[2]);
      root.append(node);
      continue;
    }
    const quote = line.match(/^\s*>\s?(.*)$/);
    if (quote) {
      flushParagraph();
      flushList();
      const node = document.createElement("blockquote");
      appendSafeInlineMarkdown(node, quote[1]);
      root.append(node);
      continue;
    }
    const bullet = line.match(/^\s*[-+*]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (bullet || ordered) {
      flushParagraph();
      const nextType = ordered ? "ol" : "ul";
      if (!list || listType !== nextType) {
        flushList();
        list = document.createElement(nextType);
        listType = nextType;
      }
      const item = document.createElement("li");
      appendSafeInlineMarkdown(item, (bullet || ordered)[1]);
      list.append(item);
      continue;
    }
    paragraph.push(line.trim());
  }

  flushParagraph();
  flushList();
  flushCode();
  if (!root.childNodes.length) {
    const empty = document.createElement("p");
    empty.textContent = "暂无内容。";
    root.append(empty);
  }
  return root;
}

function splitMarkdownTableRow(line) {
  return String(line || "")
    .trim()
    .replace(/^\||\|$/g, "")
    .split(/(?<!\\)\|/)
    .map((cell) => cell.replace(/\\\|/g, "|").trim());
}

function isMarkdownTableDivider(line) {
  const cells = splitMarkdownTableRow(line);
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function appendSafeInlineMarkdown(target, text) {
  const value = String(text || "");
  const pattern = /(\[[^\]\n]+\]\([^\s)]+(?:\s+"[^"]*")?\)|`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|_[^_\n]+_)/g;
  let cursor = 0;
  for (const match of value.matchAll(pattern)) {
    if (match.index > cursor) target.append(document.createTextNode(value.slice(cursor, match.index)));
    const token = match[0];
    const link = token.match(/^\[([^\]]+)\]\(([^\s)]+)(?:\s+"([^"]*)")?\)$/);
    if (link) {
      const href = safeMarkdownHref(link[2]);
      if (href) {
        const anchor = document.createElement("a");
        anchor.textContent = link[1];
        anchor.href = href;
        anchor.target = "_blank";
        anchor.rel = "noopener noreferrer";
        if (link[3]) anchor.title = link[3];
        target.append(anchor);
      } else {
        target.append(document.createTextNode(link[1]));
      }
    } else if (token.startsWith("`")) {
      const node = document.createElement("code");
      node.textContent = token.slice(1, -1);
      target.append(node);
    } else if (token.startsWith("**") || token.startsWith("__")) {
      const node = document.createElement("strong");
      node.textContent = token.slice(2, -2);
      target.append(node);
    } else {
      const node = document.createElement("em");
      node.textContent = token.slice(1, -1);
      target.append(node);
    }
    cursor = match.index + token.length;
  }
  if (cursor < value.length) target.append(document.createTextNode(value.slice(cursor)));
}

function safeMarkdownHref(rawHref) {
  try {
    const url = new URL(rawHref, window.location.origin);
    return ["http:", "https:", "mailto:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}

window.renderSafeMarkdown = renderSafeMarkdown;

// Extract assistant text from common response shapes.
function extractReply(response) {
  const value =
    response?.content ??
    response?.text ??
    response?.message ??
    response?.data?.content ??
    "后端没有返回内容。";

  if (typeof value === "string") {
    return value;
  }

  if (typeof value?.text === "string") {
    return value.text;
  }

  return JSON.stringify(value, null, 2);
}

// Append one visible message.
function appendMessage(role, content) {
  const item = document.createElement("article");
  item.className = `message ${role}`;

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (role === "assistant") {
    bubble.classList.add("markdown-bubble");
    bubble.append(renderSafeMarkdown(content));
  } else {
    bubble.textContent = content;
  }

  item.appendChild(bubble);
  messages.appendChild(item);
  messages.scrollTop = messages.scrollHeight;
}

let activeStreamingMessage = null;

function appendStreamingMessage() {
  const item = document.createElement("article");
  item.className = "message assistant";
  const bubble = document.createElement("div");
  bubble.className = "bubble markdown-bubble streaming-bubble";
  item.append(bubble);
  messages.append(item);
  let text = "";
  let reasoning = "";
  let streaming = true;
  const render = () => {
    const inline = splitVisibleThinking(text);
    const visibleReasoning = [reasoning, inline.reasoning].filter(Boolean).join("\n\n");
    const answer = inline.answer;
    bubble.replaceChildren();
    if (visibleReasoning) {
      bubble.append(createThinkingDetails(visibleReasoning, streaming));
    } else if (streaming && !answer) {
      const status = document.createElement("p");
      status.className = "thinking-status";
      status.textContent = "正在思考…";
      bubble.append(status);
    }
    if (answer) bubble.append(renderSafeMarkdown(answer));
    messages.scrollTop = messages.scrollHeight;
  };
  render();
  const api = {
    append(delta) {
      text += String(delta || "");
      render();
    },
    appendReasoning(delta) {
      reasoning += String(delta || "");
      render();
    },
    finish(finalText, finalReasoning = "") {
      text = String(finalText || text || "后端没有返回内容。");
      reasoning = String(finalReasoning || reasoning || splitVisibleThinking(text).reasoning || "");
      streaming = false;
      bubble.classList.remove("streaming-bubble");
      render();
      requestAnimationFrame(() => scrollMessageToTop(item));
      if (activeStreamingMessage === api) activeStreamingMessage = null;
    },
    interrupt() {
      text = text ? `${text}\n\n_回答已中断。_` : "回答已中断。";
      streaming = false;
      bubble.classList.remove("streaming-bubble");
      render();
      if (activeStreamingMessage === api) activeStreamingMessage = null;
    },
  };
  activeStreamingMessage = api;
  return api;
}

function scrollMessageToTop(item) {
  if (!item?.isConnected) return;
  const top = messages.scrollTop
    + item.getBoundingClientRect().top
    - messages.getBoundingClientRect().top
    - 12;
  messages.scrollTo({ top: Math.max(0, top), behavior: "auto" });
}

function splitVisibleThinking(value) {
  const text = String(value || "");
  const trimmed = text.trimStart();
  if (!trimmed.startsWith("<think>")) return { reasoning: "", answer: text };
  const start = text.indexOf("<think>") + "<think>".length;
  const end = text.indexOf("</think>", start);
  if (end < 0) return { reasoning: text.slice(start), answer: "" };
  return {
    reasoning: text.slice(start, end).trim(),
    answer: text.slice(end + "</think>".length).trimStart(),
  };
}

function createThinkingDetails(content, open) {
  const details = document.createElement("details");
  details.className = "thinking-details";
  details.open = Boolean(open);
  const summary = document.createElement("summary");
  summary.textContent = open ? "正在思考…" : "思考过程";
  const body = document.createElement("div");
  body.className = "thinking-details-body";
  body.append(renderSafeMarkdown(content));
  details.append(summary, body);
  return details;
}

window.splitVisibleThinking = splitVisibleThinking;
window.createThinkingDetails = createThinkingDetails;

function finishInterruptedMessage() {
  if (activeStreamingMessage) activeStreamingMessage.interrupt();
  else appendMessage("assistant", "回答已中断。");
}

async function streamSseJson(response, onDelta = () => {}, onReasoning = () => {}) {
  if (!response.ok) throw new Error(`请求失败：${response.status}`);
  if (!response.body) throw new Error("当前浏览器不支持流式响应。");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result = null;
  const consume = (block) => {
    let eventName = "message";
    const data = [];
    block.split(/\r?\n/).forEach((line) => {
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    });
    if (!data.length) return;
    const payload = JSON.parse(data.join("\n"));
    if (eventName === "delta") onDelta(payload.text || "");
    if (eventName === "reasoning") onReasoning(payload.text || "");
    if (eventName === "result") result = payload;
    if (eventName === "error") throw new Error(payload.message || "流式请求失败。");
  };
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() || "";
    blocks.forEach(consume);
    if (done) break;
  }
  if (buffer.trim()) consume(buffer);
  return result;
}

window.streamSseJson = streamSseJson;

// Toggle request state.
function setLoading(isLoading, interruptible = false) {
  isGenerating = isLoading;
  sendButton.disabled = isLoading && !interruptible;
  sendButton.classList.toggle("is-stop", isLoading && interruptible);
  sendButton.textContent = isLoading
    ? (interruptible ? "中断" : (currentMode === "paper_reading" ? "解析中" : "生成中"))
    : (currentMode === "paper_reading" ? "解析论文" : "发送");
}

// Get persistent local session id.
function getSessionId() {
  const key = "novicesynapse_session_id";
  const existing = window.localStorage.getItem(key);
  if (existing) {
    return existing;
  }

  const created = `web-${crypto.randomUUID()}`;
  window.localStorage.setItem(key, created);
  return created;
}

// Bind cursor glow position.
function bindCursorGlow() {
  let targetX = window.innerWidth / 2;
  let targetY = window.innerHeight / 2;
  let currentX = targetX;
  let currentY = targetY;

  window.addEventListener("pointermove", (event) => {
    targetX = event.clientX;
    targetY = event.clientY;
  });

  function tick() {
    currentX += (targetX - currentX) * 0.12;
    currentY += (targetY - currentY) * 0.12;
    cursorGlow.style.transform = `translate(${currentX}px, ${currentY}px)`;
    requestAnimationFrame(tick);
  }

  tick();
}

// Start lightweight canvas particles.
function startParticleField() {
  const canvas = document.querySelector("#particle-canvas");
  const ctx = canvas.getContext("2d");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const pointer = {
    x: window.innerWidth / 2,
    y: window.innerHeight / 2,
  };

  let width = 0;
  let height = 0;
  let particles = [];

  window.addEventListener("pointermove", (event) => {
    pointer.x = event.clientX;
    pointer.y = event.clientY;
  });

  window.addEventListener("resize", resize);
  resize();

  if (reducedMotion) {
    drawStaticParticles();
    return;
  }

  requestAnimationFrame(draw);

  // Resize canvas and reduce particle count on small screens.
  function resize() {
    width = window.innerWidth;
    height = window.innerHeight;
    canvas.width = Math.floor(width * window.devicePixelRatio);
    canvas.height = Math.floor(height * window.devicePixelRatio);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ctx.setTransform(window.devicePixelRatio, 0, 0, window.devicePixelRatio, 0, 0);

    const baseCount = width < 720 ? 48 : 88;
    const hardwareLimit = navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 4 ? 0.68 : 1;
    const count = Math.floor(baseCount * hardwareLimit);
    particles = Array.from({ length: count }, createParticle);
  }

  // Create one particle.
  function createParticle() {
    return {
      x: Math.random() * width,
      y: Math.random() * height,
      vx: (Math.random() - 0.5) * 0.34,
      vy: (Math.random() - 0.5) * 0.34,
      r: Math.random() * 1.8 + 0.6,
    };
  }

  // Draw one animation frame.
  function draw() {
    ctx.clearRect(0, 0, width, height);
    drawConnections();
    drawParticles();
    requestAnimationFrame(draw);
  }

  // Draw particle links.
  function drawConnections() {
    for (let i = 0; i < particles.length; i += 1) {
      for (let j = i + 1; j < particles.length; j += 1) {
        const a = particles[i];
        const b = particles[j];
        const distance = Math.hypot(a.x - b.x, a.y - b.y);
        if (distance > 132) {
          continue;
        }

        ctx.strokeStyle = `rgba(102, 245, 214, ${0.13 * (1 - distance / 132)})`;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
    }
  }

  // Draw and update particles.
  function drawParticles() {
    for (const particle of particles) {
      const dx = pointer.x - particle.x;
      const dy = pointer.y - particle.y;
      const distance = Math.hypot(dx, dy);
      if (distance < 180) {
        particle.vx += dx * 0.000018;
        particle.vy += dy * 0.000018;
      }

      particle.x += particle.vx;
      particle.y += particle.vy;
      particle.vx *= 0.995;
      particle.vy *= 0.995;

      if (particle.x < -20) particle.x = width + 20;
      if (particle.x > width + 20) particle.x = -20;
      if (particle.y < -20) particle.y = height + 20;
      if (particle.y > height + 20) particle.y = -20;

      ctx.fillStyle = "rgba(190, 255, 240, 0.78)";
      ctx.beginPath();
      ctx.arc(particle.x, particle.y, particle.r, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  // Draw one static frame when reduced motion is requested.
  function drawStaticParticles() {
    ctx.clearRect(0, 0, width, height);
    drawConnections();
    drawParticles();
  }
}
