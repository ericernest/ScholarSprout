# NoviceSynapse 功能开发说明

这份文档面向论文精读和领域入门功能开发同学。以下功能设计是初步设想，可以讨论修改。

## 当前开发目标

当前只需要实现两个功能：

- 论文精读：`paper_reading`
- 领域入门：`domain_onboarding`

当前先不考虑：

- 会话管理的存取
- 论文管理的存取
- Web UI 改造
- 新 channel
- bus 持久化
- 数据库

功能开发过程中如果对框架或功能有任何问题，随时联系讨论。功能开发完并确定输出格式后，请联系组长接入网页 UI。

## 主要开发文件

论文精读主要改：

```text
handlers/paper_reading_handler.py
```

领域入门主要改：

```text
handlers/domain_onboarding_handler.py
```
可以根据需要注册新的agents和tools

参考文件：

```text
handlers/chat_handler.py
runtime/agent_runner.py
agents/agent.py
tools/base.py
tools/registry.py
tools/builtin/time_tool.py
```

## 功能开发约定

### handler 约定

handler 是每个功能 mode 的业务入口。

函数输入：

- `message: ChannelMessage`
- `app_state: Any`

函数输出：

`ChannelMessage.content` 的类型是 `Any`，建议实际返回 JSON 风格的 `dict`，方便后续 UI 读取和展示。

handler 不负责：

- 处理 Web/QQ 协议
- 直接操作 bus
- 直接修改 gateway route
- 处理会话持久化
- 处理论文持久化

### agent 约定

如果功能需要 agent，可以参考 chat 的实现方式。

相关文件：

```text
agents/agent.py
agents/profiles.py
agents/profiles.json
runtime/agent_runner.py
```

建议方式：

- 在 `agents/profiles.json` 中增加对应 profile
- profile 只描述 `name`、`type`、`role`、`system_prompt`、`tools`
- 不要把用户 API key、base_url、model_name 写进 profile
- 模型配置属于 `config`，运行时模型对象从 `app_state.model` 获取
- 需要统一 agent 执行时，参考 `chat_handler.py` 调用 `run_agent()`

示例方向：

```text
paper_reading -> paper_reading agent profile
domain_onboarding -> domain_onboarding agent profile
```

### tools 约定

如果功能需要工具，可以参考当前时间工具。

相关文件：

```text
tools/base.py
tools/registry.py
tools/builtin/time_tool.py
```

新增工具建议：

- 在 `tools/builtin/` 新建工具文件
- 继承 `BaseTool`
- 定义 `ToolSpec`
- 实现 `run(arguments: dict)`
- 在 `tools/registry.py` 的 `create_builtin_tool_registry()` 中注册
- 在对应 agent profile 的 `tools` 中加入工具名

不要把工具写死在：

- gateway route
- LLM client
- handler 的临时分支里

### runtime 约定

`runtime/agent_runner.py` 当前负责：

- 构造 system 和 user messages
- 调用模型
- 根据 agent profile 读取工具权限
- 执行有限轮工具调用

如果论文精读或领域入门需要 LLM + tools 的 agent 流程，优先参考 `run_agent()`。如果第一版只是整理结构化输出，也可以先在 handler 中写清楚最小逻辑。

## 领域入门初步设想

输入：用户输入一个研究方向或领域。

示例输入：

```text
我想入门多模态大模型方向
```

输出建议包含：

- 前置知识
- 领域发展脉络
- 现状（问题，主要子方向）
- 学习路径

领域发展脉络建议将多个工作聚合形成多个阶段，每个阶段包括：

- 概述
- 动机
- 代表论文
- 核心知识概念
- 主要技术
- 问题

学习路径建议由以下内容生成：

- 前置知识
- 核心概念
- 关键技术
- 代表论文

领域入门第一版可以先返回结构化 JSON，后续再接 UI 展示学习路径、概念关系和论文列表。

## 论文精读初步设想

输入：论文链接。

示例输入：

```text
https://arxiv.org/abs/xxxx.xxxxx
```

输出建议包含：

- 论文背景
- 关键词解释
- 相关工作关系图
- 方法拆解
- 实验拆解
- 代码对应
- 数据集说明
- 已有工作对比
- 单论文知识图谱
- 多论文知识图谱

论文精读第一版可以先完成单篇论文的结构化阅读。多论文知识图谱可以先保留字段或输出占位说明，等论文管理和多论文存取确定后再扩展。


最终输出格式可以讨论修改。格式确定后再联系接入网页 UI。

## doctor 检查建议

功能实现后，可以在 `doctor/app.py` 里增加轻量测试函数。

提交和推送前建议执行：

```bash
novicesynapse doctor
```
