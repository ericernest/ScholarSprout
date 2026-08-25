const VIEWS = {
  conversations: { title: "会话", kicker: "CONVERSATIONS", description: "回到最近的科研讨论，保留每一次问题演进。", endpoint: "/api/research/conversations", empty: "开始一次新会话后，它会自动出现在这里。" },
  "domain-onboardings": { title: "领域入门", kicker: "DOMAIN ONBOARDING", description: "保存完整的领域地图、学习路径和推荐论文。", endpoint: "/api/research/domain-onboardings", empty: "选择领域入门模式，完成的研究地图会集中保存在这里。" },
  "paper-readings": { title: "论文精读", kicker: "PAPER READING", description: "查看每篇论文的阅读进度，并继续章节分析、Fork 探索和标注。", endpoint: "/api/research/paper-readings", empty: "导入一篇 PDF 并开始阅读，精读记录会显示在这里。" },
  papers: { title: "论文管理", kicker: "PAPER LIBRARY", description: "收藏、标记阅读状态，并管理论文上的高亮与注释。", endpoint: "/api/research/papers", empty: "在全部论文中选择需要长期管理的论文。" },
};

const state = {
  view: new URLSearchParams(location.search).get("view") || "conversations",
  searchByView: { conversations: "", "domain-onboardings": "", "paper-readings": "", papers: "" },
  allPapers: false, readingFilter: "all", importFile: null, items: [], folders: [], folderId: "",
  expandedFolders: new Set(), importFolderId: "", folderForm: null, folderPicker: null,
  deleteFolderId: "", counts: {},
};
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
    state.searchByView[state.view] = event.target.value.trim();
    searchTimer = setTimeout(loadItems, 220);
  });
  document.querySelector("#show-all-papers").addEventListener("change", (event) => {
    state.allPapers = event.target.checked;
    loadItems();
  });
  bindReadingFilter();
  bindPaperFilePicker();
  document.querySelector("#create-folder-button").addEventListener("click", () => openFolderForm({ parentId: null }));
  document.querySelector("#folder-root-create").addEventListener("click", () => openFolderForm({ parentId: null }));
  document.querySelector("#folder-tree").addEventListener("click", handleFolderTreeAction);
  document.querySelector("#import-folder-button").addEventListener("click", () => openFolderPicker({ type: "import", folderId: state.importFolderId }));
  document.querySelector("#paper-import-button").addEventListener("click", importPaper);
  document.querySelector("#folder-form").addEventListener("submit", saveFolderForm);
  document.querySelector("#folder-picker-form").addEventListener("submit", confirmFolderPicker);
  document.querySelector("#folder-delete-form").addEventListener("submit", deleteSelectedFolder);
  document.querySelectorAll("[data-close-dialog]").forEach((button) => button.addEventListener("click", () => document.querySelector(`#${button.dataset.closeDialog}`).close()));
  items.addEventListener("click", handleItemAction);
  items.addEventListener("keydown", (event) => {
    if ((event.key === "Enter" || event.key === " ") && event.target.matches(".item-card")) {
      event.preventDefault();
      openCard(event.target);
    }
  });
  loadCounts();
  setView(state.view, false);
}

async function loadCounts() {
  try {
    const counts = await fetchJson("/api/research/summary");
    state.counts = counts;
    Object.entries(counts).forEach(([key, value]) => {
      const target = document.querySelector(`[data-count="${key}"]`);
      if (target) target.textContent = value;
    });
    if (state.view === "papers") renderMainFolderTree();
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
  document.querySelector("#paper-import").hidden = state.view !== "papers";
  document.querySelector("#folder-pane").hidden = state.view !== "papers";
  document.querySelector("#records-layout").classList.toggle("has-folder-pane", state.view === "papers");
  document.querySelector("#search-input").placeholder = state.view === "papers" ? "搜索标题、作者或摘要" : "搜索标题或内容";
  document.querySelector("#search-input").value = state.searchByView[state.view];
  if (updateUrl) history.replaceState(null, "", `/library?view=${encodeURIComponent(state.view)}`);
  if (state.view === "papers") {
    loadFolders().then(loadItems).catch((error) => toast(error.message, true));
  } else {
    loadItems();
  }
}

async function loadFolders() {
  state.folders = await fetchJson("/api/research/paper-folders");
  state.folders.filter((folder) => !folder.parent_folder_id).forEach((folder) => state.expandedFolders.add(folder.folder_id));
  renderMainFolderTree();
  syncFolderLabels();
}

async function loadItems() {
  const config = VIEWS[state.view];
  const search = state.searchByView[state.view];
  showSkeletons();
  try {
    const url = new URL(config.endpoint, location.origin);
    if (search) url.searchParams.set("search", search);
    if (state.view === "papers") url.searchParams.set("library_only", String(!state.allPapers));
    if (state.view === "papers" && state.folderId) url.searchParams.set("folder_id", state.folderId);
    if (state.view === "papers" && state.readingFilter !== "all") url.searchParams.set("reading_scope", state.readingFilter);
    state.items = await fetchJson(url);
    renderItems();
  } catch (error) {
    items.replaceChildren();
    showEmpty("读取失败", error.message);
  }
}

function renderItems() {
  items.replaceChildren();
  const search = state.searchByView[state.view];
  const readingLabel = state.view === "papers" ? ({ reviewed: " · 已精读", unreviewed: " · 未精读" })[state.readingFilter] || "" : "";
  document.querySelector("#result-summary").textContent = `${state.items.length} 条记录${readingLabel}${search ? ` · 搜索“${search}”` : ""}`;
  if (!state.items.length) {
    showEmpty("这里还没有内容", VIEWS[state.view].empty);
    return;
  }
  document.querySelector("#empty-state").hidden = true;
  state.items.forEach((item) => items.append(renderCard(item)));
}

function renderCard(item) {
  const card = element("article", "item-card");
  const paperView = state.view === "paper-readings" || state.view === "papers";
  if (paperView) card.classList.add("paper-record-card");
  card.tabIndex = 0;
  card.setAttribute("role", "link");
  card.dataset.id = item.conversation_id || item.artifact_id || item.reading_session_id || item.paper_id;
  if (state.view === "conversations") card.dataset.href = conversationUrl(item.conversation_id);
  if (state.view === "paper-readings") card.dataset.href = paperWorkspace(item.paper_id, item.reading_session_id);
  if (state.view === "papers") card.dataset.href = item.has_document || item.reading_count ? paperWorkspace(item.paper_id, "") : (item.source_url || "");
  if (state.view === "domain-onboardings") card.dataset.href = domainWorkspace(item.artifact_id);
  const head = element("div", "item-head");
  const heading = element("div", paperView ? "paper-heading" : "");
  if (paperView) heading.append(element("span", "paper-kind", state.view === "paper-readings" ? "PAPER READING" : "PAPER LIBRARY"));
  heading.append(element("h2", "item-title", item.paper_title || item.title || item.query || "未命名记录"));
  head.append(heading, element("time", "item-time", formatDate(item.updated_at)));
  card.append(head);
  const preview = previewFor(item);
  if (preview) card.append(element("p", "item-preview", preview));
  const meta = element("div", "item-meta");
  meta.append(...metaFor(item).map(([text, blue]) => element("span", `chip${blue ? " is-blue" : ""}`, text)));
  card.append(meta);
  if (state.view === "paper-readings") {
    const percentage = Math.max(0, Math.min(100, Math.round(Number(item.progress?.percentage || 0))));
    const progress = element("div", "paper-progress");
    const label = element("div", "paper-progress-label");
    label.append(element("span", "", item.current_section_title ? `当前章节 · ${item.current_section_title}` : "尚未选择章节"), element("strong", "", `${percentage}%`));
    const track = element("span", "paper-progress-track");
    const fill = element("i");
    fill.style.width = `${percentage}%`;
    track.append(fill);
    progress.append(label, track);
    card.append(progress);
  }
  const actions = element("div", "item-actions");
  if (state.view === "conversations") {
    actions.append(link(conversationUrl(item.conversation_id), "继续会话", true));
  } else if (state.view === "domain-onboardings") {
    actions.append(link(domainWorkspace(item.artifact_id), "进入领域入门  ↗", true));
  } else if (state.view === "paper-readings") {
    actions.append(link(paperWorkspace(item.paper_id, item.reading_session_id), "继续论文精读  ↗", true));
    actions.append(renderPaperNoteButton(item));
  } else {
    if (item.reading_count) {
      actions.append(link(paperWorkspace(item.paper_id, item.latest_reading_session_id), "继续论文精读  ↗", true));
    } else if (item.has_document) {
      const start = element("button", "accent", "开始论文精读  ↗");
      start.dataset.action = "start-paper-reading";
      start.dataset.paperId = item.paper_id;
      actions.append(start);
    } else {
      const start = element("button", "accent", paperPdfUrl(item) ? "导入 PDF 并精读" : "上传 PDF 并精读");
      start.dataset.action = "attach-paper";
      start.dataset.paperId = item.paper_id;
      start.dataset.pdfUrl = paperPdfUrl(item);
      actions.append(start);
    }
    actions.append(renderPaperNoteButton(item), renderPaperControls(item));
  }
  card.append(actions);
  return card;
}

function renderPaperNoteButton(item) {
  const noteButton = element("button", "paper-note-view-button", "查看笔记");
  noteButton.dataset.action = "view-paper-note";
  noteButton.dataset.paperId = item.paper_id;
  noteButton.dataset.paperTitle = item.paper_title || item.title || "论文";
  noteButton.dataset.canEdit = String(state.view === "paper-readings" || Boolean(item.has_document || item.reading_count));
  return noteButton;
}

function renderPaperControls(item) {
  const editor = document.createElement("details");
  editor.className = "paper-management-editor";
  const summary = document.createElement("summary");
  summary.textContent = "管理阅读状态、文件夹与备注";
  const wrap = element("div", "paper-controls");
  const select = document.createElement("select");
  select.dataset.paperStatus = item.paper_id;
  [["unread","未读"],["reading","阅读中"],["read","完成"],["archived","归档"]].forEach(([value,label]) => select.add(new Option(label, value, false, (item.reading_status || "unread") === value)));
  const statusField = element("label", "management-field");
  statusField.append(element("span", "", "阅读状态"), select);
  const note = document.createElement("input");
  note.dataset.paperNote = item.paper_id;
  note.placeholder = "论文备注（可选）";
  note.value = item.library_note || "";
  const noteField = element("label", "management-field");
  noteField.append(element("span", "", "备注"), note);
  const folder = element("button", "folder-dropzone");
  folder.type = "button";
  folder.dataset.action = "choose-paper-folder";
  folder.dataset.paperId = item.paper_id;
  folder.dataset.currentFolderId = item.folder_id || "";
  const folderCopy = element("span", "folder-drop-copy");
  folderCopy.append(element("strong", "", "选择文件夹"), element("small", "folder-choice-label", item.folder_path || item.folder_name || "未放入文件夹"));
  folder.append(
    element("span", "folder-drop-icon", "↥"),
    folderCopy,
  );
  const save = element("button", "", item.in_library ? "保存管理信息" : "加入论文库");
  save.dataset.action = "save-paper";
  save.dataset.paperId = item.paper_id;
  wrap.append(statusField, folder, noteField, save);
  if (item.in_library) {
    const remove = element("button", "remove", "移出论文库");
    remove.dataset.action = "remove-paper";
    remove.dataset.paperId = item.paper_id;
    wrap.append(remove);
  }
  editor.append(summary, element("p", "management-hint", "导入后默认未读；开始精读会自动变为阅读中；完成和归档可在这里手动切换。"), wrap);
  return editor;
}

async function handleItemAction(event) {
  const button = event.target.closest("[data-action]");
  if (!button) {
    if (!event.target.closest("a,button,input,select,textarea,summary,details")) openCard(event.target.closest(".item-card"));
    return;
  }
  event.stopPropagation();
  if (button.dataset.action === "attach-paper") {
    await attachManagedPaper(button.dataset.paperId, button.dataset.pdfUrl, button);
    return;
  }
  if (button.dataset.action === "start-paper-reading") {
    await startPaperReading(button.dataset.paperId, button);
    return;
  }
  if (button.dataset.action === "choose-paper-folder") {
    openFolderPicker({
      type: "paper",
      paperId: button.dataset.paperId,
      folderId: button.dataset.currentFolderId,
    });
    return;
  }
  if (button.dataset.action === "view-paper-note") {
    await openLibraryPaperNote(
      button.dataset.paperId,
      button.dataset.paperTitle,
      button.dataset.canEdit === "true",
    );
    return;
  }
  const paperId = button.dataset.paperId;
  button.disabled = true;
  try {
    if (button.dataset.action === "save-paper") {
      const readingStatus = document.querySelector(`[data-paper-status="${cssEscape(paperId)}"]`).value;
      const note = document.querySelector(`[data-paper-note="${cssEscape(paperId)}"]`).value.trim();
      const folderId = document.querySelector(`[data-action="choose-paper-folder"][data-paper-id="${cssEscape(paperId)}"]`).dataset.currentFolderId || null;
      await fetchJson(`/api/research/papers/${encodeURIComponent(paperId)}/library`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reading_status: readingStatus, note, folder_id: folderId }) });
      toast("论文管理信息已保存。");
    } else {
      await fetchJson(`/api/research/papers/${encodeURIComponent(paperId)}/library`, { method: "DELETE" });
      toast("论文已移出论文库，精读记录和标注仍然保留。");
    }
    await Promise.all([loadCounts(), loadFolders(), loadItems()]);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
}

function openCard(card) {
  if (!card) return;
  if (card.dataset.href) window.location.href = card.dataset.href;
}

async function openLibraryPaperNote(paperId, paperTitle, canEdit) {
  const dialog = document.querySelector("#paper-note-dialog");
  const content = document.querySelector("#library-paper-note-content");
  const edit = document.querySelector("#library-paper-note-edit");
  document.querySelector("#library-paper-note-title").textContent = `${paperTitle || "论文"} · 笔记`;
  content.replaceChildren(element("p", "paper-note-empty", "正在读取笔记…"));
  edit.href = paperWorkspace(paperId, "");
  edit.hidden = !canEdit;
  dialog.showModal();
  try {
    const note = await fetchJson(`/api/research/papers/${encodeURIComponent(paperId)}/note`);
    content.replaceChildren();
    if (note.content_markdown) {
      content.append(window.renderPaperMarkdown(note.content_markdown));
    } else {
      content.append(element("p", "paper-note-empty", "这篇论文还没有笔记。"));
    }
  } catch (error) {
    content.replaceChildren(element("p", "paper-note-empty is-error", error.message || "笔记读取失败。"));
  }
}

function foldersByParent(parentId) {
  return state.folders
    .filter((folder) => (folder.parent_folder_id || "") === (parentId || ""))
    .sort((left, right) => left.name.localeCompare(right.name, "zh-CN"));
}

function folderById(folderId) {
  return state.folders.find((folder) => folder.folder_id === folderId) || null;
}

function folderPath(folderId) {
  if (!folderId) return "未放入文件夹";
  return folderById(folderId)?.path || folderById(folderId)?.name || "未放入文件夹";
}

function folderSubtreeCount(folderId) {
  const folder = folderById(folderId);
  return Number(folder?.paper_count || 0) + foldersByParent(folderId).reduce((sum, child) => sum + folderSubtreeCount(child.folder_id), 0);
}

function renderMainFolderTree() {
  const tree = document.querySelector("#folder-tree");
  tree.replaceChildren();
  tree.append(renderLibraryRootRow("", "全部论文", Number(state.counts.library_papers || 0), "▱"));
  tree.append(renderLibraryRootRow("__unfiled__", "未放入文件夹", unfiledCount(), "◇"));
  tree.append(element("div", "folder-section-label", "文件夹"));
  foldersByParent(null).forEach((folder) => tree.append(renderFolderBranch(folder, 0, { manage: true })));
  const active = state.folderId ? folderPath(state.folderId) : "全部论文";
  document.querySelector("#folder-breadcrumb").textContent = active;
}

function renderLibraryRootRow(folderId, label, count, icon) {
  const row = element("button", `folder-root-row${state.folderId === folderId ? " is-active" : ""}`);
  row.type = "button";
  row.dataset.folderOpen = folderId;
  row.append(element("span", "folder-row-icon", icon), element("span", "folder-row-name", label), element("b", "folder-row-count", String(count)));
  return row;
}

function renderFolderBranch(folder, depth, options = {}) {
  const branch = element("div", "folder-branch");
  const children = foldersByParent(folder.folder_id);
  const expanded = state.expandedFolders.has(folder.folder_id);
  const row = element("div", `folder-row${state.folderId === folder.folder_id ? " is-active" : ""}`);
  row.style.setProperty("--folder-depth", String(depth));
  const toggle = element("button", `folder-toggle${children.length ? "" : " is-empty"}`, children.length ? (expanded ? "⌄" : "›") : "");
  toggle.type = "button";
  toggle.dataset.folderToggle = folder.folder_id;
  const open = element("button", "folder-open");
  open.type = "button";
  open.dataset.folderOpen = folder.folder_id;
  open.append(element("span", "folder-row-icon", expanded ? "◆" : "◇"), element("span", "folder-row-name", folder.name), element("b", "folder-row-count", String(folderSubtreeCount(folder.folder_id))));
  row.append(toggle, open);
  if (options.manage) {
    const actions = element("div", "folder-row-actions");
    [["new", "＋", "新建子文件夹"], ["edit", "✎", "重命名或移动"], ["delete", "×", "删除文件夹"]].forEach(([action, label, title]) => {
      const button = element("button", action === "delete" ? "is-danger" : "", label);
      button.type = "button";
      button.title = title;
      button.dataset.folderAction = action;
      button.dataset.folderId = folder.folder_id;
      actions.append(button);
    });
    row.append(actions);
  }
  branch.append(row);
  if (children.length && expanded) {
    const childWrap = element("div", "folder-children");
    children.forEach((child) => childWrap.append(renderFolderBranch(child, depth + 1, options)));
    branch.append(childWrap);
  }
  return branch;
}

function handleFolderTreeAction(event) {
  const toggle = event.target.closest("[data-folder-toggle]");
  if (toggle) {
    const folderId = toggle.dataset.folderToggle;
    if (state.expandedFolders.has(folderId)) state.expandedFolders.delete(folderId);
    else state.expandedFolders.add(folderId);
    renderMainFolderTree();
    return;
  }
  const action = event.target.closest("[data-folder-action]");
  if (action) {
    const folder = folderById(action.dataset.folderId);
    if (!folder) return;
    if (action.dataset.folderAction === "new") openFolderForm({ parentId: folder.folder_id });
    if (action.dataset.folderAction === "edit") openFolderForm({ folderId: folder.folder_id });
    if (action.dataset.folderAction === "delete") openDeleteFolder(folder.folder_id);
    return;
  }
  const open = event.target.closest("[data-folder-open]");
  if (open) {
    state.folderId = open.dataset.folderOpen;
    renderMainFolderTree();
    loadItems();
  }
}

function unfiledCount() {
  return Number(state.counts.unfiled_papers || 0);
}

function renderDialogFolderTree(container, selectedId, excludedId = "", includeUnfiled = true) {
  container.replaceChildren();
  const root = element("button", `dialog-folder-row${selectedId === "" ? " is-selected" : ""}`);
  root.type = "button";
  root.dataset.selectFolder = "";
  root.append(element("span", "folder-row-icon", includeUnfiled ? "◇" : "▱"), element("span", "folder-row-name", includeUnfiled ? "不放入文件夹" : "资料库根目录"));
  container.append(root);
  const excluded = new Set(excludedId ? [excludedId, ...folderDescendantIds(excludedId)] : []);
  function append(parentId, depth) {
    foldersByParent(parentId).forEach((folder) => {
      if (excluded.has(folder.folder_id)) return;
      const row = element("button", `dialog-folder-row${selectedId === folder.folder_id ? " is-selected" : ""}`);
      row.type = "button";
      row.style.setProperty("--folder-depth", String(depth));
      row.dataset.selectFolder = folder.folder_id;
      row.append(element("span", "folder-row-icon", "◇"), element("span", "folder-row-name", folder.name), element("small", "", String(folderSubtreeCount(folder.folder_id))));
      container.append(row);
      append(folder.folder_id, depth + 1);
    });
  }
  append(null, 0);
  container.onclick = (event) => {
    const row = event.target.closest("[data-select-folder]");
    if (!row) return;
    const value = row.dataset.selectFolder;
    if (container.id === "folder-parent-tree") state.folderForm.parentId = value || null;
    else state.folderPicker.folderId = value;
    renderDialogFolderTree(container, value, excludedId, includeUnfiled);
  };
}

function folderDescendantIds(folderId) {
  const result = [];
  foldersByParent(folderId).forEach((child) => {
    result.push(child.folder_id, ...folderDescendantIds(child.folder_id));
  });
  return result;
}

function openFolderForm({ folderId = "", parentId = null } = {}) {
  const folder = folderById(folderId);
  state.folderForm = { folderId, parentId: folder ? folder.parent_folder_id : parentId };
  document.querySelector("#folder-form-title").textContent = folder ? "编辑文件夹" : "新建文件夹";
  document.querySelector("#folder-form-submit").textContent = folder ? "保存修改" : "创建文件夹";
  document.querySelector("#folder-name-input").value = folder?.name || "";
  document.querySelector("#folder-form-error").hidden = true;
  renderDialogFolderTree(document.querySelector("#folder-parent-tree"), state.folderForm.parentId || "", folderId, false);
  const dialog = document.querySelector("#folder-form-dialog");
  dialog.showModal();
  requestAnimationFrame(() => document.querySelector("#folder-name-input").focus());
}

async function saveFolderForm(event) {
  event.preventDefault();
  const error = document.querySelector("#folder-form-error");
  const name = document.querySelector("#folder-name-input").value.trim();
  if (!name) { error.textContent = "请输入文件夹名称。"; error.hidden = false; return; }
  const submit = document.querySelector("#folder-form-submit");
  submit.disabled = true;
  try {
    const editing = Boolean(state.folderForm.folderId);
    const endpoint = editing ? `/api/research/paper-folders/${encodeURIComponent(state.folderForm.folderId)}` : "/api/research/paper-folders";
    await fetchJson(endpoint, { method: editing ? "PATCH" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, parent_folder_id: state.folderForm.parentId }) });
    document.querySelector("#folder-form-dialog").close();
    await loadFolders();
    await loadItems();
    toast(editing ? "文件夹已更新。" : "文件夹已创建。");
  } catch (exception) {
    error.textContent = exception.message;
    error.hidden = false;
  } finally { submit.disabled = false; }
}

function openFolderPicker({ type, paperId = "", folderId = "" }) {
  state.folderPicker = { type, paperId, folderId: folderId || "" };
  document.querySelector("#folder-picker-title").textContent = type === "import" ? "选择导入位置" : "选择论文文件夹";
  document.querySelector("#folder-picker-copy").textContent = type === "import" ? "新导入的论文会直接保存到所选文件夹。" : "选择后保存管理信息即可完成移动。";
  renderDialogFolderTree(document.querySelector("#folder-picker-tree"), state.folderPicker.folderId, "", true);
  document.querySelector("#folder-picker-dialog").showModal();
}

function confirmFolderPicker(event) {
  event.preventDefault();
  const folderId = state.folderPicker.folderId || "";
  if (state.folderPicker.type === "import") {
    state.importFolderId = folderId;
    document.querySelector("#import-folder-label").textContent = folderPath(folderId);
  } else {
    const button = document.querySelector(`[data-action="choose-paper-folder"][data-paper-id="${cssEscape(state.folderPicker.paperId)}"]`);
    if (button) {
      button.dataset.currentFolderId = folderId;
      button.querySelector(".folder-choice-label").textContent = folderPath(folderId);
    }
  }
  document.querySelector("#folder-picker-dialog").close();
}

function openDeleteFolder(folderId) {
  state.deleteFolderId = folderId;
  document.querySelector("#folder-delete-copy").textContent = `删除“${folderPath(folderId)}”？仅空文件夹可以删除。`;
  document.querySelector("#folder-delete-dialog").showModal();
}

async function deleteSelectedFolder(event) {
  event.preventDefault();
  try {
    await fetchJson(`/api/research/paper-folders/${encodeURIComponent(state.deleteFolderId)}`, { method: "DELETE" });
    if (state.folderId === state.deleteFolderId) state.folderId = "";
    document.querySelector("#folder-delete-dialog").close();
    await loadFolders();
    await loadItems();
    toast("文件夹已删除。");
  } catch (error) { toast(error.message, true); }
}

function syncFolderLabels() {
  document.querySelector("#import-folder-label").textContent = folderPath(state.importFolderId);
  document.querySelectorAll('[data-action="choose-paper-folder"]').forEach((button) => {
    button.querySelector(".folder-choice-label").textContent = folderPath(button.dataset.currentFolderId);
  });
}

async function importPaper() {
  const button = document.querySelector("#paper-import-button");
  const file = state.importFile || document.querySelector("#paper-file-input").files[0];
  const pdfUrl = document.querySelector("#paper-url-input").value.trim();
  if (!file && !pdfUrl) return toast("请选择 PDF 文件或填写链接。", true);
  button.disabled = true;
  button.textContent = "正在添加…";
  try {
    const payload = file
      ? { pdf_data: await fileToBase64(file), metadata: { original_filename: file.name, size_bytes: file.size } }
      : { pdf_url: pdfUrl };
    const uploaded = await uploadPaperPayload(payload);
    if (state.importFolderId) {
      await fetchJson(`/api/research/papers/${encodeURIComponent(uploaded.paper_id)}/folder`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folder_id: state.importFolderId }),
      });
    }
    setSelectedLibraryPaperFile(null);
    document.querySelector("#paper-file-input").value = "";
    document.querySelector("#paper-url-input").value = "";
    await Promise.all([loadCounts(), loadFolders(), loadItems()]);
    toast("论文已添加到论文管理，可直接开始精读。");
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = "添加到论文管理"; }
}

function bindPaperFilePicker() {
  const input = document.querySelector("#paper-file-input");
  const button = document.querySelector("#paper-file-button");
  const area = document.querySelector("#paper-import");
  button.addEventListener("click", () => input.click());
  input.addEventListener("change", () => {
    if (!setSelectedLibraryPaperFile(input.files?.[0] || null)) input.value = "";
  });
  ["dragenter", "dragover"].forEach((eventName) => area.addEventListener(eventName, (event) => {
    event.preventDefault();
    button.classList.add("is-dragging");
    event.dataTransfer.dropEffect = "copy";
  }));
  ["dragleave", "dragend"].forEach((eventName) => area.addEventListener(eventName, () => button.classList.remove("is-dragging")));
  area.addEventListener("drop", (event) => {
    event.preventDefault();
    button.classList.remove("is-dragging");
    const file = event.dataTransfer.files?.[0] || null;
    if (!setSelectedLibraryPaperFile(file)) return;
  });
}

function setSelectedLibraryPaperFile(file) {
  const button = document.querySelector("#paper-file-button");
  const label = document.querySelector("#paper-file-label");
  if (file && file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
    state.importFile = null;
    toast("请选择 PDF 文件。", true);
    return false;
  }
  state.importFile = file;
  label.textContent = file ? `${file.name} · ${formatBytes(file.size)}` : "选择 PDF";
  button.classList.toggle("has-file", Boolean(file));
  return true;
}

function bindReadingFilter() {
  const control = document.querySelector("#reading-filter");
  const values = ["all", "reviewed", "unreviewed"];
  let dragging = false;
  const valueAt = (clientX) => {
    const rect = control.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(.999, (clientX - rect.left) / rect.width));
    return values[Math.floor(ratio * values.length)];
  };
  control.addEventListener("click", (event) => {
    const button = event.target.closest("[data-reading-filter]");
    if (button) selectReadingFilter(button.dataset.readingFilter);
  });
  control.addEventListener("pointerdown", (event) => {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    dragging = true;
    control.setPointerCapture(event.pointerId);
    previewReadingFilter(valueAt(event.clientX));
  });
  control.addEventListener("pointermove", (event) => {
    if (dragging) previewReadingFilter(valueAt(event.clientX));
  });
  control.addEventListener("pointerup", (event) => {
    if (!dragging) return;
    dragging = false;
    selectReadingFilter(valueAt(event.clientX));
  });
  control.addEventListener("pointercancel", () => {
    dragging = false;
    previewReadingFilter(state.readingFilter);
  });
}

function previewReadingFilter(value) {
  const values = ["all", "reviewed", "unreviewed"];
  const control = document.querySelector("#reading-filter");
  control.style.setProperty("--reading-filter-index", String(Math.max(0, values.indexOf(value))));
  control.querySelectorAll("[data-reading-filter]").forEach((button) => {
    const active = button.dataset.readingFilter === value;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-checked", String(active));
  });
}

function selectReadingFilter(value) {
  if (!["all", "reviewed", "unreviewed"].includes(value)) return;
  previewReadingFilter(value);
  if (state.readingFilter === value) return;
  state.readingFilter = value;
  loadItems();
}

async function uploadPaperPayload(payload) {
  const response = await fetch("/paper_reading", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "upload_paper", session_id: "", paper_id: "", content: "", metadata: {}, ...payload }) });
  const envelope = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(envelope.detail || `导入失败（HTTP ${response.status}）`);
  let data = envelope.content ?? envelope;
  if (typeof data === "string") data = JSON.parse(data);
  if (data.status === "error") throw new Error(data.message || data.error || "论文导入失败");
  return data.data || data;
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.onerror = () => reject(new Error("读取 PDF 文件失败。"));
    reader.readAsDataURL(file);
  });
}

function paperPdfUrl(paper) {
  if (paper.arxiv_id) return `https://arxiv.org/pdf/${paper.arxiv_id}`;
  return /\.pdf(?:$|[?#])/i.test(paper.source_url || "") ? paper.source_url : "";
}

async function attachManagedPaper(paperId, pdfUrl, button) {
  let payload = { paper_id: paperId };
  if (pdfUrl) {
    payload.pdf_url = pdfUrl;
  } else {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "application/pdf,.pdf";
    const file = await new Promise((resolve) => {
      input.addEventListener("change", () => resolve(input.files[0] || null), { once: true });
      input.click();
    });
    if (!file) { button.disabled = false; return; }
    payload.pdf_data = await fileToBase64(file);
    payload.metadata = { original_filename: file.name, size_bytes: file.size };
  }
  button.disabled = true;
  try {
    const uploaded = await uploadPaperPayload(payload);
    const session = await createPaperReadingSession(uploaded.paper_id);
    window.location.href = paperWorkspace(uploaded.paper_id, session.reading_session_id);
  } catch (error) {
    button.disabled = false;
    toast(error.message, true);
  }
}

async function createPaperReadingSession(paperId) {
  return fetchJson(`/api/research/papers/${encodeURIComponent(paperId)}/reading-session`, { method: "POST" });
}

async function startPaperReading(paperId, button) {
  button.disabled = true;
  try {
    const session = await createPaperReadingSession(paperId);
    window.location.href = paperWorkspace(paperId, session.reading_session_id);
  } catch (error) {
    button.disabled = false;
    toast(error.message, true);
  }
}

function previewFor(item) {
  if (state.view === "paper-readings") return item.paper_abstract || "暂无摘要";
  if (state.view === "papers") return item.abstract || "暂无摘要";
  return item.preview || item.query || "";
}

function metaFor(item) {
  if (state.view === "conversations") return [[`${item.message_count} 条消息`], ...(item.modes || []).map((mode) => [modeLabel(mode), true]), item.parent_conversation_id ? ["Fork 会话", true] : null].filter(Boolean);
  if (state.view === "domain-onboardings") return [[stateLabel(item.state)], [`阶段：${item.current_stage}`, true], [`${item.recommendation_count} 篇推荐论文`]].filter(Boolean);
  if (state.view === "paper-readings") return [[readingState(item.state)], [`${Math.round(Number(item.progress?.percentage || 0))}%`], [`${item.block_count} 个分析块`, true], [`${item.annotation_count} 条标注`, true]];
  return [[item.in_library ? statusLabel(item.reading_status) : "未加入论文库"], item.folder_path ? [`文件夹：${item.folder_path}`, true] : ["未放入文件夹"], item.publication_year ? [String(item.publication_year), true] : null, [`${item.reading_count} 次精读`], [`${item.annotation_count} 条标注`, true]].filter(Boolean);
}

function showSkeletons() { document.querySelector("#empty-state").hidden = true; items.replaceChildren(...[1,2,3].map(() => element("div", "skeleton"))); }
function showEmpty(title, copy) { const empty = document.querySelector("#empty-state"); empty.hidden = false; document.querySelector("#empty-title").textContent = title; document.querySelector("#empty-copy").textContent = copy; }
function domainWorkspace(artifactId) { return `/app/domain-onboarding?task_id=${encodeURIComponent(artifactId)}`; }
function paperWorkspace(paperId, sessionId) { const params = new URLSearchParams({ paper_id: paperId }); if (sessionId) params.set("session_id", sessionId); return `/app/paper-reading?${params}`; }
function conversationUrl(conversationId) { return `/app?conversation_id=${encodeURIComponent(conversationId)}`; }
function element(tag, className = "", text = "") { const node = document.createElement(tag); if (className) node.className = className; if (text) node.textContent = text; return node; }
function link(href, text, accent = false) { const item = element("a", accent ? "accent" : "", text); item.href = href; return item; }
function formatDate(value) { const date = new Date(value); return Number.isNaN(date.valueOf()) ? "" : new Intl.DateTimeFormat("zh-CN", { month:"short",day:"numeric",hour:"2-digit",minute:"2-digit" }).format(date); }
function formatBytes(bytes) { if (!Number.isFinite(bytes) || bytes <= 0) return "0 B"; const units = ["B","KB","MB","GB"]; const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1); return `${(bytes / (1024 ** index)).toFixed(index ? 1 : 0)} ${units[index]}`; }
function modeLabel(mode) { return ({ chat:"日常聊天",domain_onboarding:"领域入门",paper_reading:"论文精读" })[mode] || mode; }
function stateLabel(value) { return ({ queued:"排队中",running:"进行中",paused:"已暂停",completed:"已完成",failed:"失败",cancelled:"已取消" })[value] || value; }
function readingState(value) { return ({ active:"阅读中",paused:"已暂停",completed:"已完成" })[value] || value; }
function statusLabel(value) { return ({ unread:"未读",reading:"阅读中",read:"完成",archived:"已归档" })[value] || value; }
function cssEscape(value) { return window.CSS?.escape ? CSS.escape(value) : String(value).replace(/["\\]/g,"\\$&"); }
async function fetchJson(input, options) { const response = await fetch(input, options); const data = await response.json().catch(() => ({})); if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `请求失败（HTTP ${response.status}）`); return data; }
function toast(text, error = false) { const node = element("div", `toast${error ? " is-error" : ""}`, text); document.querySelector("#toast-region").append(node); setTimeout(() => node.remove(), 3800); }
