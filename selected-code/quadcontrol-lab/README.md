# QuadControl-Lab Selected Code

精选公开源码快照（selected public code snapshot），用于导师审阅四旋翼仿真架构、观测边界、配对实验和机制审计；**不是完整源仓库、可独立运行的复现包或飞控部署包**。正式数值以[项目证据索引](../../projects/QuadControl-Lab/evidence/validation_summary.md)及源项目冻结记录为准，本目录没有运行实验或生成结果。

## Why these files are included

以尽量少的文件覆盖“动力学/积分 → 控制分配 → 观测来源 → 受控实验 → 审计”主线。相对于旧快照，本版补入正式配对、Layer A 和 Layer B 的关键实现，保留少量接口与回归测试；不复制大量重复测试、原始结果或临时脚本。

## Architecture Map

`QuadState / rigid-body / RK4 → controller / mixer / motor → SensorContext / IdealImu / observation contract → IDEAL or ESTIMATED consumer → paired evaluation → Layer A/B audit`。

这是模块与审计入口的阅读顺序，不意味着所有依赖都包含在本快照。配对实验的两臂共享 plant/controller/reference 等配置，只切换 controller 消费的 observation source；不能据此把全体车辆轨迹差异归因于某一估计器子环节。

## Dynamics & Integration

| Source path at commit | Reading point |
| --- | --- |
| [`core/state/state.py`](core/state/state.py) | 13D `QuadState` 与状态边界 |
| [`core/dynamics/rigid_body.py`](core/dynamics/rigid_body.py) | Newton–Euler 刚体动力学 |
| [`simulation/integrator/model.py`](simulation/integrator/model.py) | 固定步长 RK4 积分 |
| [`simulation/motors/model.py`](simulation/motors/model.py) | 电机/转子状态更新 |

## Control Pipeline

| Source path at commit | Reading point |
| --- | --- |
| [`controllers/pipeline.py`](controllers/pipeline.py) | 姿态/角速度控制链调用边界 |
| [`controllers/attitude/controller.py`](controllers/attitude/controller.py) | 姿态外环 |
| [`controllers/rate/controller.py`](controllers/rate/controller.py) | 角速度内环 |
| [`core/mixer/allocation.py`](core/mixer/allocation.py) | 控制分配与失败记录 |

## Observation / Sensor / Estimation

| Source path at commit | Reading point |
| --- | --- |
| [`core/contracts/observation.py`](core/contracts/observation.py) | `ControllerObservation` 与来源/字段有效性 |
| [`core/contracts/consumer_observation_adapter.py`](core/contracts/consumer_observation_adapter.py) | Consumer-specific observation boundary |
| [`simulation/runner/sensor_context.py`](simulation/runner/sensor_context.py) | 采样时刻及 `SensorContext` |
| [`simulation/sensors/model.py`](simulation/sensors/model.py) | `IdealImu` 与 synthetic IMU 数据 |
| [`estimation/attitude/estimator.py`](estimation/attitude/estimator.py) | `MinimalAttitudeEstimator`，不是 EKF/全状态估计 |

## Auditable Experiment Infrastructure

| Source path at commit | Reading point |
| --- | --- |
| [`experiments/simulation/paired_configuration.py`](experiments/simulation/paired_configuration.py) | 配对条件与 observation-source 区分 |
| [`experiments/simulation/paired_runner.py`](experiments/simulation/paired_runner.py) | 单次受控配对执行及审计边界 |
| [`experiments/simulation/layer_a_controlled_input.py`](experiments/simulation/layer_a_controlled_input.py) | Layer A 受控输入、公式检查及判据 |
| [`experiments/simulation/layer_b_equivalence.py`](experiments/simulation/layer_b_equivalence.py) | Layer B parent/replay 逐字段等价检查 |
| [`experiments/simulation/layer_b_analysis.py`](experiments/simulation/layer_b_analysis.py) | B-1/B-2/B-3 与估计器 roll-error 预算评估 |

## Representative Tests

| Source path at commit | Reading point |
| --- | --- |
| [`tests/unit/test_observation_boundary.py`](tests/unit/test_observation_boundary.py) | 观测来源/consumer 边界示例 |
| [`tests/unit/test_layer_b_infrastructure.py`](tests/unit/test_layer_b_infrastructure.py) | 合成 fixture、回放和诊断基础设施检查 |

以上共 **20 个 source/test 文件**。测试仅作为源码示例，迁移时未运行。所选文件未构成完整 import/config/dependency closure；若要追踪完整实验协议或诊断事件，请使用源仓库和冻结证据，而不是据此推断可以直接在 Portfolio 内执行。

## Evidence Boundary

本快照支持 **simulation-oriented implementation review**。正式现象仅限 synthetic plant、`IdealImu`、`MinimalAttitudeEstimator`、单个 seed、固定 +10° roll `ATTITUDE_STEP`、无噪声/偏置与外部扰动的 10 s 配对条件。Layer A 是估计器输入的受控比较；Layer B 是全环回放的机制一致性检查。`R_C` 只比较估计器 roll-error 的两项有符号净累计预算，不是车辆轨迹差异的因果份额。

无硬件、HIL、实机飞行、EKF、完整位置/速度估计、完整自主飞控、稳定性证明或真实飞行性能验证；也不保证完整复现。未包含完整场景、参数/配置闭包、冻结原始 JSON、校准数据和结果文件。

## Source Snapshot

- Source repository: [QuadControl-Lab](https://github.com/feiranzhao15077/QuadControl-Lab)（若未公开，访问可能需要授权）。
- Snapshot basis: `123ab8a57f355e7c615a9c841fcd94b84b5ddd91`（final visualization commit；其祖先为 final result freeze commit `427ed6a0ef3c45805b565f09369c2deb34888c7c`）。
- 每个表格中的相对路径也是该 commit 源树中的路径；复制时保留文件内容，按源 commit 的 Git blob 逐一校验。不声称这些文件均首次创建于该 commit。
- [项目页与冻结图](../../projects/QuadControl-Lab/) · [公开证据索引](../../projects/QuadControl-Lab/evidence/validation_summary.md)。
