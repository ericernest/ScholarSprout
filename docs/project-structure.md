# NoviceSynapse 当前结构记录

这份文档用于记录当前可运行框架，方便后续在额度切换或换工具时继续开发。

## 当前范围

当前已实现：
- CLI 层
- 交互式 config 命令
- doctor 配置检查聚合
- OpenAI SDK client 声明
- gateway 模块级 app 与 start_gateway_server
- JSON agent profile 加载
- 默认 chat agent 初始化
- WebChannel 和内存 MessageBus
- chat agent runner 和一轮工具调用
- 简单 Web UI

## 当前 CLI TODO

- CLI 这一层当前有 4 个入口：`gateway()`、`config()`、`doctor()`、`agent()`
- `agent()` 待做
- `config()` 当前为交互式模型配置流程
- `doctor()` 当前检查 `base_url` 和 `api-key` 是否可用
- 当前正在开发 `gateway()`

当前未实现：
- 多 channel
- 论文精读真实业务
- 方向入门真实业务

## 当前目录结构

```text
NoviceSynapse/
├─ agents/
│  ├─ __init__.py
│  ├─ agent.py
│  ├─ profiles.py
│  └─ profiles.json
├─ cli/
│  ├─ __init__.py
│  └─ main.py
├─ config/
│  ├─ __init__.py
│  ├─ app.py
│  ├─ manager.py
│  └─ schema.py
├─ doctor/
│  ├─ __init__.py
│  └─ app.py
├─ docs/
│  └─ project-structure.md
├─ gateway/
│  ├─ __init__.py
│  └─ app.py
├─ models/
│  ├─ __init__.py
│  └─ client.py
├─ tools/
│  └─ __init__.py
├─ LICENSE
├─ README.md
└─ pyproject.toml
```

## gateway 启动流程

`gateway/app.py` 当前按以下方式组织：

1. 模块级声明 `app = FastAPI(title="NoviceSynapse Gateway")`
2. 路由保留 `/health` 和预留 `/ask`
3. `start_gateway_server()` 内部读取配置
4. 用 `config.client` 创建 `OpenAIClient`
5. 用 `create_agent(model, "chat")` 创建默认 chat agent
6. 将 `model` 写入 `app.state.model`
7. 将 `chat_agent` 写入 `app.state.chat_agent`
8. 调用 `uvicorn.run(app, host=host, port=port)`

## 分层边界

CLI 层负责：
- 挂载命令入口
- 调用 gateway 启动函数
- 调用 doctor 检查函数
- 调用 config 交互流程

config 层负责：
- 配置 schema
- 配置加载与保存
- 配置命令逻辑

doctor 层负责：
- 聚合配置检查项
- 检查 `base_url` 是否为空或格式错误
- 检查 `api-key` 是否为空

models 层负责：
- 从配置读取连接信息
- 用 `api_key`、`base_url`、`timeout`、`max_retries` 创建 OpenAI SDK client

agents 层负责：
- 从 JSON 读取 agent profile
- 根据 `agent_type` 生成 system_prompt、role、tools
- 绑定 llm 得到运行时 agent

gateway 层负责：
- 声明 FastAPI app
- 在 `start_gateway_server()` 内初始化 client 和默认 chat agent
- 将后续路由要用的对象挂到 `app.state`
- 暴露基础路由

## 当前配置结构

当前配置只保留 `client`：
- `api_key`
- `base_url`
- `model_name`
- `timeout`
- `max_retries`

## 当前命令

源码目录下可直接运行：

```bash
python -m cli.main --help
python -m cli.main config
python -m cli.main gateway --host 127.0.0.1 --port 8000
python -m cli.main doctor
python -m cli.main agent
```

## channels、bus 和 session message API

当前以本节为准。

新增目录：

```text
NoviceSynapse/
|-- bus/
|   |-- __init__.py
|   |-- events.py
|   `-- message_bus.py
|-- channels/
|   |-- __init__.py
|   |-- base.py
|   `-- web.py
|-- handlers/
|   |-- __init__.py
|   |-- chat_handler.py
|   |-- paper_reading_handler.py
|   `-- domain_onboarding_handler.py
|-- runtime/
|   |-- __init__.py
|   `-- agent_runner.py
|-- gateway/
|   |-- app.py
|   |-- message_flow.py
|   `-- static/
|       |-- index.html
|       |-- chat.html
|       |-- app.js
|       `-- style.css
```

`channels` 的边界：
- channel 是外部平台适配器，负责 Web/QQ/CLI 等外部渠道进入和出去
- 当前只实现 `WebChannel`
- `WebChannel.create_inbound_message()` 将 Web 请求创建为 inbound `ChannelMessage`
- `WebChannel.publish_inbound()` 将 inbound message 发布到共享 `MessageBus`
- `WebChannel.send_outbound()` 第一版只记录 outbound，HTTP response 由 route 返回
- channel 不调用 model、agent，不判断 mode，不实现业务流程

`bus` 的边界：
- 当前只实现内存 `MessageBus`
- `publish()` 保存 `BusEvent`
- `publish_message()` 根据 `message.direction` 生成 `MESSAGE_RECEIVED` 或 `MESSAGE_SENT`
- `get_events()` 返回全部事件或指定 session 的事件
- bus 不调用 model、agent，不判断 mode，不处理 Web/QQ/CLI 协议，不做持久化

`tools` 的边界：
- `tools/base.py` 定义 `ToolSpec` 和 `BaseTool`
- `tools/registry.py` 定义 `ToolRegistry` 和 `create_builtin_tool_registry()`
- `tools/builtin/time_tool.py` 实现 `get_current_time`
- 工具只负责执行自身能力，不调用 agent、LLM、route，不判断 mode

gateway 启动阶段挂载：
- `app.state.model = model`
- `app.state.tool_registry = tool_registry`
- `app.state.message_bus = message_bus`
- `app.state.default_channel_name = input_channel.name`
- `app.state.channels = {input_channel.name: input_channel}`
- `input_channel.start()` 在启动阶段调用，当前第一版为 no-op

当前 chat agent profile：
- `name`: `default_chat`
- `type`: `chat`
- `role`: `chat`
- `tools`: `["get_current_time"]`

chat handler 流程：
1. `handle_chat_message()` 只调用 `run_agent()`
2. `runtime/agent_runner.py` 负责单次 agent 执行
3. `run_agent()` 从 agent profile 读取 system_prompt 和 tools
4. `run_agent()` 调用 `model.chat()` 并处理 tool calls
5. 工具必须通过 `tool_registry.get()` 获取
6. `max_steps` 控制工具调用循环上限
7. route 不直接调用工具，不直接调用模型，不拼接 prompt

新入口：
- `POST /chat`
- `POST /paper_reading`
- `POST /domain_onboarding`
- 请求体包含 `session_id`、`content`、`user_id`、`metadata`

Web UI：
- `GET /` 返回首页
- `GET /app` 返回聊天页
- 页面只调用现有 HTTP route，不直接操作 channel、bus、agent 或 tool

请求流程：
1. 三个 HTTP 入口直接写在 `gateway/app.py`
2. route 调用 `process_channel_input()`，不直接绑定具体 channel 实例
3. `process_channel_input()` 根据 `app.state.default_channel_name` 从 `app.state.channels` 取出默认 channel
4. channel 调用 `receive_message()` 将外部输入转换为 inbound `ChannelMessage`
5. `process_channel_message()` 调用 `channel.publish_inbound()`，bus 记录 `MESSAGE_RECEIVED`
6. `process_channel_message()` 调用 route 指定的 handler 完成处理
7. `process_channel_message()` 包装 outbound message 并调用 `channel.send_outbound()`，bus 记录 `MESSAGE_SENT`
8. route 返回 outbound `ChannelMessage` 给 Web UI

当前第一版中，bus 负责记录和承接消息事件，不主动调度 handler；handler 由 gateway route 以注册路由的形式传入统一消息流程。

已废弃入口：
- `POST /api/sessions/{session_id}/messages` 已从 gateway 中删除
- `GET /api/sessions/{session_id}/events` 已从 gateway 中删除
- `POST /ask` 已从 gateway 中删除
