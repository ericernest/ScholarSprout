# 本地存储字段设计（v1）

本目录记录论文管理、领域入门、论文精读、会话与会话内记忆的数据库契约。实现位于仓库根级独立包 `storage/`，默认使用 SQLite；PDF 和图片保存在本地文件系统，结构化论文全文、模式结果和会话状态写入同一个 `research.sqlite3`。

## 运行时接入

- 日常聊天：`gateway/message_flow.py` 在 handler 前后分别保存用户消息和可见的助手回复；同一页面会话始终复用一个 `conversation_id`，只有显式点击“新会话”才生成新 ID，标题取第一次提问。
- 领域入门：同步 handler 保存完整分块结果；异步 job 在提交、阶段推进和完成/失败时更新同一个 artifact。
- 论文精读：现有 `SessionManager` API 保持不变，但 `PaperReadingStorage` 已改由 SQLite 保存论文、阅读状态、checkpoint 和知识图谱；PDF/图片仍作为本地文件保存。上传、解析状态和详情查询不会创建会话；用户明确点击“开始论文精读”时创建空会话，后续 Agent 问答继续写入该会话。
- 默认数据目录为 `~/.novicesynapse/`，可在 `/settings` 配置向导或 `~/.novicesynapse/config.json` 的 `storage.data_dir` 修改。`NOVICESYNAPSE_DATA_DIR` 仍作为部署环境的最高优先级覆盖项。领域入门 job 默认也使用同一个 `research.sqlite3`；只有显式配置 `DOMAIN_ONBOARDING_JOB_DB` 时才使用独立文件。

数据目录修改后需要重启服务。新目录承接重启后的新读写，不会自动搬迁旧目录的数据；这样可以避免一次普通配置修改暗中移动或覆盖用户文件。

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
| `paper_folders` | `folder_id`, `name`, `parent_folder_id`, 时间 | 论文管理中的多层文件夹树；同一父目录下名称唯一，不同分支允许同名。父目录筛选会包含全部后代文件夹。 |
| `library_items` | `paper_id`, `reading_status`, `note`, `folder_id`, `added_at`, `updated_at` | 进入论文管理的论文及其阅读状态、备注和分类。阅读状态仅为 `unread / reading / read / archived`。 |
| `paper_annotations` | `annotation_id`, `paper_id`, `reading_session_id`, `annotation_type`, `color`, `page_number`, `section_id`, `selected_text`, `anchor_schema_version`, `anchor_json`, `note_text`, 时间 | PDF 高亮与注释。`anchor_json` 使用 `pdf-rects-v1`，保存页面内归一化矩形，因此缩放后仍能准确恢复；会话字段只记录来源，标注归属于论文并在该论文的 Fork/精读会话间共享。 |
| `paper_notes` | `paper_id`, `content_markdown`, `created_at`, `updated_at` | 每篇论文唯一的一份 Markdown 笔记。笔记归属于论文，不归属于某次精读会话，因此从任意精读会话进入都会读取同一内容。 |

领域入门推荐论文后：

1. 选择“加入论文管理”时，创建/复用 `papers`，再写入 `library_items`。
2. 选择“论文精读”时，导入/复用论文并默认写入 `library_items`；从论文管理点击“开始论文精读”时先创建并持久化精读会话，再把 `unread` 推进为 `reading`，不会覆盖用户已有的备注或文件夹。
3. 论文管理页也可直接上传 PDF 或粘贴 PDF 链接，导入完成即进入管理列表，并提供相同的精读入口。

推荐论文已经有 `paper_id` 时，精读导入会把 PDF 附着到该论文实体，而不是另建一条同名记录；没有可下载链接时，论文卡片会要求用户选择本地 PDF。

用户重复上传文件时先按 PDF 的 SHA-256 去重；在线论文还会按 arXiv ID/来源链接复用已有论文。命中后返回原 `paper_id`，不会新增论文管理条目。

PDF 的 outline/bookmark 是可选能力，并非所有出版平台都会写入。存在时直接作为目录；不存在时根据正文标题生成章节索引，此状态属于普通信息提示而不是解析错误。摘要依次尝试独立标签、同行 `Abstract—...`、PDF Subject 元数据和首页长段落回退。

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
- `library_items.reading_status`：论文管理状态，`unread / reading / read / archived` 分别显示为“未读 / 阅读中 / 完成 / 归档”；导入默认为未读，开始精读自动推进为阅读中，完成和归档由用户在管理区手动切换。
- `conversations.state`：聊天是否仍可继续，`active / closed`。

不要把这四类状态合并成一列：它们回答的是不同问题，分别对应模式执行、pipeline 阶段、阅读 UI 和通用聊天。

## 前端资料库与 API

`/library` 是统一研究资料库，左侧四个视图分别读取会话、领域入门、论文精读和论文管理数据。列表只读取稳定展示字段及必要计数，不直接暴露数据库中的 JSON 字符串。领域入门记录直接进入领域工作台；会话按其最新关联产物进入领域入门或论文精读工作台，只有纯聊天会话返回聊天页。

- `GET /api/research/conversations`：会话标题、模式、消息数和最后一条消息摘要。
- `GET /api/research/domain-onboardings`：领域任务状态、阶段、推荐论文数和质量摘要。
- `GET /api/research/domain-onboardings/{artifact_id}`：提供领域结果的稳定持久化字段及推荐论文信息。
- `GET /api/research/domain-onboardings/{artifact_id}/workspace`：优先恢复仍在 job store 中的实时任务；任务记录已过期时，从正式持久化内容重建只读工作台快照。
- `GET /api/research/paper-readings`：论文、阅读进度、分析块及标注数。
- `GET /api/research/papers`：论文管理状态、备注、文件夹、最新精读会话、精读及标注数；可按 `folder_id` 筛选，也可用 `reading_scope=reviewed/unreviewed` 区分是否已经创建过精读会话。
- `GET/POST/PATCH/DELETE /api/research/paper-folders`：读取、新建、重命名、移动和删除文件夹；为防误删，仅空文件夹可以删除。
- `PATCH /api/research/papers/{paper_id}/folder`：只移动论文归属，不覆盖阅读状态或备注。
- `POST /api/research/papers/{paper_id}/reading-session`：在进入工作台前创建精读会话并把论文推进为“阅读中”，因此论文精读列表可以立即看到记录。
- `GET/PUT /api/research/papers/{paper_id}/note`：读取或保存该论文唯一的 Markdown 笔记；精读页底部笔记抽屉使用此接口自动保存和手动保存。
- `PUT/DELETE /api/research/papers/{paper_id}/library`：加入、更新或移出论文管理，更新体包含状态、备注和文件夹；移出不会删除论文、精读记录或标注。
- `GET/PUT/DELETE /api/research/papers/{paper_id}/annotations/...`：恢复、保存和删除高亮/注释。

论文工作台仍在浏览器保存一份标注缓存，用于短时离线兜底；SQLite 是正式数据源。首次打开升级后的工作台时，已有浏览器标注会自动补写到 SQLite。
