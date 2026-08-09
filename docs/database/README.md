# 本地存储字段设计（v1）

本目录记录论文管理、领域入门、论文精读、会话与会话内记忆的数据库契约。实现位于仓库根级独立包 `storage/`，默认使用 SQLite；PDF 和图片保存在本地文件系统，结构化论文全文、模式结果和会话状态写入同一个 `research.sqlite3`。

## 运行时接入

- 日常聊天：`gateway/message_flow.py` 在 handler 前后分别保存用户消息和可见的助手回复。
- 领域入门：同步 handler 保存完整分块结果；异步 job 在提交、阶段推进和完成/失败时更新同一个 artifact。
- 论文精读：现有 `SessionManager` API 保持不变，但 `PaperReadingStorage` 已改由 SQLite 保存论文、阅读状态、checkpoint 和知识图谱；PDF/图片仍作为本地文件保存。
- 默认数据目录为 `~/.novicesynapse/`，可通过 `NOVICESYNAPSE_DATA_DIR` 修改。领域入门 job 默认也使用同一个 `research.sqlite3`；只有显式配置 `DOMAIN_ONBOARDING_JOB_DB` 时才使用独立文件。

## 字段策略：关系字段 + 分块 JSON

不使用“一整份模式输出 JSON”，也不在模式仍快速演进时将每个概念、每个评分、每个技能输出拆成大量表。具体规则如下：

- **关系字段**：需要去重、关联、筛选或驱动用户动作的信息必须是列或关联表。例如论文身份、推荐顺序、是否加入论文库、会话 fork 关系、当前阅读位置和模式状态。
- **分块 JSON**：一个完整且内部结构仍在开发的产品内容块，使用带 `schema_version` 的 JSON 列。例如领域入门的研究计划、学习路径、质量报告和知识图谱；论文精读的导读地图和技能分析块。
- **不存储**：原始模型供应商响应、内部推理、提示词拼装、重复的会话历史、无实际用途的 `metadata`。消息正文是会话事实；checkpoint 不再复制消息全文。

这样，前端需要稳定读取和筛选的内容稳定，pipeline 的输出结构又可以在不改表的前提下迭代。某个 JSON 块一旦需要独立编辑、跨任务检索或统计，再有证据地拆成表。

## 表与字段

### 论文与论文管理

| 表 | 字段 | 用途 |
|---|---|---|
| `papers` | `paper_id`, `title`, `authors_json`, `abstract`, `publication_year`, `venue`, `doi`, `arxiv_id`, `source_url`, 时间 | 唯一的论文实体；DOI/arXiv 用于去重。被推荐、精读不等于已收藏。 |
| `paper_files` | `paper_file_id`, `paper_id`, `file_kind`, `storage_uri`, `sha256`, `created_at` | PDF、抽取全文或图片的本地文件索引及完整性校验。 |
| `paper_documents` | `paper_id`, `content_schema_version`, `document_json`, `updated_at` | 解析后的章节、图表索引、导读地图及仍在迭代的论文文档结构。 |
| `paper_knowledge_graphs` | `graph_id`, `paper_id`, `graph_scope`, `graph_json`, `updated_at` | 单篇或跨论文知识图谱快照。 |
| `library_items` | `paper_id`, `reading_status`, `note`, `added_at`, `updated_at` | 用户明确“加入论文管理”才创建。阅读状态仅为 `unread / reading / read / archived`。 |

领域入门推荐论文后：

1. 选择“加入论文管理”时，创建/复用 `papers`，再写入 `library_items`。
2. 选择“直接论文精读”时，创建/复用 `papers` 并创建精读会话，但不创建 `library_items`。
3. 后续收藏同一篇已精读论文时，只补 `library_items`，不会复制论文或精读结果。

### 领域入门

| 表 | 字段 | 用途 |
|---|---|---|
| `work_artifacts` | `artifact_id`, `artifact_kind`, `title`, `state`, 时间 | 所有模式实例的薄公共入口。v1 仅有 `domain_onboarding` 与 `paper_reading`。 |
| `domain_onboardings` | `artifact_id`, `query`, `language`, `current_stage`, `output_schema_version`, `learner_profile_json`, `overview_json`, `research_plan_json`, `learning_path_json`, `quality_json`, `knowledge_graph_json`, `error_summary` | 可恢复的领域入门任务。`current_stage` 记录 `planning / retrieval / ranking / generation / quality / repair / completed` 等用户可理解的阶段，而不是内部调用日志。 |
| `domain_recommendations` | `artifact_id`, `paper_id`, `recommendation_rank`, `paper_role`, `reading_priority`, `is_canonical`, `reason`, `reading_focus_json` | 将结构化推荐与真实 `papers` 关联；推荐理由与阅读重点会在论文库和精读入口复用。 |

现有 pipeline 已有领域概览、学习者任务画像、研究计划、前置知识、发展阶段、研究现状、学习路径、精选论文、证据论断、质量结果和知识图谱。因此它们不应被压成一段 Markdown；又因模式仍在开发，除论文推荐外先按上述内容块保存。

### 论文精读与 fork

| 表 | 字段 | 用途 |
|---|---|---|
| `paper_reading_sessions` | `reading_session_id`, `artifact_id`, `paper_id`, `conversation_id`, `parent_reading_session_id`, `fork_context`, `state`, 当前章节/段落、章节数、已激活技能、完成章节、章节状态、`progress_json`、时间 | 保存真实的精读 UI 状态，可暂停、恢复与 fork。`parent_reading_session_id` 取代冗余的子会话 ID 数组。 |
| `paper_reading_blocks` | `reading_block_id`, `reading_session_id`, `block_type`, `content_schema_version`, `content_json`, `rendered_text`, `created_at` | 导读地图、方法分析、数学验证、批判性评价、idea 等可展示结果。每种 block 在一次精读会话中只有当前一版。 |
| `reading_checkpoints` | `checkpoint_id`, `reading_session_id`, 当前章节/段落、已激活技能、`knowledge_graph_json`, `created_at` | 暂停或显式保存时的恢复点；不复制聊天记录。 |

目前精读代码已经有 `active / paused / completed` 状态、章节进度、激活技能、checkpoint、父/子 fork、导读地图以及多个后处理技能输出。这些都是应保存的用户可见状态。模型调用次数、耗时和推理文本仅用于一次响应，不作为产品存档。

### 通用会话与会话内记忆

| 表 | 字段 | 用途 |
|---|---|---|
| `conversations` | `conversation_id`, `title`, `state`, `parent_conversation_id`, `forked_from_message_id`, 时间 | 通用 Agent 对话；可独立于精读模式存在，也能作为精读 fork 的对话承载体。 |
| `messages` | `message_id`, `conversation_id`, `sequence_number`, `role`, `mode`, `channel`, `content`, `created_at` | 三种模式下用户、助手和确需展示的工具消息。论文上传正文不会保存 base64。 |
| `conversation_artifacts` | `conversation_id`, `artifact_id`, `relation`, `linked_at` | 会话与领域入门/论文精读的 `created / continued / discussed` 关系。 |
| `conversation_memory_snapshots` | `memory_snapshot_id`, `conversation_id`, `through_message_id`, `current_goal`, `confirmed_decisions_json`, `open_questions_json`, `summary`, `created_at` | 仅服务当前会话的压缩记忆。fork 可从分叉点的最新快照继承后独立更新。 |

v1 不创建跨会话检索表、全局 memory 表、用户画像或向量库。`conversation_memory_snapshots` 的来源必须指向本会话的消息边界，因此不会被误用为长期记忆。

## 状态边界

- `work_artifacts.state`：模式整体生命周期，`queued / running / paused / completed / failed / cancelled`。
- `domain_onboardings.current_stage`：领域入门当前可恢复阶段；与整体状态互补。
- `paper_reading_sessions.state`：用户的阅读会话，`active / paused / completed`。
- `conversations.state`：聊天是否仍可继续，`active / closed`。

不要把这四类状态合并成一列：它们回答的是不同问题，分别对应模式执行、pipeline 阶段、阅读 UI 和通用聊天。
