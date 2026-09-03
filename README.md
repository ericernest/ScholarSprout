<div align="center">
  <img src="gateway/static/favicon.svg" width="92" alt="科研萌芽·ScholarSprout" />
  <h1>科研萌芽·ScholarSprout</h1>
  <p><strong>Where Research Takes Root.</strong></p>
  <p>让每一个科研问题从好奇萌芽。</p>
</div>

科研萌芽·ScholarSprout 是一款本地优先的 AI 研究工作台，把领域入门、论文发现、PDF 精读、研究对话与个人笔记连接成一条连续工作流。它面向刚进入新方向的研究者，也适合需要长期管理论文、证据和研究上下文的用户。

> v1 以 Windows 本地使用为主。模型配置、会话、论文、PDF、标注和笔记均由本地后端管理；除调用用户配置的模型与论文检索服务外，不需要额外部署前端服务器。

<!-- 截图占位：首页 / 对话工作台 -->

## 核心功能

### 研究对话

- Markdown、公式、代码块和表格展示
- 模型回答实时流式输出，可随时中断
- 可选择一篇论文或一个领域作为“当前讨论”，Agent 只读取该范围内的研究资料
- 从其他页面返回后继续显示并增量更新尚未完成的回答
- 支持飞书机器人长连接，飞书消息与网页会话共享同一套研究对话 Agent

### 领域入门

- **前置知识梳理**：建立概念、方法和基础工作的依赖关系
- **领域发展路径**：理解关键阶段、技术转折与代表工作
- **概念全景**：从核心问题进入相关方向与论文
- **论文清单**：最多 3 篇 Survey，再补充最多 3 篇由这些 Survey 引用的论文
- 显示任务阶段与进度，支持取消、失败重试和继续查看
- 可将推荐论文加入资料库，或下载 PDF 后直接开始论文精读

<!-- 截图占位：领域入门 -->

### 论文精读

- 上传本地 PDF，或从 PDF/arXiv 链接导入论文
- PDF 原文、论文目录和智能索引双向联动
- 研究总览集中整理研究问题、核心方法、方法步骤、实验支撑与局限
- 智能体解释、分析本节、原文选区提问和专项探索
- 高亮、注释、阅读位置与进度自动保存
- 为每篇论文保存 Markdown 笔记，支持普通编辑、源码编辑、表格和公式

<!-- 截图占位：论文精读 -->

### 研究资料库

- 统一管理会话、领域入门任务、论文和精读记录
- 使用文件夹组织论文，恢复阅读状态并继续精读
- 自动合并重复记录，保留论文来源、PDF 状态及关联上下文

## 直接使用 Windows 版

从 [v1 Release](https://github.com/ericernest/NoviceSynapse/releases/tag/v1.0.0) 下载任一版本：

| 版本 | 文件 | 适合场景 |
| --- | --- | --- |
| **快速启动目录版（推荐）** | `ScholarSprout-v1.0.0-windows-x64-portable.zip` | 完整解压后双击 `ScholarSprout.exe`；启动快，移动时需要保留整个文件夹 |
| **单文件版** | `ScholarSprout-v1.0.0-windows-x64.exe` | 只需携带一个 exe；每次启动需要先释放运行文件，因此会更慢 |

两种版本均为 Windows x64 免安装程序，已包含 Python 运行时、Web 前端、PDF 解析依赖和飞书 SDK。使用者不需要安装 Python、Node.js 或其它环境，只需准备一个 OpenAI 兼容的大模型 API。

程序默认监听 `127.0.0.1:8000`；如果端口被占用，会在 `8001-8099` 中选择可用端口并打开浏览器。运行期间可通过系统托盘重新打开页面或选择“退出科研萌芽”。

> Windows SmartScreen 可能提示未识别的发布者，这是因为当前 v1 尚未使用商业代码签名证书。请只从本仓库 Release 下载。

## 从源码运行

支持 Windows 和 Linux，要求 Python 3.11+：

```bash
git clone https://github.com/ericernest/NoviceSynapse.git
cd NoviceSynapse
python -m pip install -e .
novicesynapse gateway --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000/>。

仓库已包含可直接运行的前端构建产物。只有修改了 `webui/` 时才需要 Node.js 18+ 并重新构建：

```bash
cd webui
npm install
npm run build
```

## 配置

首次启动会进入教程，也可以随时打开 <http://127.0.0.1:8000/settings>。配置分为“模型与数据”和“多渠道”两个页面。

### 基础模型（必填）

| 配置项 | 说明 | 示例 |
| --- | --- | --- |
| Base URL | OpenAI 兼容接口的完整地址 | `https://api.example.com/v1` |
| API Key | 模型服务商提供的密钥，只填写 Key 本身 | `sk-...` |
| 模型名称 | 服务商接口接受的 `model` 参数 | `qwen-plus` |

API Key 保存在运行科研萌芽的电脑后端，不会在配置页面加载时回传明文。配置文件默认位于当前用户目录下的 `.novicesynapse/config.json`。

### Embedding（可选）

Embedding 用于论文排序与证据匹配。URL 和 API Key 留空时复用基础模型配置；如果服务商不提供 Embedding，可以保持默认，相关能力调用失败时会自动降级，不影响基础对话与论文阅读。

### 数据目录

会话数据库、论文、PDF、图片、标注和笔记默认保存在当前用户的 `.novicesynapse` 目录。配置页支持改为其它绝对路径，例如：

```text
Windows: D:\ScholarSproutData
Linux:   /home/user/scholarsprout-data
```

修改数据目录后需要重启科研萌芽，原目录的数据不会自动迁移。

### 飞书机器人（可选）

在“多渠道”页面填写飞书 App ID 和 App Secret。飞书开放平台需要完成：

1. 创建企业自建应用并启用机器人能力；
2. 在事件订阅中选择“使用长连接接收事件”；
3. 添加事件 `im.message.receive_v1`；
4. 开通 `im:message` 和 `im:message.p2p_msg:readonly` 权限；
5. 创建版本并发布最新版本；
6. 将 App ID 和 App Secret 填入科研萌芽，启用后保存并重启程序。

无需公网回调地址。机器人接收文本消息后会复用科研萌芽的研究对话 Agent，回答同时保存在本地会话数据库中。

## 页面入口

| 页面 | 地址 | 用途 |
| --- | --- | --- |
| 首页 | `/` | 开始体验、进入教程和资料库 |
| 研究对话 | `/app` | 日常聊天与研究上下文对话 |
| 领域入门 | `/app/domain-onboarding` | 前置知识、发展路径、概念全景和论文清单 |
| 论文精读 | `/app/paper-reading` | PDF、智能索引、研究总览、选区问答和笔记 |
| 资料库 | `/library` | 会话、论文、文件夹和精读记录 |
| 配置 | `/settings` | 模型、数据目录和飞书渠道 |

## 数据与隐私

- 网页配置接口默认只允许本机访问。
- 模型 API Key、飞书密钥和业务数据保存在本地后端。
- 论文内容只会按功能需要发送给用户配置的模型服务。
- 若要迁移数据，请完整备份配置的数据目录。

## 技术栈

- 前端：Vue 3、TypeScript、Vite，以及按页面拆分的兼容层
- 后端：Python、FastAPI、SQLite
- PDF：PyMuPDF、PDF.js、KaTeX
- 模型协议：OpenAI 兼容 API
- 多渠道：飞书/Lark 长连接
- Windows 发布：PyInstaller

## License

[MIT License](LICENSE)
