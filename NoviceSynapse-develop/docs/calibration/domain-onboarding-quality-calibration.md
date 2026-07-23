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
