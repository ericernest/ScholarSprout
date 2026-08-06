# NoviceSynapse

NoviceSynapse 是一个本地优先的 AI Research Assistant，目标是帮助科研新手建立第一批“科研知识连接”，把方向、论文、概念、方法和实验串联起来。

当前项目使用 Python 开发，部署和运行标准以 Linux 为准。Web 端以聊天页作为统一入口，当前提供三个模式：

- 日常聊天：支持 Markdown、表格、流式回答、思考过程折叠和生成中断。
- 论文精读：从聊天卡片进入独立工作台，支持论文解析、章节索引、PDF 原文定位与缓存、高亮注释、阅读进度恢复、Copilot 问答和 Fork 探索。
- 领域入门：从聊天卡片进入独立工作台，生成学习者画像、前置知识、发展路径、概念全景、学习路线、论文清单和质量评估，并支持任务进度、取消与重试。

## 团队文档

- 架构介绍：[docs/project-structure.md](docs/project-structure.md)
- 功能开发说明：[docs/development-guide.md](docs/development-guide.md)
- Git 分支开发流程：[docs/git-workflow.md](docs/git-workflow.md)
- 领域入门 V1：[docs/domain-onboarding-v1.md](docs/domain-onboarding-v1.md)
- 领域入门前后端交接：[docs/领域入门-前后端接口与展示方案.md](docs/领域入门-前后端接口与展示方案.md)
- 论文精读后端交接：[docs/论文精读-后端功能与接口交接文档.md](docs/论文精读-后端功能与接口交接文档.md)
- 论文精读前端方案：[docs/论文精读-前端接口与展示方案.md](docs/论文精读-前端接口与展示方案.md)
- 论文精读原始设计：[DOCX](docs/reference/paper-reading/论文精读.docx) /
  [PDF](docs/reference/paper-reading/论文精读.pdf)

框架、gateway、channel、bus、基础 chat agent 和 Web UI 已经搭好。功能开发同学请优先阅读功能开发说明，并主要在 `handlers/` 中完成论文精读和领域入门逻辑。

## 快速开始

推荐 Python 版本：`Python 3.11`

使用 conda：

```bash
conda create -n novicesynapse python=3.11 -y
```

```bash
conda activate novicesynapse
```

进入项目目录并安装：

```bash
cd NoviceSynapse
```

```bash
pip install -e .
```

## 配置模型

首次运行或修改模型时执行：

```bash
novicesynapse config
```

配置文件保存在：

```text
~/.novicesynapse/config.json
```

中科大 LLM 平台示例配置：

```json
{
  "client": {
    "api_key": "<your-ustc-api-key>",
    "base_url": "https://api.llm.ustc.edu.cn/v1",
    "model_name": "qwen-chat",
    "timeout": 60.0,
    "max_retries": 2,
    "input_cost_per_million_tokens": null,
    "output_cost_per_million_tokens": null
  }
}
```

注意：配置文件中的 `api_key` 只填写 key 本身，不需要加 `Bearer ` 前缀；OpenAI SDK 会自动生成 `Authorization: Bearer <api_key>` 请求头。

也可以使用其它兼容 OpenAI SDK 的模型服务，只要提供 `base_url`、`api_key` 和可用的 `model_name`。

如需估算领域入门重试产生的额外货币成本，可以按模型计价填写每百万输入、输出 token 单价。未配置单价时仍会记录实际 token，但 `estimated_cost` 返回 `null`。

## CLI 命令

查看命令：

```bash
novicesynapse --help
```

交互式配置：

```bash
novicesynapse config
```

检查配置：

```bash
novicesynapse doctor
```

启动本地 gateway：

```bash
novicesynapse gateway --host 127.0.0.1 --port 8000
```

## Gateway 接口命令

启动 gateway 后，可以用命令行直接访问当前三个功能入口。

日常聊天：

```bash
curl -X POST http://127.0.0.1:8000/chat -H "Content-Type: application/json" -d '{"session_id":"s1","content":"你好","user_id":"local","metadata":{}}'
curl -X POST http://127.0.0.1:8000/chat -H "Content-Type: application/json" -d '{"session_id":"s1","content":"现在是什么时间","user_id":"local","metadata":{}}'
```

论文精读：

```bash
curl -X POST http://127.0.0.1:8000/paper_reading -H "Content-Type: application/json" -d '{"session_id":"s1","content":"请帮我精读这篇论文：https://arxiv.org/abs/xxxx.xxxxx","user_id":"local","metadata":{}}'
```

领域入门：

```bash
curl -X POST http://127.0.0.1:8000/domain_onboarding -H "Content-Type: application/json" -d '{"session_id":"s1","content":"我想入门多模态大模型方向","user_id":"local","metadata":{}}'
```

聊天和论文精读同时提供 SSE 流式接口，使用 `curl -N` 可以直接查看增量输出：

```bash
curl -N -X POST http://127.0.0.1:8000/chat/stream -H "Content-Type: application/json" -d '{"session_id":"s1","content":"解释一下 RAG","user_id":"local","metadata":{}}'
curl -N -X POST http://127.0.0.1:8000/paper_reading/stream -H "Content-Type: application/json" -d '{"session_id":"s1","content":"总结当前章节","user_id":"local","metadata":{}}'
```

领域入门的独立工作台使用异步任务接口：

- 创建任务：`POST /domain_onboarding/jobs`
- 查询任务：`GET /domain_onboarding/jobs/{task_id}`
- 订阅进度：`GET /domain_onboarding/jobs/{task_id}/events`
- 取消任务：`DELETE /domain_onboarding/jobs/{task_id}`
- 重试任务：`POST /domain_onboarding/jobs/{task_id}/retry`

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

领域入门运行指标：

```bash
curl http://127.0.0.1:8000/metrics/domain_onboarding
```

该接口返回请求耗时、重试率、改善率、额外模型调用次数、额外 token 和可选费用估算。指标保存在当前 Gateway 进程内，服务重启后重新计数。

## Web UI

启动 gateway 后访问首页：

```text
http://127.0.0.1:8000/
```

点击“开始体验”进入聊天页，也可以直接访问：

```text
http://127.0.0.1:8000/app
```

聊天页默认模式是“日常聊天”。点击输入框旁边的 `+` 可以选择：

- 日常聊天：`chat`
- 论文精读：`paper_reading`
- 领域入门：`domain_onboarding`

选择非默认模式后，输入框内会出现当前模式的小气泡，点击 `×` 可以取消并回到日常聊天。

论文精读和领域入门都会先在聊天中返回任务卡片。论文解析或领域入门任务准备完成后，点击卡片进入对应工作台：

```text
http://127.0.0.1:8000/app/paper-reading
http://127.0.0.1:8000/app/domain-onboarding
```

模型生成期间可以点击“中断”。流式输出结束后，页面只保留最终回答，思考过程默认折叠在回答上方；回答会定位到内容开头，便于从头阅读。

## 测试

运行完整测试：

```bash
python -m pytest -q
```
