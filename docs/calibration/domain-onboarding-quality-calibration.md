# 领域入门质量评分校准报告

## 方法

使用真实模型生成的多领域输出提取五维完整度特征，并从每个真实输出构造中度缺失、严重缺失和双维度缺失样本。
独立验收标签要求五个维度均不低于 0.45，且五维平均值不低于 0.72。
候选权重以 5 分为步长、总和固定为 100，重试阈值在 60 至 90 之间搜索，以平衡准确率为主指标。

## 真实样本

| 领域 | 状态 | 当前分数 | 调用耗时(ms) | token |
| --- | --- | ---: | ---: | ---: |
| 多模态大模型 | ok | 100 | 91585.758 | 3592 |
| 图神经网络 | ok | 100 | 42589.721 | 2135 |
| 联邦学习 | ok | 100 | 56870.287 | 2532 |
| 具身智能 | ok | 100 | 62524.313 | 2076 |
| 量子机器学习 | ok | 100 | 65422.71 | 2245 |

## 校准结果

- 有效真实样本：5
- 扩展标注样本：105
- 校准前策略：{'domain_summary': 10, 'prerequisites': 20, 'development_stages': 30, 'current_landscape': 15, 'learning_path': 25}，阈值 75
- 校准前策略指标：balanced_accuracy=0.6333，sensitivity=1.0000，specificity=0.2667
- 当前已应用策略：{'domain_summary': 15, 'prerequisites': 20, 'development_stages': 25, 'current_landscape': 15, 'learning_path': 25}，阈值 89
- 当前已应用策略指标：balanced_accuracy=1.0000，sensitivity=1.0000，specificity=1.0000
- 推荐策略：{'domain_summary': 15, 'prerequisites': 20, 'development_stages': 25, 'current_landscape': 15, 'learning_path': 25}，阈值 89
- 推荐策略指标：balanced_accuracy=1.0000，sensitivity=1.0000，specificity=1.0000

## 结论边界

本报告校准的是结构化内容完整度，不评估事实准确性。扩展样本来自真实输出的受控降级，适合工程阈值初调，但不能替代团队人工标注。

## V1 分层质量与修复决策校准

上述五维百分制报告是 V0 结构完整度基线。V1 使用 0 至 1 的七维评分，
并将结构、论文身份和证据支持作为独立硬门槛。两组分数不能直接横向比较。

V1 修复决策固定样本位于
`docs/calibration/domain-onboarding-quality-repair-scenarios.jsonl`，覆盖：

- 显著改善且关键维度不回退；
- 改善幅度不足；
- 修复后硬门槛失败；
- 关键维度回退；
- 修复动作失败且结果无改善。

这些样本是确定性决策回归基线，不是真实模型效果结论。后续需使用真实检索结果、
真实模型输出和人工标注持续校准 `quality_threshold`、`min_improvement_delta`
与证据支持阈值。
