# LowAlt-MD — Selected Code Snapshot

This directory contains a **selected implementation snapshot** from the LowAlt-MD repository at commit `38684af`, prepared for research demonstration.

## Included

The snapshot shows rotor kinematics, image geometry, Fresnel reflection, layered complex-echo models (M0–M3), signal features, representative experiment scripts, and focused tests. The files retain their paths within the original project to make their provenance and imports easier to follow.

## Scope and limitations

- **Synthetic data only.** The models and experiments represented here use synthetic signals.
- The propagation model uses a **flat lossy ground assumption** and a low-order specular multipath approximation.
- The rotor uses an **isotropic point scatterer proxy**, not calibrated blade RCS or a full-wave target-background model.
- There is **no real radar validation** or measured radar data.
- This is **not a complete reproduction package**. Raw data, generated results, figures, the full experiment repository, and a pinned dependency environment are not included.
- The matched-budget protocol helper documents constraints; its presence alone does not reproduce the reported negative result.

For the project summary, evidence, and full scientific boundaries, see the [LowAlt-MD portfolio page](../../projects/lowalt-md/README.md) and [validation evidence](../../projects/lowalt-md/evidence/validation_summary.md).

## Runtime notes

The selected scripts import NumPy, SciPy, and Matplotlib; the tests use pytest. These dependencies are inferred from the selected files. The source commit does not provide a tracked dependency lock or requirements file, and this snapshot has not been independently tested as a standalone package.

The experiment scripts write outputs under `results/` and `figures/` when run. Their provenance helpers may read the enclosing Git checkout's `HEAD`; inside Research-Portfolio that identifies the portfolio checkout, not the original LowAlt-MD commit. Use the commit recorded above as the source provenance. No execution or reproduction claim is made here.

