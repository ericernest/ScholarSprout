const STORAGE_KEY = "domain_onboarding_workspace_v1_5";
const TERMINAL_STATES = new Set(["completed", "failed", "cancelled", "interrupted"]);
const EVENT_NAMES = [
  "accepted",
  "profile_ready",
  "plan_ready",
  "papers_ready",
  "development_ready",
  "landscape_ready",
  "learning_path_ready",
  "quality_ready",
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
const STAGE_LABELS = {
  accepted: "任务已接收",
  profile_ready: "学习者画像已完成",
  plan_ready: "调研计划已完成",
  papers_ready: "论文检索已完成",
  development_ready: "发展脉络已完成",
  landscape_ready: "概念全景已完成",
  learning_path_ready: "学习路线已完成",
  quality_ready: "质量评估已完成",
  repair_started: "正在修复质量问题",
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
const QUALITY_LABELS = {
  structure: "结构完整性",
  paper_validity: "论文真实性",
  paper_relevance: "论文相关性",
  evidence_grounding: "证据支撑",
  topic_coverage: "主题覆盖",
  development_coherence: "发展连贯性",
  learning_path: "路线可执行性",
  goal_alignment: "目标匹配",
  language_alignment: "语言一致性",
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

  if (requestedTaskId) {
    state.taskId = requestedTaskId;
    if (saved?.task_id === requestedTaskId) {
      state.accessToken = saved.access_token || saved.snapshot?.access_token || "";
      consumeSnapshot(saved.snapshot, false);
    }
  } else if (query) {
    try {
      const job = await submitTask({
        query,
        session_id: getSessionId(),
        user_id: "local-web",
        metadata: {},
      });
      state.taskId = job.task_id;
      state.accessToken = job.access_token || "";
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

  try {
    await refreshSnapshot();
  } catch (error) {
    if (!state.snapshot) {
      showEmpty("无法读取任务", error.message, false);
      return;
    }
    toast("暂时无法连接后端，已展示浏览器中的最近快照。", true);
  }

  if (state.snapshot && !TERMINAL_STATES.has(state.snapshot.state)) {
    connectEvents();
  }
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
  const response = await fetch("/domain_onboarding/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...request,
      client_request_id: crypto.randomUUID(),
    }),
  });
  const payload = await readJson(response);
  if (!response.ok || !payload?.task_id) {
    throw new Error(readError(payload, `创建任务失败（HTTP ${response.status}）`));
  }
  return payload;
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
    current_stage: payload.event,
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
  renderQuality(data);
  renderGraph(data);
  if (state.selected) showDetail(state.selected.kind, state.selected.id);
}

function renderStatus() {
  const snapshot = state.snapshot || {};
  const progress = Math.max(0, Math.min(1, Number(snapshot.progress) || 0));
  const terminal = TERMINAL_STATES.has(snapshot.state);
  const status = terminal
    ? STATUS_LABELS[snapshot.state]
    : STAGE_LABELS[snapshot.current_stage] || STATUS_LABELS[snapshot.state] || "正在分析任务";
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
  const profile = data.learner_profile || {};
  const qualityState = data.quality?.state || data.status || "";
  const qualityClass = qualityState === "failed" || data.status === "quality_failed"
    ? "error"
    : qualityState === "warning" || data.status === "quality_warning"
      ? "warning"
      : "";
  const tags = [
    data.schema_version ? `<span class="tag">${escapeHtml(data.schema_version)}</span>` : "",
    qualityState ? `<span class="tag ${qualityClass}">${escapeHtml(qualityStatusLabel(qualityState))}</span>` : "",
  ].join("");
  $("overview-content").classList.remove("loading-section");
  $("overview-content").innerHTML = `
    <div class="hero-kicker">${tags}</div>
    <h2 class="hero-title">${escapeHtml(data.domain || data.query || state.snapshot?.request?.query || "正在理解你的学习目标")}</h2>
    <p class="hero-copy">${escapeHtml(data.text || "正在检索论文并构建领域发展脉络，已完成的内容会自动出现在下方。")}</p>
    <div class="profile-strip">
      ${profileItem("已有基础", arrayText(profile.background || profile.known_concepts) || "待解析")}
      ${profileItem("学习目标", profile.goal || state.snapshot?.request?.query || "待解析")}
      ${profileItem("时间预算", profile.time_budget_weeks ? `${profile.time_budget_weeks} 周` : "待解析")}
      ${profileItem("路线偏好", preferenceLabel(profile.preference))}
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
    container.innerHTML = sectionStatusCopy("发展路径", data);
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
  const problems = landscape.problem_details || (landscape.problems || []).map((name, index) => ({
    problem_id: `problem-${index}`,
    name,
  }));
  const directions = landscape.subdirection_details || (landscape.subdirections || []).map((name, index) => ({
    subdirection_id: landscape.subdirection_ids?.[name] || `sub-${index}`,
    name,
  }));
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
            </span>
            <span class="branch-action">展开分支 ↗</span>
          </button>
        `).join("") : emptyCopy("暂无结构化子方向。")}
      </div>
    </div>
  `;
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
      const start = step.start_week || index + 1;
      const end = step.end_week || start;
      const week = start === end ? `W${start}` : `W${start}–${end}`;
      const hasTimeBudget = data.learner_profile?.time_budget_weeks;
      const timeCopy = step.estimated_hours
        ? `${step.estimated_hours} 小时`
        : hasTimeBudget
          ? fieldStatusCopy("时间")
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
  const papers = state.paperFilter === "all"
    ? data.papers
    : data.papers.filter((paper) => paper.reading_priority === state.paperFilter);
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

function renderQuality(data) {
  const quality = data.quality;
  if (!quality || typeof quality !== "object") {
    const container = $("quality-content");
    container.classList.remove("loading-grid");
    container.innerHTML = sectionStatusCopy("质量评估", data);
    return;
  }
  const dimensions = Object.entries(quality.dimensions || {});
  const gates = quality.hard_gates || [];
  const issues = quality.issues || [];
  const stateClass = quality.state === "failed" ? "error" : quality.state === "warning" ? "warning" : "";
  const container = $("quality-content");
  container.classList.remove("loading-grid");
  container.innerHTML = `
    <div class="quality-summary">
      <div class="score-card">
        <b>${Math.round((Number(quality.score) || 0) * 100)}</b>
        <span>质量分 / 100</span>
        <span class="tag ${stateClass}">${escapeHtml(qualityStatusLabel(quality.state))}</span>
      </div>
      <div class="dimension-list">
        ${dimensions.map(([name, score]) => `
          <div class="dimension-row">
            <span>${escapeHtml(QUALITY_LABELS[name] || name)}</span>
            <span class="mini-track"><span style="width:${Math.round(Number(score) * 100)}%"></span></span>
            <b>${Math.round(Number(score) * 100)}</b>
          </div>
        `).join("") || emptyCopy("暂无维度评分。")}
      </div>
    </div>
    <div class="quality-detail-grid">
      <div class="quality-box">
        <h3>硬门槛 · ${quality.passed_hard_gates ? "全部通过" : "存在失败"}</h3>
        <div class="gate-list">
          ${gates.map((gate) => `
            <div class="gate-row">
              <strong>${escapeHtml(gate.gate)}</strong>
              <span>${escapeHtml(gate.status)}${gate.score != null ? ` · ${formatScore(gate.score)} / ${formatScore(gate.threshold)}` : ""}</span>
            </div>
          `).join("") || "<div class=\"gate-row\">后端未返回硬门槛明细。</div>"}
        </div>
      </div>
      <div class="quality-box">
        <h3>质量问题 · ${issues.length}</h3>
        <div class="issue-list">
          ${issues.map((issue) => `
            <div class="issue-row">
              <strong>${escapeHtml(issue.message)}</strong>
              <span>${escapeHtml(issue.target_path)} · ${escapeHtml(issue.recommended_action)}</span>
            </div>
          `).join("") || "<div class=\"issue-row\">没有发现需要处理的问题。</div>"}
        </div>
      </div>
    </div>
  `;
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
  const papers = new Map((data.papers || []).map((paper) => [String(paper.paper_id), paper]));
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
    blocks.push(detailList("关键概念", item.key_points));
    paperIds = item.related_paper_ids || [];
  } else if (kind === "stage") {
    const item = (data.development_stages || []).find((value, index) => String(value.stage_id || index) === id);
    if (!item) return;
    title = item.name;
    subtitle = item.historical_period || item.period || "发展阶段";
    summary = item.summary || item.motivation;
    blocks.push(detailList("核心概念", item.core_concepts));
    blocks.push(detailList("主要技术", item.main_techniques));
    blocks.push(detailList("开放问题", item.open_problems));
    paperIds = [
      ...(item.related_paper_ids || []),
      ...(item.representative_papers || []).map((paper) => paper.paper_id),
    ];
  } else if (kind === "problem") {
    const item = (data.current_landscape?.problem_details || []).find((value, index) => String(value.problem_id || index) === id);
    if (!item) return;
    title = item.name;
    subtitle = "当前核心问题";
    summary = item.description;
    blocks.push(detailList("关联阶段", namesForStages(item.related_stage_ids || item.affected_stage_ids, data)));
    blocks.push(detailList("关联方向", namesForSubdirections(item.related_subdirection_ids, data)));
    paperIds = item.related_paper_ids || [];
  } else if (kind === "subdirection") {
    const item = (data.current_landscape?.subdirection_details || []).find((value, index) => String(value.subdirection_id || index) === id);
    if (!item) return;
    title = item.name;
    subtitle = "研究分支";
    summary = item.description || item.why_it_matters;
    blocks.push(item.why_it_matters ? `<div class="detail-block"><h3>为什么重要</h3><p class="detail-summary">${escapeHtml(item.why_it_matters)}</p></div>` : "");
    blocks.push(detailList("可继续追问", item.research_questions));
    blocks.push(detailList("关联阶段", namesForStages(item.related_stage_ids, data)));
    paperIds = item.related_paper_ids || [];
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
  const scoreRows = [
    ["综合推荐度", paper.final_score],
    ["主题相关", paper.relevance_score],
    ["领域语境", paper.context_score],
    ["引用影响", paper.citation_score],
    ["时效性", paper.recency_score],
    ["内容差异性", paper.diversity_score],
  ].filter(([, value]) => Number.isFinite(Number(value)));
  const metadata = [
    paper.source ? `来源：${paper.source}` : "",
    paper.citation_count != null && Number.isFinite(Number(paper.citation_count))
      ? `引用数：${Number(paper.citation_count)}`
      : "",
    paper.doi ? `DOI：${paper.doi}` : "",
    paper.arxiv_id ? `arXiv：${paper.arxiv_id}` : "",
    (paper.publication_types || []).length ? `类型：${paper.publication_types.join("、")}` : "",
  ].filter(Boolean);
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
        <h3>推荐依据 <span class="score-note">归一化信号 · 非论文质量绝对分</span></h3>
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
      ${paper.url ? `<a class="source-link" href="${escapeHtml(paper.url)}" target="_blank" rel="noreferrer">查看论文来源 ↗</a>` : ""}
      <button class="detail-action" type="button" data-import-paper="${escapeHtml(paper.paper_id)}">
        ${paper.arxiv_id ? "导入论文精读" : "打开论文来源"}
      </button>
    </div>
  `;
}

async function importPaper(paperId) {
  const paper = (currentData()?.papers || []).find((item) => String(item.paper_id) === String(paperId));
  if (!paper) return;
  if (!paper.arxiv_id) {
    if (paper.url) window.open(paper.url, "_blank", "noopener,noreferrer");
    return;
  }
  try {
    toast("正在导入论文精读…");
    const response = await fetch("/paper_reading", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action: "upload_paper",
        session_id: "",
        paper_id: "",
        content: "",
        pdf_url: `https://arxiv.org/pdf/${encodeURIComponent(paper.arxiv_id)}.pdf`,
        metadata: {
          source: "domain_onboarding",
          domain: currentData()?.domain || "",
          source_paper_id: paper.paper_id,
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
    localStorage.setItem("paper_reading_paper_id", importedId);
    localStorage.removeItem("paper_reading_session_id");
    window.location.href = "/app/paper-reading";
  } catch (error) {
    toast(`论文导入失败：${error.message}`, true);
  }
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
    schema_version: "1.5",
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
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    return value?.schema_version === "1.5" ? value : null;
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
  return `<span class="chip">${escapeHtml(value)}</span>`;
}

function emptyCopy(message) {
  return `<div class="empty-copy">${escapeHtml(message)}</div>`;
}

function detailList(title, values) {
  const items = (values || []).filter(Boolean);
  if (!items.length) return "";
  return `<div class="detail-block"><h3>${escapeHtml(title)}</h3><ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>`;
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
  if (!start) return "个性化学习步骤";
  return start === end ? `第 ${start} 周` : `第 ${start}–${end} 周`;
}

function arrayText(values) {
  return Array.isArray(values) ? values.filter(Boolean).join("、") : String(values || "");
}

function preferenceLabel(value) {
  return {
    balanced: "理论与实践平衡",
    theory_first: "理论优先",
    experiment_first: "实验优先",
  }[value] || value || "平衡路线";
}

function qualityStatusLabel(value) {
  return {
    passed: "质量通过",
    warning: "建议复核",
    failed: "硬门槛未通过",
    ok: "质量通过",
    quality_warning: "建议复核",
    quality_failed: "硬门槛未通过",
  }[value] || value || "质量检查中";
}

function formatScore(value) {
  const score = Number(value);
  return Number.isFinite(score) ? score.toFixed(2) : "—";
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
