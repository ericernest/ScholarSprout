# NoviceSynapse 架构介绍

这份文档用于说明 NoviceSynapse 当前框架结构、目录职责和消息流。功能开发细节请看 [development-guide.md](development-guide.md)。

## 当前范围

当前已经完成：

- Typer CLI：`config`、`doctor`、`gateway`、`agent`
- 本地配置读取与保存
- OpenAI SDK 兼容模型客户端
- FastAPI gateway
- Web UI 首页和聊天页
- channel 和 bus 的第一版骨架
- chat agent、agent runner 和工具调用骨架
- Default Skill、Special Skill 注册与按需加载骨架
- 内置工具 `get_current_time`

当前主要业务功能：

- 论文精读：`paper_reading`
- 领域入门：`domain_onboarding`

## 目录职责

```text
NoviceSynapse/
|-- agents/
|   |-- agent.py
|   |-- profiles.py
|   `-- profiles.json
|-- bus/
|   |-- events.py
|   `-- message_bus.py
|-- channels/
|   |-- base.py
|   `-- web.py
|-- cli/
|   `-- main.py
|-- config/
|   |-- app.py
|   |-- manager.py
|   `-- schema.py
|-- doctor/
|   `-- app.py
|-- gateway/
|   |-- app.py
|   |-- message_flow.py
|   `-- static/
|       |-- index.html
|       |-- chat.html
|       |-- app.js
|       `-- style.css
|-- handlers/
|   |-- chat_handler.py
|   |-- paper_reading_handler.py
|   |-- paper_reading/
|   |   |-- handler.py
|   |   |-- pipeline/
|   |   |-- schemas/
|   |   |-- kg/
|   |   |-- harness/
|   |   `-- postprocessors/
|   |-- domain_onboarding_handler.py
|   `-- domain_onboarding/
|-- models/
|   `-- client.py
|-- runtime/
|   `-- agent_runner.py
|-- skills/
|   |-- models.py
|   |-- loader.py
|   |-- registry.py
|   |-- selector.py
|   `-- builtin/
|       |-- domain/
|       |-- reading/
|       |-- chat/default_chat/SKILL.md
|       |-- chat/research_discussion/SKILL.md
|       `-- custom/
|-- tools/
|   |-- base.py
|   |-- registry.py
|   `-- builtin/
|       `-- time_tool.py
|-- README.md
`-- pyproject.toml
```

## CLI 层

位置：`cli/main.py`

职责：

- 注册命令入口
- 调用配置流程
- 调用 doctor 检查
- 启动 gateway

当前命令：

- `novicesynapse config`
- `novicesynapse doctor`
- `novicesynapse gateway --host 127.0.0.1 --port 8000`
- `novicesynapse agent`

`agent` 当前只是占位命令。

## config 层

位置：`config/`

职责：

- 定义配置结构
- 从 `~/.novicesynapse/config.json` 读取配置
- 保存模型配置
- 提供交互式配置流程

当前配置重点是 `client`：

- `api_key`
- `base_url`
- `model_name`
- `timeout`
- `max_retries`

## doctor 层

位置：`doctor/app.py`

职责：

- 聚合项目检查项
- 当前检查 `base_url`、`api-key`、`model_name`
- 后续可以增加功能级轻量测试

## gateway 层

位置：`gateway/`

职责：

- 声明 FastAPI app
- 注册 Web 页面和 HTTP 功能入口
- 启动模型、agent、Skill/Tool Registry、Capability Selector、message bus 和默认 channel
- 将 route 指定的 handler 交给统一消息流程

当前入口：

- `GET /`
- `GET /app`
- `GET /health`
- `POST /chat`
- `POST /paper_reading`
- `POST /domain_onboarding`

`gateway/app.py` 只注册功能入口，不直接写功能业务。

`gateway/message_flow.py` 负责统一流程：

```text
外部输入 source
-> channel.receive_message()
-> channel.publish_inbound()
-> bus 记录 MESSAGE_RECEIVED
-> route 指定的 handler
-> build outbound ChannelMessage
-> channel.send_outbound()
-> bus 记录 MESSAGE_SENT
```

公共流程不绑定 Web，也不绑定 QQ。当前 Web route 传入的是 FastAPI `Request`，以后 QQ channel 可以传入 QQ 原始事件。

## channels 层

位置：`channels/`

职责：

- 将外部平台输入转换为统一 `ChannelMessage`
- 将系统输出发送回外部平台
- 不调用 agent
- 不调用 LLM
- 不判断具体业务 mode

当前只有 `WebChannel`。

`BaseChannel.receive_message(source, mode)` 是渠道输入适配入口。不同 channel 可以有不同的 `source`，但最终都应该返回 `ChannelMessage`。

## bus 层

位置：`bus/`

职责：

- 记录消息事件
- 区分 inbound 和 outbound
- 当前只做内存记录

bus 不负责：

- handler 分发
- mode 判断
- agent 调用
- 持久化

## handlers 层

位置：`handlers/`

职责：

- 每个功能 mode 的业务入口
- 接收 `ChannelMessage`
- 使用 `app_state` 取已有 agent、Skill/Tool Registry 等对象
- 返回 JSON 风格的结果

当前文件：

- `chat_handler.py`：已接入 chat agent 和 agent runner
- `paper_reading_handler.py`：论文精读功能开发入口
- `domain_onboarding_handler.py`：领域入门功能开发入口

领域入门 V1 的业务实现已经收敛到 `handlers/domain_onboarding/`，Handler 只负责
请求转换、调用 Pipeline、响应转换和指标记录。详细模块和约束见
[domain-onboarding-v1.md](domain-onboarding-v1.md)。

## agents 层

位置：`agents/`

职责：

- 定义 agent profile
- 从 `profiles.json` 读取预设 profile
- 根据 agent type 创建 agent

用户模型配置属于 `config`。Agent Profile 描述角色、提示词、Default Skill、候选 Special Skill 和工具权限，Agent 运行时持有对应的 LLM 实例。

## models 层

位置：`models/client.py`

职责：

- 根据配置创建 OpenAI SDK client
- 使用 `api_key`、`base_url`、`model_name` 调用模型
- 对外提供 `chat()` 方法

模型调用不要写在 gateway route 中。

## runtime 层

位置：`runtime/agent_runner.py`

职责：

- 执行 agent
- 加载 Agent Profile 指定的 Default Skill
- 从候选列表中按需选择零个或一个 Special Skill
- 拼接 Agent Role、Default Skill、Special Skill 和 user message
- 将 Profile 授权的全部 Tool schema 交给主模型
- 通过 `agent.llm` 调用模型
- 处理一轮或有限轮工具调用

Profile 只有 Default Skill 时，Runtime 直接加载它，不调用 Selector。Profile 没有任何 Skill 配置时保留基础 Prompt 行为。无论是否选择 Special Skill，Runtime 都会提供 Profile 中全部授权 Tool。

## skills 层

位置：`skills/`

职责：

- 从内置目录和 `~/.novicesynapse/skills/` 扫描 Skill
- 解析并校验 `SKILL.md` 的 YAML Front Matter
- 初始扫描只建立元数据索引，被选中后再加载完整正文
- 按 Profile 的可选 `default_skill` 加载模式通用 Skill
- 根据精确 ID 或 `domain.*` 等分类通配解析候选 Special Skill
- 为当前任务选择零个或一个 Special Skill

Default Skill 保存当前模式每次都需要的通用方法，Profile 配置后由 Runtime 固定加载。Special Skill 保存只对部分任务生效的专项方法，由 Selector 按当前任务选择。Skill 描述方法、步骤和输出要求，不声明 Tool，也不执行 Python。

chat Agent 配置 Default Skill `chat.default` 和 Special Skill `chat.research_discussion`。论文精读 Agent 配置 Default Skill `reading.method_analyst`，并注册八个 `reading.*` Special Skill。领域入门执行要求仍保存在其 Profile 的 `system_prompt` 中。

## tools 层

位置：`tools/`

职责：

- 定义工具基类
- 注册内置工具
- 将工具转换为 OpenAI tools schema
- 将 Agent Profile 授权的全部 Tool 提供给主模型，并在执行时再次校验权限

当前内置工具：

- `get_current_time`
- `paper_search`
- `pdf_parse`
- `kg_query`
- `kg_build`

## Web UI

位置：`gateway/static/`

职责：

- 提供首页
- 提供聊天页
- 调用当前 gateway HTTP route

当前页面：

- `GET /` 首页
- `GET /app` 聊天页

UI 当前可以选择：

- 日常聊天：`chat`
- 论文精读：`paper_reading`
- 领域入门：`domain_onboarding`

## 当前消息流

以 Web UI 调用论文精读为例：

```text
Web UI
-> POST /paper_reading
-> gateway route 指定 handle_paper_reading_message
-> process_channel_input()
-> WebChannel.receive_message()
-> ChannelMessage(direction="inbound", mode="paper_reading")
-> MessageBus 记录 MESSAGE_RECEIVED
-> handle_paper_reading_message(message, app_state)
-> ChannelMessage(direction="outbound", content=handler_result)
-> MessageBus 记录 MESSAGE_SENT
-> 返回给 Web UI
```
