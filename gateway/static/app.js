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

const DOMAIN_WORKSPACE_KEY = "domain_onboarding_workspace_v1_9";
const DOMAIN_PENDING_REQUEST_KEY = "domain_onboarding_pending_request_v1";
const DOMAIN_TERMINAL_STATES = new Set(["completed", "failed", "cancelled", "interrupted"]);
const CHAT_PENDING_GENERATION_KEY = "scholarsprout_pending_chat_generation_v1";
const tutorialParams = new URLSearchParams(window.location.search);
const tutorialActive = tutorialParams.get("tutorial") === "1";

let currentMode = "chat";
let isGenerating = false;
let activeResponseController = null;
let activeGenerationId = "";
let selectedPaperFile = null;
let activeDiscussions = [];
let conversationContexts = [];
let contextImportCandidates = [];
let contextImportSelection = new Set();
let contextImportKind = "all";
let conversationHistoryReload = null;
let conversationHistorySignature = "";
let conversationContextSignature = "";
let conversationHistoryRendered = false;
let conversationScrollTarget = "";
const persistedMessageContents = new Map();
let activeGenerationWatcher = null;
let explicitStopRequested = false;
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
const stopButton = document.querySelector("#stop-button");
const cursorGlow = document.querySelector("#cursor-glow");
const selectedModeChip = document.querySelector("#selected-mode-chip");
const selectedModeLabel = document.querySelector("#selected-mode-label");
const clearModeButton = document.querySelector("#clear-mode-button");
const paperModeInput = document.querySelector("#paper-mode-input");
const paperFileInput = document.querySelector("#paper-file-input");
const paperFileButton = document.querySelector("#paper-file-button");
const paperFileLabel = document.querySelector("#paper-file-label");
const paperUrlInput = document.querySelector("#paper-url-input");
const discussionBar = document.querySelector("#discussion-context-bar");
const discussionButton = document.querySelector("#discussion-context-button");
const discussionValue = document.querySelector("#discussion-context-value");
const discussionMenu = document.querySelector("#discussion-context-menu");
const contextImportModal = document.querySelector("#context-import-modal");
const contextImportSearch = document.querySelector("#context-import-search");
const contextImportResults = document.querySelector("#context-import-results");
const startExperienceLink = document.querySelector("#start-experience");

if (startExperienceLink) {
  startExperienceLink.href = "/app?new=1";
  fetch("/api/tutorial/status", { cache: "no-store" })
    .then((response) => response.ok ? response.json() : { completed: false })
    .then((status) => {
      if (!status.completed) window.location.replace("/app?tutorial=1");
    })
    .catch(() => {});
}
if (document.body.classList.contains("chat-body") && !tutorialActive && tutorialParams.get("tutorial") !== "skip") {
  fetch("/api/tutorial/status", { cache: "no-store" })
    .then((response) => response.ok ? response.json() : { completed: true })
    .then((status) => {
      if (!status.completed) window.location.replace("/app?tutorial=1");
    })
    .catch(() => {});
}

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
    if (!event.target.closest(".discussion-context-picker")) {
      setDiscussionMenuOpen(false);
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeModeMenu();
      setDiscussionMenuOpen(false);
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

  stopButton?.addEventListener("click", () => {
    void stopActiveGeneration();
  });

  discussionButton?.addEventListener("click", (event) => {
    event.stopPropagation();
    setDiscussionMenuOpen(discussionMenu.hidden);
  });
  discussionMenu?.addEventListener("click", (event) => {
    event.stopPropagation();
    const importButton = event.target.closest("button[data-import-contexts]");
    if (importButton) {
      setDiscussionMenuOpen(false);
      void openContextImport();
      return;
    }
    const option = event.target.closest("button[data-context-key]");
    if (option) toggleDiscussion(option.dataset.contextKey || "");
  });
  document.querySelector("#context-import-close")?.addEventListener("click", closeContextImport);
  document.querySelector("#context-import-cancel")?.addEventListener("click", closeContextImport);
  document.querySelector("#context-import-confirm")?.addEventListener("click", () => void importSelectedContexts());
  contextImportModal?.addEventListener("click", (event) => {
    if (event.target === contextImportModal) closeContextImport();
  });
  contextImportSearch?.addEventListener("input", debounce(() => void loadContextCandidates(), 220));
  document.querySelector("#context-import-filters")?.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-kind]");
    if (!button) return;
    contextImportKind = button.dataset.kind || "all";
    document.querySelectorAll("#context-import-filters button").forEach((item) => item.classList.toggle("is-active", item === button));
    renderContextCandidates();
  });
  contextImportResults?.addEventListener("click", (event) => {
    const card = event.target.closest("button[data-artifact-id]");
    if (!card || card.disabled) return;
    const artifactId = card.dataset.artifactId || "";
    if (contextImportSelection.has(artifactId)) contextImportSelection.delete(artifactId);
    else contextImportSelection.add(artifactId);
    renderContextCandidates();
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
  if (tutorialActive) return;
  void restoreConversationHistory().finally(() => {
    restoreDomainOnboardingCard();
    void restorePendingChatGeneration();
  });
  const reloadWhenEnteringChat = () => {
    if (!isGenerating) {
      void restoreConversationHistory().finally(() => restorePendingChatGeneration());
    }
  };
  window.addEventListener("pageshow", reloadWhenEnteringChat);
  window.addEventListener("focus", reloadWhenEnteringChat);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") reloadWhenEnteringChat();
  });
  window.setInterval(reloadWhenEnteringChat, 650);
}

async function restoreConversationHistory() {
  if (conversationHistoryReload) return conversationHistoryReload;
  conversationHistoryReload = reloadConversationHistory();
  try {
    await conversationHistoryReload;
  } finally {
    conversationHistoryReload = null;
  }
}

async function reloadConversationHistory() {
  try {
    const response = await fetch(`/api/research/conversations/${encodeURIComponent(sessionId)}`, { cache: "no-store" });
    if (!response.ok) return;
    const conversation = await response.json();
    const history = Array.isArray(conversation.messages) ? conversation.messages : [];
    const contexts = normalizeConversationContexts(
      Array.isArray(conversation.contexts) ? conversation.contexts : [],
    );
    const wasRendered = conversationHistoryRendered;
    const previousScrollTop = messages.scrollTop;
    const contextSignature = JSON.stringify(
      contexts.map((context) => [context.kind, context.id, context.linked_at]),
    );
    const signature = JSON.stringify({
      updatedAt: conversation.updated_at || "",
      messages: history.map((message) => [message.message_id, message.created_at, message.content]),
      contexts: contexts.map((context) => [context.kind, context.id, context.linked_at]),
    });
    if (signature === conversationHistorySignature) {
      if (conversationScrollTarget) {
        scrollRestoredConversation({ wasRendered, previousScrollTop });
      }
      return;
    }
    if (
      conversationHistoryRendered
      && contextSignature === conversationContextSignature
      && patchPersistedMessages(history)
    ) {
      conversationHistorySignature = signature;
      scrollRestoredConversation({ wasRendered, previousScrollTop });
      return;
    }
    conversationHistorySignature = signature;
    conversationContextSignature = contextSignature;
    conversationContexts = contexts;
    restoreDiscussionSelector();
    if (!history.length && !conversationContexts.length) return;
    messages.replaceChildren();
    persistedMessageContents.clear();
    const timeline = [
      ...history.map((message) => ({ type: "message", time: message.created_at || "", value: message })),
      ...conversationContexts.map((context) => ({ type: "context", time: context.linked_at || "", value: context })),
    ].sort((left, right) => String(left.time).localeCompare(String(right.time)));
    for (const entry of timeline) {
      if (entry.type === "context") {
        if (entry.value.kind === "paper_reading") await restorePaperReadingCard(entry.value);
        if (entry.value.kind === "domain_onboarding") restoreDomainContextCard(entry.value);
        continue;
      }
      const message = entry.value;
      if (message.role === "assistant" && message.mode === "domain_onboarding") continue;
      if (["user", "assistant"].includes(message.role)) {
        appendMessage(message.role, message.content, message.message_id);
      }
    }
    conversationHistoryRendered = true;
    scrollRestoredConversation({ wasRendered, previousScrollTop });
  } catch {
    // Keep the welcome message when history is temporarily unavailable.
  }
}

function normalizeConversationContexts(contexts) {
  const byIdentity = new Map();
  contexts.forEach((context) => {
    const identity = context?.kind === "paper_reading"
      ? `paper_reading:${context.paper_id || context.id || ""}`
      : contextKey(context);
    if (!identity || identity.endsWith(":")) return;
    const previous = byIdentity.get(identity);
    if (!previous || String(context.linked_at || "") >= String(previous.linked_at || "")) {
      byIdentity.set(identity, context);
    }
  });
  return [...byIdentity.values()];
}

function visiblePersistedMessages(history) {
  return history.filter((message) => (
    ["user", "assistant"].includes(message.role)
    && !(message.role === "assistant" && message.mode === "domain_onboarding")
  ));
}

function patchPersistedMessages(history) {
  const expected = visiblePersistedMessages(history);
  const existing = [...messages.querySelectorAll("[data-persisted-message-id]")];
  if (existing.length > expected.length || expected.length - existing.length > 1) return false;
  for (let index = 0; index < existing.length; index += 1) {
    if (existing[index].dataset.persistedMessageId !== String(expected[index].message_id || "")) {
      return false;
    }
  }
  for (let index = 0; index < expected.length; index += 1) {
    const message = expected[index];
    const messageId = String(message.message_id || "");
    const content = String(message.content || "");
    const item = existing[index];
    if (!item) {
      appendMessage(message.role, content, messageId);
      continue;
    }
    if (persistedMessageContents.get(messageId) === content) continue;
    const bubble = item.querySelector(".bubble");
    if (!bubble) return false;
    if (message.role === "assistant") bubble.replaceChildren(renderSafeMarkdown(content));
    else bubble.textContent = content;
    persistedMessageContents.set(messageId, content);
  }
  return true;
}

async function restorePaperReadingCard(context) {
  const paperId = String(context.paper_id || "").trim();
  const readingSessionId = String(context.id || "").trim();
  if (!paperId || !readingSessionId) return;
  try {
    const detail = await callPaperReading({
      action: "get_paper_detail", session_id: readingSessionId, paper_id: paperId, content: "", metadata: {},
    });
    appendPaperCard(detail.data?.paper, {
      paperId,
      sessionId: readingSessionId,
      sourceLabel: "当前论文精读",
    });
  } catch {
    appendPaperCard({ title: context.title || "当前论文精读", parse_status: "" }, {
      paperId,
      sessionId: readingSessionId,
      sourceLabel: "当前论文精读",
    });
  }
}

function restoreDomainContextCard(context) {
  if (!context?.id || document.querySelector(`.domain-card-message[data-task-id="${CSS.escape(context.id)}"]`)) return;
  const item = document.createElement("article");
  item.className = "message assistant domain-card-message";
  item.dataset.taskId = context.id;
  item.dataset.contextKey = contextKey(context);
  const card = document.createElement("button");
  card.type = "button";
  card.className = "domain-chat-card";
  card.innerHTML = `
    <span class="paper-card-kicker">DOMAIN ONBOARDING · 已保存</span>
    <strong>${escapeHtml(context.title || "领域入门")}</strong>
    <span class="domain-card-copy">继续查看领域概览、学习路线与论文清单。</span>
    <span class="paper-card-enter">进入领域学习工作台 <b>↗</b></span>
  `;
  card.addEventListener("click", () => {
    window.location.href = `/app/domain-onboarding?task_id=${encodeURIComponent(context.id)}&conversation_id=${encodeURIComponent(sessionId)}`;
  });
  item.append(card);
  messages.append(item);
}

function contextKey(context) {
  return `${context?.kind || ""}:${context?.id || ""}`;
}

function restoreDiscussionSelector() {
  if (!discussionMenu || !discussionButton || !discussionValue || !discussionBar) return;
  discussionMenu.replaceChildren();
  const local = conversationContexts.filter((context) => context.relation !== "discussed");
  const imported = conversationContexts.filter((context) => context.relation === "discussed");
  appendDiscussionGroup("本会话涉及", local);
  if (imported.length) appendDiscussionGroup("已引入的外部结果", imported);
  const importButton = document.createElement("button");
  importButton.type = "button";
  importButton.className = "discussion-context-import";
  importButton.dataset.importContexts = "1";
  importButton.innerHTML = '<span>＋</span><span><strong>引入会话外部结果</strong><small>搜索已有论文精读与领域入门</small></span>';
  discussionMenu.append(importButton);
  const params = new URLSearchParams(window.location.search);
  const requestedKeys = params.getAll("context");
  const legacyKey = `${params.get("context_kind") || ""}:${params.get("context_id") || ""}`;
  const previousKeys = activeDiscussions.map(contextKey);
  const desiredKeys = requestedKeys.length ? requestedKeys : (legacyKey !== ":" ? [legacyKey] : previousKeys);
  activeDiscussions = conversationContexts.filter((item) => desiredKeys.includes(contextKey(item)));
  if (!activeDiscussions.length && !requestedKeys.length && legacyKey === ":" && conversationContexts.length) {
    activeDiscussions = [conversationContexts.at(-1)];
  }
  syncDiscussionPicker();
  discussionBar.hidden = false;
}

function appendDiscussionGroup(label, contexts) {
  const heading = document.createElement("div");
  heading.className = "discussion-context-group";
  heading.textContent = label;
  discussionMenu.append(heading);
  if (!contexts.length) {
    const empty = document.createElement("div");
    empty.className = "discussion-context-empty";
    empty.textContent = "暂无结果";
    discussionMenu.append(empty);
    return;
  }
  contexts.forEach((context) => discussionMenu.append(createDiscussionOption(context)));
}

function createDiscussionOption(context) {
  const option = document.createElement("button");
  const key = context ? contextKey(context) : "";
  const kind = context?.kind === "paper_reading" ? "论文" : context ? "领域" : "通用";
  option.type = "button";
  option.className = "discussion-context-option";
  option.dataset.contextKey = key;
  option.setAttribute("role", "option");
  option.innerHTML = `
    <span class="discussion-context-option-kind">${kind}</span>
    <span class="discussion-context-option-title">${escapeHtml(context?.title || "不指定")}</span>
    <span class="discussion-context-option-check" aria-hidden="true"></span>
  `;
  return option;
}

function toggleDiscussion(key) {
  const item = conversationContexts.find((context) => contextKey(context) === key);
  if (!item) return;
  const selected = activeDiscussions.some((context) => contextKey(context) === key);
  activeDiscussions = selected
    ? activeDiscussions.filter((context) => contextKey(context) !== key)
    : [...activeDiscussions, item];
  const params = new URLSearchParams(window.location.search);
  params.delete("context_kind");
  params.delete("context_id");
  params.delete("context");
  activeDiscussions.forEach((context) => params.append("context", contextKey(context)));
  const query = params.toString();
  window.history.replaceState(null, "", `${window.location.pathname}${query ? `?${query}` : ""}`);
  syncDiscussionPicker();
}

function syncDiscussionPicker() {
  if (!discussionValue || !discussionMenu) return;
  const selectedKeys = new Set(activeDiscussions.map(contextKey));
  discussionValue.textContent = activeDiscussions.length === 1
    ? `${activeDiscussions[0].kind === "paper_reading" ? "论文" : "领域"} · ${activeDiscussions[0].title || "未命名"}`
    : activeDiscussions.length > 1 ? `已选择 ${activeDiscussions.length} 项当前讨论` : "不指定论文或领域";
  discussionMenu.querySelectorAll(".discussion-context-option").forEach((option) => {
    const selected = selectedKeys.has(option.dataset.contextKey || "");
    option.classList.toggle("is-selected", selected);
    option.setAttribute("aria-selected", String(selected));
    const check = option.querySelector(".discussion-context-option-check");
    if (check) check.textContent = selected ? "✓" : "";
  });
}

async function openContextImport() {
  if (!contextImportModal) return;
  contextImportSelection = new Set();
  contextImportModal.hidden = false;
  document.body.classList.add("context-import-open");
  await loadContextCandidates();
  contextImportSearch?.focus();
}

function closeContextImport() {
  if (!contextImportModal) return;
  contextImportModal.hidden = true;
  document.body.classList.remove("context-import-open");
}

async function loadContextCandidates() {
  if (!contextImportResults) return;
  contextImportResults.innerHTML = '<div class="context-import-loading">正在整理研究结果…</div>';
  const search = encodeURIComponent(contextImportSearch?.value.trim() || "");
  try {
    const response = await fetch(`/api/research/conversations/${encodeURIComponent(sessionId)}/context-candidates?search=${search}&limit=150`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    contextImportCandidates = await response.json();
    renderContextCandidates();
  } catch {
    contextImportResults.innerHTML = '<div class="context-import-loading">暂时无法读取研究结果，请稍后重试。</div>';
  }
}

function renderContextCandidates() {
  if (!contextImportResults) return;
  const items = contextImportCandidates.filter((item) => contextImportKind === "all" || item.kind === contextImportKind);
  contextImportResults.replaceChildren();
  if (!items.length) contextImportResults.innerHTML = '<div class="context-import-loading">没有找到匹配的研究结果。</div>';
  items.forEach((item) => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "context-import-card";
    card.dataset.artifactId = item.artifact_id;
    card.disabled = Boolean(item.linked);
    card.classList.toggle("is-selected", contextImportSelection.has(item.artifact_id));
    card.innerHTML = `<span class="context-import-card-kind">${item.kind === "paper_reading" ? "论文精读" : "领域入门"}</span><strong>${escapeHtml(item.title || "未命名")}</strong><small>${item.linked ? "已在当前会话" : "可引入当前会话"}</small><span class="context-import-card-check">${item.linked ? "已引入" : contextImportSelection.has(item.artifact_id) ? "✓" : ""}</span>`;
    contextImportResults.append(card);
  });
  const count = document.querySelector("#context-import-count");
  const confirm = document.querySelector("#context-import-confirm");
  if (count) count.textContent = contextImportSelection.size ? `已选择 ${contextImportSelection.size} 项` : "未选择";
  if (confirm) confirm.disabled = !contextImportSelection.size;
}

async function importSelectedContexts() {
  if (!contextImportSelection.size) return;
  const confirm = document.querySelector("#context-import-confirm");
  if (confirm) confirm.disabled = true;
  try {
    const response = await fetch(`/api/research/conversations/${encodeURIComponent(sessionId)}/contexts`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ artifact_ids: [...contextImportSelection] }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
    conversationContexts = Array.isArray(payload.contexts) ? payload.contexts : conversationContexts;
    const importedIds = new Set(payload.linked_artifact_ids || []);
    activeDiscussions = [...activeDiscussions, ...conversationContexts.filter((item) => importedIds.has(item.artifact_id))]
      .filter((item, index, all) => all.findIndex((other) => contextKey(other) === contextKey(item)) === index);
    closeContextImport();
    restoreDiscussionSelector();
    syncDiscussionUrl();
  } catch (error) {
    if (contextImportResults) contextImportResults.innerHTML = `<div class="context-import-loading">${escapeHtml(error.message || "引入失败")}</div>`;
  } finally {
    if (confirm) confirm.disabled = !contextImportSelection.size;
  }
}

function syncDiscussionUrl() {
  const params = new URLSearchParams(window.location.search);
  params.delete("context_kind"); params.delete("context_id"); params.delete("context");
  activeDiscussions.forEach((context) => params.append("context", contextKey(context)));
  const query = params.toString();
  window.history.replaceState(null, "", `${window.location.pathname}${query ? `?${query}` : ""}`);
}

function debounce(callback, delay) {
  let timer;
  return (...args) => { window.clearTimeout(timer); timer = window.setTimeout(() => callback(...args), delay); };
}

function setDiscussionMenuOpen(open) {
  if (!discussionMenu || !discussionButton) return;
  discussionMenu.hidden = !open;
  discussionButton.setAttribute("aria-expanded", String(open));
}

function scrollRestoredConversation({ wasRendered = false, previousScrollTop = 0 } = {}) {
  const params = new URLSearchParams(window.location.search);
  const requestedKey = params.getAll("context")[0]
    || `${params.get("context_kind") || ""}:${params.get("context_id") || ""}`;
  const requestedCard = requestedKey === ":"
    ? null
    : messages.querySelector(`[data-context-key="${CSS.escape(requestedKey)}"]`);
  requestAnimationFrame(() => {
    if (conversationScrollTarget === "latest-assistant") {
      const latestAssistant = [...messages.querySelectorAll(".message.assistant[data-persisted-message-id]")].at(-1);
      if (latestAssistant) {
        conversationScrollTarget = "";
        scrollMessageToTop(latestAssistant);
        return;
      }
    }
    if (requestedCard && !wasRendered) {
      scrollMessageToTop(requestedCard);
      return;
    }
    if (wasRendered) {
      messages.scrollTop = Math.max(0, previousScrollTop);
      return;
    }
    messages.scrollTop = messages.scrollHeight;
  });
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
  if (!isGenerating) sendButton.textContent = isPaperReading ? "解析论文" : "发送";
  closeModeMenu();
}

// Send user message to current backend endpoint.
async function sendMessage() {
  if (tutorialActive) return;
  if (isGenerating) {
    await stopActiveGeneration();
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
  if (window.ensureBaseModelConfigured && !(await window.ensureBaseModelConfigured())) return;

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
      updateDomainOnboardingCard(job.task_id, job);
      watchDomainOnboardingCard(job.task_id, job.access_token || "");
      return;
    }

    const controller = new AbortController();
    const generationId = globalThis.crypto?.randomUUID?.()
      || `generation-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    explicitStopRequested = false;
    activeResponseController = controller;
    activeGenerationId = generationId;
    savePendingChatGeneration({
      generation_id: generationId,
      session_id: sessionId,
      question: content,
      started_at: new Date().toISOString(),
    });
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
        metadata: {
          generation_id: generationId,
          ...(activeDiscussions.length ? { active_contexts: activeDiscussions.map((context) => ({
            kind: context.kind,
            id: context.id,
            title: context.title || "",
          })) } : {}),
        },
      }),
      signal: controller.signal,
    });
    const data = await streamSseJson(
      response,
      (delta) => streaming.append(delta),
      (delta) => streaming.appendReasoning(delta),
    );
    streaming.finish(
      extractReply(data),
      data?.reasoning || "",
    );
    clearPendingChatGeneration(generationId);
  } catch (error) {
    const generationId = activeGenerationId;
    if (error.name === "AbortError" && explicitStopRequested) {
      finishInterruptedMessage();
      clearPendingChatGeneration(generationId);
    } else {
      const streaming = activeStreamingMessage || appendStreamingMessage();
      if (generationId) {
        void watchChatGeneration(generationId, streaming);
        return;
      }
      streaming.finish(`请求失败：${error.message}`);
    }
  } finally {
    if (!activeGenerationWatcher) {
      activeResponseController = null;
      activeGenerationId = "";
      explicitStopRequested = false;
      setLoading(false);
    }
  }
}

async function stopActiveGeneration() {
  if (!isGenerating) return;
  explicitStopRequested = true;
  const generationId = activeGenerationId;
  if (stopButton) {
    stopButton.disabled = true;
    stopButton.textContent = "正在中断…";
  }
  activeResponseController?.abort();
  if (!generationId) return;
  try {
    const response = await fetch(`/chat/generations/${encodeURIComponent(generationId)}/cancel`, {
      method: "POST",
      keepalive: true,
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
  } catch {
    if (isGenerating && stopButton) {
      stopButton.disabled = false;
      stopButton.textContent = "重试中断";
    }
  }
}

function savePendingChatGeneration(value) {
  try {
    window.localStorage.setItem(CHAT_PENDING_GENERATION_KEY, JSON.stringify(value));
  } catch {
    // A private browser context may deny storage; the live SSE still works.
  }
}

function loadPendingChatGeneration() {
  try {
    const value = JSON.parse(window.localStorage.getItem(CHAT_PENDING_GENERATION_KEY) || "null");
    return value && typeof value === "object" ? value : null;
  } catch {
    return null;
  }
}

function clearPendingChatGeneration(generationId = "") {
  try {
    const current = loadPendingChatGeneration();
    if (!generationId || !current || current.generation_id === generationId) {
      window.localStorage.removeItem(CHAT_PENDING_GENERATION_KEY);
    }
  } catch {
    // Ignore unavailable storage.
  }
}

async function restorePendingChatGeneration() {
  const saved = loadPendingChatGeneration();
  if (!saved?.generation_id || saved.session_id !== sessionId) return;
  if (activeGenerationId === saved.generation_id || activeGenerationWatcher) return;
  activeGenerationId = saved.generation_id;
  explicitStopRequested = false;
  setLoading(true, true);
  const streaming = appendStreamingMessage();
  await watchChatGeneration(saved.generation_id, streaming);
}

async function watchChatGeneration(generationId, streaming) {
  if (!generationId) return false;
  if (activeGenerationWatcher) return activeGenerationWatcher;
  const watcher = (async () => {
    let notFoundCount = 0;
    while (true) {
      let response;
      try {
        response = await fetch(
          `/chat/generations/${encodeURIComponent(generationId)}?session_id=${encodeURIComponent(sessionId)}`,
          { cache: "no-store" },
        );
      } catch {
        await waitForGenerationPoll(500);
        continue;
      }
      if (response.status === 404) {
        notFoundCount += 1;
        if (notFoundCount < 5) {
          await waitForGenerationPoll(300);
          continue;
        }
        streaming.finish("生成任务已失效，请重新发送问题。");
        break;
      }
      if (!response.ok) {
        await waitForGenerationPoll(500);
        continue;
      }
      notFoundCount = 0;
      const snapshot = await response.json();
      streaming.setContent(snapshot.text || "", snapshot.reasoning || "");
      if (snapshot.status === "running") {
        await waitForGenerationPoll(document.visibilityState === "visible" ? 120 : 500);
        continue;
      }
      if (snapshot.status === "completed") {
        const finalText = snapshot.result == null ? snapshot.text : extractReply(snapshot.result);
        streaming.finish(finalText || snapshot.text, snapshot.reasoning || "");
      } else if (snapshot.status === "interrupted") {
        streaming.interrupt();
      } else {
        streaming.finish(`请求失败：${snapshot.error || "生成任务未完成。"}`);
      }
      break;
    }
    clearPendingChatGeneration(generationId);
    conversationHistorySignature = "";
    activeResponseController = null;
    activeGenerationId = "";
    explicitStopRequested = false;
    setLoading(false);
    window.setTimeout(() => void restoreConversationHistory(), 250);
    return true;
  })();
  activeGenerationWatcher = watcher;
  try {
    return await watcher;
  } finally {
    if (activeGenerationWatcher === watcher) activeGenerationWatcher = null;
  }
}

function waitForGenerationPoll(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

// Submit domain onboarding as a background job so the chat request never times out.
async function submitDomainOnboardingJob(content) {
  const reusable = await findReusableDomainJob(content);
  if (reusable) return reusable;

  const clientRequestId = getPendingDomainRequestId(content);
  const response = await fetch("/domain_onboarding/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: sessionId,
      content,
      user_id: "local-web",
      metadata: {},
      client_request_id: clientRequestId,
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
    schema_version: "1.9",
    saved_at: new Date().toISOString(),
    task_id: payload.task_id,
    access_token: payload.access_token,
    request: { query: content, session_id: sessionId, user_id: "local-web", metadata: {} },
    client_request_id: clientRequestId,
    snapshot: { ...payload, progress: 0 },
  });
  clearPendingDomainRequest(clientRequestId);
  return payload;
}

// Reuse the same active task when the user retries or returns to the chat page.
async function findReusableDomainJob(content) {
  const saved = loadDomainWorkspace();
  if (!saved?.task_id || !saved?.access_token) return null;
  if (saved.request?.session_id !== sessionId || saved.request?.query?.trim() !== content.trim()) return null;
  if (DOMAIN_TERMINAL_STATES.has(saved.snapshot?.state)) return null;

  try {
    const response = await fetch(`/domain_onboarding/jobs/${encodeURIComponent(saved.task_id)}`, {
      headers: { Authorization: `Bearer ${saved.access_token}` },
    });
    if (!response.ok) return null;
    const snapshot = await response.json();
    if (DOMAIN_TERMINAL_STATES.has(snapshot?.state)) return null;
    const reusable = { ...snapshot, task_id: saved.task_id, access_token: saved.access_token };
    saveDomainWorkspace({ ...saved, saved_at: new Date().toISOString(), snapshot: reusable });
    return reusable;
  } catch {
    return saved.snapshot
      ? { ...saved.snapshot, task_id: saved.task_id, access_token: saved.access_token }
      : null;
  }
}

// Keep one request id across network retries so the backend can deduplicate POSTs.
function getPendingDomainRequestId(content) {
  try {
    const pending = JSON.parse(localStorage.getItem(DOMAIN_PENDING_REQUEST_KEY) || "null");
    const fresh = Date.now() - Date.parse(pending?.created_at || "") < 15 * 60 * 1000;
    if (fresh && pending?.session_id === sessionId && pending?.query === content.trim()) {
      return pending.client_request_id;
    }
  } catch {
    // Replace malformed browser state below.
  }
  const clientRequestId = crypto.randomUUID();
  localStorage.setItem(DOMAIN_PENDING_REQUEST_KEY, JSON.stringify({
    session_id: sessionId,
    query: content.trim(),
    client_request_id: clientRequestId,
    created_at: new Date().toISOString(),
  }));
  return clientRequestId;
}

function clearPendingDomainRequest(clientRequestId) {
  try {
    const pending = JSON.parse(localStorage.getItem(DOMAIN_PENDING_REQUEST_KEY) || "null");
    if (pending?.client_request_id === clientRequestId) localStorage.removeItem(DOMAIN_PENDING_REQUEST_KEY);
  } catch {
    localStorage.removeItem(DOMAIN_PENDING_REQUEST_KEY);
  }
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
    <span class="domain-card-copy">正在为你检索真实论文、梳理发展脉络并生成标准学习路线。你可以随时进入工作台查看进度。</span>
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
    window.location.href = `/app/domain-onboarding?task_id=${encodeURIComponent(job.task_id)}&conversation_id=${encodeURIComponent(sessionId)}`;
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
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "domain-card-retry";
  retry.textContent = "重试生成";
  retry.hidden = true;
  retry.addEventListener("click", async () => {
    retry.disabled = true;
    retry.textContent = "正在重试";
    await retryDomainOnboardingFromCard(item, query, job.access_token || "");
  });
  item.append(card, cancel, retry);
  messages.appendChild(item);
  messages.scrollTop = messages.scrollHeight;
  if (!conversationContexts.some((context) => contextKey(context) === `domain_onboarding:${job.task_id}`)) {
    conversationContexts.push({ kind: "domain_onboarding", id: job.task_id, title: query });
  }
  const discussion = conversationContexts.find((context) => contextKey(context) === `domain_onboarding:${job.task_id}`);
  if (discussion && !activeDiscussions.some((context) => contextKey(context) === contextKey(discussion))) activeDiscussions.push(discussion);
  restoreDiscussionSelector();
}

async function retryDomainOnboardingFromCard(item, query, accessToken) {
  const taskId = item?.dataset?.taskId || "";
  const retry = item?.querySelector(".domain-card-retry");
  const stateNode = item?.querySelector("[data-domain-state]");
  if (!taskId) return;
  if (window.ensureBaseModelConfigured && !(await window.ensureBaseModelConfigured())) return;
  try {
    const response = await fetch(`/domain_onboarding/jobs/${encodeURIComponent(taskId)}/retry`, {
      method: "POST",
      headers: accessToken
        ? { Accept: "application/json", Authorization: `Bearer ${accessToken}` }
        : { Accept: "application/json" },
    });
    let job;
    try {
      job = await response.json();
    } catch {
      job = null;
    }
    if (!response.ok || !job?.task_id) {
      const detail = typeof job?.detail === "string" ? job.detail : "";
      throw new Error(detail || `重试任务失败（HTTP ${response.status}）`);
    }

    const saved = loadDomainWorkspace() || {};
    const nextAccessToken = job.access_token || "";
    const nextSnapshot = { ...job, progress: 0 };
    saveDomainWorkspace({
      ...saved,
      schema_version: "1.9",
      saved_at: new Date().toISOString(),
      task_id: job.task_id,
      access_token: nextAccessToken,
      request: saved.request || { query, session_id: sessionId, user_id: "local-web", metadata: {} },
      snapshot: nextSnapshot,
    });
    const previousContextKey = `domain_onboarding:${taskId}`;
    conversationContexts = conversationContexts.filter(
      (context) => contextKey(context) !== previousContextKey,
    );
    activeDiscussions = activeDiscussions.filter((context) => contextKey(context) !== previousContextKey);
    item.remove();
    appendDomainOnboardingCard(job, query);
    updateDomainOnboardingCard(job.task_id, nextSnapshot);
    watchDomainOnboardingCard(job.task_id, nextAccessToken);
  } catch (error) {
    if (stateNode) stateNode.textContent = error.message || "重试失败";
    if (retry) {
      retry.disabled = false;
      retry.textContent = "重试失败，再试一次";
    }
  }
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
      updateDomainOnboardingCard(taskId, snapshot, labels);
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

function updateDomainOnboardingCard(taskId, snapshot, labels = null) {
  const item = Array.from(document.querySelectorAll(".domain-card-message"))
    .find((node) => node.dataset.taskId === taskId);
  if (!item || !snapshot) return;
  const stateLabels = labels || {
    queued: "等待执行",
    running: "正在生成",
    cancel_requested: "正在取消",
    completed: "学习路线已生成",
    failed: "生成失败，可进入工作台重试",
    cancelled: "任务已取消",
    interrupted: "任务因服务重启中断，可进入工作台重试",
  };
  const progress = Math.max(0, Math.min(1, Number(snapshot.progress) || 0));
  const stateNode = item.querySelector("[data-domain-state]");
  const progressNode = item.querySelector("[data-domain-progress]");
  const fillNode = item.querySelector(".domain-card-progress-fill");
  const kickerNode = item.querySelector(".paper-card-kicker");
  if (stateNode) stateNode.textContent = stateLabels[snapshot.state] || snapshot.current_stage || "处理中";
  if (progressNode) progressNode.textContent = `${Math.round(progress * 100)}%`;
  if (fillNode) fillNode.style.transform = `scaleX(${progress})`;
  if (kickerNode) kickerNode.textContent =
    snapshot.state === "completed"
      ? "DOMAIN ONBOARDING · 已完成"
      : snapshot.state === "failed"
        ? "DOMAIN ONBOARDING · 生成失败"
        : "DOMAIN ONBOARDING · 生成中";
  const cancel = item.querySelector(".domain-card-cancel");
  if (cancel) cancel.hidden = DOMAIN_TERMINAL_STATES.has(snapshot.state);
  const retry = item.querySelector(".domain-card-retry");
  if (retry) {
    const canRetry = ["failed", "interrupted"].includes(snapshot.state) && Boolean(snapshot.retryable);
    retry.hidden = !canRetry;
    retry.disabled = !canRetry;
    if (canRetry) retry.textContent = "重试生成";
  }
}

// Returning from the workspace must restore the task card instead of losing it.
function restoreDomainOnboardingCard() {
  const saved = loadDomainWorkspace();
  if (!saved?.task_id || !saved?.request?.query) return;
  if (saved.request?.session_id !== sessionId) return;
  const exists = Array.from(document.querySelectorAll(".domain-card-message"))
    .some((node) => node.dataset.taskId === saved.task_id);
  if (exists) return;
  const job = { ...(saved.snapshot || {}), task_id: saved.task_id, access_token: saved.access_token || "" };
  appendDomainOnboardingCard(job, saved.request.query);
  updateDomainOnboardingCard(saved.task_id, saved.snapshot || job);
  if (!DOMAIN_TERMINAL_STATES.has(saved.snapshot?.state)) {
    watchDomainOnboardingCard(saved.task_id, saved.access_token || "");
  }
}

function loadDomainWorkspace() {
  try {
    const value = JSON.parse(localStorage.getItem(DOMAIN_WORKSPACE_KEY) || "null");
    return value?.schema_version === "1.9" ? value : null;
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

  if (window.ensureBaseModelConfigured && !(await window.ensureBaseModelConfigured())) return;

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
    const createdSession = await callPaperReading({
      action: "create_session",
      session_id: "",
      conversation_id: sessionId,
      paper_id: paperId,
      content: "",
      metadata: {},
    });
    const readingSessionId = createdSession.data?.session_id || "";
    if (!readingSessionId) throw new Error("创建论文精读后未返回 reading session ID。");
    localStorage.setItem("paper_reading_paper_id", paperId);
    localStorage.setItem("paper_reading_session_id", readingSessionId);
    localStorage.setItem("paper_reading_conversation_id", sessionId);
    const detail = await callPaperReading({
      action: "get_paper_detail",
      session_id: "",
      paper_id: paperId,
      content: "",
      metadata: {},
    });
    const paperCard = appendPaperCard(detail.data?.paper, {
      paperId,
      sessionId: readingSessionId,
      sourceLabel,
      kgBuild: upload.data?.kg_build || {},
    });
    const discussion = {
      kind: "paper_reading",
      id: readingSessionId,
      paper_id: paperId,
      title: detail.data?.paper?.title || "当前论文",
    };
    conversationContexts.push(discussion);
    activeDiscussions = [discussion];
    restoreDiscussionSelector();
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
  if (context.sessionId) item.dataset.contextKey = `paper_reading:${context.sessionId}`;

  const card = document.createElement("button");
  card.type = "button";
  card.className = "paper-chat-card";
  updatePaperCard(card, paper, context);
  card.addEventListener("click", () => {
    localStorage.setItem("paper_reading_paper_id", context.paperId);
    if (context.sessionId) localStorage.setItem("paper_reading_session_id", context.sessionId);
    localStorage.setItem("paper_reading_conversation_id", sessionId);
    const query = context.sessionId
      ? `?paper_id=${encodeURIComponent(context.paperId)}&session_id=${encodeURIComponent(context.sessionId)}&conversation_id=${encodeURIComponent(sessionId)}`
      : "";
    window.location.href = `/app/paper-reading${query}`;
  });
  item.appendChild(card);
  if (context.placement === "prepend") messages.prepend(item);
  else messages.appendChild(item);
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
function appendMessage(role, content, messageId = "") {
  const item = document.createElement("article");
  item.className = `message ${role}`;
  if (messageId) {
    item.dataset.persistedMessageId = messageId;
    persistedMessageContents.set(messageId, String(content || ""));
  }

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
  let renderFrame = 0;
  const status = document.createElement("p");
  status.className = "thinking-status";
  const plainAnswer = document.createElement("div");
  plainAnswer.className = "streaming-plain-text";
  const render = () => {
    renderFrame = 0;
    const stickToBottom = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 120;
    const inline = splitVisibleThinking(text);
    const visibleReasoning = [reasoning, inline.reasoning].filter(Boolean).join("\n\n");
    const answer = inline.answer;
    if (streaming) {
      status.textContent = visibleReasoning ? `正在思考…（${visibleReasoning.length} 字）` : "正在思考…";
      status.hidden = Boolean(answer);
      plainAnswer.textContent = answer;
      plainAnswer.hidden = !answer;
      if (!status.isConnected || !plainAnswer.isConnected) bubble.replaceChildren(status, plainAnswer);
    } else {
      bubble.replaceChildren();
      if (visibleReasoning) bubble.append(createThinkingDetails(visibleReasoning, false));
      if (answer) bubble.append(renderSafeMarkdown(answer));
    }
    if (stickToBottom) messages.scrollTop = messages.scrollHeight;
  };
  const scheduleRender = () => {
    if (renderFrame) return;
    renderFrame = window.requestAnimationFrame(render);
  };
  const renderNow = () => {
    if (renderFrame) window.cancelAnimationFrame(renderFrame);
    renderFrame = 0;
    render();
  };
  renderNow();
  const api = {
    append(delta) {
      text += String(delta || "");
      scheduleRender();
    },
    appendReasoning(delta) {
      reasoning += String(delta || "");
      scheduleRender();
    },
    setContent(nextText, nextReasoning = "") {
      text = String(nextText || "");
      reasoning = String(nextReasoning || "");
      scheduleRender();
    },
    finish(finalText, finalReasoning = "") {
      text = String(finalText || text || "后端没有返回内容。");
      reasoning = String(finalReasoning || reasoning || splitVisibleThinking(text).reasoning || "");
      streaming = false;
      conversationScrollTarget = "latest-assistant";
      bubble.classList.remove("streaming-bubble");
      renderNow();
      requestAnimationFrame(() => scrollMessageToTop(item));
      if (activeStreamingMessage === api) activeStreamingMessage = null;
    },
    interrupt() {
      text = text ? `${text}\n\n_回答已中断。_` : "回答已中断。";
      streaming = false;
      bubble.classList.remove("streaming-bubble");
      renderNow();
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
  const canInterrupt = isLoading && interruptible;
  sendButton.disabled = isLoading;
  sendButton.classList.remove("is-stop");
  sendButton.textContent = isLoading
    ? (currentMode === "paper_reading" ? "解析中" : "生成中")
    : (currentMode === "paper_reading" ? "解析论文" : "发送");
  if (stopButton) {
    stopButton.hidden = !canInterrupt;
    stopButton.disabled = false;
    stopButton.textContent = "中断";
  }
}

// Get persistent local session id.
function getSessionId() {
  const key = "scholarsprout_session_id";
  const query = new URLSearchParams(window.location.search);
  const requested = query.get("conversation_id");
  if (requested) {
    window.localStorage.setItem(key, requested);
    return requested;
  }
  if (query.get("new") === "1") {
    const created = `web-${crypto.randomUUID()}`;
    window.localStorage.setItem(key, created);
    query.delete("new");
    query.set("conversation_id", created);
    window.history.replaceState(null, "", `${window.location.pathname}?${query}`);
    return created;
  }
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
