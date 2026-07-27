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

let currentMode = "chat";
let isGenerating = false;
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
    const file = paperFileInput.files?.[0] || null;
    if (file && file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
      appendMessage("assistant", "请选择 PDF 文件。");
      paperFileInput.value = "";
      selectedPaperFile = null;
      return;
    }
    selectedPaperFile = file;
    paperFileLabel.textContent = file ? `${file.name} · ${formatBytes(file.size)}` : "选择 PDF";
    paperFileButton.classList.toggle("has-file", Boolean(file));
  });

  const initialMode = new URLSearchParams(window.location.search).get("mode");
  setMode(initialMode === "paper_reading" ? "paper_reading" : currentMode);
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
  setLoading(true);

  try {
    const response = await fetch(endpoint, {
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
    });

    if (!response.ok) {
      throw new Error(`请求失败：${response.status}`);
    }

    const data = await response.json();
    appendMessage("assistant", extractReply(data));
  } catch (error) {
    appendMessage("assistant", `请求失败：${error.message}`);
  } finally {
    setLoading(false);
  }
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
    appendPaperCard(detail.data?.paper, {
      paperId,
      sourceLabel,
      kgBuild: upload.data?.kg_build || {},
    });
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
  card.innerHTML = `
    <span class="paper-card-kicker">${escapeHtml(context.sourceLabel)} · 解析完成</span>
    <strong>${escapeHtml(paper?.title || "未命名论文")}</strong>
    <span class="paper-card-authors">${escapeHtml((paper?.authors || []).join("、") || "作者信息暂无")}</span>
    <span class="paper-card-abstract">${escapeHtml(paper?.abstract || "论文已经完成结构化解析，点击进入精读工作台。")}</span>
    <span class="paper-card-stats">
      <span>${paper?.sections?.length || 0} 章节</span>
      <span>${context.kgBuild?.new_nodes ?? "—"} 节点</span>
      <span>${context.kgBuild?.new_edges ?? "—"} 关系</span>
    </span>
    <span class="paper-card-enter">进入论文精读 <b>↗</b></span>
  `;
  card.addEventListener("click", () => {
    localStorage.setItem("paper_reading_paper_id", context.paperId);
    window.location.href = "/app/paper-reading";
  });
  item.appendChild(card);
  messages.appendChild(item);
  messages.scrollTop = messages.scrollHeight;
}

function clearPreviousPaperSession() {
  localStorage.removeItem("paper_reading_session_id");
  localStorage.removeItem("paper_reading_current_section");
  localStorage.removeItem("paper_reading_scroll_top");
}

function resetPaperComposer() {
  selectedPaperFile = null;
  paperFileInput.value = "";
  paperFileLabel.textContent = "选择 PDF";
  paperFileButton.classList.remove("has-file");
  paperUrlInput.value = "";
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
  bubble.textContent = content;

  item.appendChild(bubble);
  messages.appendChild(item);
  messages.scrollTop = messages.scrollHeight;
}

// Toggle request state.
function setLoading(isLoading) {
  isGenerating = isLoading;
  sendButton.disabled = isLoading;
  sendButton.textContent = isLoading ? (currentMode === "paper_reading" ? "解析中" : "生成中") : (currentMode === "paper_reading" ? "解析论文" : "发送");
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
