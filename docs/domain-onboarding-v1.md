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
- `ranking.py`：去重、验证、角色分类和多信号排序；
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
- 质量阈值 0.75；
- 最小显著改善 0.05；
- 最多一次内容修复。

默认并发调用 Semantic Scholar、arXiv 与 Crossref，并按来源轮询合并结果；
单个来源失败时继续其他来源。可恢复的网络错误以及 408、425、429、5xx 响应
使用有上限的指数退避重试，`Retry-After` 会在最大退避范围内优先采用。相同查询
使用进程内 TTL 缓存，arXiv 连续请求默认间隔 3 秒。
Ranker 在截取候选上限时也按来源轮询取样，避免高产来源仅凭返回数量占满候选池；
进入候选池后仍由相关性、引用、时效和多样性分数决定最终推荐顺序。
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
- `topic_coverage`；
- `development_coherence`；
- `learning_path`；
- `goal_alignment`。

论文真实性、Schema 和必要模块属于硬门槛。修复结果只有同时满足硬门槛、
总分至少提高 `min_improvement_delta`，且结构、论文和学习路线维度没有回退时
才会替换第一次结果。

## 测试

V1 测试位于 `tests/domain_onboarding_v1/`，全部使用假模型和假检索器，不需要
外部网络或付费模型。Semantic Scholar 的响应转换通过契约测试覆盖。

```bash
python -m unittest discover -s tests -v
```
