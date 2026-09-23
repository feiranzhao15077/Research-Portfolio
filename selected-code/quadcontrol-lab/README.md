# QuadControl-Lab — Selected Code Snapshot

## Purpose

This selected implementation snapshot is intended for mentor review of:

- rigid-body dynamics
- the controller–mixer–motor simulation chain
- RK4 integration
- the IMU interface
- the observation contract
- a minimal attitude-estimation interface

It is not a complete flight controller, hardware validation, a hardware-in-the-loop (HIL) system, an EKF state estimator, or a real-flight system.

## Source

Source commit: `7b9cb3a`  
Resolved commit: `7b9cb3a4f1a992ec9c583491d1b24c9e97976ed8`

## Contents

- `core/state/`: quaternion and state representations
- `core/dynamics/rigid_body.py`: rigid-body dynamics
- `core/mixer/allocation.py` and `controllers/pipeline.py`: control pipeline and allocation interface
- `simulation/integrator/`, `simulation/motors/`, `simulation/sensors/`: RK4 integrator, motor model, and IMU-facing sensor model
- `core/contracts/`: observation contract and consumer observation adapter
- `estimation/attitude/estimator.py`: minimal attitude-estimation interface
- `tests/`: selected observation-boundary and estimator-physics test examples

## Evidence Boundary

This snapshot supports **simulation-oriented implementation review**. It does not support **complete autonomous aircraft validation**.

The estimated-attitude interface is a minimal interface validation, not a complete estimated-state flight loop. The included modules and tests should not be read as evidence of hardware performance, HIL validation, or real flight.

## Dependency Notes

This selected-code snapshot is not a standalone package. It does not include:

- full simulation scenarios
- result files
- calibration data

The complete dependency and configuration closure is also not included. Some selected modules and tests rely on supporting repository modules outside this snapshot. Tests were not run as part of this migration.
