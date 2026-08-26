# 研见 · SeeFurther

**See Further into Research.**

PaperAurora（研见）是一款面向科研学习的本地优先 AI 研究助手：把论文、领域知识和对话放在同一个工作台中，从一篇论文，看见一个领域。v1 面向个人电脑本地部署，数据与模型配置由本地 PaperAurora 后端管理；未来可在同一套前端结构上扩展集中式服务。

## 功能

- **对话工作台**：Markdown、表格、流式输出、可折叠推理内容，以及可中断的长任务。
- **领域入门**：前置知识梳理、领域发展路径、学习路径和相关论文推荐。论文清单遵循“Survey 主导”规则：最多 3 篇 Survey，再补充最多 3 篇由这些 Survey 引用的论文。
- **论文精读**：PDF 导入与解析、导读地图、实验重点分析、原文选区提问、批注与阅读进度。
- **本地优先**：无需额外启动前端服务；静态前端由本地网关提供，模型、API Key 和数据目录都在本机配置。

## 快速开始

### 1. 安装

需要 Python 3.11 或更高版本。

```bash
python -m pip install -e .
```

### 2. 启动本地网关

```bash
novicesynapse gateway --host 127.0.0.1 --port 8000
```

然后打开 <http://127.0.0.1:8000/>。`novicesynapse` 是 v1 的兼容命令名，产品名称为 PaperAurora（研见 · SeeFurther）。Windows 和 Linux 均可使用同一启动方式。

### 3. 配置模型

在网页的“设置”页面配置提供商、模型和 API Key。默认本地数据目录为当前用户目录下的 `.novicesynapse`（不会把用户名写死在代码中）；也可以通过后端配置覆盖。

## 页面入口

| 页面 | 地址 | 用途 |
| --- | --- | --- |
| 对话 | `/app` | 研究问答与任务流 |
| 领域入门 | `/app/domain-onboarding` | 领域知识与论文推荐 |
| 论文精读 | `/app/paper-reading` | 导读地图、实验分析、选区提问 |
| 论文库 | `/library` | 已导入论文与阅读状态 |
| 设置 | `/settings` | 模型与运行配置 |

## 前端开发

前端位于 `webui/`，采用 Vue 3 + TypeScript + Vite 多页面结构。后端运行时使用已经构建并提交的 `gateway/static/app-v2/` 静态资源；修改前端后执行：

```bash
cd webui
npm install
npm run build
```

## 架构概览

浏览器请求由 FastAPI 网关接收，领域入门、论文精读、对话和设置分别由后端处理器提供能力；前端只负责展示、交互和调用现有 API。这样 v1 可以保持本地部署轻量，v2 也能将同一套页面接入集中式后端。

## 许可

本项目采用 [MIT License](LICENSE)。
