# EMvision evidence index

本页把展示页中的主要判断映射到可复核的结果文件和图件。数字均来自冻结的合成实验；本展示目录没有重新运行实验。

| Conclusion | Evidence | Location |
| --- | --- | --- |
| C/G/P 训练端干预在固定测试域上形成八个可配对角点 | `protocol_map.png`; frozen factorial result metadata | [protocol map](../assets/protocol_map.png); [n=20 results](https://github.com/feiranzhao15077/EMvision/blob/master/outputs/em-sense/data/factorial_attribution_n20/results.json) |
| `v000 → v111` 的端点增益为 0.8033，paired bootstrap 95% CI 为 [0.7729, 0.8299] | `aggregate.corners`, `aggregate.total_gain` | [n=20 results](https://github.com/feiranzhao15077/EMvision/blob/master/outputs/em-sense/data/factorial_attribution_n20/results.json); [corner figure](../assets/n20_corner_performance.png) |
| 条件路径边际发生符号变化：`CP|G=0` = −0.1713，`CP|G=1` = 0.4212 | `aggregate.interactions` | [n=20 results](https://github.com/feiranzhao15077/EMvision/blob/master/outputs/em-sense/data/factorial_attribution_n20/results.json); [path/interaction figure](../assets/path_interaction_audit.png) |
| 三阶交互 `CGP` = 0.5925，95% CI [0.5260, 0.6620] | `aggregate.interactions.three_way` | [n=20 results](https://github.com/feiranzhao15077/EMvision/blob/master/outputs/em-sense/data/factorial_attribution_n20/results.json); [path/interaction figure](../assets/path_interaction_audit.png) |
| `v011` 在 17/20 个种子上为单类预测，Macro-F1 = 0.1667 | `v011_n20_logits_features.md` and its JSON companion | [v011 audit](https://github.com/feiranzhao15077/EMvision/blob/master/outputs/em-sense/docs/validation/v011_n20_logits_features.md); [audit figure](../assets/v011_collapse_audit.png) |
| 负对照接近三分类 chance | `protocol_controls.json` | [protocol controls](https://github.com/feiranzhao15077/EMvision/blob/master/outputs/em-sense/data/protocol_controls.json) |

## Reading notes

- `C` = channel matching; `G` = generator-family matching; `P` = parameter-distribution matching.
- 主结果使用固定的稀疏生成器、`fp=9.5 GHz`、`f0=10 GHz` 联合外推测试域；每个角点共享同一测试目标，不把测试数据用于拟合或选择。
- Shapley 在这里用于压缩六条路径的性能分解信息，不应解释为物理因果贡献或普适重要性排序。
- 结果不包含实测雷达、CST 单站外部验证或完整物理模型不确定性。
