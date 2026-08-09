const VIEWS = {
  conversations: { title: "会话", kicker: "CONVERSATIONS", description: "回到最近的科研讨论，保留每一次问题演进。", endpoint: "/api/research/conversations", empty: "开始一次新会话后，它会自动出现在这里。" },
  "domain-onboardings": { title: "领域入门", kicker: "DOMAIN ONBOARDING", description: "保存完整的领域地图、学习路径和推荐论文。", endpoint: "/api/research/domain-onboardings", empty: "选择领域入门模式，完成的研究地图会集中保存在这里。" },
  "paper-readings": { title: "论文精读", kicker: "PAPER READING", description: "继续阅读、查看进度，并回到已有的 Fork 和分析结果。", endpoint: "/api/research/paper-readings", empty: "导入一篇 PDF 并开始阅读，精读记录会显示在这里。" },
  papers: { title: "论文管理", kicker: "PAPER LIBRARY", description: "收藏、标记阅读状态，并管理论文上的高亮与注释。", endpoint: "/api/research/papers", empty: "在全部论文中选择需要长期管理的论文。" },
};

const state = { view: new URLSearchParams(location.search).get("view") || "conversations", search: "", allPapers: false, items: [] };
if (!(state.view in VIEWS)) state.view = "conversations";
const items = document.querySelector("#items");
let searchTimer = null;

boot();

function boot() {
  document.querySelector("#library-nav").addEventListener("click", (event) => {
    const button = event.target.closest("[data-view]");
    if (button) setView(button.dataset.view);
  });
  document.querySelector("#search-input").addEventListener("input", (event) => {
    clearTimeout(searchTimer);
    state.search = event.target.value.trim();
    searchTimer = setTimeout(loadItems, 220);
  });
  document.querySelector("#show-all-papers").addEventListener("change", (event) => {
    state.allPapers = event.target.checked;
    loadItems();
  });
  items.addEventListener("click", handleItemAction);
  loadCounts();
  setView(state.view, false);
}

async function loadCounts() {
  try {
    const counts = await fetchJson("/api/research/summary");
    Object.entries(counts).forEach(([key, value]) => {
      const target = document.querySelector(`[data-count="${key}"]`);
      if (target) target.textContent = value;
    });
  } catch (error) { toast(error.message, true); }
}

function setView(view, updateUrl = true) {
  state.view = view in VIEWS ? view : "conversations";
  const config = VIEWS[state.view];
  document.querySelectorAll("[data-view]").forEach((button) => button.classList.toggle("is-active", button.dataset.view === state.view));
  document.querySelector("#view-title").textContent = config.title;
  document.querySelector("#view-kicker").textContent = config.kicker;
  document.querySelector("#view-description").textContent = config.description;
  document.querySelector("#paper-scope").hidden = state.view !== "papers";
  document.querySelector("#search-input").placeholder = state.view === "papers" ? "搜索标题、作者或摘要" : "搜索标题或内容";
  if (updateUrl) history.replaceState(null, "", `/library?view=${encodeURIComponent(state.view)}`);
  loadItems();
}

async function loadItems() {
  const config = VIEWS[state.view];
  showSkeletons();
  try {
    const url = new URL(config.endpoint, location.origin);
    if (state.search) url.searchParams.set("search", state.search);
    if (state.view === "papers") url.searchParams.set("library_only", String(!state.allPapers));
    state.items = await fetchJson(url);
    renderItems();
  } catch (error) {
    items.replaceChildren();
    showEmpty("读取失败", error.message);
  }
}

function renderItems() {
  items.replaceChildren();
  document.querySelector("#result-summary").textContent = `${state.items.length} 条记录${state.search ? ` · 搜索“${state.search}”` : ""}`;
  if (!state.items.length) {
    showEmpty("这里还没有内容", VIEWS[state.view].empty);
    return;
  }
  document.querySelector("#empty-state").hidden = true;
  state.items.forEach((item) => items.append(renderCard(item)));
}

function renderCard(item) {
  const card = element("article", "item-card");
  card.dataset.id = item.conversation_id || item.artifact_id || item.reading_session_id || item.paper_id;
  const head = element("div", "item-head");
  head.append(element("h2", "item-title", item.paper_title || item.title || item.query || "未命名记录"), element("time", "item-time", formatDate(item.updated_at)));
  card.append(head);
  const preview = previewFor(item);
  if (preview) card.append(element("p", "item-preview", preview));
  const meta = element("div", "item-meta");
  meta.append(...metaFor(item).map(([text, blue]) => element("span", `chip${blue ? " is-blue" : ""}`, text)));
  card.append(meta);
  const actions = element("div", "item-actions");
  if (state.view === "conversations") {
    actions.append(link(`/app?conversation_id=${encodeURIComponent(item.conversation_id)}`, "继续会话", true));
  } else if (state.view === "domain-onboardings") {
    actions.append(element("span", "chip", "结果已完整保存"));
  } else if (state.view === "paper-readings") {
    actions.append(link(paperWorkspace(item.paper_id, item.reading_session_id), "继续精读", true));
  } else {
    actions.append(link(paperWorkspace(item.paper_id, ""), item.reading_count ? "打开论文" : "开始精读", true));
    actions.append(renderPaperControls(item));
  }
  card.append(actions);
  return card;
}

function renderPaperControls(item) {
  const wrap = element("div", "paper-controls");
  const select = document.createElement("select");
  select.dataset.paperStatus = item.paper_id;
  [["unread","未读"],["reading","阅读中"],["read","已读"],["archived","归档"]].forEach(([value,label]) => select.add(new Option(label, value, false, (item.reading_status || "unread") === value)));
  const note = document.createElement("input");
  note.dataset.paperNote = item.paper_id;
  note.placeholder = "论文备注（可选）";
  note.value = item.library_note || "";
  const save = element("button", "", item.in_library ? "保存管理信息" : "加入论文库");
  save.dataset.action = "save-paper";
  save.dataset.paperId = item.paper_id;
  wrap.append(select, note, save);
  if (item.in_library) {
    const remove = element("button", "remove", "移出论文库");
    remove.dataset.action = "remove-paper";
    remove.dataset.paperId = item.paper_id;
    wrap.append(remove);
  }
  return wrap;
}

async function handleItemAction(event) {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const paperId = button.dataset.paperId;
  button.disabled = true;
  try {
    if (button.dataset.action === "save-paper") {
      const readingStatus = document.querySelector(`[data-paper-status="${cssEscape(paperId)}"]`).value;
      const note = document.querySelector(`[data-paper-note="${cssEscape(paperId)}"]`).value.trim();
      await fetchJson(`/api/research/papers/${encodeURIComponent(paperId)}/library`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reading_status: readingStatus, note }) });
      toast("论文管理信息已保存。");
    } else {
      await fetchJson(`/api/research/papers/${encodeURIComponent(paperId)}/library`, { method: "DELETE" });
      toast("论文已移出论文库，精读记录和标注仍然保留。");
    }
    await Promise.all([loadCounts(), loadItems()]);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
}

function previewFor(item) {
  if (state.view === "paper-readings") return [item.authors?.join("、"), item.current_section_id ? `当前：${item.current_section_id}` : ""].filter(Boolean).join(" · ");
  if (state.view === "papers") return item.abstract || item.authors?.join("、") || "暂无摘要";
  return item.preview || item.query || "";
}

function metaFor(item) {
  if (state.view === "conversations") return [[`${item.message_count} 条消息`], ...(item.modes || []).map((mode) => [modeLabel(mode), true]), item.parent_conversation_id ? ["Fork 会话", true] : null].filter(Boolean);
  if (state.view === "domain-onboardings") return [[stateLabel(item.state)], [`阶段：${item.current_stage}`, true], [`${item.recommendation_count} 篇推荐论文`], item.quality_score != null ? [`质量 ${Math.round(item.quality_score * 100)}%`, true] : null].filter(Boolean);
  if (state.view === "paper-readings") return [[readingState(item.state)], [`${Math.round(Number(item.progress?.percentage || 0))}%`], [`${item.block_count} 个分析块`, true], [`${item.annotation_count} 条标注`, true]];
  return [[item.in_library ? statusLabel(item.reading_status) : "未加入论文库"], item.publication_year ? [String(item.publication_year), true] : null, [`${item.reading_count} 次精读`], [`${item.annotation_count} 条标注`, true]].filter(Boolean);
}

function showSkeletons() { document.querySelector("#empty-state").hidden = true; items.replaceChildren(...[1,2,3].map(() => element("div", "skeleton"))); }
function showEmpty(title, copy) { const empty = document.querySelector("#empty-state"); empty.hidden = false; document.querySelector("#empty-title").textContent = title; document.querySelector("#empty-copy").textContent = copy; }
function paperWorkspace(paperId, sessionId) { const params = new URLSearchParams({ paper_id: paperId }); if (sessionId) params.set("session_id", sessionId); return `/app/paper-reading?${params}`; }
function element(tag, className = "", text = "") { const node = document.createElement(tag); if (className) node.className = className; if (text) node.textContent = text; return node; }
function link(href, text, accent = false) { const item = element("a", accent ? "accent" : "", text); item.href = href; return item; }
function formatDate(value) { const date = new Date(value); return Number.isNaN(date.valueOf()) ? "" : new Intl.DateTimeFormat("zh-CN", { month:"short",day:"numeric",hour:"2-digit",minute:"2-digit" }).format(date); }
function modeLabel(mode) { return ({ chat:"日常聊天",domain_onboarding:"领域入门",paper_reading:"论文精读" })[mode] || mode; }
function stateLabel(value) { return ({ queued:"排队中",running:"进行中",paused:"已暂停",completed:"已完成",failed:"失败",cancelled:"已取消" })[value] || value; }
function readingState(value) { return ({ active:"阅读中",paused:"已暂停",completed:"已完成" })[value] || value; }
function statusLabel(value) { return ({ unread:"未读",reading:"阅读中",read:"已读",archived:"已归档" })[value] || value; }
function cssEscape(value) { return window.CSS?.escape ? CSS.escape(value) : String(value).replace(/["\\]/g,"\\$&"); }
async function fetchJson(input, options) { const response = await fetch(input, options); const data = await response.json().catch(() => ({})); if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `请求失败（HTTP ${response.status}）`); return data; }
function toast(text, error = false) { const node = element("div", `toast${error ? " is-error" : ""}`, text); document.querySelector("#toast-region").append(node); setTimeout(() => node.remove(), 3800); }
