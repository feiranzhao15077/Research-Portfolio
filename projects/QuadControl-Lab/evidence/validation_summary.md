# QuadControl-Lab — Frozen Evidence Index

本页是 [QuadControl-Lab 项目页](../README.md)的公开证据索引。事实源为 QuadControl-Lab source commit `123ab8a57f355e7c615a9c841fcd94b84b5ddd91` 中的 `docs/experiments/phase3_7_10_final_result_freeze.md`；final result freeze commit 为 `427ed6a0ef3c45805b565f09369c2deb34888c7c`。这里只摘录已冻结结果及文件身份，不复制原始实验 JSON，也没有重新运行实验。源仓库若未公开，原始产物需授权核查。

## Formal Paired Phenomenon

固定合成 plant、`IdealImu`、`MinimalAttitudeEstimator`、+10° roll `ATTITUDE_STEP`、seed 0、10 s、无噪声和外部扰动。两臂的 plant、controller、mixer、actuator、scheduler、reference 和初始化一致，仅控制器消费的 observation source 不同。

| Predeclared paired metric | Frozen value |
| --- | ---: |
| RMS true-attitude paired angular distance | `1.096029 rad` |
| RMS true-body-rate paired difference | `0.201693 rad/s` |
| Maximum true-attitude paired angular distance | `1.969332 rad` |
| Maximum true-body-rate paired difference | `0.212150 rad/s` |
| RMS controller torque-command paired difference | `0.000441743 N·m` |
| Same-side observation-to-truth attitude RMS — IDEAL | `0 rad` |
| Same-side observation-to-truth attitude RMS — ESTIMATED | `1.184857 rad` |

七个展示值均按预先定义的对齐和计算规则从 parent 两臂产物导出，并非 JSON 中直接存储的指标字段；不将其解释为跟踪、稳定性、估计器泛化或实机性能。

## Controlled Mechanism — Layer A

在估计器输入层面保持 gyro、specific-force 模长、时间戳及初始化相同，仅改变 specific-force 方向。A1/A2 终端姿态误差分别为 `0 rad` 和 `0.3354319429327265 rad`；两臂公式检查各 `200/200 PASS`，冻结判定 `SUPPORTED`。该结果不是完整 plant 反事实，也不外推真实 IMU。

## Full-Loop Mechanism — Layer B

Parent replay 为 `10,000 rows / 328,049 recursive fields / 0 mismatches`。B-1 `993/993`、B-2 `0 prefix violations`、B-3 closure residual `0 rad`，冻结判定 `SUPPORTED`。估计器 roll-error 两项有符号净累计预算：`S_P=+0.0010590188090782893 rad`，`S_C=-2.074123335176487 rad`，最终 roll error `-2.0730643163674087 rad`，`R_C=0.9994896743377543`。

`R_C=|S_C|/(|S_C|+|S_P|)` **只比较估计器 roll-error 两项净累计预算，不是 IDEAL/ESTIMATED 车辆轨迹分离的因果占比**；结果不意味着关闭 correction 会恢复 IDEAL 轨迹。

## Artifact Identities

下面是 final result freeze 记录的原始文件 SHA-256；它们是来源身份，不表示这些大体积产物已随 Portfolio 公开。

| Key | Frozen artifact | SHA-256 |
| --- | --- | --- |
| P-M | Paired manifest | `98c1dd2d861adccd6633a7fc2a8644614e29e517e6e79e390fadc47db8b5e8a7` |
| P-I | IDEAL_BENCHMARK | `ace54199fc8b9e65c45bc8f037def161e2092dc25d988446e6b58cd3cb0dfd53` |
| P-E | ESTIMATED_ATTITUDE_LOOP | `4a2fe35e45112cd7a59acf681dd794cc0abd47c4de8baac085201f6cd8e88951` |
| A-1 | Layer A1 input/result | `5e346ea9772fe874223726939d44db4eaf088f3173226d6d5b4d9abe0de9bbf1` |
| A-2 | Layer A2 input/result | `c8281a4a052cc18d37221bc561ee810dafb2f60b52353da0f3f638ba7f14e237` |
| A-X | Layer A analysis | `e645f42e2bc3e3a8543083de1aefe6f03c8c25112b101186831c7ed59288a795` |
| B-R | Layer B replay | `0eaf664db7ea3b61f324c433d717cd3606f4846206a8d41f19f4276166b30d9e` |
| B-D | Layer B diagnostics | `8cedbf8f76336ad7c56278a2f0e2863452e12c2a6a9c9e531eaca88128a2ce51` |
| B-X | Layer B analysis | `779a962f36500323c115fd7650d89fa90d9ac1377d1557e1f6956530c6e3f700` |

## Figures

这些是 source commit 中冻结的 SVG，按原字节复制，未重绘或更改数值。

| Figure | Portfolio file | Source SVG SHA-256 | Scope |
| --- | --- | --- | --- |
| 1 Paired architecture | [figure_01](../assets/figure_01_paired_architecture.svg) | `d56acb62397de943595d9277d21f280153eb15ad819018759cc1cdf18f63c13f` | 概念结构，非性能 |
| 2 Formal response | [figure_02](../assets/figure_02_formal_closed_loop_response.svg) | `b5afead0164024d2ad5f2109d380f8d5ac0b36d995410e6211c85587b3983a58` | 单种子配对响应 |
| 3 Divergence timeline | [figure_03](../assets/figure_03_divergence_timeline.svg) | `acec4f3211bed2ab2c1232f10df97cdd146b02089306b03833f0ad41e59f557a` | 描述性时间线 |
| 4 Layer A | [figure_04](../assets/figure_04_layer_a_mechanism.svg) | `abee3245bca365d97f9da4325341fef844fd2d6baa72e3a7d48ffe39f7da5961` | 估计器输入层面 |
| 5 Layer B | [figure_05](../assets/figure_05_layer_b_roll_error_budget.svg) | `7db2c1a2281c0356c7c3d21a74db3ea47206f846c08f5d9bbdcebe6740e03fed` | 净累计误差预算 |

## Evidence Boundary

仅限上述合成、固定、确定性条件；无真实传感器、硬件、HIL 或实机飞行验证。未验证全局稳定性、现实噪声/偏置下的鲁棒性、完整状态估计或跨算法性能。早期 attitude-step/torque-pulse 工程基线不替代本次正式配对及 Layer A/B 证据。
