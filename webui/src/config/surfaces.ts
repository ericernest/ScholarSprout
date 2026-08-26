export type SurfaceId =
  | "home"
  | "chat"
  | "paper-reading"
  | "domain-onboarding"
  | "library"
  | "settings";

export interface SurfaceDefinition {
  id: SurfaceId;
  sourcePath: string;
  title: string;
  guideTitle: string;
  guideSteps: readonly string[];
  autoOpenGuide?: boolean;
}

export const surfaces: Record<SurfaceId, SurfaceDefinition> = {
  home: {
    id: "home",
    sourcePath: "/static/index.html",
    title: "研见 PaperAurora",
    guideTitle: "从一篇论文，看见一个领域",
    guideSteps: ["进入研究对话", "选择论文精读或领域入门", "在资料库继续已有工作"],
    autoOpenGuide: true
  },
  chat: {
    id: "chat",
    sourcePath: "/static/chat.html",
    title: "研究对话 · 研见 PaperAurora",
    guideTitle: "研究从这里开始",
    guideSteps: ["选择对话、论文精读或领域入门模式", "上传 PDF 或粘贴论文链接", "随时中断生成并从资料库恢复"]
  },
  "paper-reading": {
    id: "paper-reading",
    sourcePath: "/static/paper-reading/index.html",
    title: "论文精读 · 研见 PaperAurora",
    guideTitle: "论文精读工作台",
    guideSteps: ["左侧浏览章节与阅读地图", "中间切换 PDF 与重排阅读", "右侧提问、使用 Skill 或创建 Fork" ]
  },
  "domain-onboarding": {
    id: "domain-onboarding",
    sourcePath: "/static/domain-onboarding/index.html",
    title: "领域入门 · 研见 PaperAurora",
    guideTitle: "领域学习工作台",
    guideSteps: ["查看生成进度与研究阶段", "沿学习地图展开问题和方向", "把推荐论文加入资料库或继续精读"]
  },
  library: {
    id: "library",
    sourcePath: "/static/library/index.html",
    title: "研究资料库 · 研见 PaperAurora",
    guideTitle: "统一管理研究资产",
    guideSteps: ["切换会话、领域地图、精读记录和论文", "用文件夹与状态整理论文", "从任意卡片继续研究"]
  },
  settings: {
    id: "settings",
    sourcePath: "/static/settings/index.html",
    title: "配置 · 研见 PaperAurora",
    guideTitle: "本地优先的模型配置",
    guideSteps: ["配置主模型接口", "按需配置嵌入与 MinerU", "确认本地数据目录后保存"]
  }
};
