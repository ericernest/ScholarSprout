(function () {
  const params = new URLSearchParams(window.location.search);
  const active = params.get("tutorial") === "1";
  const nativeFetch = window.fetch.bind(window);
  const stepKey = "seefurther_real_tour_step_v2";
  const surveyTitle = "A Survey on Large Language Model based Autonomous Agents";

  const demoPaper = {
    paper_id: "tutorial-agent-survey",
    title: surveyTitle,
    paper_title: surveyTitle,
    authors: ["Lei Wang", "Chen Ma", "Xiaoming Zhang"],
    year: 2024,
    venue: "Frontiers of Computer Science",
    abstract: "这篇综述系统梳理了基于大语言模型的自主智能体，包括构建方式、应用场景、评测与开放问题。",
    source: "tutorial",
    source_url: "https://arxiv.org/abs/2308.11432",
    arxiv_id: "2308.11432",
    pdf_url: "https://arxiv.org/pdf/2308.11432.pdf",
    citation_count: 1800,
    paper_role: "survey",
    recommendation_category: "influential_survey",
    reading_priority: "core",
    in_library: true,
    has_document: true,
    reading_count: 0,
    reading_status: "unread",
    folder_id: "tutorial-folder",
    folder_name: "智能体研究",
    folder_path: "智能体研究",
    updated_at: "2026-08-28T00:00:00Z"
  };

  const demoDomainResult = {
    domain: "智能体 Agent",
    text: "从智能体的感知、规划、记忆、工具使用与多智能体协作出发，建立可继续扩展的领域知识框架。",
    prerequisites: [
      { prerequisite_id: "p1", name: "大语言模型基础", why_needed: "理解智能体的推理与生成核心。", key_points: ["Transformer", "上下文学习", "指令遵循"] },
      { prerequisite_id: "p2", name: "工具调用与环境交互", why_needed: "理解智能体如何把决策转化为行动。", key_points: ["Function Calling", "状态反馈", "任务闭环"] }
    ],
    development_stages: [
      { stage_id: "s1", sequence: 1, historical_period: "2022 以前", name: "任务型智能体", summary: "围绕明确流程与工具链完成单一任务。", core_concepts: ["任务规划", "环境反馈"] },
      { stage_id: "s2", sequence: 2, historical_period: "2023—2024", name: "LLM 自主智能体", summary: "语言模型开始统一承担规划、记忆与工具选择。", transition_from_previous: "通用语言模型提升了开放任务中的迁移能力。", core_concepts: ["ReAct", "长期记忆", "工具使用"] },
      { stage_id: "s3", sequence: 3, historical_period: "2024 至今", name: "多智能体与可验证执行", summary: "研究重点转向协作、评测、安全和长期可靠运行。", transition_from_previous: "复杂任务暴露出单智能体的上下文与可靠性边界。", core_concepts: ["协作协议", "验证反馈", "Agent Harness"] }
    ],
    current_landscape: {
      problem_details: [
        { problem_id: "problem-1", name: "长期任务可靠性", description: "智能体在长链路执行中容易累积错误。" },
        { problem_id: "problem-2", name: "可验证评测", description: "需要可复现地衡量规划、工具调用与恢复能力。" }
      ],
      subdirection_details: [
        { subdirection_id: "direction-1", name: "记忆与上下文工程", description: "管理跨步骤、跨会话的有效信息。", typical_tasks: ["记忆检索", "上下文压缩"] },
        { subdirection_id: "direction-2", name: "多智能体协作", description: "通过角色分工和验证反馈完成复杂任务。", typical_tasks: ["任务分解", "交叉验证"] }
      ]
    },
    learning_path: [
      { step: "1", goal: "理解智能体基本循环", expected_outcome: "能够解释感知、规划、行动与反馈。", estimated_hours: 4, paper_ids: [demoPaper.paper_id] },
      { step: "2", goal: "复现一个工具调用智能体", expected_outcome: "完成一次可观察的端到端任务。", estimated_hours: 8, paper_ids: [demoPaper.paper_id] }
    ],
    papers: [demoPaper]
  };

  const demoDomainSnapshot = {
    task_id: "tutorial-domain",
    state: "completed",
    current_stage: "completed",
    progress: 1,
    request: { query: "智能体 Agent" },
    result: demoDomainResult
  };

  const demoReadingPaper = {
    paper_id: demoPaper.paper_id,
    title: demoPaper.title,
    authors: demoPaper.authors,
    year: demoPaper.year,
    venue: demoPaper.venue,
    abstract: demoPaper.abstract,
    source: "tutorial",
    parse_status: "completed",
    sections: [
      { section_id: "sec:introduction", title: "1 Introduction", level: 1, start_page: 1, content: "智能体以大语言模型为核心完成规划、记忆、工具使用和环境交互。" },
      { section_id: "sec:construction", title: "2 Agent Construction", level: 1, start_page: 5, content: "智能体构建包含画像、记忆、规划和行动模块。" },
      { section_id: "sec:applications", title: "3 Applications", level: 1, start_page: 12, content: "智能体已用于科研、软件工程与复杂决策。" },
      { section_id: "sec:challenges", title: "4 Challenges and Future Directions", level: 1, start_page: 18, content: "开放问题包括长期可靠性、评测、安全与多智能体协作。" }
    ],
    reading_map: {
      status: "llm_done",
      map_variant: "research",
      research_problem: { title: "研究问题", question: "如何构建能够持续规划、调用工具并从反馈中恢复的通用智能体？", source_sections: [{ section_id: "sec:introduction", page: 1 }] },
      core_method: { title: "核心框架", one_sentence: "以 LLM 为大脑，连接记忆、规划、工具与环境反馈。", source_sections: [{ section_id: "sec:construction", page: 5 }] },
      method_steps: [
        { title: "规划与行动", goal: "拆解任务", operation: "生成计划并选择工具", output: "可执行动作", source_sections: [{ section_id: "sec:construction", page: 6 }] },
        { title: "反馈与修正", goal: "提高可靠性", operation: "观察结果并调整后续步骤", output: "更新后的计划", source_sections: [{ section_id: "sec:construction", page: 8 }] }
      ],
      experimental_support: [{ title: "案例与基准", summary: "论文比较了多个智能体系统的任务类型与评测方式。", source_sections: [{ section_id: "sec:applications", page: 12 }] }],
      limitations_and_questions: [{ title: "未来方向", summary: "长期可靠性、可信评测、安全边界与多智能体协作仍是关键问题。", source_sections: [{ section_id: "sec:challenges", page: 18 }] }],
      section_guides: [
        { section_id: "sec:introduction", cards: [{ title: "为什么需要智能体", summary: "语言模型需要通过规划与工具从回答问题走向完成任务。" }, { title: "阅读重点", summary: "关注智能体与普通对话模型的能力边界。" }] },
        { section_id: "sec:construction", cards: [{ title: "模块结构", summary: "画像、记忆、规划和行动共同构成智能体主循环。" }, { title: "关键联系", summary: "每次行动都会把环境反馈带回下一轮规划。" }] },
        { section_id: "sec:challenges", cards: [{ title: "开放问题", summary: "长任务错误累积与缺少统一评测仍待解决。" }] }
      ]
    }
  };

  function jsonResponse(value, status) {
    return new Response(JSON.stringify(value), {
      status: status || 200,
      headers: { "Content-Type": "application/json" }
    });
  }

  function tutorialFetch(input, options) {
    const requestUrl = typeof input === "string" ? input : (input instanceof URL ? input.href : input.url);
    const url = new URL(requestUrl, window.location.origin);
    const method = String((options && options.method) || (typeof input !== "string" && input.method) || "GET").toUpperCase();
    if (url.pathname.indexOf("/api/tutorial/") === 0) return nativeFetch(input, options);
    if (url.pathname === "/api/config") {
      return Promise.resolve(jsonResponse({
        client: { base_url: "", model_name: "", api_key_configured: false },
        embedding: { base_url: "", model_name: "qwen3-embedding", uses_client_base_url: true, api_key_configured: false, uses_client_api_key: true },
        storage: { data_dir: "~/.novicesynapse", effective_data_dir: "~/.novicesynapse", environment_override: false },
        channels: { feishu: { enabled: false, app_id: "", app_secret_configured: false, environment_override: false } },
        setup_complete: true
      }));
    }
    if (url.pathname === "/api/research/summary") {
      return Promise.resolve(jsonResponse({ conversations: 1, domain_onboardings: 1, paper_readings: 0, papers: 1, library_papers: 1, unfiled_papers: 0 }));
    }
    if (url.pathname === "/api/research/paper-folders") {
      return Promise.resolve(jsonResponse([{ folder_id: "tutorial-folder", parent_folder_id: null, name: "智能体研究", path: "智能体研究", paper_count: 1 }]));
    }
    if (url.pathname === "/api/research/papers" && method === "GET") return Promise.resolve(jsonResponse([demoPaper]));
    if (url.pathname.indexOf("/api/research/papers/") === 0 && url.pathname.endsWith("/note")) {
      return Promise.resolve(jsonResponse({ paper_id: demoPaper.paper_id, paper_title: demoPaper.title, content_markdown: "", updated_at: null }));
    }
    if (url.pathname.indexOf("/api/research/papers/") === 0 && url.pathname.endsWith("/annotations")) return Promise.resolve(jsonResponse([]));
    if (url.pathname.indexOf("/api/research/") === 0) return Promise.resolve(jsonResponse(method === "GET" ? [] : { saved: true }));
    if (url.pathname.indexOf("/domain_onboarding/") === 0) return Promise.resolve(jsonResponse(demoDomainSnapshot));
    if (url.pathname === "/paper_reading" || url.pathname === "/paper_reading/stream") {
      return Promise.resolve(jsonResponse({ status: "ok", data: {}, session: {} }));
    }
    if (url.pathname.indexOf("/chat/") === 0) return Promise.resolve(jsonResponse({ status: "ok" }));
    return nativeFetch(input, options);
  }

  window.SeeFurtherTutorial = {
    active: active,
    demoDomainSnapshot: demoDomainSnapshot,
    demoReadingPaper: demoReadingPaper,
    nativeFetch: nativeFetch
  };
  if (!active) return;
  window.fetch = tutorialFetch;
  document.documentElement.dataset.seefurtherTutorial = "active";

  const steps = [
    { id: "paper-mode", page: "chat", route: "/app?tutorial=1", target: "#mode-button", title: "选择论文精读", copy: "先从研究模式中选择论文精读，单独导入一篇想读的论文。" },
    { id: "paper-upload", page: "chat", route: "/app?tutorial=1", target: "#paper-file-button", companion: "#paper-mode-input", title: "上传一篇论文", copy: "选择本地 PDF，也可以拖入 PDF，或粘贴 PDF、arXiv 链接。" },
    { id: "paper-submit", page: "chat", route: "/app?tutorial=1", target: "#send-button", title: "解析论文", copy: "点击解析论文后，研见会提取元数据、章节结构并生成精读任务。" },
    { id: "paper-card", page: "chat", route: "/app?tutorial=1", target: ".tour-paper-card", title: "进入论文精读", copy: "解析完成后，从论文卡片进入完整精读工作台。" },
    { id: "reading-index", page: "reading", route: "/app/paper-reading?tutorial=1", target: "#paper-outline", title: "智能索引", copy: "章节索引与 PDF 页码联动，点击章节即可定位原文。" },
    { id: "reading-map", page: "reading", route: "/app/paper-reading?tutorial=1", target: "#reading-map-panel", title: "研究总览", copy: "集中查看研究问题、核心方法、方法步骤、实验支撑与局限。" },
    { id: "reading-explain", page: "reading", route: "/app/paper-reading?tutorial=1", target: "[data-tour-anchor='reading-map-explain']", title: "智能体解释", copy: "从总览卡片发起解释时，会收起总览并进入右侧研究对话。" },
    { id: "reading-analyze", page: "reading", route: "/app/paper-reading?tutorial=1", target: "#analyze-section-button", title: "分析本节", copy: "围绕当前章节分析核心内容、论证结构和关键证据。" },
    { id: "reading-selection", page: "reading", route: "/app/paper-reading?tutorial=1", target: "#structured-reader .guide-card p", companion: "#selection-toolbar", title: "选区提问、高亮与注释", copy: "划选正文后，可以围绕选区提问、解释、深入探索，并添加高亮或注释。" },
    { id: "reading-note", page: "reading", route: "/app/paper-reading?tutorial=1", target: "#paper-note-button", companion: "#paper-note-drawer", title: "论文笔记", copy: "阅读时可以随时记录 Markdown 笔记，在普通编辑与源码编辑间切换，笔记会关联当前论文。" },
    { id: "domain-mode", page: "chat", route: "/app?tutorial=1&tutorial_phase=domain", target: "#mode-button", title: "选择领域入门", copy: "完成单篇精读介绍后，再从研究模式进入一个领域的系统学习。" },
    { id: "domain-query", page: "chat", route: "/app?tutorial=1&tutorial_phase=domain", target: "#message-input", title: "提出一个领域", copy: "示例领域：智能体 Agent。" },
    { id: "domain-submit", page: "chat", route: "/app?tutorial=1&tutorial_phase=domain", target: "#send-button", title: "生成领域地图", copy: "研见会梳理领域基础、发展路径、概念全景和核心论文。" },
    { id: "domain-card", page: "chat", route: "/app?tutorial=1&tutorial_phase=domain", target: ".tour-domain-card", title: "进入领域卡片", copy: "任务完成后，从卡片进入领域学习地图。" },
    { id: "domain-prerequisites", page: "domain", route: "/app/domain-onboarding?tutorial=1", target: "#prerequisites-content [data-detail-kind='prerequisite']", companion: ".inspector", title: "前置知识梳理", copy: "选择知识卡片，右侧同步展示关键概念与关联论文。" },
    { id: "domain-development", page: "domain", route: "/app/domain-onboarding?tutorial=1", target: "#development-content [data-detail-kind='stage'], #development-content [data-detail-kind='research-stage']", companion: ".inspector", title: "领域发展路径", copy: "选择发展阶段，右侧同步展示技术转折、核心概念与代表工作。" },
    { id: "domain-landscape", page: "domain", route: "/app/domain-onboarding?tutorial=1", target: "#landscape-content [data-detail-kind='problem']", companion: ".inspector", title: "概念全景", copy: "选择核心问题，右侧同步展开相关阶段、研究方向与论文。" },
    { id: "domain-papers", page: "domain", route: "/app/domain-onboarding?tutorial=1", target: "#papers", companion: ".inspector", title: "Survey 主导论文清单", copy: "论文清单由后端筛选规则生成，右侧可查看当前论文的完整信息。" },
    { id: "domain-paper", page: "domain", route: "/app/domain-onboarding?tutorial=1", target: ".paper-row", companion: ".inspector", title: "选择智能体综述", copy: "选择论文后，右侧会显示论文信息与继续操作。" },
    { id: "domain-start-reading", page: "domain", route: "/app/domain-onboarding?tutorial=1", target: "[data-import-paper]", title: "开始论文精读", copy: "除了单独上传论文，也可以从领域入门的论文详情下载 PDF，并从这里进入论文精读。" },
    { id: "library-folders", page: "library", route: "/library?view=papers&tutorial=1", target: "#folder-tree", title: "论文文件夹", copy: "使用真实资料库页面新建文件夹、移动论文并管理阅读状态。" },
    { id: "library-paper", page: "library", route: "/library?view=papers&tutorial=1", target: ".item-card", title: "论文管理", copy: "从论文卡片继续精读、查看笔记或调整所在文件夹。" },
    { id: "discussion-select", page: "return-chat", route: "/app?tutorial=1&tutorial_phase=return", target: "#discussion-context-button", title: "多选当前讨论", copy: "一个会话可以同时选择多篇论文和领域入门，研见会在这些范围内比较与综合。" },
    { id: "discussion-import", page: "return-chat", route: "/app?tutorial=1&tutorial_phase=return", target: "[data-import-contexts]", title: "引入会话外部结果", copy: "还可以打开研究结果选择页，搜索并多选其他会话中的论文精读或领域入门，再加入当前讨论。" },
    { id: "discussion-import-picker", page: "return-chat", route: "/app?tutorial=1&tutorial_phase=return", target: "#context-import-results", companion: "#context-import-modal", title: "搜索与多选", copy: "按论文精读或领域入门筛选，选中多个结果后点击“引入并选中”。" },
    { id: "discussion-query", page: "return-chat", route: "/app?tutorial=1&tutorial_phase=return", target: "#message-input", title: "围绕综述继续提问", copy: "示例问题：这个综述认为的未来可做的有哪些？" },
    { id: "discussion-answer", page: "return-chat", route: "/app?tutorial=1&tutorial_phase=return", target: "#send-button", title: "围绕当前讨论回答", copy: "研见会围绕选定综述中的开放问题继续讨论。" },
    { id: "settings", page: "settings", route: "/settings?tutorial=1", target: ".config-tabs", companion: "#settings-form", title: "配置模型与多渠道", copy: "最后配置模型、数据目录与飞书等消息渠道；密钥仅保存在本地后端。" }
  ];

  function pageName() {
    if (window.location.pathname === "/app/domain-onboarding") return "domain";
    if (window.location.pathname === "/library") return "library";
    if (window.location.pathname === "/app/paper-reading") return "reading";
    if (window.location.pathname === "/settings") return "settings";
    if (params.get("tutorial_phase") === "return") return "return-chat";
    return "chat";
  }

  function appendChatMessage(role, text, className) {
    const list = document.querySelector("#messages");
    if (!list || list.querySelector("." + className)) return;
    const item = document.createElement("article");
    item.className = "message " + role + " " + className;
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = text;
    item.appendChild(bubble);
    list.appendChild(item);
  }

  function prepare(stepIndex) {
    const step = steps[stepIndex];
    const input = document.querySelector("#message-input");
    if (pageName() === "chat") {
      if (step.id.indexOf("paper-") === 0) {
        if (typeof window.setMode === "function") window.setMode("paper_reading");
      }
      if (["paper-upload", "paper-submit", "paper-card"].includes(step.id)) {
        prepareTutorialPaperUpload();
      }
      if (step.id === "paper-card") appendTutorialPaperCard();
      if (step.id.indexOf("domain-") === 0) {
        if (typeof window.setMode === "function") window.setMode("domain_onboarding");
        if (input) input.value = "智能体 Agent";
      }
      if (step.id === "domain-card") {
        const list = document.querySelector("#messages");
        if (list && !list.querySelector(".tour-domain-card")) {
          const card = document.createElement("button");
          card.type = "button";
          card.className = "domain-task-card tour-domain-card";
          card.innerHTML = "<strong>智能体 Agent · 领域入门</strong><span>领域地图已完成</span><small>前置知识 · 发展路径 · 概念全景 · 论文清单</small>";
          list.appendChild(card);
        }
      }
    }
    if (pageName() === "domain") {
      const detailTargets = {
        "domain-prerequisites": "#prerequisites-content [data-detail-kind='prerequisite']",
        "domain-development": "#development-content [data-detail-kind='stage'], #development-content [data-detail-kind='research-stage']",
        "domain-landscape": "#landscape-content [data-detail-kind='problem']",
        "domain-papers": ".paper-row",
        "domain-paper": ".paper-row",
        "domain-start-reading": ".paper-row"
      };
      const detailSelector = detailTargets[step.id];
      const detailTarget = detailSelector ? document.querySelector(detailSelector) : null;
      if (detailTarget && typeof detailTarget.click === "function") detailTarget.click();
    }
    if (pageName() === "reading" && ["reading-map", "reading-explain"].includes(step.id)) {
      window.SeeFurtherTutorial?.openReadingMap?.();
    }
    if (step.id === "reading-selection") {
      selectTutorialReaderText();
    }
    if (pageName() === "reading" && step.id === "reading-note") {
      const drawer = document.querySelector("#paper-note-drawer");
      if (drawer?.getAttribute("aria-hidden") !== "false") {
        document.querySelector("#paper-note-button")?.click();
      }
    }
   if (pageName() === "return-chat") {
      const bar = document.querySelector("#discussion-context-bar");
      const value = document.querySelector("#discussion-context-value");
     if (bar) bar.hidden = false;
     if (value) value.textContent = surveyTitle;
     const menu = document.querySelector("#discussion-context-menu");
     if (menu && !menu.querySelector("[data-import-contexts]")) {
       menu.insertAdjacentHTML("beforeend", "<button type='button' class='discussion-context-import' data-import-contexts='1'><span>＋</span><span><strong>引入会话外部结果</strong><small>搜索已有论文精读与领域入门</small></span></button>");
     }
     if (step.id === "discussion-import") menu.hidden = false;
     if (step.id === "discussion-import-picker") {
       const modal = document.querySelector("#context-import-modal");
       const results = document.querySelector("#context-import-results");
       if (modal) modal.hidden = false;
       if (results) results.innerHTML = "<button class='context-import-card is-selected' type='button'><span class='context-import-card-kind'>论文精读</span><strong>智能体综述</strong><small>可引入当前会话</small><span class='context-import-card-check'>✓</span></button><button class='context-import-card is-selected' type='button'><span class='context-import-card-kind'>领域入门</span><strong>智能体 Agent</strong><small>可引入当前会话</small><span class='context-import-card-check'>✓</span></button>";
     }
      if (input) input.value = "这个综述认为的未来可做的有哪些？";
      if (step.id === "discussion-answer") {
        appendChatMessage("user", "这个综述认为的未来可做的有哪些？", "tour-future-question");
        appendChatMessage("assistant", "综述重点指出四类方向：提升长期任务可靠性、建立统一且可复现的智能体评测、明确安全与权限边界，以及改进多智能体协作中的分工与验证机制。建议回到精读页的 Challenges and Future Directions 核对原文。", "tour-future-answer");
      }
    }
  }

  function prepareTutorialPaperUpload() {
    const input = document.querySelector("#paper-file-input");
    if (!input || input.files?.length) return;
    try {
      const transfer = new DataTransfer();
      transfer.items.add(new File(["%PDF-1.4\n% SeeFurther tutorial"], "LLM-Agent-Survey.pdf", { type: "application/pdf" }));
      input.files = transfer.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    } catch (_) {
      const label = document.querySelector("#paper-file-label");
      const button = document.querySelector("#paper-file-button");
      if (label) label.textContent = "LLM-Agent-Survey.pdf";
      button?.classList.add("has-file");
    }
  }

  function appendTutorialPaperCard() {
    const list = document.querySelector("#messages");
    if (!list || list.querySelector(".tour-paper-card")) return;
    const item = document.createElement("article");
    item.className = "message assistant paper-card-message";
    const card = document.createElement("button");
    card.type = "button";
    card.className = "paper-chat-card tour-paper-card";
    card.innerHTML = "<span class='paper-card-kicker'>本地 PDF · 解析完成</span><strong>" + surveyTitle + "</strong><span class='paper-card-authors'>Lei Wang、Chen Ma、Xiaoming Zhang</span><span class='paper-card-abstract'>系统梳理基于大语言模型的自主智能体、评测与开放问题。</span><span class='paper-card-stats'><span>4 章节</span></span><span class='paper-card-enter'>进入论文精读 <b>↗</b></span>";
    item.appendChild(card);
    list.appendChild(item);
  }

  function selectTutorialReaderText() {
    const paragraph = document.querySelector("#structured-reader .guide-card p");
    if (!paragraph) return;
    const walker = document.createTreeWalker(paragraph, NodeFilter.SHOW_TEXT);
    let textNode = walker.nextNode();
    while (textNode && String(textNode.textContent || "").trim().length < 8) textNode = walker.nextNode();
    if (!textNode) return;
    const value = String(textNode.textContent || "");
    const start = Math.min(value.length, Math.max(0, value.search(/\S/)));
    const end = Math.min(value.length, start + Math.max(8, Math.min(28, value.length - start)));
    const range = document.createRange();
    range.setStart(textNode, start);
    range.setEnd(textNode, end);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    const rect = range.getBoundingClientRect();
    paragraph.dispatchEvent(new MouseEvent("mouseup", {
      bubbles: true,
      clientX: rect.right,
      clientY: rect.bottom
    }));
  }

  function installStyles() {
    const style = document.createElement("style");
    style.id = "seefurther-real-tour-styles";
    style.textContent = [
      ".sf-tour-spotlight{position:fixed;z-index:30000;border:2px solid #66f5d6;border-radius:16px;box-shadow:0 0 0 9999px rgba(13,18,24,.72),0 0 32px rgba(102,245,214,.28);pointer-events:none;transition:all .24s ease}",
      ".sf-tour-bubble{position:fixed;z-index:30001;display:grid;gap:10px;width:min(380px,calc(100vw - 28px));padding:18px;border:1px solid rgba(102,245,214,.42);border-radius:18px;color:#eafff7;background:linear-gradient(145deg,rgba(5,35,33,.99),rgba(17,28,53,.99));box-shadow:0 26px 80px rgba(0,0,0,.48);font-family:Inter,'Microsoft YaHei',sans-serif}",
      ".sf-tour-bubble small{color:#66f5d6;font-weight:850;letter-spacing:.1em}.sf-tour-bubble h2{margin:0;font-size:1.08rem}.sf-tour-bubble p{margin:0;color:#c5ded8;font-size:.84rem;line-height:1.65}",
      ".sf-tour-actions{display:flex;justify-content:space-between;gap:8px}.sf-tour-actions button{border-radius:99px;padding:9px 14px;font:750 .78rem/1 Inter,'Microsoft YaHei',sans-serif;cursor:pointer}",
      ".sf-tour-skip{border:1px solid rgba(255,255,255,.2);color:#dcefeb;background:transparent}.sf-tour-next{border:0;color:#04110e;background:linear-gradient(135deg,#66f5d6,#b8fff1)}",
      ".tour-domain-card{display:grid;gap:7px;width:min(760px,94%);margin:12px 0;padding:18px;text-align:left;border:1px solid rgba(102,245,214,.34);border-radius:18px;color:#eafff7;background:rgba(12,43,42,.9)}",
      ".tour-domain-card span,.tour-domain-card small{color:#a8ccc4}"
    ].join("");
    document.head.appendChild(style);
  }

  async function markComplete(destination) {
    try { await nativeFetch("/api/tutorial/complete", { method: "POST" }); } catch (_) {}
    sessionStorage.removeItem(stepKey);
    window.location.replace(destination || "/app?new=1&tutorial=skip");
  }

  function position(spotlight, bubble, target, companion) {
    target.scrollIntoView({ block: "center", behavior: "auto" });
    window.setTimeout(function () {
      const primaryRect = target.getBoundingClientRect();
      const companionRect = companion?.getBoundingClientRect();
      const rect = companionRect ? {
        top: Math.min(primaryRect.top, companionRect.top),
        left: Math.min(primaryRect.left, companionRect.left),
        right: Math.max(primaryRect.right, companionRect.right),
        bottom: Math.max(primaryRect.bottom, companionRect.bottom),
        width: Math.max(primaryRect.right, companionRect.right) - Math.min(primaryRect.left, companionRect.left),
        height: Math.max(primaryRect.bottom, companionRect.bottom) - Math.min(primaryRect.top, companionRect.top)
      } : primaryRect;
      const pad = 7;
      spotlight.style.top = Math.max(7, rect.top - pad) + "px";
      spotlight.style.left = Math.max(7, rect.left - pad) + "px";
      spotlight.style.width = Math.max(42, Math.min(window.innerWidth - Math.max(7, rect.left - pad) - 7, rect.width + pad * 2)) + "px";
      spotlight.style.height = Math.max(36, Math.min(window.innerHeight - Math.max(7, rect.top - pad) - 7, rect.height + pad * 2)) + "px";
      const bubbleWidth = Math.min(380, window.innerWidth - 28);
      const rightFits = rect.right + bubbleWidth + 28 < window.innerWidth;
      bubble.style.left = (rightFits ? rect.right + 15 : Math.max(14, rect.left - bubbleWidth - 15)) + "px";
      bubble.style.top = Math.max(72, Math.min(window.innerHeight - 260, rect.top)) + "px";
    }, 45);
  }

  function start() {
    installStyles();
    let index = Number(sessionStorage.getItem(stepKey));
    if (!Number.isInteger(index) || index < 0 || index >= steps.length) index = 0;
    const currentPage = pageName();
    if (steps[index].page !== currentPage) {
      const match = steps.findIndex(function (item) { return item.page === currentPage; });
      if (match >= 0) index = match;
    }
    prepare(index);
    const current = steps[index];
    const spotlight = document.createElement("div");
    spotlight.className = "sf-tour-spotlight";
    const bubble = document.createElement("section");
    bubble.className = "sf-tour-bubble";
    bubble.innerHTML = "<small>真实页面引导 " + (index + 1) + "/" + steps.length + "</small><h2></h2><p></p><div class='sf-tour-actions'><button class='sf-tour-skip' type='button'>跳过教程</button><button class='sf-tour-next' type='button'>" + (index === steps.length - 1 ? "完成" : "下一步") + "</button></div>";
    bubble.querySelector("h2").textContent = current.title;
    bubble.querySelector("p").textContent = current.copy;
    document.body.append(spotlight, bubble);

    const locate = function () {
      const target = document.querySelector(current.target);
      if (!target) {
        window.setTimeout(locate, 90);
        return;
      }
      const companion = current.companion ? document.querySelector(current.companion) : null;
      position(spotlight, bubble, target, companion);
    };
    locate();
    window.addEventListener("resize", locate);
    bubble.querySelector(".sf-tour-skip").addEventListener("click", function () { markComplete(); });
    bubble.querySelector(".sf-tour-next").addEventListener("click", function () {
      if (index >= steps.length - 1) {
        markComplete("/settings");
        return;
      }
      const nextIndex = index + 1;
      sessionStorage.setItem(stepKey, String(nextIndex));
      if (steps[nextIndex].page !== current.page) {
        window.location.href = steps[nextIndex].route;
      } else {
        bubble.remove();
        spotlight.remove();
        window.location.reload();
      }
    });
  }

  window.setTimeout(start, 220);
})();
