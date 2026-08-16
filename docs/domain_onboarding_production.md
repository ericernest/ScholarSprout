# Domain Onboarding 生产运行手册

## 必需配置

先通过 `novicesynapse config` 设置真实 `api_key`、`base_url` 和聊天模型。生产环境还应设置：
启用本地多语言 embedding 的部署需要安装 `pip install '.[embeddings]'`。

```text
DOMAIN_ONBOARDING_AUDIT_DIR=/var/lib/novicesynapse/audit
DOMAIN_ONBOARDING_AUDIT_FSYNC=1
# 默认调用当前 OpenAI-compatible 端点的 qwen3-embedding。
# 如需优先使用本地 ONNX 多语言 embedding：
# DOMAIN_ONBOARDING_LOCAL_EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
# DOMAIN_ONBOARDING_EMBEDDING_CACHE_DIR=/var/cache/novicesynapse/embeddings
# The browser settings page stores embedding.model_name and embedding.base_url.
# Environment variables remain the highest-priority deployment overrides.
DOMAIN_ONBOARDING_EMBEDDING_BASE_URL=<openai-compatible-embedding-base-url>
# 覆盖远程模型 ID：DOMAIN_ONBOARDING_EMBEDDING_MODEL=<embedding-model>
# 显式关闭 embedding 并只使用 TF-IDF：DOMAIN_ONBOARDING_EMBEDDING_ENABLED=false
SEMANTIC_SCHOLAR_API_KEY=<optional-higher-quota-key>
CROSSREF_MAILTO=<deployment-contact-email>
```

### 分模块模型路由

领域规划、发展阶段、领域全景、学习路径和定向修复可以使用不同模型。每个变量接受按优先级排列的逗号分隔模型 ID；第一个模型在分配到的尝试时间内失败时，才会调用下一个模型。未设置变量时继续使用用户配置中的 `client.model_name`。

```text
DOMAIN_ONBOARDING_PLANNING_MODELS=<fast-json-model>,<planning-backup>
DOMAIN_ONBOARDING_DEVELOPMENT_MODELS=<long-form-model>,<development-backup>
DOMAIN_ONBOARDING_LANDSCAPE_MODELS=<relation-model>,<landscape-backup>
DOMAIN_ONBOARDING_LEARNING_PATH_MODELS=<fast-instruction-model>,<path-backup>
DOMAIN_ONBOARDING_REPAIR_MODELS=<fast-instruction-model>,<repair-backup>
```

`DOMAIN_ONBOARDING_GENERATION_MODELS` 是三个内容段的公共默认路由；某个内容段的专用变量优先。路由共享调用方原有 deadline，不会因为增加备用模型而无界延长请求。结果的 `reproducibility.planning_model_route` 与 `reproducibility.generation_model_routes` 会记录实际尝试、选中模型和耗时，但不会记录 API Key 或服务端错误正文。

上线前必须用真实模块 Prompt 校准主备顺序。仅凭模型名称不能保证时延；如果网关本身不可达，同一网关下的全部模型都会失败，此时应由服务监控告警，而不是提交 Mock 或人工补全输出。

同时在用户配置的 `client` 节点填写 `input_cost_per_million_tokens` 和 `output_cost_per_million_tokens`，否则成本会明确显示为 `null`，不会伪造估值。

## 启动与探针

```bash
novicesynapse gateway --host 0.0.0.0 --port 8000
curl -f http://127.0.0.1:8000/health
curl -f http://127.0.0.1:8000/ready
```

`/health` 只表示进程存活；`/ready` 只有在模型、V1 pipeline、指标和审计均已装配时返回 200。部署滚动更新应以 `/ready` 为就绪探针。

互动链路默认对每个论文源最多发送 4 条查询，单个外部请求最多 8 秒。
检索源对 429 和短暂网络错误最多尝试 3 次，默认按 2 秒、4 秒有界退避，
并尊重服务端 `Retry-After`。结果存入 TTL 缓存；当某个论文源失效时，
其他数据源和已有证据仍可继续生成，不会因引用数补全失败而丢弃论文。
规划阶段/模型调用预算为 300/280 秒；单次检索和排序阶段预算均为 180 秒；
远程 embedding 单批最多等待 30 秒。发展基础块预算为 240 秒，发展阶段按顺序生成、
单阶段预算为 300 秒；全景和学习路径使用两个 worker 并行生成，单模块预算为 300 秒。
整体生成、修复和请求总预算分别为 3600、300 和 4800 秒。已通过 SSE 发布的分段不等待任务终态。
同一质量评估中 embedding 首次失败后，其余证据论述直接使用多语言 TF-IDF；如果总分已达标、
且只有因语义降级产生的证据支持硬门失败，则保留首次结果并跳过完整 LLM 修复。
修改这些边界后应重跑六领域受控回归，不应只为单个慢请求无界提高 deadline。

## 监控

- JSON 聚合：`GET /metrics/domain_onboarding`
- Prometheus 文本：`GET /metrics/domain_onboarding/prometheus`
- 请求审计：`$DOMAIN_ONBOARDING_AUDIT_DIR/domain-onboarding-YYYY-MM-DD.jsonl`

建议告警：5 分钟超时率大于 5%；检索来源失败率大于 20%；embedding fallback 持续增长；审计写失败大于 0；P95 超过 240 秒；质量 hard-gate 失败率大于 10%。

## 发布门禁

发布前运行 `python -m unittest discover -s tests`，再执行六领域受控在线回归。知识图谱保持 shadow mode，只有 `graph_path_evaluation` 的 `promotion_recommended` 为真且人工检查通过后才能设计主动模式变更。

密钥不得写入日志、审计、提交或容器镜像。审计目录需使用持久卷并限制为服务账户可读写。
`CROSSREF_MAILTO` 也应只在本地 shell、密钥管理器或受保护的部署环境文件中设置，
不要把真实邮箱写入仓库。

## 子方向证据检索与引用数

规划器固定产生 3 个可检索子方向，每个方向包含中英文名称、范围、包含词、
排除词、研究问题和 2 条英文查询。Pipeline 会对每个方向独立检索和排序；
如果论文数、摘要或方法/综述/评测角色覆盖不足，最多再发送 1 条定向补搜查询。
补搜仍失败时，该方向标记为 `limited`，但不会让整个任务失败。

子方向排序以相关性为主：相关性 55%、路径命中 15%、论文角色 10%、
时效性 10%、摘要完整度 5%、按发表年限归一化的引用数 5%。
因此高引用但不相关的论文不能仅凭引用数进入结果。引用数为 `0` 会标记为
`known`，查不到才标记为 `unknown`，避免把“真实为 0”和“接口没返回”混在一起。

Semantic Scholar 批量接口用于补全引用数，默认可以无 Key 调用；
`SEMANTIC_SCHOLAR_API_KEY` 只是获取更高配额的可选配置。Crossref 作为独立检索源，
`CROSSREF_MAILTO` 仅用于 polite pool 联系信息，不是 API Key。

## 证据论文与用户推荐分离

Pipeline 内部保留两个论文集合：`evidence_papers` 为发展阶段、子方向、
概念和技术说明提供证据；`papers` 是面向用户展示的精选阅读清单。
证据论文不会因为没有进入推荐清单而丢失，也不会全部堆到用户界面。

推荐流程从规划模型给出的标准英文领域名、别名和真实子方向生成候选 Survey 查询。
固定扩展表只保存 RAG、GNN 等基础且不易混淆的标准别名；长尾或歧义领域不依赖静态领域词表。
候选查询先进行小规模真实检索，并按照相关性、Survey 命中率、摘要完整度、近期论文比例和来源覆盖评分；
只有包含真实 Survey 且达到阈值的查询可以进入推荐候选池。

第一轮没有合格 Survey 时，Pipeline 从已经验证的证据论文标题和摘要中提取领域锚定短语，
执行最多一轮动态扩展查询。该步骤只使用证据论文发现搜索词，不会把证据论文混入推荐候选。
Survey 排序为：
相关性 45%、时效性 25%、按发表年限归一化的引用表现 20%、摘要完整度 10%；
结果中较新 Survey 优先于较旧高引 Survey。入选 Survey 再通过
Semantic Scholar `GET /graph/v1/paper/{paper_id}/references` 获取参考文献，
以相关性 65%、引用表现 20%、时效性 15% 筛选代表方法论文。
每篇来自综述参考文献的推荐都保留 `survey_source_ids`，可追溯它来自哪篇综述。

Survey 初搜和动态补搜都失败时，用户推荐 `papers` 保持为空，内部 `evidence_papers` 继续支持其他模块；
Pipeline 不再把发展路径证据标记为推荐。结果通过 `recommendation_strategy=survey_degraded_no_result`
和逐查询审计说明失败原因，并在指标中记录查询数、验证通过数、扩展轮次和降级次数。

仓库提供 `Dockerfile` 与 `deploy/docker-compose.yml`。复制
`deploy/domain-onboarding.env.example` 为部署系统的受保护环境配置后，可从
`deploy/` 目录运行 `docker compose --env-file <protected-env-file> up -d --build`。
模型密钥仍只存放于只读挂载的 NoviceSynapse 用户配置目录。
