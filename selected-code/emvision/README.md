# EMvision — Selected Code

This directory contains selected implementation snapshots from EMVision commit `aae9127`. It is a **selected implementation snapshot** for reviewing:

- experiment design
- factorial implementation
- attribution workflow
- audit workflow

It is **not a complete reproduction package** and is **not a verified standalone runnable package**. Results, frozen tables, and complete experiment artifacts cited by the Portfolio should be consulted through the Portfolio evidence.

## Study Scope

The study uses **synthetic simulation only** and a **fixed synthetic test domain**. Its C/G/P 2×2×2 factorial design varies three training-domain factors:

- **C — channel matching**
- **G — generator-family matching**
- **P — parameter-distribution matching**

The experiment uses a fixed MLP architecture and training configuration as a measurement instrument, not as a proposed classification algorithm. All factorial corners use the same architecture and training configuration, but each corner is trained independently; the eight corners do **not** share the same trained weights.

## Protocol and Results

`protocol/factorial_attribution_protocol.json` records the base frozen protocol, whose seed list is **n=6**. `scripts/run_factorial_n20.py` extends the outer seed set under that protocol to **n=20** using a separate source bank. The main factorial results presented in the Portfolio use the **n=20 outer-seed audit results**; the protocol JSON itself does not define n=20.

The `v011` seed-level audit found that **17/20 seeds** had single-class collapse with Macro-F1 = **0.1667**; the other **3 seeds showed partial recovery**. This is a seed-level result, not an inevitable outcome or evidence of a deterministic failure mechanism.

Noise-only and shuffled-label controls are independent control experiments, not factorial corners.

Shapley is a **path-order summary under the frozen protocol**. It is neither physical causal attribution nor protocol-independent feature importance.

## Snapshot Contents

- `src/`: selected signal-generation, recognition, robustness, diagnostics, and protocol modules.
- `scripts/`: selected factorial, verification, audit, and control scripts.
- `protocol/`: the base factorial attribution protocol JSON.

## Dependency Notes

This snapshot does not include:

- full training datasets and source banks
- generated result files and frozen result tables
- trained model artifacts
- complete source repository dependencies

Some scripts depend on original-repository modules that were not migrated. Data, helper modules, and the original protocol path expected by some scripts are also absent from this snapshot. Therefore, **selected-code is for implementation review, not direct execution**.

## Evidence Boundary

Conclusions are limited to the stated synthetic protocol and fixed test domain. There is **no real radar validation**, **no real plasma environment validation**, and **no generalization guarantee**.

