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
    title: "科研萌芽·ScholarSprout",
    guideTitle: "Where Research Takes Root.",
    guideSteps: ["进入研究对话", "选择论文精读或领域入门", "在资料库继续已有工作"],
    autoOpenGuide: false
  },
  chat: {
    id: "chat",
    sourcePath: "/static/chat.html",
    title: "研究对话 · 科研萌芽·ScholarSprout",
    guideTitle: "研究从这里开始",
    guideSteps: ["选择对话、论文精读或领域入门模式", "上传 PDF 或粘贴论文链接", "随时中断生成并从资料库恢复"]
  },
  "paper-reading": {
    id: "paper-reading",
    sourcePath: "/static/paper-reading/index.html",
    title: "论文精读 · 科研萌芽·ScholarSprout",
    guideTitle: "论文精读",
    guideSteps: [
      "导读地图：快速把握论文的问题、方法、结论与章节联系",
      "实验重点分析：聚焦实验设置、关键结果、对比与局限",
      "原文选区提问：选中 PDF 原文后，围绕选区继续追问"
    ]
  },
  "domain-onboarding": {
    id: "domain-onboarding",
    sourcePath: "/static/domain-onboarding/index.html",
    title: "领域入门 · 科研萌芽·ScholarSprout",
    guideTitle: "领域入门",
    guideSteps: [
      "前置知识梳理：明确进入该领域需要掌握的基础概念",
      "领域发展路径：沿研究阶段理解关键问题与方法演进",
      "相关论文推荐：查看 Survey 主导的论文清单并继续精读"
    ]
  },
  library: {
    id: "library",
    sourcePath: "/static/library/index.html",
    title: "研究资料库 · 科研萌芽·ScholarSprout",
    guideTitle: "统一管理研究资产",
    guideSteps: ["切换会话、领域地图、精读记录和论文", "用文件夹与状态整理论文", "从任意卡片继续研究"]
  },
  settings: {
    id: "settings",
    sourcePath: "/static/settings/index.html",
    title: "配置 · 科研萌芽·ScholarSprout",
    guideTitle: "本地优先的模型配置",
    guideSteps: ["配置主模型接口", "按需配置嵌入模型", "确认本地数据目录后保存"]
  }
};
