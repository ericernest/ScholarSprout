# Domain Onboarding V1

`domain_onboarding` V1 将原来的单次结构化生成升级为可替换、可测试的有依据流水线：

```text
请求与画像
→ STORM-lite 领域规划
→ Semantic Scholar + arXiv + Crossref 论文检索
→ 去重、验证和多信号排序
→ 候选论文约束生成
→ 分层质量评估
→ 最多一次定向修复
→ 指标记录与 API 响应
```

第一版不构建知识图谱。Schema 已保留 `paper_id`、`stage_id`、
`subdirection_id`、`prerequisite_id`、`related_paper_ids` 和
`prerequisite_ids`，后续可以在排序与学习路径之间插入图模块。

## 模块

实现位于 `handlers/domain_onboarding/`：

- `schemas.py`：模块间数据契约；
- `config.py`：数量、权重和质量阈值；
- `profile.py`：metadata 优先的请求级画像；
- `planner.py`：单次 LLM STORM-lite 规划及确定性降级；
- `retrieval.py`：Semantic Scholar、arXiv、Crossref 和来源失败隔离；
- `text_similarity.py`：可替换的本地 TF-IDF 文本向量与余弦相似度；
- `ranking.py`：去重、验证、角色分类、多信号评分和 MMR 选取；
- `generator.py`：候选论文约束生成和五阶段学习路径；
- `quality.py`：硬门槛与六维软评分；
- `repair.py`：代码修复和一次受约束局部修复；
- `metrics.py`：阶段耗时、论文、质量、token 和修复指标；
- `pipeline.py`：只负责编排模块。

旧的 V0 逻辑保存在 `legacy.py`，仅用于未装配 V1 Pipeline 的兼容调用。
Gateway 默认装配 V1。

## 论文真实性约束

生成器接收排序后的 `RankedPaper`，模型只能输出候选集合中的
`paper_id`。代码使用检索结果重新构造标题、作者、年份和 URL，不接受模型
返回的论文元数据。以下情况会导致论文硬门槛失败：

- 引用候选集合以外的 ID；
- 修改候选论文标题、年份或 URL；
- 学习路径或发展阶段存在悬空论文引用。

所有检索查询失败时返回 `retrieval_failed`，不会让模型依靠参数记忆补造论文。

## 配置

默认参数由 `DomainOnboardingConfig` 管理。主要参数包括：

- 每次最多 6 个检索查询；
- 每个查询最多 10 篇论文；
- 最多保留 40 篇候选、选择 12 篇；
- 排序权重：相关性 0.55、引用 0.20、时效 0.15、多样性 0.10；
- MMR 质量权重 0.70、未覆盖论文角色加分 0.05；
- 质量阈值 0.75；
- 最小显著改善 0.05；
- 最多一次内容修复。

默认并发调用 Semantic Scholar、arXiv 与 Crossref，并按来源轮询合并结果；
单个来源失败时继续其他来源。可恢复的网络错误以及 408、425、429、5xx 响应
使用有上限的指数退避重试，`Retry-After` 会在最大退避范围内优先采用。相同查询
使用进程内 TTL 缓存，arXiv 连续请求默认间隔 3 秒。
Metrics 同时记录每个 provider 的请求、重试、限流、延迟、结果数和错误数。单个
provider 连续失败 3 次后默认熔断 30 秒，冷却后只允许一个探测请求；全部实时来源
不可用时，可以使用不超过 24 小时的陈旧缓存，并在指标中明确标记，避免把降级结果
误认为实时检索。
Ranker 在截取候选上限时也按来源轮询取样，避免高产来源仅凭返回数量占满候选池；
进入候选池后使用本地 TF-IDF 向量计算查询相关性和论文间相似度，再通过 MMR
综合论文质量、相对已选集合的新颖度和角色覆盖选择最终论文。向量器通过接口注入，
以后可以替换为 embedding 实现而不改动 Ranker 流程。当前提供批处理和有界缓存的
`CachedEmbeddingTextVectorizer`；embedding 服务异常时 Ranker 自动降级到 TF-IDF，
并记录实际 backend 和 fallback 指标。至少存在一篇达到相关性下限的论文时，明显
低于下限的候选会在 MMR 前过滤，避免高引用但无关的论文仅凭多样性进入结果。
DOI 与 arXiv ID 会统一移除解析器前缀和版本号并校验格式；Crossref 与 arXiv
记录还必须满足来源、`paper_id`、标识符和 URL 一致。Crossref 仅接收论文型 work
type，Semantic Scholar 中的数据集、社论、来信和新闻记录不会进入排序。
默认验证策略还要求候选论文具有年份。2026-07-27 的三领域真实评测中，
36 篇入选论文有 2 篇缺少年份，因此 `require_verified_paper_year`
默认为 `true`。只有显式兼容无年份的历史数据源时才应关闭该规则。

首次排序后，`PaperCoverageAnalyzer` 会检查每个预期子方向是否有足够相关的论文，
并检查综述、奠基工作、评测和前沿论文角色是否齐全。缺口被表示为带稳定
`subdirection_id`、缺失角色、原因和补充查询的 `CoverageGap`。Pipeline 最多执行
一轮补充检索，而且只发送这些缺口对应的查询；补充结果仍需经过同一套验证、去重、
排序和 MMR 选择，不会直接进入生成器。

固定排序基准保存在 `tests/domain_onboarding_v1/fixtures/ranking_benchmark.json`，使用
人工相关性等级持续检查 Precision@K、Recall@K、NDCG@K 和论文角色覆盖率。修改
权重、向量器或 MMR 策略时必须同时通过该基准，避免只凭单次示例主观调参。
Semantic Scholar 不要求 API Key，但公共配额可能限流。若需要更高配额，可设置：

```bash
export SEMANTIC_SCHOLAR_API_KEY="..."
```

Crossref 推荐通过 `mailto` 加入 polite pool：

```bash
export CROSSREF_MAILTO="researcher@example.org"
```

系统不会将该值写入请求指标或日志。

## 请求画像

请求正文之外，Web 请求的 `metadata` 可以包含：

```json
{
  "background": ["Python", "Transformer"],
  "goal": "六周内复现一个 RAG 基线",
  "time_budget_weeks": 6,
  "preference": "experiment_first",
  "known_concepts": ["向量检索"]
}
```

缺失字段使用科研新手默认值，不进行阻塞式追问。

## 质量报告

响应中的 `quality.dimensions` 包含：

- `structure`；
- `paper_validity`；
- `evidence_grounding`；
- `topic_coverage`；
- `development_coherence`；
- `learning_path`；
- `goal_alignment`。

论文真实性、Schema 和必要模块属于硬门槛。修复结果只有同时满足硬门槛、
总分至少提高 `min_improvement_delta`，且结构、论文和学习路线维度没有回退时
才会替换第一次结果。

Pipeline 状态与最终质量保持一致：只有通过硬门槛且达到阈值时返回 `ok`；
通过硬门槛但低于阈值时返回 `quality_warning`；未通过硬门槛时返回
`quality_failed`。后两者仍携带结构化输出和质量问题，便于前端提示和定位修复点。

`quality_attempts` 保留首次与修复后的完整评估，不会因最终选择某一版而丢失
未选中候选的评分与问题。`repair_record` 记录修复动作、问题 ID、目标路径、
实际变更路径、前后指纹以及结果选择决策。旧的 `score`、`attempts`、
`selected_attempt` 和 `retry_status` 字段继续保留，以保持客户端兼容。

修复结果选择原因使用稳定代码，包括：

- `quality_threshold_met`；
- `significant_improvement`；
- `hard_gate_failed`；
- `improvement_too_small`；
- `critical_dimension_regressed`；
- `repair_execution_failed`。

输出中的关键技术与历史论述通过 `evidence_claims` 绑定已验证 `paper_id`，并区分
`abstract_explicit`、`metadata_inference` 和 `background_synthesis`。质量评估会检查
非法或空证据、发展阶段证据覆盖，以及标题和摘要是否能支持论述。候选集合之外的
证据 ID 和同语言下明显不受支持的直接断言属于硬失败；中英文跨语言文本在未配置
跨语言 embedding 时只产生警告，避免把词面不一致误判为虚假论文。证据维度也参与
修复结果的关键维度回退保护。

## Deadline 与取消

Pipeline 默认共享一个请求级 90 秒 deadline，并为画像、规划、检索、排序、生成、
评估和修复设置独立阶段预算。调用方可以传入 `PipelineExecutionContext`，并通过其
`cancel()` 方法发出协作式取消信号。超时返回 `timeout`，取消返回 `cancelled`；
Metrics 会记录中断阶段。第三方阻塞调用仍由各自的原生网络 timeout 负责终止，
请求级控制保证中断后不会继续进入后续检索、生成或修复阶段。

## 模型调用指标

Metrics 的 `model_usage` 分别聚合 `primary`、`retry` 和 `total` 调用。即使模型
返回非法 JSON，响应中的 token 仍会计入；如果请求在获得 usage 前发生网络异常，
仍记录模型调用次数和耗时，并增加 `unreported_usage_calls`。只有所有已发生调用都
报告 usage 时，`usage_complete` 才为 `true`，避免把不完整 token 当作完整成本。

Metrics 同时聚合首次和最终质量状态、问题类型、失败硬门槛、修复动作状态、
选择原因、变更路径数量与维度改善值。这些指标只记录结构化摘要，不记录 Prompt、
API Key 或完整生成内容。

### 请求审计持久化

设置 `DOMAIN_ONBOARDING_AUDIT_DIR` 后，每个 V1 请求会按 UTC 日期追加到
`domain-onboarding-YYYY-MM-DD.jsonl`。记录包含请求 ID、策略版本、阶段耗时、
模型用量、论文计数、质量尝试和修复记录，但只保存用户、会话与查询的 SHA-256
摘要，不保存原始输入、Prompt、API Key 或完整生成内容。设置
`DOMAIN_ONBOARDING_AUDIT_FSYNC=true` 可在每次追加后强制同步文件。未配置目录时
使用 No-Op 存储；写入失败不会改变业务响应，但会计入 Metrics 的
`audit.write_failures`。

### 离线质量评测

固定六领域数据位于
`evaluation/fixtures/domain_onboarding/v1/cases.jsonl`，通过以下命令生成报告：

```bash
python -m evaluation.domain_onboarding \
  evaluation/fixtures/domain_onboarding/v1/cases.jsonl \
  --output .artifacts/domain-onboarding-offline-report.json
```

报告按策略版本和领域计算首次硬门槛通过率、与标注的一致率、修复改善率、问题
误报率、七个维度与标注的平均绝对误差及重复评估稳定性。仓库内首批六条记录是用于
锁定框架行为的 `seed` 标注，不冒充已经完成的人工实验；团队复核后应把
`annotation_status` 更新为 `human_verified`，再将结果用于策略上线门槛。

### 受控在线端到端测试

2026-07-27 的三领域受控真实评测记录到总耗时 111–147 秒、规划
13–26 秒、生成 78–103 秒。默认超时因此校准为请求 300 秒、规划
45 秒、生成和修复各 120 秒。在线报告会记录 `interrupted_stage` 和分阶段
耗时，以区分模型延迟、论文检索延迟和质量失败。

真实论文源和真实模型测试默认禁用。固定中英文配对用例位于
`evaluation/fixtures/domain_onboarding/v1/online-cases.jsonl`，运行时必须同时提供
环境开关、费用确认、最大用例数和费用上限：

```bash
RUN_DOMAIN_ONBOARDING_ONLINE=1 \
python -m evaluation.domain_onboarding.online_cli \
  evaluation/fixtures/domain_onboarding/v1/online-cases.jsonl \
  --output .artifacts/domain-onboarding-online-report.json \
  --max-cases 2 \
  --max-estimated-cost-usd 0.5 \
  --cost-reserve-per-case-usd 0.25 \
  --confirm-online
```

默认要求配置输入与输出 token 单价；只有显式传入 `--allow-unpriced` 才能在无法
估算费用时运行。报告记录成功率、论文元数据有效率、硬门槛通过率、p50/p95
延迟、token、费用和跨语言证据警告率。跨语言警告只作为待人工复核队列，不能在
缺少人工标签时直接宣称为误报。

### 质量策略版本

阈值、七个质量维度权重、硬门槛、允许 LLM 修复的问题类型、关键维度和最小
改善幅度统一组成不可变的 `DomainOnboardingPolicy`。默认版本为
`domain-quality-v1.0.0`。每次评估、修复、Pipeline 响应和请求 Trace 都同时记录
`policy_version` 与内容指纹；同一版本号不能注册两份不同策略，避免实验指标被
静默混合。只要上述任一决策参数发生变化，都必须发布新的策略版本。

## 测试

V1 测试位于 `tests/domain_onboarding_v1/`，全部使用假模型和假检索器，不需要
外部网络或付费模型。Semantic Scholar 的响应转换通过契约测试覆盖。

```bash
python -m unittest discover -s tests -v
```
