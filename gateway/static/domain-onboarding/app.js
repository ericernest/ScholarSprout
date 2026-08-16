const STORAGE_KEY = "domain_onboarding_workspace_v1_9";
const LEGACY_STORAGE_KEYS = ["domain_onboarding_workspace_v1_5"];
const PENDING_REQUEST_KEY = "domain_onboarding_pending_request_v1";
const TERMINAL_STATES = new Set(["completed", "failed", "cancelled", "interrupted"]);
const EVENT_NAMES = [
  "accepted",
  "profile_ready",
  "plan_ready",
  "papers_ready",
  "stage_plan_ready",
  "stage_retrieval_ready",
  "llm_delta",
  "development_ready",
  "landscape_ready",
  "learning_path_ready",
  "quality_ready",
  "final_quality_ready",
  "repair_started",
  "section_replaced",
  "completed",
  "failed",
  "cancelled",
];
const STATUS_LABELS = {
  queued: "任务排队中",
  running: "正在生成学习地图",
  cancel_requested: "正在取消",
  completed: "学习地图已生成",
  failed: "生成失败，可重试",
  cancelled: "任务已取消",
  interrupted: "任务因服务重启中断",
};
const STREAM_STAGE_LABELS = {
  planning: "正在规划调研范围",
  stage_planning: "正在生成发展阶段提纲",
  development_foundation: "正在生成前置知识",
  development_stage: "正在分阶段生成发展内容",
  development: "正在生成前置知识与发展路径",
  landscape: "正在梳理核心问题与研究方向",
  learning_path: "正在生成标准学习路线",
  generation: "正在生成领域学习地图",
  repair: "正在完善生成结果",
};
const STAGE_LABELS = {
  accepted: "任务已接收",
  profile_ready: "标准新手路线已确定",
  plan_ready: "调研计划已完成",
  papers_ready: "论文检索已完成",
  stage_plan_ready: "发展阶段提纲已完成",
  stage_retrieval_ready: "正在按发展阶段检索论文",
  development_ready: "发展脉络已完成",
  landscape_ready: "概念全景已完成",
  learning_path_ready: "学习路线已完成",
  quality_ready: "结果校验已完成",
  final_quality_ready: "最终结果已生成",
  repair_started: "正在完善生成结果",
  section_replaced: "已更新问题分区",
  completed: "学习地图已生成",
  failed: "生成失败，可重试",
  cancelled: "任务已取消",
};
const PRIORITY_LABELS = {
  core: "核心",
  recommended: "推荐",
  optional: "选读",
  extended: "拓展",
};
const state = {
  taskId: "",
  accessToken: "",
  snapshot: null,
  partial: {},
  result: null,
  revision: 0,
  lastEventId: 0,
  eventSource: null,
  pollTimer: null,
  activeLLMStage: "",
  paperFilter: "all",
  selected: null,
};

const $ = (id) => document.getElementById(id);

bindInteractions();
initialize();

async function initialize() {
  const params = new URLSearchParams(window.location.search);
  const saved = readWorkspace();
  const requestedTaskId = params.get("task_id") || "";
  const query = (params.get("query") || "").trim();
  let loadedWorkspace = false;

  if (requestedTaskId) {
    state.taskId = requestedTaskId;
    try {
      const workspace = await loadResearchWorkspace(requestedTaskId);
      state.accessToken = workspace.access_token || "";
      consumeSnapshot(workspace, false);
      loadedWorkspace = true;
    } catch (error) {
      if (saved?.task_id === requestedTaskId) {
        state.accessToken = saved.access_token || saved.snapshot?.access_token || "";
        consumeSnapshot(saved.snapshot, false);
      } else {
        showEmpty("无法读取任务", error.message, false);
        return;
      }
    }
  } else if (query) {
    try {
      const request = {
        query,
        session_id: getSessionId(),
        user_id: "local-web",
        metadata: {},
      };
      const job = await submitTask(request);
      state.taskId = job.task_id;
      state.accessToken = job.access_token || "";
      consumeSnapshot({ ...job, progress: Number(job.progress) || 0, request });
      history.replaceState(null, "", `/app/domain-onboarding?task_id=${encodeURIComponent(state.taskId)}`);
    } catch (error) {
      showEmpty("任务创建失败", error.message, true);
      return;
    }
  } else if (saved?.task_id) {
    state.taskId = saved.task_id;
    state.accessToken = saved.access_token || saved.snapshot?.access_token || "";
    consumeSnapshot(saved.snapshot, false);
    history.replaceState(null, "", `/app/domain-onboarding?task_id=${encodeURIComponent(state.taskId)}`);
  } else {
    showEmpty("还没有领域任务", "从聊天页选择“领域入门”并提出你的学习目标。", false);
    return;
  }

  if (!loadedWorkspace) {
    try {
      await refreshSnapshot();
    } catch (error) {
      if (!state.snapshot) {
        showEmpty("无法读取任务", error.message, false);
        return;
      }
      toast("暂时无法连接后端，已展示浏览器中的最近快照。", true);
    }
  }

  if (state.snapshot && !TERMINAL_STATES.has(state.snapshot.state) && state.snapshot.workspace_source !== "catalog") {
    connectEvents();
  }
}

async function loadResearchWorkspace(taskId) {
  const response = await fetch(
    `/api/research/domain-onboardings/${encodeURIComponent(taskId)}/workspace`,
    { headers: { Accept: "application/json" } },
  );
  const workspace = await readJson(response);
  if (!response.ok) {
    throw new Error(readError(workspace, `读取领域记录失败（HTTP ${response.status}）`));
  }
  return workspace;
}

function bindInteractions() {
  $("section-nav").addEventListener("click", (event) => {
    const button = event.target.closest("[data-target]");
    if (!button) return;
    document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("is-active", item === button));
    document.getElementById(button.dataset.target)?.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  $("content").addEventListener("click", async (event) => {
    const filter = event.target.closest("[data-paper-filter]");
    if (filter) {
      state.paperFilter = filter.dataset.paperFilter;
      renderPapers(currentData());
      return;
    }
    const target = event.target.closest("[data-detail-kind]");
    if (!target) return;
    document.querySelectorAll(".interactive-card.is-selected").forEach((item) => item.classList.remove("is-selected"));
    target.classList.add("is-selected");
    showDetail(target.dataset.detailKind, target.dataset.detailId);
  });

  $("inspector-content").addEventListener("click", async (event) => {
    const paperButton = event.target.closest("[data-paper-id]");
    if (paperButton) {
      showDetail("paper", paperButton.dataset.paperId);
      return;
    }
    const importButton = event.target.closest("[data-import-paper]");
    if (importButton) {
      await importPaper(importButton.dataset.importPaper);
      return;
    }
    const libraryButton = event.target.closest("[data-add-paper-library]");
    if (libraryButton) {
      await addPaperToLibrary(libraryButton.dataset.addPaperLibrary, libraryButton);
    }
  });

  $("cancel-button").addEventListener("click", cancelTask);
  ["retry-button", "topbar-retry-button"].forEach((id) => $(id).addEventListener("click", retryTask));
  bindScrollSpy();
}

function bindScrollSpy() {
  const sections = Array.from(document.querySelectorAll(".content-section"));
  const observer = new IntersectionObserver(
    (entries) => {
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (!visible) return;
      document.querySelectorAll(".nav-item").forEach((item) => {
        item.classList.toggle("is-active", item.dataset.target === visible.target.id);
      });
    },
    { rootMargin: "-90px 0px -62% 0px", threshold: [0.08, 0.35] },
  );
  sections.forEach((section) => observer.observe(section));
}

async function submitTask(request) {
  const clientRequestId = pendingRequestId(request);
  const response = await fetch("/domain_onboarding/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...request,
      client_request_id: clientRequestId,
    }),
  });
  const payload = await readJson(response);
  if (!response.ok || !payload?.task_id) {
    throw new Error(readError(payload, `创建任务失败（HTTP ${response.status}）`));
  }
  clearPendingRequest(clientRequestId);
  return payload;
}

function pendingRequestId(request) {
  const query = String(request?.query || "").trim();
  try {
    const pending = JSON.parse(localStorage.getItem(PENDING_REQUEST_KEY) || "null");
    const fresh = Date.now() - Date.parse(pending?.created_at || "") < 15 * 60 * 1000;
    if (fresh && pending?.session_id === request.session_id && pending?.query === query) {
      return pending.client_request_id;
    }
  } catch {
    // Replace malformed browser state below.
  }
  const clientRequestId = crypto.randomUUID();
  localStorage.setItem(PENDING_REQUEST_KEY, JSON.stringify({
    session_id: request.session_id,
    query,
    client_request_id: clientRequestId,
    created_at: new Date().toISOString(),
  }));
  return clientRequestId;
}

function clearPendingRequest(clientRequestId) {
  try {
    const pending = JSON.parse(localStorage.getItem(PENDING_REQUEST_KEY) || "null");
    if (pending?.client_request_id === clientRequestId) localStorage.removeItem(PENDING_REQUEST_KEY);
  } catch {
    localStorage.removeItem(PENDING_REQUEST_KEY);
  }
}

async function refreshSnapshot() {
  if (!state.taskId) return;
  const response = await fetch(`/domain_onboarding/jobs/${encodeURIComponent(state.taskId)}`, {
    headers: jobAuthHeaders({ Accept: "application/json" }),
  });
  const snapshot = await readJson(response);
  if (!response.ok) throw new Error(readError(snapshot, `读取任务失败（HTTP ${response.status}）`));
  consumeSnapshot(snapshot);
}

function consumeSnapshot(snapshot, persist = true) {
  if (!snapshot || typeof snapshot !== "object") return;
  state.snapshot = snapshot;
  state.revision = Math.max(state.revision, Number(snapshot.revision) || 0);
  if (Object.prototype.hasOwnProperty.call(snapshot, "partial_result")) {
    state.partial = snapshot.partial_result && typeof snapshot.partial_result === "object"
      ? snapshot.partial_result
      : {};
  }
  if (Object.prototype.hasOwnProperty.call(snapshot, "result")) {
    state.result = snapshot.result && typeof snapshot.result === "object" ? snapshot.result : null;
  }
  render();
  if (persist) saveWorkspace();

  if (TERMINAL_STATES.has(snapshot.state)) {
    state.activeLLMStage = "";
    closeLiveUpdates();
    if (!state.result && !Object.keys(state.partial).length) {
      const message = snapshot.error || "任务没有生成可展示的内容。";
      showEmpty(STATUS_LABELS[snapshot.state] || "任务已结束", message, snapshot.retryable);
    }
  }
}

function connectEvents() {
  if (!state.taskId || state.eventSource || TERMINAL_STATES.has(state.snapshot?.state)) return;
  const query = new URLSearchParams();
  if (state.lastEventId) query.set("after", String(state.lastEventId));
  if (state.accessToken) query.set("access_token", state.accessToken);
  const queryString = query.toString();
  const suffix = queryString ? `?${queryString}` : "";
  const source = new EventSource(`/domain_onboarding/jobs/${encodeURIComponent(state.taskId)}/events${suffix}`);
  state.eventSource = source;
  EVENT_NAMES.forEach((name) => source.addEventListener(name, handleEvent));
  source.onerror = () => {
    source.close();
    if (state.eventSource === source) state.eventSource = null;
    if (!TERMINAL_STATES.has(state.snapshot?.state)) startPolling();
  };
}

function handleEvent(event) {
  let payload;
  try {
    payload = JSON.parse(event.data);
  } catch {
    return;
  }
  state.lastEventId = Math.max(state.lastEventId, Number(event.lastEventId || payload.id) || 0);
  const revision = Number(payload.revision) || 0;
  if (revision <= state.revision) return;
  state.revision = revision;

  const data = payload.data || {};
  const isLLMDelta = payload.event === "llm_delta";
  if (isLLMDelta) {
    state.activeLLMStage = String(data.stage || "generation");
  } else {
    state.activeLLMStage = "";
  }
  if (data.result && typeof data.result === "object") {
    state.result = data.result;
  } else {
    for (const path of payload.replace_paths || []) {
      const key = String(path).split(".", 1)[0];
      if (key !== "result" && Object.prototype.hasOwnProperty.call(data, key)) {
        state.partial[key] = data[key];
      }
    }
  }

  const eventState = data.state || (payload.event === "completed" ? "completed" : null);
  state.snapshot = {
    ...(state.snapshot || {}),
    task_id: state.taskId,
    state: eventState || state.snapshot?.state || "running",
    revision,
    current_stage: isLLMDelta
      ? state.snapshot?.current_stage || "accepted"
      : payload.event,
    progress: payload.progress,
    partial_result: state.partial,
    result: state.result,
    error: data.error || state.snapshot?.error || null,
  };
  render();
  saveWorkspace();

  if (TERMINAL_STATES.has(state.snapshot.state)) {
    closeLiveUpdates();
    refreshSnapshot().catch(() => {});
  }
}

function startPolling() {
  if (state.pollTimer) return;
  const poll = async () => {
    state.pollTimer = null;
    try {
      await refreshSnapshot();
    } catch {
      // A later poll can recover a transient disconnect.
    }
    if (!TERMINAL_STATES.has(state.snapshot?.state)) {
      state.pollTimer = window.setTimeout(poll, 3000);
    }
  };
  state.pollTimer = window.setTimeout(poll, 900);
}

function closeLiveUpdates() {
  state.eventSource?.close();
  state.eventSource = null;
  if (state.pollTimer) window.clearTimeout(state.pollTimer);
  state.pollTimer = null;
}

function render() {
  $("empty-state").hidden = true;
  renderStatus();
  const data = currentData();
  if (!data || !Object.keys(data).length) return;
  renderOverview(data);
  renderPrerequisites(data);
  renderDevelopment(data);
  renderLandscape(data);
  renderLearningPath(data);
  renderPapers(data);
  renderGraph(data);
  if (state.selected) showDetail(state.selected.kind, state.selected.id);
}

function renderStatus() {
  const snapshot = state.snapshot || {};
  const progress = Math.max(0, Math.min(1, Number(snapshot.progress) || 0));
  const terminal = TERMINAL_STATES.has(snapshot.state);
  const status = terminal
    ? STATUS_LABELS[snapshot.state]
    : STREAM_STAGE_LABELS[state.activeLLMStage]
      || STAGE_LABELS[snapshot.current_stage]
      || STATUS_LABELS[snapshot.state]
      || "正在分析任务";
  $("status-label").textContent = status;
  $("progress-label").textContent = `${Math.round(progress * 100)}%`;
  $("progress-fill").style.transform = `scaleX(${progress})`;
  $("status-dot").className = `status-dot${
    ["failed", "interrupted"].includes(snapshot.state)
      ? " is-error"
      : ["cancel_requested", "cancelled"].includes(snapshot.state)
        ? " is-warning"
        : ""
  }`;
  $("cancel-button").disabled = terminal || !state.taskId;
  $("cancel-button").hidden = terminal;
  const canRetry = terminal && Boolean(snapshot.retryable) && Boolean(state.taskId);
  $("topbar-retry-button").hidden = !canRetry;
  $("topbar-retry-button").disabled = !canRetry;
}

function renderOverview(data) {
  const tags = [
    data.schema_version ? `<span class="tag">${escapeHtml(data.schema_version)}</span>` : "",
  ].join("");
  $("overview-content").classList.remove("loading-section");
  $("overview-content").innerHTML = `
    <div class="hero-kicker">${tags}</div>
    <h2 class="hero-title">${escapeHtml(data.domain || data.query || state.snapshot?.request?.query || "正在理解你的学习目标")}</h2>
    <p class="hero-copy">${escapeHtml(data.text || "正在检索论文并构建领域发展脉络，已完成的内容会自动出现在下方。")}</p>
    <div class="profile-strip">
      ${profileItem("适用对象", "普通科研新手")}
      ${profileItem("路线类型", "标准学习路线")}
      ${profileItem("学习顺序", "基础 → 方法 → 实践 → 前沿")}
      ${profileItem("时间安排", "按实际情况自主安排")}
    </div>
  `;
  $("sidebar-domain").textContent = data.domain || "领域入门";
  $("sidebar-policy").textContent = data.policy_version || "生成策略执行中";
}

function renderPrerequisites(data) {
  const container = $("prerequisites-content");
  container.classList.remove("loading-grid");
  if (!Array.isArray(data.prerequisites) || !data.prerequisites.length) {
    container.innerHTML = sectionStatusCopy("前置知识", data);
    return;
  }
  const items = data.prerequisites;
  container.innerHTML = items.length
    ? items.map((item, index) => `
      <button class="interactive-card" type="button" data-detail-kind="prerequisite" data-detail-id="${escapeHtml(item.prerequisite_id || String(index))}">
        <span class="card-index">FOUNDATION ${String(index + 1).padStart(2, "0")}</span>
        <h3>${escapeHtml(item.name)}</h3>
        <p>${escapeHtml(item.why_needed || fieldStatusCopy("说明"))}</p>
        <span class="chip-row">${(item.key_points || []).slice(0, 3).map(chip).join("")}</span>
      </button>
    `).join("")
    : emptyCopy("当前结果没有单独列出前置知识。");
}

function renderDevelopment(data) {
  const container = $("development-content");
  container.classList.remove("loading-grid");
  if (!Array.isArray(data.development_stages) || !data.development_stages.length) {
    const planned = data.research_plan?.development_stage_plans || [];
    container.innerHTML = planned.length
      ? planned.map((stage, index) => `
        <button class="interactive-card timeline-card" type="button" data-detail-kind="research-stage" data-detail-id="${escapeHtml(stage.stage_id || String(index))}">
          <span class="timeline-period">${escapeHtml(stage.period || `阶段 ${index + 1}`)}</span>
          <span class="timeline-body">
            <span class="card-index">RESEARCHING STAGE ${String(stage.sequence || index + 1).padStart(2, "0")}</span>
            <h3>${escapeHtml(stage.name)}</h3>
            <p>${escapeHtml(stage.focus)}</p>
            <span class="chip-row"><span class="chip">已绑定 ${(stage.selected_paper_ids || []).length} 篇真实论文</span></span>
          </span>
        </button>
      `).join("")
      : sectionStatusCopy("发展路径", data);
    return;
  }
  const items = [...data.development_stages].sort((a, b) => Number(a.sequence) - Number(b.sequence));
  container.innerHTML = items.length
    ? items.map((stage, index) => `
      <button class="interactive-card timeline-card" type="button" data-detail-kind="stage" data-detail-id="${escapeHtml(stage.stage_id || String(index))}">
        <span class="timeline-period">${escapeHtml(stage.historical_period || stage.period || `阶段 ${index + 1}`)}</span>
        <span class="timeline-body">
          <span class="card-index">STAGE ${String(stage.sequence || index + 1).padStart(2, "0")}</span>
          <h3>${escapeHtml(stage.name)}</h3>
          <p>${escapeHtml(stage.summary)}</p>
          ${stage.transition_from_previous ? `<p class="transition-note">技术转折 · ${escapeHtml(stage.transition_from_previous)}</p>` : ""}
          <span class="chip-row">${(stage.core_concepts || []).slice(0, 4).map(chip).join("")}</span>
        </span>
      </button>
    `).join("")
    : emptyCopy("当前结果没有可展示的发展阶段。");
}

function renderLandscape(data) {
  const landscape = data.current_landscape;
  if (!landscape || typeof landscape !== "object") {
    const container = $("landscape-content");
    container.classList.remove("loading-grid");
    container.innerHTML = sectionStatusCopy("概念全景", data);
    return;
  }
  const problems = mergeLandscapeItems(
    landscape.problem_details,
    landscape.problems,
    "problem_id",
    "problem",
  );
  const directions = mergeLandscapeItems(
    landscape.subdirection_details,
    landscape.subdirections,
    "subdirection_id",
    "sub",
    landscape.subdirection_ids,
  );
  const container = $("landscape-content");
  container.classList.remove("loading-grid");
  container.innerHTML = `
    <div class="landscape-grid">
      <div class="landscape-column">
        <h3>当前核心问题</h3>
        ${problems.length ? problems.map((item, index) => `
          <button class="interactive-card" type="button" data-detail-kind="problem" data-detail-id="${escapeHtml(item.problem_id || String(index))}">
            <span class="card-index">PROBLEM ${String(index + 1).padStart(2, "0")}</span>
            <h3>${escapeHtml(item.name)}</h3>
            <p>${escapeHtml(item.description || "点击查看关联阶段、方向与论文。")}</p>
          </button>
        `).join("") : emptyCopy("暂无结构化问题。")}
      </div>
      <div class="landscape-column">
        <h3>主要子方向</h3>
        ${directions.length ? directions.map((item, index) => `
          <button class="interactive-card branch-card" type="button" data-detail-kind="subdirection" data-detail-id="${escapeHtml(item.subdirection_id || String(index))}">
            <span class="card-copy">
              <span class="card-index">BRANCH ${String(index + 1).padStart(2, "0")}</span>
              <h3>${escapeHtml(item.name)}</h3>
              <p>${escapeHtml(item.description || item.why_it_matters || "展开这条研究分支。")}</p>
              ${(item.typical_tasks || []).length ? `<span class="chip-row">${item.typical_tasks.slice(0, 2).map(chip).join("")}</span>` : ""}
            </span>
            <span class="branch-action">展开分支 ↗</span>
          </button>
        `).join("") : emptyCopy("暂无结构化子方向。")}
      </div>
    </div>
  `;
}

function isInternalLandscapeLabel(value) {
  return /^(?:(?:problem|sub|subdirection|direction)_[a-z0-9_]+|(?:problem|sub|subdirection|direction)-?\d+)$/i
    .test(String(value || "").trim());
}

// Detail objects are the canonical reader-facing representation. Summary-only
// names remain a fallback for old partial tasks, but internal IDs are never
// rendered as titles.
function mergeLandscapeItems(details, names, idField, idPrefix, idsByName = {}) {
  const byName = new Map();
  for (const item of Array.isArray(details) ? details : []) {
    const name = String(item?.name || "").trim();
    if (!name || isInternalLandscapeLabel(name)) continue;
    byName.set(name, { ...item, name });
  }
  if (byName.size) return [...byName.values()];
  for (const rawName of Array.isArray(names) ? names : []) {
    const name = String(rawName || "").trim();
    if (!name || isInternalLandscapeLabel(name) || byName.has(name)) continue;
    byName.set(name, {
      [idField]: idsByName?.[name] || `${idPrefix}-${byName.size}`,
      name,
    });
  }
  return [...byName.values()];
}

function renderLearningPath(data) {
  if (!Array.isArray(data.learning_path) || !data.learning_path.length) {
    const container = $("learning-content");
    container.classList.remove("loading-grid");
    container.innerHTML = sectionStatusCopy("学习路线", data);
    return;
  }
  const container = $("learning-content");
  container.classList.remove("loading-grid");
  container.innerHTML = data.learning_path.length
    ? data.learning_path.map((step, index) => {
      const week = `S${String(index + 1).padStart(2, "0")}`;
      const timeCopy = step.estimated_hours
        ? `${step.estimated_hours} 小时`
        : "自由安排";
      return `
        <button class="interactive-card learning-card" type="button" data-detail-kind="learning" data-detail-id="${escapeHtml(String(index))}">
          <span class="week-block"><b>${escapeHtml(week)}</b><span>${escapeHtml(timeCopy)}</span></span>
          <span class="learning-main">
            <span class="card-index">STEP ${escapeHtml(step.step || String(index + 1))}</span>
            <h3>${escapeHtml(step.goal || fieldStatusCopy("目标"))}</h3>
            <p>${escapeHtml(step.expected_outcome || "")}</p>
            ${step.milestone ? `<p class="milestone">里程碑 · ${escapeHtml(step.milestone)}</p>` : ""}
          </span>
          <span class="learning-meta">${(step.paper_ids || []).length} 篇论文 ↗</span>
        </button>
      `;
    }).join("")
    : emptyCopy("当前结果没有可展示的学习步骤。");
}

function renderPapers(data) {
  if (!Array.isArray(data.papers) || !data.papers.length) {
    const container = $("papers-content");
    container.classList.remove("loading-grid");
    container.innerHTML = sectionStatusCopy("论文清单", data);
    return;
  }
  const priorities = ["all", "core", "recommended", "optional", "extended"];
  $("paper-filters").innerHTML = priorities.map((priority) => `
    <button class="filter-button${state.paperFilter === priority ? " is-active" : ""}" type="button" data-paper-filter="${priority}">
      ${priority === "all" ? "全部" : PRIORITY_LABELS[priority]}
    </button>
  `).join("");
  const papers = data.papers
    .filter((paper) => state.paperFilter === "all" || paper.reading_priority === state.paperFilter)
    .sort((left, right) => Number(right.final_score || 0) - Number(left.final_score || 0));
  const container = $("papers-content");
  container.classList.remove("loading-grid");
  container.innerHTML = papers.length
    ? papers.map((paper) => `
      <button class="interactive-card paper-row" type="button" data-detail-kind="paper" data-detail-id="${escapeHtml(paper.paper_id)}">
        <span class="paper-title">
          <span class="chip-row">
            <span class="chip">${escapeHtml(PRIORITY_LABELS[paper.reading_priority] || paper.reading_priority || "论文")}</span>
            <span class="chip">${escapeHtml(paper.paper_role || "other")}</span>
            ${paper.is_canonical ? '<span class="chip">Canonical</span>' : ""}
          </span>
          <h3>${escapeHtml(paper.title)}</h3>
          <small>${escapeHtml((paper.authors || []).slice(0, 4).join("、") || "作者未知")} · ${escapeHtml(paper.year || "年份未知")}</small>
        </span>
        <span class="paper-score"><b>${formatPercentScore(paper.final_score)}<small>/100</small></b><span>综合推荐度</span></span>
      </button>
    `).join("")
    : emptyCopy("当前筛选下没有论文。");
}

function renderGraph(data) {
  const graph = data.knowledge_graph;
  const section = $("knowledge-graph");
  const nav = $("graph-nav");
  if (!graph || !Array.isArray(graph.nodes)) {
    section.hidden = true;
    nav.hidden = true;
    return;
  }
  section.hidden = false;
  nav.hidden = false;
  $("graph-content").innerHTML = `
    <div class="graph-grid">
      ${graph.nodes.map((node) => `
        <button class="graph-node" type="button" data-detail-kind="graph-node" data-detail-id="${escapeHtml(node.node_id)}">
          <span>${escapeHtml(node.node_type)}</span>
          <strong>${escapeHtml(node.label)}</strong>
        </button>
      `).join("")}
    </div>
    <div class="edge-list">
      ${(graph.edges || []).map((edge) => `<span class="edge-pill">${escapeHtml(edge.source_id)} · ${escapeHtml(edge.edge_type)} → ${escapeHtml(edge.target_id)}</span>`).join("")}
    </div>
  `;
}

function showDetail(kind, id) {
  const data = currentData();
  if (!data) return;
  state.selected = { kind, id };
  const papers = paperIndex(data);
  let title = "学习详情";
  let subtitle = "与当前学习地图保持关联";
  let summary = "";
  let blocks = [];
  let paperIds = [];

  if (kind === "prerequisite") {
    const item = (data.prerequisites || []).find((value, index) => String(value.prerequisite_id || index) === id);
    if (!item) return;
    title = item.name;
    subtitle = "前置知识";
    summary = item.why_needed;
    blocks.push(detailList("关键概念", item.key_points, papers));
    paperIds = [
      ...(item.related_paper_ids || []),
      ...(item.key_points || []).flatMap(detailPaperIds),
    ];
  } else if (kind === "research-stage") {
    const item = (data.research_plan?.development_stage_plans || []).find((value, index) => String(value.stage_id || index) === id);
    if (!item) return;
    title = item.name;
    subtitle = item.period || "阶段研究提纲";
    summary = item.focus;
    blocks.push(detailList("阶段检索词", item.search_queries));
    if (item.transition_from_previous) blocks.push(detailList("与上一阶段的转折", [item.transition_from_previous]));
    paperIds = item.selected_paper_ids || [];
  } else if (kind === "stage") {
    const item = (data.development_stages || []).find((value, index) => String(value.stage_id || index) === id);
    if (!item) return;
    title = item.name;
    subtitle = item.historical_period || item.period || "发展阶段";
    summary = item.summary || item.motivation;
    blocks.push(detailList("核心概念", item.core_concepts, papers));
    blocks.push(detailList("主要技术", item.main_techniques, papers));
    blocks.push(detailList("开放问题", item.open_problems));
    paperIds = [
      ...(item.related_paper_ids || []),
      ...(item.representative_papers || []).map((paper) => paper.paper_id),
      ...(item.core_concepts || []).flatMap(detailPaperIds),
      ...(item.main_techniques || []).flatMap(detailPaperIds),
    ];
  } else if (kind === "problem") {
    const item = mergeLandscapeItems(
      data.current_landscape?.problem_details,
      data.current_landscape?.problems,
      "problem_id",
      "problem",
    ).find((value, index) => String(value.problem_id || index) === id);
    if (!item) return;
    title = item.name;
    subtitle = "当前核心问题";
    summary = item.description;
    blocks.push(detailList("关联阶段", namesForStages(item.related_stage_ids || item.affected_stage_ids, data)));
    blocks.push(detailList("关联方向", namesForSubdirections(item.related_subdirection_ids, data)));
    paperIds = item.related_paper_ids || [];
  } else if (kind === "subdirection") {
    const item = mergeLandscapeItems(
      data.current_landscape?.subdirection_details,
      data.current_landscape?.subdirections,
      "subdirection_id",
      "sub",
      data.current_landscape?.subdirection_ids,
    ).find((value, index) => String(value.subdirection_id || index) === id);
    if (!item) return;
    title = item.name;
    subtitle = "研究分支";
    summary = item.description || item.why_it_matters;
    blocks.push(item.why_it_matters ? `<div class="detail-block"><h3>为什么重要</h3><p class="detail-summary">${escapeHtml(item.why_it_matters)}</p></div>` : "");
    blocks.push(detailList("典型研究任务", item.typical_tasks));
    blocks.push(detailList("需要先掌握", item.prerequisites));
    blocks.push(detailList("常用技术", item.common_techniques, papers));
    blocks.push(detailList("数据集与基准", item.datasets_and_benchmarks));
    blocks.push(detailList("常用评估指标", item.evaluation_metrics));
    blocks.push(item.starter_project ? `<div class="detail-block"><h3>第一个可做项目</h3><p class="detail-summary">${escapeHtml(item.starter_project)}</p></div>` : "");
    blocks.push(detailList("建议研究流程", item.research_workflow));
    blocks.push(detailList("可继续追问", item.research_questions));
    blocks.push(detailList("关联阶段", namesForStages(item.related_stage_ids, data)));
    paperIds = [
      ...(item.related_paper_ids || []),
      ...(item.common_techniques || []).flatMap(detailPaperIds),
    ];
  } else if (kind === "learning") {
    const item = data.learning_path?.[Number(id)];
    if (!item) return;
    title = item.goal || `学习步骤 ${item.step}`;
    subtitle = weekLabel(item);
    summary = item.expected_outcome;
    blocks.push(detailList("学习主题", item.topics));
    blocks.push(detailList("实践活动", item.activities));
    blocks.push(detailList("交付物", item.deliverables));
    blocks.push(detailList("完成标准", item.completion_criteria));
    blocks.push(detailList("复现检查", item.reproducibility_checklist));
    blocks.push(detailList("评估指标", item.evaluation_metrics));
    paperIds = item.paper_ids || (item.papers || []).map((paper) => paper.paper_id);
  } else if (kind === "paper") {
    const paper = papers.get(String(id));
    if (!paper) return;
    renderPaperDetail(paper);
    return;
  } else if (kind === "graph-node") {
    const node = data.knowledge_graph?.nodes?.find((value) => String(value.node_id) === id);
    if (!node) return;
    title = node.label;
    subtitle = `知识图谱 · ${node.node_type}`;
    summary = `来源路径：${node.source_path}`;
    if (node.paper_id) paperIds = [node.paper_id];
    const connected = (data.knowledge_graph.edges || [])
      .filter((edge) => edge.source_id === id || edge.target_id === id)
      .map((edge) => `${edge.source_id} — ${edge.edge_type} → ${edge.target_id}`);
    blocks.push(detailList("关联关系", connected));
  }

  $("inspector-title").textContent = title;
  $("inspector-subtitle").textContent = subtitle;
  const relatedPapers = [...new Set(paperIds)].map((paperId) => papers.get(String(paperId))).filter(Boolean);
  $("inspector-content").innerHTML = `
    <div class="detail-stack">
      ${summary ? `<div class="detail-summary">${escapeHtml(summary)}</div>` : ""}
      ${blocks.filter(Boolean).join("")}
      ${relatedPapers.length ? `
        <div class="detail-block">
          <h3>关联论文 · ${relatedPapers.length}</h3>
          <div class="detail-stack">${relatedPapers.map(paperMini).join("")}</div>
        </div>
      ` : ""}
    </div>
  `;
}

function renderPaperDetail(paper) {
  const guidance = paperGuidance(paper);
  const contribution = paper.contribution || guidance.contribution;
  const readingFocus = (paper.reading_focus || []).length
    ? paper.reading_focus
    : guidance.reading_focus;
  const scoreBreakdown = paper.score_breakdown;
  const explainableScore = paper.score_version === "paper-score-v2"
    && scoreBreakdown
    && typeof scoreBreakdown === "object";
  const scoreRows = explainableScore
    ? [
        ["综合推荐度", paper.final_score],
        ["主题相关 · 65%", scoreBreakdown.topic_relevance],
        ["检索覆盖 · 15%", scoreBreakdown.query_coverage],
        ["时效性 · 10%", scoreBreakdown.recency],
        ["信息完整度 · 10%", scoreBreakdown.metadata_completeness],
      ]
    : [["旧版综合推荐度", paper.final_score]];
  const scoreNote = explainableScore
    ? "总分由下列四项严格加权计算；引用数仅作元数据展示，不参与推荐度"
    : "历史任务使用旧版评分口径；重新生成后可查看完整分项";
  const citationKnown = paper.citation_count != null
    && Number.isFinite(Number(paper.citation_count));
  const metadata = [
    paper.source ? `来源：${paper.source}` : "",
    citationKnown
      ? `引用数：${Number(paper.citation_count)}`
      : "引用数：暂未获取",
    paper.doi ? `DOI：${paper.doi}` : "",
    paper.arxiv_id ? `arXiv：${paper.arxiv_id}` : "",
    (paper.publication_types || []).length ? `类型：${paper.publication_types.join("、")}` : "",
  ].filter(Boolean);
  const pdfUrl = paperPdfUrl(paper);
  state.selected = { kind: "paper", id: String(paper.paper_id) };
  $("inspector-title").textContent = paper.title || "论文详情";
  $("inspector-subtitle").textContent = `${paper.year || "年份未知"} · ${paper.paper_role || "论文"}`;
  $("inspector-content").innerHTML = `
    <div class="detail-stack">
      <div class="chip-row">
        <span class="chip">${escapeHtml(PRIORITY_LABELS[paper.reading_priority] || paper.reading_priority || "论文")}</span>
        ${paper.is_canonical ? '<span class="chip">Canonical</span>' : ""}
      </div>
      <div class="detail-summary">${escapeHtml(paper.abstract || "暂无摘要。")}</div>
      <div class="detail-block">
        <h3>作者</h3>
        <p class="detail-summary">${escapeHtml((paper.authors || []).join("、") || "作者信息暂无")}</p>
      </div>
      ${metadata.length ? detailList("论文元数据", metadata) : ""}
      <div class="detail-block">
        <h3>推荐依据 <span class="score-note">${escapeHtml(scoreNote)}</span></h3>
        <div class="paper-score-grid">
          ${scoreRows.map(([label, value]) => `
            <div class="paper-score-row">
              <span>${escapeHtml(label)}</span>
              <span class="mini-track"><span style="width:${formatPercentScore(value)}%"></span></span>
              <b>${formatPercentScore(value)}</b>
            </div>
          `).join("")}
        </div>
      </div>
      <div class="detail-block">
        <h3>主要贡献</h3>
        <p class="detail-summary">${escapeHtml(contribution || fieldStatusCopy("贡献说明"))}</p>
      </div>
      ${readingFocus.length ? detailList("阅读重点", readingFocus) : `<div class="detail-block"><h3>阅读重点</h3><p class="detail-summary">${escapeHtml(fieldStatusCopy("阅读重点"))}</p></div>`}
      ${pdfUrl ? `<a class="source-link" href="${escapeHtml(pdfUrl)}" target="_blank" rel="noreferrer">查看 PDF 原文 ↗</a>` : ""}
      <button class="detail-action" type="button" data-add-paper-library="${escapeHtml(paper.paper_id)}">加入论文管理</button>
      <button class="detail-action" type="button" data-import-paper="${escapeHtml(paper.paper_id)}" ${pdfUrl ? "" : "disabled"}>
        ${pdfUrl ? "下载并开始论文精读" : "暂未找到 PDF"}
      </button>
    </div>
  `;
}

async function addPaperToLibrary(paperId, button) {
  const paper = (currentData()?.papers || []).find((item) => String(item.paper_id) === String(paperId));
  if (!paper) return;
  button.disabled = true;
  try {
    if (paperPdfUrl(paper)) {
      await downloadDomainPaper(paper);
      button.textContent = "已下载并加入论文管理";
      toast("PDF 已保存到论文管理。可稍后从资料库开始精读。");
      return;
    }
    const response = await fetch(`/api/research/papers/${encodeURIComponent(paperId)}/library`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reading_status: "unread", note: "" }),
    });
    if (!response.ok) throw new Error(await response.text());
    button.textContent = "已加入论文管理";
    toast("论文信息已加入资料库；当前来源没有可下载的 PDF。")
  } catch (error) {
    button.disabled = false;
    toast(`加入论文库失败：${error.message}`, true);
  }
}

async function importPaper(paperId) {
  const paper = (currentData()?.papers || []).find((item) => String(item.paper_id) === String(paperId));
  if (!paper) return;
  try {
    toast("正在下载 PDF 并导入论文精读…");
    const importedId = await downloadDomainPaper(paper);
    localStorage.setItem("paper_reading_paper_id", importedId);
    localStorage.removeItem("paper_reading_session_id");
    window.location.href = "/app/paper-reading";
  } catch (error) {
    toast(`论文导入失败：${error.message}`, true);
  }
}

function paperPdfUrl(paper) {
  const explicit = String(paper?.pdf_url || "").trim();
  if (explicit) return explicit;
  const arxivId = String(paper?.arxiv_id || "").trim();
  if (arxivId) return `https://arxiv.org/pdf/${encodeURIComponent(arxivId)}.pdf`;
  const source = String(paper?.url || "").trim();
  const arxivMatch = source.match(/arxiv\.org\/(?:abs|pdf)\/(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?/i);
  if (arxivMatch) return `https://arxiv.org/pdf/${encodeURIComponent(arxivMatch[1])}.pdf`;
  return /\.pdf(?:$|[?#])/i.test(source) ? source : "";
}

async function downloadDomainPaper(paper) {
  const pdfUrl = paperPdfUrl(paper);
  if (!pdfUrl) throw new Error("当前论文没有可下载的 PDF 地址。");
  const response = await fetch("/paper_reading", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      action: "upload_paper",
      session_id: "",
      paper_id: "",
      content: "",
      pdf_url: pdfUrl,
      metadata: {
        source: "domain_onboarding",
        domain: currentData()?.domain || "",
        source_paper_id: paper.paper_id,
        title: paper.title || "",
        authors: paper.authors || [],
        abstract: paper.abstract || "",
        year: paper.year || null,
        source_url: paper.url || pdfUrl,
      },
    }),
  });
  const envelope = await readJson(response);
  let payload = envelope?.content ?? envelope;
  if (typeof payload === "string") payload = JSON.parse(payload);
  if (!response.ok || payload?.status === "error") {
    throw new Error(readError(payload, `导入失败（HTTP ${response.status}）`));
  }
  const importedId = payload?.data?.paper_id;
  if (!importedId) throw new Error("导入响应缺少 paper_id。");
  return importedId;
}

async function cancelTask() {
  if (!state.taskId || TERMINAL_STATES.has(state.snapshot?.state)) return;
  $("cancel-button").disabled = true;
  try {
    const response = await fetch(`/domain_onboarding/jobs/${encodeURIComponent(state.taskId)}`, {
      method: "DELETE",
      headers: jobAuthHeaders(),
    });
    const payload = await readJson(response);
    if (!response.ok) throw new Error(readError(payload, "取消失败"));
    state.snapshot = { ...(state.snapshot || {}), state: payload.state || "cancel_requested" };
    renderStatus();
    startPolling();
  } catch (error) {
    toast(error.message, true);
    $("cancel-button").disabled = false;
  }
}

async function retryTask() {
  const request = state.snapshot?.request || readWorkspace()?.request;
  if (!state.taskId) {
    window.location.href = "/app?mode=domain_onboarding";
    return;
  }
  setRetryButtonsDisabled(true);
  try {
    const response = await fetch(
      `/domain_onboarding/jobs/${encodeURIComponent(state.taskId)}/retry`,
      { method: "POST", headers: jobAuthHeaders({ Accept: "application/json" }) },
    );
    const job = await readJson(response);
    if (!response.ok || !job?.task_id) {
      throw new Error(readError(job, `重试任务失败（HTTP ${response.status}）`));
    }
    closeLiveUpdates();
    state.taskId = job.task_id;
    state.accessToken = job.access_token || "";
    state.snapshot = { ...job, progress: 0, request };
    state.partial = {};
    state.result = null;
    state.revision = 0;
    state.lastEventId = 0;
    state.activeLLMStage = "";
    state.selected = null;
    history.replaceState(null, "", `/app/domain-onboarding?task_id=${encodeURIComponent(state.taskId)}`);
    $("empty-state").hidden = true;
    renderStatus();
    saveWorkspace();
    await refreshSnapshot();
    connectEvents();
  } catch (error) {
    toast(error.message, true);
    setRetryButtonsDisabled(false);
  }
}

function setRetryButtonsDisabled(disabled) {
  ["retry-button", "topbar-retry-button"].forEach((id) => {
    $(id).disabled = disabled;
  });
}

function currentData() {
  const partial = state.partial && typeof state.partial === "object" ? state.partial : {};
  const result = state.result && typeof state.result === "object" ? state.result : {};
  if (!Object.keys(partial).length && !Object.keys(result).length) return null;
  return { ...partial, ...result };
}

function isTerminalTask() {
  return TERMINAL_STATES.has(state.snapshot?.state);
}

function sectionStatusCopy(label, data) {
  if (!isTerminalTask()) return emptyCopy(`${label}待生成`);
  return `<div class="empty-copy">
    ${escapeHtml(label)}内容待完善
    <div class="empty-action">点击下方重试按钮生成更优内容</div>
  </div>`;
}

function fieldStatusCopy(label) {
  return `${label}${isTerminalTask() ? "待完善" : "待生成"}`;
}

function paperGuidance(paper) {
  const data = currentData() || {};
  const references = [
    ...(data.development_stages || []).flatMap((stage) => stage.representative_papers || []),
    ...(data.learning_path || []).flatMap((step) => step.papers || []),
  ];
  const matches = references.filter((item) => String(item.paper_id) === String(paper.paper_id));
  return {
    contribution: matches.find((item) => item.contribution)?.contribution || "",
    reading_focus: [...new Set(matches.flatMap((item) => item.reading_focus || []))],
  };
}

function saveWorkspace() {
  const request = state.snapshot?.request || readWorkspace()?.request || null;
  localStorage.setItem(STORAGE_KEY, JSON.stringify({
    schema_version: "1.9",
    saved_at: new Date().toISOString(),
    task_id: state.taskId,
    access_token: state.accessToken,
    request,
    snapshot: {
      ...(state.snapshot || {}),
      partial_result: state.partial,
      result: state.result,
    },
  }));
}

function jobAuthHeaders(headers = {}) {
  return state.accessToken
    ? { ...headers, Authorization: `Bearer ${state.accessToken}` }
    : { ...headers };
}

function readWorkspace() {
  try {
    const current = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    if (current?.schema_version === "1.9") return current;
    for (const key of LEGACY_STORAGE_KEYS) {
      const legacy = JSON.parse(localStorage.getItem(key) || "null");
      if (!legacy?.task_id) continue;
      const migrated = { ...legacy, schema_version: "1.9", migrated_from: legacy.schema_version || "unknown" };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(migrated));
      return migrated;
    }
    return null;
  } catch {
    return null;
  }
}

function showEmpty(title, message, retryable) {
  $("empty-title").textContent = title;
  $("empty-message").textContent = message;
  $("retry-button").hidden = !retryable;
  $("retry-button").disabled = false;
  $("empty-state").hidden = false;
}

function toast(message, isError = false) {
  const item = document.createElement("div");
  item.className = `toast${isError ? " error" : ""}`;
  item.textContent = message;
  $("toast-region").appendChild(item);
  window.setTimeout(() => item.remove(), 4200);
}

async function readJson(response) {
  try {
    return await response.json();
  } catch {
    throw new Error(`后端返回了无法解析的响应（HTTP ${response.status}）`);
  }
}

function readError(payload, fallback) {
  if (typeof payload?.detail === "string") return payload.detail;
  if (Array.isArray(payload?.detail)) return payload.detail.map((item) => item.msg || JSON.stringify(item)).join("；");
  return payload?.message || payload?.error || fallback;
}

function profileItem(label, value) {
  return `<div class="profile-item"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value || "待解析")}</strong></div>`;
}

function chip(value) {
  return `<span class="chip">${escapeHtml(detailName(value))}</span>`;
}

function emptyCopy(message) {
  return `<div class="empty-copy">${escapeHtml(message)}</div>`;
}

function detailList(title, values, paperMap = null) {
  const items = (values || []).filter(Boolean);
  if (!items.length) return "";
  return `<div class="detail-block"><h3>${escapeHtml(title)}</h3><ul>${items.map((item) => {
    const description = detailDescription(item);
    const evidence = paperMap
      ? detailPaperIds(item)
        .map((paperId) => paperMap.get(String(paperId))?.title || paperId)
        .filter(Boolean)
      : [];
    return `<li><strong>${escapeHtml(detailName(item))}</strong>${description ? `<p>${escapeHtml(description)}</p>` : ""}${evidence.length ? `<small class="detail-evidence">论文依据 · ${evidence.map(escapeHtml).join("；")}</small>` : ""}</li>`;
  }).join("")}</ul></div>`;
}

function detailName(value) {
  if (value && typeof value === "object") return value.name || value.title || value.label || "未命名条目";
  return String(value || "");
}

function detailDescription(value) {
  if (!value || typeof value !== "object") return "";
  return [value.explanation || value.description, value.mechanism, value.why_it_matters]
    .filter(Boolean)
    .join(" · ");
}

function detailPaperIds(value) {
  return value && typeof value === "object" && Array.isArray(value.related_paper_ids)
    ? value.related_paper_ids
    : [];
}

function paperIndex(data) {
  return new Map(
    [
      ...(data?.evidence_papers || []),
      ...(data?.papers || []),
    ].map((paper) => [String(paper.paper_id), paper]),
  );
}

function paperMini(paper) {
  return `
    <button class="paper-mini" type="button" data-paper-id="${escapeHtml(paper.paper_id)}">
      <strong>${escapeHtml(paper.title)}</strong>
      <span>${escapeHtml((paper.authors || []).slice(0, 2).join("、") || "作者未知")} · ${escapeHtml(paper.year || "年份未知")}</span>
    </button>
  `;
}

function namesForStages(ids, data) {
  const wanted = new Set(ids || []);
  return (data.development_stages || []).filter((item) => wanted.has(item.stage_id)).map((item) => item.name);
}

function namesForSubdirections(ids, data) {
  const wanted = new Set(ids || []);
  return (data.current_landscape?.subdirection_details || [])
    .filter((item) => wanted.has(item.subdirection_id))
    .map((item) => item.name);
}

function weekLabel(item) {
  const start = item.start_week;
  const end = item.end_week || start;
  if (!start) return "标准学习步骤";
  return start === end ? `第 ${start} 周` : `第 ${start}–${end} 周`;
}

function formatPercentScore(value) {
  const score = Number(value);
  if (!Number.isFinite(score)) return 0;
  return Math.max(0, Math.min(100, Math.round(score <= 1 ? score * 100 : score)));
}

function escapeHtml(value) {
  const span = document.createElement("span");
  span.textContent = String(value ?? "");
  return span.innerHTML;
}

function getSessionId() {
  const key = "novicesynapse_session_id";
  const existing = localStorage.getItem(key);
  if (existing) return existing;
  const created = `web-${crypto.randomUUID()}`;
  localStorage.setItem(key, created);
  return created;
}
