<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";

type TourView = "chat" | "domain" | "library" | "reading" | "return-chat" | "settings";
interface TourStep { view: TourView; target: string; title: string; copy: string; }

const storageKey = "seefurther_feature_tour_v1";
const faviconPath = "/static/app-v2/favicon.svg";
const stepIndex = ref(0);
const spotlight = reactive({ top: 120, left: 120, width: 320, height: 80 });
const steps: TourStep[] = [
  { view: "chat", target: "chat-input", title: "从一句你好开始", copy: "教程中的输入和回答均为预生成内容。点击输入框，看看研见能做什么。" },
  { view: "chat", target: "chat-answer", title: "认识研见", copy: "日常聊天可以解释概念、梳理研究问题，也能进入领域入门和论文精读。" },
  { view: "domain", target: "domain-input", title: "提出一个领域", copy: "选择“领域入门”，输入固定演示领域：智能体 Agent。" },
  { view: "domain", target: "domain-card", title: "进入领域工作台", copy: "任务会以卡片持续更新。教程不执行检索，展示的是预生成结果。" },
  { view: "domain", target: "domain-map", title: "沿路径认识领域", copy: "依次查看前置知识、发展路径和概念全景，建立可继续扩展的认知结构。" },
  { view: "domain", target: "paper-list", title: "Survey 主导的论文清单", copy: "示例选择一篇智能体综述。真实任务会按 Survey 主导规则生成论文清单。" },
  { view: "domain", target: "download-paper", title: "下载并加入论文管理", copy: "点击后，论文会进入资料库。教程仅改变当前演示画面，不会真实下载或写库。" },
  { view: "library", target: "folder-tree", title: "用文件夹整理论文", copy: "资料库支持新建文件夹、移动论文和按研究主题持续归档。" },
  { view: "library", target: "open-reading", title: "开始论文精读", copy: "已下载的论文可以直接进入精读，不需要再次下载。" },
  { view: "reading", target: "smart-index", title: "智能索引", copy: "智能索引按章节组织导读卡片，并与 PDF 原文页码保持双向联动。" },
  { view: "reading", target: "research-overview", title: "研究总览", copy: "研究问题、核心方法、方法步骤、实验支撑与局限会集中展示。" },
  { view: "reading", target: "agent-explain", title: "让智能体解释", copy: "从总览卡片发起解释时，会自动收起总览并把来源上下文交给右侧智能体。" },
  { view: "reading", target: "analyze-section", title: "分析本节", copy: "围绕当前章节获取核心内容、论证结构、关键证据和重点概念。" },
  { view: "reading", target: "selection-question", title: "原文选区提问", copy: "选中 PDF 原文后，可只针对选区提问，避免上下文漂移。" },
  { view: "reading", target: "annotation", title: "高亮与注释", copy: "为原文添加颜色高亮和本地注释，后续阅读仍可恢复。" },
  { view: "return-chat", target: "discussion-picker", title: "返回会话并选择讨论", copy: "把刚才的智能体综述设为当前讨论，聊天智能体只能使用这个讨论范围内的信息。" },
  { view: "return-chat", target: "future-question", title: "围绕综述继续提问", copy: "固定演示问题：这个综述认为的未来可做的有哪些？" },
  { view: "return-chat", target: "future-answer", title: "得到有边界的回答", copy: "回答只引用当前综述的开放问题，并提示回到精读页核对原文。" },
  { view: "settings", target: "model-config", title: "最后配置模型", copy: "填写基础模型 Base URL、API Key 和模型名称。API Key 只保存在本地后端。" },
];

const step = computed(() => steps[stepIndex.value]);
const progress = computed(() => Math.round((stepIndex.value + 1) / steps.length * 100));
const bubbleStyle = computed(() => {
  const bubbleWidth = Math.min(380, window.innerWidth - 28);
  const useRight = spotlight.left + spotlight.width + bubbleWidth + 36 < window.innerWidth;
  const left = useRight
    ? spotlight.left + spotlight.width + 18
    : Math.max(14, spotlight.left - bubbleWidth - 18);
  const top = Math.min(window.innerHeight - 250, Math.max(76, spotlight.top));
  return { left: `${left}px`, top: `${top}px`, width: `${bubbleWidth}px` };
});

function updateSpotlight(): void {
  const target = document.querySelector<HTMLElement>(`[data-tour-id="${step.value.target}"]`);
  if (!target) return;
  const bounds = target.getBoundingClientRect();
  spotlight.top = Math.max(8, bounds.top - 7);
  spotlight.left = Math.max(8, bounds.left - 7);
  spotlight.width = Math.min(window.innerWidth - spotlight.left - 8, bounds.width + 14);
  spotlight.height = Math.min(window.innerHeight - spotlight.top - 8, bounds.height + 14);
}

async function advance(): Promise<void> {
  if (stepIndex.value >= steps.length - 1) {
    finish("/settings?tutorial=complete");
    return;
  }
  stepIndex.value += 1;
  await nextTick();
  updateSpotlight();
}

function targetClicked(target: string): void {
  if (step.value.target === target) void advance();
}

function finish(destination = "/app?new=1&tutorial=skip"): void {
  localStorage.setItem(storageKey, "completed");
  window.location.href = destination;
}

watch(stepIndex, async () => {
  await nextTick();
  window.setTimeout(updateSpotlight, 60);
});
onMounted(async () => {
  await nextTick();
  updateSpotlight();
  window.addEventListener("resize", updateSpotlight);
});
onBeforeUnmount(() => window.removeEventListener("resize", updateSpotlight));
</script>

<template>
  <main class="tour-app">
    <header class="tour-topbar">
      <div class="tour-brand"><img :src="faviconPath" alt="" /><strong>研见 · SeeFurther</strong></div>
      <div class="tour-progress"><span>功能教程 {{ stepIndex + 1 }}/{{ steps.length }}</span><i><b :style="{ width: `${progress}%` }" /></i></div>
      <button type="button" @click="finish()">跳过教程</button>
    </header>

    <div class="tour-demo-note">教程演示 · 所有输入、回答和研究结果均为预生成内容，不调用模型，也不会写入数据库</div>

    <section v-if="step.view === 'chat'" class="tour-screen chat-screen">
      <div class="demo-chat-header"><strong>研究对话</strong><span>当前模式：日常聊天</span></div>
      <div class="demo-thread">
        <article class="demo-assistant">你好，我是研见。可以从论文精读、领域探索或科研讨论开始。</article>
        <article v-if="stepIndex >= 1" data-tour-id="chat-answer" class="demo-assistant answer" @click="targetClicked('chat-answer')">
          我可以帮助你梳理一个研究领域、管理论文，并在 PDF 原文、智能索引和研究总览之间建立可追溯的阅读路径。
        </article>
      </div>
      <button data-tour-id="chat-input" class="demo-composer" type="button" @click="targetClicked('chat-input')"><span>你好</span><b>发送</b></button>
    </section>

    <section v-else-if="step.view === 'domain'" class="tour-screen domain-screen">
      <aside><strong>领域入门</strong><span>前置知识</span><span>发展路径</span><span>概念全景</span><span>论文清单</span></aside>
      <div class="domain-main">
        <button data-tour-id="domain-input" type="button" class="domain-prompt" @click="targetClicked('domain-input')"><span>领域入门</span><strong>智能体 Agent</strong><b>生成</b></button>
        <button v-if="stepIndex >= 3" data-tour-id="domain-card" type="button" class="domain-result-card" @click="targetClicked('domain-card')">
          <small>DOMAIN ONBOARDING · 已完成</small><strong>智能体 Agent</strong><span>已生成前置知识、发展路径、概念全景与 Survey 主导论文清单</span>
        </button>
        <div v-if="stepIndex >= 4" data-tour-id="domain-map" class="domain-map" @click="targetClicked('domain-map')">
          <article><b>01</b><strong>前置知识</strong><span>LLM、工具调用、记忆与规划</span></article>
          <article><b>02</b><strong>发展路径</strong><span>ReAct → 自主规划 → 多智能体协作</span></article>
          <article><b>03</b><strong>概念全景</strong><span>感知、推理、行动、反馈与评估</span></article>
        </div>
        <div v-if="stepIndex >= 5" data-tour-id="paper-list" class="tour-paper-list" @click="targetClicked('paper-list')">
          <small>SURVEY · 高引用综述</small>
          <strong>A Survey on Large Language Model based Autonomous Agents</strong>
          <span>系统梳理基于大语言模型的自主智能体架构、应用与评估。</span>
          <button data-tour-id="download-paper" type="button" @click.stop="targetClicked('download-paper')">{{ stepIndex >= 7 ? "已加入" : "下载论文" }}</button>
        </div>
      </div>
    </section>

    <section v-else-if="step.view === 'library'" class="tour-screen library-screen">
      <aside data-tour-id="folder-tree" @click="targetClicked('folder-tree')">
        <strong>论文管理</strong><button type="button">＋ 新建文件夹</button><span>全部论文</span><span class="active">智能体研究</span><span>待读</span>
      </aside>
      <div class="library-paper">
        <small>智能体研究 / Survey</small><h2>A Survey on Large Language Model based Autonomous Agents</h2>
        <p>已下载 PDF · 已加入论文管理</p>
        <button data-tour-id="open-reading" type="button" @click="targetClicked('open-reading')">开始论文精读</button>
      </div>
    </section>

    <section v-else-if="step.view === 'reading'" class="tour-screen reading-screen">
      <aside><strong>论文索引</strong><span class="active">1 Introduction</span><span>2 Agent Construction</span><span>3 Applications</span><span>4 Evaluation</span><span>5 Challenges</span></aside>
      <div class="reader-demo">
        <div class="reader-tabs"><b>智能索引</b><span>PDF 原文</span></div>
        <article data-tour-id="smart-index" class="smart-index-card" @click="targetClicked('smart-index')">
          <small>章节 02</small><h2>Agent Construction</h2><div><b>核心信息</b><span>智能体由画像、记忆、规划和行动模块构成。</span></div><button type="button">跳转 PDF 第 6 页</button>
        </article>
        <div data-tour-id="selection-question" class="selection-demo" @click="targetClicked('selection-question')">已选中：“Memory enables agents to acquire and retain information...” <button type="button">询问选区</button></div>
        <div data-tour-id="annotation" class="annotation-demo" @click="targetClicked('annotation')"><mark>Memory enables agents to retain experience.</mark><button type="button">高亮 · 添加注释</button></div>
      </div>
      <aside class="copilot-demo">
        <div data-tour-id="research-overview" @click="targetClicked('research-overview')"><small>研究总览</small><strong>开放环境中的自主决策</strong><span>架构 · 记忆 · 规划 · 行动 · 评估</span></div>
        <button data-tour-id="agent-explain" type="button" @click="targetClicked('agent-explain')">让智能体解释</button>
        <button data-tour-id="analyze-section" type="button" @click="targetClicked('analyze-section')">分析本节</button>
        <p>该节说明智能体如何借助记忆与规划完成持续决策……</p>
      </aside>
    </section>

    <section v-else-if="step.view === 'return-chat'" class="tour-screen chat-screen">
      <div class="demo-chat-header"><strong>研究对话</strong><span>当前模式：日常聊天</span></div>
      <button data-tour-id="discussion-picker" class="discussion-demo" type="button" @click="targetClicked('discussion-picker')"><span>当前讨论</span><strong>A Survey on Large Language Model based Autonomous Agents</strong><b>⌄</b></button>
      <div class="demo-thread">
        <article data-tour-id="future-answer" class="demo-assistant answer" @click="targetClicked('future-answer')">
          该综述重点提出三类未来方向：更可靠的长期记忆、更可验证的规划与反思，以及统一且可复现的智能体评估。建议回到“Challenges”章节核对原文证据。
        </article>
      </div>
      <button data-tour-id="future-question" class="demo-composer" type="button" @click="targetClicked('future-question')"><span>这个综述认为的未来可做的有哪些？</span><b>发送</b></button>
    </section>

    <section v-else class="tour-screen settings-screen">
      <div data-tour-id="model-config" class="settings-demo" @click="targetClicked('model-config')">
        <small>LOCAL CONFIGURATION</small><h2>模型与数据</h2>
        <label>基础模型 Base URL<input readonly value="https://api.example.com/v1" /></label>
        <label>基础模型 API Key<input readonly type="password" value="local-secret" /></label>
        <label>模型名称<input readonly value="your-model-id" /></label>
        <p>API Key 仅保存在本地后端。</p>
        <button type="button">保存配置</button>
      </div>
    </section>

    <div class="tour-spotlight" :style="{ top: `${spotlight.top}px`, left: `${spotlight.left}px`, width: `${spotlight.width}px`, height: `${spotlight.height}px` }" />
    <aside class="tour-bubble" :style="bubbleStyle">
      <small>STEP {{ stepIndex + 1 }}</small><h2>{{ step.title }}</h2><p>{{ step.copy }}</p>
      <div><button type="button" class="tour-secondary" @click="finish()">跳过</button><button type="button" class="tour-primary" @click="advance">{{ stepIndex === steps.length - 1 ? "进入配置" : "下一步" }}</button></div>
    </aside>
  </main>
</template>

<style scoped>
*{box-sizing:border-box}.tour-app{min-height:100vh;color:#eafff7;background:radial-gradient(circle at 18% 12%,rgba(102,245,214,.16),transparent 28rem),linear-gradient(135deg,#06100e,#101b2d 55%,#06100e);font-family:Inter,"Microsoft YaHei",sans-serif;overflow:hidden}.tour-topbar{position:relative;z-index:30;display:grid;grid-template-columns:1fr minmax(220px,420px) 1fr;align-items:center;gap:18px;height:66px;padding:0 20px;border-bottom:1px solid rgba(134,255,225,.17);background:rgba(5,16,14,.9)}.tour-brand{display:flex;align-items:center;gap:10px}.tour-brand img{width:32px;height:32px}.tour-progress{display:grid;gap:6px;text-align:center;font-size:.72rem;color:#9ac2b7}.tour-progress i{display:block;height:4px;border-radius:99px;background:rgba(255,255,255,.1);overflow:hidden}.tour-progress b{display:block;height:100%;background:linear-gradient(90deg,#66f5d6,#87a7ff);transition:width .25s}.tour-topbar>button{justify-self:end;border:1px solid rgba(255,255,255,.16);border-radius:99px;padding:8px 14px;color:#d9eee9;background:transparent;cursor:pointer}.tour-demo-note{position:relative;z-index:30;padding:7px 16px;color:#b9d8d1;background:rgba(135,167,255,.1);text-align:center;font-size:.72rem}.tour-screen{height:calc(100vh - 98px);padding:22px;overflow:hidden}.chat-screen{display:grid;grid-template-rows:auto 1fr auto;gap:16px}.demo-chat-header,.discussion-demo{display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border:1px solid rgba(102,245,214,.18);border-radius:16px;background:rgba(8,27,23,.8)}.demo-chat-header span,.discussion-demo span{color:#9ac2b7;font-size:.78rem}.demo-thread{display:grid;align-content:start;gap:14px;padding:28px}.demo-assistant{max-width:min(820px,78%);padding:16px 18px;border:1px solid rgba(102,245,214,.17);border-radius:20px;background:rgba(255,255,255,.08);line-height:1.7}.answer{border-color:rgba(102,245,214,.34)}.demo-composer{display:flex;align-items:center;justify-content:space-between;width:calc(100% - 36px);margin:0 auto;padding:14px 16px;border:1px solid rgba(102,245,214,.28);border-radius:22px;color:#eafff7;background:rgba(8,23,21,.9);text-align:left}.demo-composer b,.domain-prompt b{padding:9px 16px;border-radius:99px;color:#04110e;background:#66f5d6}.domain-screen,.library-screen,.reading-screen{display:grid;grid-template-columns:220px minmax(0,1fr);gap:16px}.domain-screen>aside,.library-screen>aside,.reading-screen>aside{display:grid;align-content:start;gap:9px;padding:18px;border:1px solid rgba(102,245,214,.16);border-radius:18px;background:rgba(7,25,21,.82)}.domain-screen>aside span,.library-screen>aside span,.reading-screen>aside span{padding:9px;border-radius:9px;color:#9ac2b7}.active{color:#eafff7!important;background:rgba(102,245,214,.13)}.domain-main{display:grid;align-content:start;gap:16px;overflow:auto}.domain-prompt{display:flex;align-items:center;gap:15px;padding:15px;border:1px solid rgba(102,245,214,.25);border-radius:18px;color:#eafff7;background:rgba(8,27,23,.86)}.domain-prompt strong{margin-right:auto}.domain-result-card,.tour-paper-list{display:grid;gap:9px;padding:20px;border:1px solid rgba(135,167,255,.32);border-radius:20px;color:#eafff7;background:linear-gradient(145deg,rgba(13,35,31,.98),rgba(8,24,30,.96));text-align:left}.domain-result-card small,.tour-paper-list small,.settings-demo small{color:#66f5d6;letter-spacing:.1em}.domain-map{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.domain-map article{display:grid;gap:8px;padding:18px;border:1px solid rgba(102,245,214,.17);border-radius:16px;background:rgba(255,255,255,.055)}.domain-map b{color:#66f5d6}.tour-paper-list button,.library-paper button,.settings-demo button{justify-self:start;border:0;border-radius:99px;padding:10px 16px;color:#04110e;background:#66f5d6;font-weight:800}.library-screen{grid-template-columns:260px 1fr}.library-screen aside button{border:1px solid rgba(102,245,214,.3);border-radius:10px;padding:9px;color:#dffff8;background:rgba(102,245,214,.08)}.library-paper{align-self:start;padding:28px;border:1px solid rgba(102,245,214,.2);border-radius:22px;background:rgba(255,255,255,.06)}.library-paper h2{max-width:780px;font:700 2rem/1.25 Georgia,serif}.library-paper p{color:#9ac2b7}.reading-screen{grid-template-columns:220px minmax(360px,1fr) 300px}.reader-demo{display:grid;grid-template-rows:auto 1fr auto auto;gap:12px;padding:14px;border-radius:18px;color:#25332f;background:#f8f6ef}.reader-tabs{display:flex;gap:8px;color:#66736f}.reader-tabs>*{padding:8px 12px;border-radius:9px}.reader-tabs b{color:#04110e;background:#66f5d6}.smart-index-card{padding:24px;border:1px solid rgba(35,75,64,.15);border-radius:15px;background:#fff}.smart-index-card small{color:#13856d}.smart-index-card h2{font-family:Georgia,serif}.smart-index-card div{display:grid;gap:6px;padding:12px;border-radius:10px;background:rgba(32,185,151,.07)}.smart-index-card button,.selection-demo button,.annotation-demo button{margin-top:12px;border:1px solid rgba(20,133,109,.22);border-radius:99px;padding:7px 10px;color:#0b725d;background:rgba(32,185,151,.08)}.selection-demo,.annotation-demo{padding:12px;border:1px solid rgba(32,185,151,.18);border-radius:12px;background:#fff}.annotation-demo mark{background:rgba(255,223,77,.62)}.copilot-demo{display:grid!important;align-content:start!important}.copilot-demo>div{display:grid;gap:8px;padding:14px;border:1px solid rgba(102,245,214,.2);border-radius:14px;background:rgba(255,255,255,.05)}.copilot-demo button{border:1px solid rgba(102,245,214,.26);border-radius:10px;padding:10px;color:#dffff8;background:rgba(102,245,214,.1)}.copilot-demo p{color:#9ac2b7;line-height:1.6}.discussion-demo{width:min(780px,90%);margin:auto}.discussion-demo strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.settings-screen{display:grid;place-items:center}.settings-demo{display:grid;gap:13px;width:min(680px,94%);padding:26px;border:1px solid rgba(102,245,214,.2);border-radius:22px;background:rgba(8,27,23,.9)}.settings-demo label{display:grid;gap:6px;color:#cbe2dd;font-size:.78rem}.settings-demo input{padding:11px;border:1px solid rgba(102,245,214,.18);border-radius:10px;color:#eafff7;background:rgba(0,0,0,.2)}.settings-demo p{color:#9ac2b7}.tour-spotlight{position:fixed;z-index:15;border:2px solid #66f5d6;border-radius:15px;box-shadow:0 0 0 9999px rgba(45,50,54,.68),0 0 28px rgba(102,245,214,.52);pointer-events:none;transition:all .22s ease}.tour-bubble{position:fixed;z-index:20;padding:20px;border:1px solid rgba(102,245,214,.36);border-radius:18px;color:#eafff7;background:linear-gradient(150deg,rgba(8,34,31,.98),rgba(18,27,54,.98));box-shadow:0 24px 70px rgba(0,0,0,.48)}.tour-bubble small{color:#66f5d6;font-weight:850;letter-spacing:.12em}.tour-bubble h2{margin:9px 0;font-size:1.2rem}.tour-bubble p{margin:0;color:#b9d8d1;font-size:.86rem;line-height:1.65}.tour-bubble div{display:flex;justify-content:flex-end;gap:8px;margin-top:16px}.tour-bubble button{border-radius:99px;padding:9px 14px;font-weight:750}.tour-secondary{border:1px solid rgba(255,255,255,.18);color:#d9eee9;background:transparent}.tour-primary{border:0;color:#04110e;background:#66f5d6}@media(max-width:900px){.tour-topbar{grid-template-columns:1fr auto}.tour-progress{display:none}.domain-screen,.library-screen{grid-template-columns:170px 1fr}.reading-screen{grid-template-columns:150px 1fr}.copilot-demo{display:none!important}.tour-bubble{left:14px!important;right:14px!important;bottom:14px!important;top:auto!important;width:auto!important}.domain-map{grid-template-columns:1fr}.tour-screen{padding:12px}.tour-demo-note{font-size:.66rem}}
</style>
