# Domain Onboarding 生产运行手册

## 必需配置

先通过 `novicesynapse config` 设置真实 `api_key`、`base_url` 和聊天模型。生产环境还应设置：
启用本地多语言 embedding 的部署需要安装 `pip install '.[embeddings]'`。

```text
DOMAIN_ONBOARDING_AUDIT_DIR=/var/lib/novicesynapse/audit
DOMAIN_ONBOARDING_AUDIT_FSYNC=1
# 推荐：本地 ONNX 多语言 embedding，不需要远程 embedding API 权限
DOMAIN_ONBOARDING_LOCAL_EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
DOMAIN_ONBOARDING_EMBEDDING_CACHE_DIR=/var/cache/novicesynapse/embeddings
# 如需改用 OpenAI-compatible embedding API，则不设置上述 LOCAL 变量：
# DOMAIN_ONBOARDING_EMBEDDING_MODEL=<multilingual-embedding-model>
SEMANTIC_SCHOLAR_API_KEY=<optional>
CROSSREF_MAILTO=<operations-email>
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

互动链路默认对每个论文源最多发送 4 条查询，单个外部请求最多 8 秒且不串行重试。
规划和生成分别限制为 1600 和 6000 tokens；每个增量生成模型调用默认限制为 60 秒，
生成阶段总 deadline 为 240 秒，请求总 deadline 为 420 秒。已通过 SSE 发布的分段不等待任务终态。
修改这些边界后应重跑六领域受控回归，不应只为单个慢请求无界提高 deadline。

## 监控

- JSON 聚合：`GET /metrics/domain_onboarding`
- Prometheus 文本：`GET /metrics/domain_onboarding/prometheus`
- 请求审计：`$DOMAIN_ONBOARDING_AUDIT_DIR/domain-onboarding-YYYY-MM-DD.jsonl`

建议告警：5 分钟超时率大于 5%；检索来源失败率大于 20%；embedding fallback 持续增长；审计写失败大于 0；P95 超过 240 秒；质量 hard-gate 失败率大于 10%。

## 发布门禁

发布前运行 `python -m unittest discover -s tests`，再执行六领域受控在线回归。知识图谱保持 shadow mode，只有 `graph_path_evaluation` 的 `promotion_recommended` 为真且人工检查通过后才能设计主动模式变更。

密钥不得写入日志、审计、提交或容器镜像。审计目录需使用持久卷并限制为服务账户可读写。

仓库提供 `Dockerfile` 与 `deploy/docker-compose.yml`。复制
`deploy/domain-onboarding.env.example` 为部署系统的受保护环境配置后，可从
`deploy/` 目录运行 `docker compose --env-file <protected-env-file> up -d --build`。
模型密钥仍只存放于只读挂载的 NoviceSynapse 用户配置目录。
