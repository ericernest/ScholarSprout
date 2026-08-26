# PaperAurora（研见）前端重构说明

## 结论

v1 使用 Vue 3 + TypeScript + Vite 的多页面构建，产物仍由 FastAPI 静态托管。后端 API、SSE、数据库、Agent 与部署命令保持不变。六个公开入口由 typed surface registry 统一声明；每个入口先加载对应的 v1 功能页面，再按顺序装载原样式、功能脚本和 PaperAurora 视觉层，因此重构期间不牺牲已经可用的交互。

这是一条刻意的渐进迁移边界：新增功能直接写成 Vue feature；旧页面按功能域逐步从 `LegacySurface` 中拆成 Vue 组件。某个功能域完成原生迁移时，只替换 registry 对应入口，不改 URL 和后端协议。

## 目录与边界

- `webui/src/config/surfaces.ts`：公开页面、标题、v1 来源和页面指引的唯一清单。
- `webui/src/components/LegacySurface.vue`：只负责生命周期、资源装载和兼容边界；不会解释业务数据。
- `webui/src/components/ProductGuide.vue`：可复用的首次引导与帮助入口。
- `webui/public/styles/legacy-overrides.css`：跨页面设计令牌与视觉兼容层。
- `gateway/static/app-v2/`：构建产物；Python 运行时只读取这里，不运行 Node。

v2 集中式网页继续沿用相同入口和组件边界，再增加身份、租户、远程对象存储和服务端权限。v1 不提前实现这些能力，也不把本地路径或 API Key 状态写进全局前端 store。

## 参考源如何使用

| 来源 | 实际采用 | 明确未照搬 |
| --- | --- | --- |
| 当前 `gateway/static` | 全部功能合同、URL、DOM 控件和脚本加载顺序 | 大文件继续增长的组织方式 |
| DeepSeek Harness `apps/web` 与 `packages/client/ui-*` | 薄入口、模块注册、token 化主题、生命周期清理 | Cordis/React 运行时与数百包拆分 |
| `web-ui-polish` | 首次引导、帮助入口、焦点环、细滚动条、减弱动效 | 依赖 hashed class 的 dist 补丁 |
| “查看本地仓库”任务末尾讨论 | Vue 3 + TypeScript + Vite、MPA、FastAPI 静态托管、渐进迁移 | SSR、微前端、全量 UI 组件库 |
| CodePen | 极光渐变、玻璃层次、清楚的卡片层级 | 第三方模板代码、重动画和外部字体依赖 |
| dsh.papertok.ai | Web server 不理解业务、浏览器模块可替换、测试区分机制与实际挂载 | 把后端改造成插件服务器 |

## 可复现调研记录

- 截止日期：2026-08-26；检索日期：2026-08-25 至 2026-08-26。
- 问题：在本地性能、后端最小改动、功能零遗漏和 v2 扩展性约束下如何重构 UI。
- 查询族：`site:codepen.io research dashboard UI paper reader Vue`、`site:codepen.io aurora glassmorphism dashboard UI`；直接读取 `https://codepen.io/`、`https://dsh.papertok.ai/`。
- 来源层级：实际仓库与本地参考实现优先；官方/作者站用于架构事实；CodePen 只用于视觉发现。
- 排除：依赖第三方 CDN 的模板、重型粒子动画、纯概念稿、需要 SSR/Node 生产服务的方案、会改变现有后端协议的方案。
- 覆盖限制：CodePen 是开放作品集合，不代表可直接商用；本实现没有复制作品代码。没有把静态检查称为真实模型或真实 PDF 的端到端验证。

## 构建与回滚

```powershell
cd webui
npm run verify
```

构建写入 `gateway/static/app-v2`。如果构建目录不存在，Gateway 自动回退到原 `gateway/static` 页面；这是开发与回滚路径，不改变业务 API。
