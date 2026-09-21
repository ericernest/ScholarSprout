# QQ 端机器人接入方案

> 接入形态：QQ 官方机器人 C2C 私聊；统一消息模型：`ChannelMessage`；业务模式：`chat`、`domain_onboarding`、`paper_reading`。本文说明当前 QQ 端的功能边界、实现链路和主要技术难点。

## 功能介绍

QQ 端作为 NoviceSynapse 的外部 Channel Adapter，不单独实现模型或科研流程，而是把 QQ 私聊消息转换为统一消息并复用现有 Handler、Pipeline、资料库和论文精读能力。

| 功能 | 用户侧行为 | 后端复用 |
| --- | --- | --- |
| 日常聊天 | 发送普通文本，机器人返回科研问答或日常对话 | `handle_chat_message` |
| 领域入门 | 输入研究领域，返回入门卡片正文，不附加网页入口 | `handle_domain_onboarding_message` |
| 论文精读 | 支持论文链接和 QQ PDF 附件 | 飞书论文输入适配及现有论文精读流程 |
| 模式切换 | 支持日常聊天、领域入门、论文精读三种模式 | QQ Channel 内的会话模式状态 |
| 模式与正文同条提交 | 如“领域入门 大模型”，切换后立即生成内容 | `_parse_mode_switch` + 业务路由 |
| 对话入库 | QQ 的用户消息和机器人回复均写入网页端资料库 | `process_channel_message`、`record_inbound`、`record_outbound` |
| 纯文本输出 | Markdown、LaTeX、表格和 JSON 转成 QQ 可读文本 | `format_plain_text` |
| 自动重连 | 长连接中断后使用已配置凭据重建客户端 | `QQChannel.start` 重连循环 |

当前支持的典型模式命令如下：

```text
日常聊天
切换到日常聊天模式
领域入门
领域入门 大模型
/入门：多模态大模型
论文精读
/论文 https://arxiv.org/abs/1706.03762
```

PDF 附件属于无歧义论文输入。用户发送 PDF 时，系统会自动切换到 `paper_reading`，不要求先发送模式切换命令。

## 实现方式

### 整体链路

```text
QQ C2C 消息
  → QQBotClient.on_c2c_message_create
  → QQChannel.handle_c2c_message
  → ChannelMessage(channel="qq")
  → process_channel_message
  → QQ Router / 业务 Handler
  → 资料库存储
  → QQChannel.send_outbound
  → QQ C2C 被动回复
```

QQ 端只负责平台协议适配。日常聊天和领域入门直接调用已有 Handler；论文精读通过适配器复用既有的 PDF 上传、解析、阅读会话和论文分析流程。

### 启动与连接

Gateway 从环境变量读取：

| 配置 | 用途 | 默认行为 |
| --- | --- | --- |
| `QQ_APP_ID` | QQ 机器人 AppID | 缺失时不注册 QQ Channel |
| `QQ_APP_SECRET` | QQ 机器人 AppSecret | 缺失时不注册 QQ Channel |
| `QQ_RECONNECT_INITIAL_SECONDS` | 初始重连等待时间 | 5 秒 |
| `QQ_RECONNECT_MAX_SECONDS` | 最大重连等待时间 | 60 秒 |
| `QQ_PDF_MAX_BYTES` | PDF 下载大小上限 | 30 MiB |

AppID 和 AppSecret 是长期配置。每次连接或重连时由 `botpy` 重新获取短期凭证，因此网络中断或长时间未连接后不需要人工重新输入密钥。连续连接失败时采用指数退避，连接稳定运行后恢复初始等待时间。

### 消息标准化与会话标识

每个 QQ 用户使用以下会话标识：

```text
qq_private_<user_openid>
```

文本或附件会被转换为：

```python
ChannelMessage(
    session_id="qq_private_<openid>",
    channel="qq",
    direction=INBOUND,
    mode="chat | domain_onboarding | paper_reading",
    content="文本或 PDF 附件信息",
    user_id="<openid>",
    metadata={
        "qq_message_id": "...",
        "qq_openid": "...",
        "message_type": "c2c",
        "has_pdf_attachment": True | False,
    },
)
```

`qq_message_id` 同时用于事件去重和异步处理完成后的被动回复定位。

### 模式识别与路由

QQ Channel 为每个 `session_id` 保存当前模式，默认值为 `chat`。模式解析同时支持：

1. 纯模式切换，例如“切换到领域入门模式”；
2. 模式切换加正文，例如“领域入门 大模型”；
3. 中英文命令和斜杠命令；
4. 当前模式查询，例如“当前模式”。

纯切换命令只返回确认文本；命令后存在正文时，会更新 `message.mode` 和 `message.content`，然后继续进入新模式 Handler，避免主题被普通聊天 Agent 处理。

路由关系如下：

| `mode` | 处理方式 |
| --- | --- |
| `chat` | `handle_chat_message` |
| `domain_onboarding` | `handle_domain_onboarding_message` |
| `paper_reading` | `handle_feishu_mode_message(..., mode="paper_reading")` |

### PDF 附件适配

QQ 附件先提取 URL、文件名和 MIME 类型。下载前只接受 HTTP/HTTPS 地址，下载时限制声明长度和实际读取长度，下载完成后验证 `%PDF-` 文件头。

由于既有论文流程使用飞书文件输入约定，QQ Router 会把附件地址转换为 `file_key`，再通过 `download_adapter` 把下载动作映射回 `_download_qq_file`。这样无需复制论文上传和解析代码。

### 资料库与消息持久化

所有正式业务消息都经过 `process_channel_message`：

```text
publish inbound
→ record inbound
→ execute handler
→ build outbound
→ record outbound
→ send outbound
```

因此 QQ 端对话与网页端使用同一存储链路。网页资料库可按 `channel="qq"` 和 `session_id` 区分来源，同时保留业务模式和平台消息 ID。

### 回复格式与发送

Handler 可以返回字符串、字典或带文本属性的对象。QQ Channel 按 `text`、`content`、`answer`、`message` 的优先级提取回复，再调用 `format_plain_text`：

- 删除 Markdown 标题、强调、代码围栏等源码符号；
- 将 Markdown 表格转换为普通文本分隔格式；
- 将常见 LaTeX 表达式转换为可读字符；
- 将多行 JSON 转换为字段列表；
- 普通模式保留论文链接；
- 领域入门模式清除“前往网页端查看”及其网址，直接显示正文。

`send_outbound` 是同步 Channel 接口，而 QQ SDK 回复 API 是异步接口。实现通过 `asyncio.run_coroutine_threadsafe` 把回复任务提交到 QQ WebSocket 所在事件循环。

## 技术难点

### 同步消息流与异步 SDK 的衔接

公共 `process_channel_message` 是同步流程，QQ 事件回调和回复接口是异步流程。如果在 WebSocket 事件循环中直接执行模型与论文 Pipeline，会阻塞心跳并导致断线。

当前实现把业务处理放入工作线程；生成完成后，再把 `_reply` 协程安全地提交回 QQ 事件循环。该边界既保证长连接稳定，也保持公共消息流不依赖具体 SDK。

### 被动回复的原消息关联

QQ C2C 被动回复需要原始 `msg_id` 和用户 `openid`。业务 Handler 完成时只持有统一 `ChannelMessage`，因此 Channel 在接收阶段暂存 `message_id → C2CMessage` 映射，在发送完成后弹出。

映射最多保留 1000 条，防止长时间运行导致内存无限增长。若业务流程未来改为超长异步任务，需要评估 QQ 被动回复窗口，并考虑主动消息或持久化关联表。

### 重复投递与幂等

QQ Gateway 可能重复投递同一事件。如果重复消息进入模型流程，会产生重复回复、重复调用成本和重复资料库记录。

当前通过最近 1000 个 `qq_message_id` 的进程内集合去重。该方案适合单进程 Gateway；多进程或多副本部署时，应改为 Redis 等共享幂等存储。

### 模式状态与自然语言命令冲突

模式状态按 QQ 用户保存在进程内。如果只识别整条消息等于命令，“领域入门 大模型”会落回旧模式；如果无条件识别前缀，又可能把普通句子误判为命令。

当前要求命令和正文之间存在空格、逗号、冒号等明确分隔符，并按最长命令优先匹配。服务重启后模式恢复为 `chat`；如需跨重启延续，应把模式写入会话存储。

### PDF 下载安全

附件 URL 来自外部平台，存在异常协议、超大文件和伪造扩展名风险。当前实现完成协议校验、下载超时、大小上限和 PDF 文件头校验，但仍属于基础防护。

生产环境可进一步增加域名白名单、内容病毒扫描、重定向次数限制和下载网络隔离，以降低 SSRF 与恶意文件风险。

### 富文本降级

模型输出包含 Markdown、公式、表格和结构化 JSON，而 QQ C2C 当前使用普通文本消息。直接发送会暴露源码格式，影响阅读。

统一纯文本格式器在不改变网页端持久化内容的前提下只处理平台发送副本。领域入门还要移除网页跳转引导，但不能误删日常聊天和论文精读中的正常引用链接，因此清理逻辑必须按 `message.mode` 执行。

### 部署边界

- 当前只处理 QQ C2C 私聊，不包含群聊 @、频道消息和交互式卡片。
- 会话模式、去重集合和回复映射均为进程内状态，默认面向单 Gateway 进程。
- AppID/AppSecret 必须通过安全配置注入，不应写入源码或日志。
- 多副本部署前需要共享会话状态、去重状态和回复关联，并确认 QQ 平台的连接与消息分片策略。

