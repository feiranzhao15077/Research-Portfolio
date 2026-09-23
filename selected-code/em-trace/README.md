# EM-Trace — Selected Code Snapshot

## Purpose

This selected-code snapshot demonstrates:

- parameterized CAD workflow
- STEP to CST geometry handoff
- complex-field snapshot extraction
- Python signal-processing pipeline

It is not a complete CST project archive, a quantitative radar validation, a high-precision RCS convergence study, or a real radar measurement. It is intended for implementation review, not as a claim of end-to-end physical validation.

## Source

Source commit: `0d6d5316e0db47299583beb6ac0f858521feb0db`

This is a selected implementation snapshot from EM-Trace commit `0d6d531`.

The four code files below are selected from this commit. Machine-specific CST installation and macro paths have been replaced by local configuration placeholders; configure them locally before execution. No CST project or simulation was opened or run during this migration.

## Contents

### `cad_cst/create_single_blade_v1.py`

Supports parameterized blade geometry and geometry-only CST project creation.

Does not provide a complete aerodynamic model or full solver validation. The script does not configure or run a solver, excitation, mesh, monitor, boundary, or RCS task.

### `cad_cst/import_quadrotor_step_validation_v1.py`

Supports AP214 STEP import into CST and CST object-tree inspection.

Does not validate solver results; the script imports geometry only and does not configure or run a solver.

### `cad_cst/extract_rotor_complex_snapshots_v1.py`

Supports complex far-field snapshot extraction, metadata recording, and direction/polarization bookkeeping. The recorded convention includes +X propagation, observation angles θ=90°, φ=180°, and handling of the CST spherical Eφ component.

This is not complete polarization validation or quantitative radar verification. Extraction reads results from existing CST projects; it does not establish their physical accuracy.

### `signal/rotor_microdoppler_pipeline_v1.py`

Supports the processing chain from complex snapshots to a slow-time signal and STFT visualization.

The script labels its output **pipeline verification only**. Its 15° CST snapshot grid does not establish a verified Micro-Doppler measurement.

## Evidence Boundary

This snapshot supports engineering workflow presentation:

`CAD → CST → Complex Field → Python Signal Processing`

It does not support claims of:

- high-precision RCS convergence
- verified Micro-Doppler measurement
- real radar validation

## Dependency Notes

This is not a standalone package. The selected scripts depend on a local CST environment, existing CST simulation projects/results, and a generated complex-snapshot CSV.

Not included:

- CST projects
- solver results
- raw simulation database

The CST installation paths are intentionally unset in the selected scripts. Configure local paths and provide the original inputs before any execution.

### Original repository layout dependency

The original scripts resolve relative inputs and outputs against the original repository layout. This selected directory does not guarantee direct execution. Configuring CST installation paths alone is insufficient.

The CAD/CST scripts retain `Path(__file__).resolve().parents[3]`, which now points to the Research-Portfolio root. The signal script retains `parents[1]`, which points to this snapshot directory. Their result directories therefore do not automatically align after migration; execution would require adapting the directory layout and input/output paths. The code is retained for implementation review.

- **CAD/CST**: requires a local CST environment and original geometry/project inputs. CST project files are not included; geometry handoff is not RCS convergence validation.
- **Complex-field extraction**: demonstrates the extraction workflow, coordinate/polarization records and provenance. Existing solver results are required; this is not real radar validation.
- **Signal pipeline**: requires the generated complex-snapshot CSV and remains **pipeline verification only**, not quantitative Micro-Doppler measurement.
