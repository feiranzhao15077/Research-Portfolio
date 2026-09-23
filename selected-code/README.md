# Selected Code

本目录提供四个项目的精选源码展示快照（selected implementation snapshots），供导师阅读实现、实验设计与审计流程。它不是完整复现包，也不代表完整工程迁移；不包含大型结果文件、商业软件工程文件或原始数据。

## Project Index

| Project | Implementation focus |
| ------- | -------------------- |
| [EM-Trace](em-trace/) | CAD–CST–complex field extraction–signal processing workflow |
| [LowAlt-MD](lowalt-md/) | Synthetic rotor micro-Doppler physics modeling |
| [EMvision](emvision/) | Controlled factorial experiment and attribution workflow |
| [QuadControl-Lab](quadcontrol-lab/) | Simulation-oriented dynamics and observation-interface design |

每个快照的 README 记录来源 commit、展示范围和依赖限制。请先阅读对应说明；部分原仓库模块、输入与运行环境未包含在快照中。

## Evidence Boundary

- Synthetic-only：相关证据来自数值仿真与受控合成实验，具体模型假设以项目页为准。
- No real radar validation：不包含真实雷达实测验证。
- No hardware/HIL validation：不包含硬件、实机飞行或 HIL 验证。
- No complete reproduction guarantee：不保证完整复现或直接独立运行；源码选读不替代项目证据。

## Portfolio Navigation

- [Research Portfolio](../README.md)
- [Research Overview PDF](../docs/Undergraduate_Research_Project_Overview.pdf)
- Project pages: [EM-Trace](../projects/em-trace/) · [LowAlt-MD](../projects/lowalt-md/) · [EMvision](../projects/emvision/) · [QuadControl-Lab](../projects/QuadControl-Lab/)
