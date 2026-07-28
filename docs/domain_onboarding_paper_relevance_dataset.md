# 六领域论文相关性标注集

数据文件：`evaluation/fixtures/domain_onboarding/v1/paper-relevance.jsonl`。

每个领域同时包含直接相关论文、邻近论文和明确负例。相关性采用四级标注：`3` 表示直接且核心，`2` 表示直接相关，`1` 表示仅作为组件或邻近背景，`0` 表示不相关。排序评测将 `>=2` 视为相关。

`role` 是期望的教学角色，不是模型当前输出。它用于分别检查相关性排序和 survey、foundational、method、evaluation、frontier 等角色覆盖。

数据默认使用 `annotation_status: seed`。只有逐项确认论文身份、主题相关性、等级和角色之后，才能改为 `human_verified`。修改必须提升 `annotation_version`，并保留可审查的 Git 历史。

标注时应只判断论文是否适合作为对应领域的入门材料，不根据当前排序器分数反推标签。存在分歧时保留较低等级，并在 `rationale` 中写明边界。
