# QuadControl-Lab — evidence index

*Curated references to the original repository; full source code and full-rate CSV logs remain in the original project.*

---

## 📌 Evidence policy

This profile page keeps only a compact summary and two derived SVG figures. It does not copy the original repository, full `results/` CSV files, caches, local paths, or intermediate debug output.

The source artifacts record experiment provenance, configuration identity, observation source, and interpretation limits. The numbers below are finite-scenario observations, not stability or real-flight claims.

## 🧪 Existing validation results

| Evidence | Source artifact | Key observation |
| --- | --- | --- |
| Hover baseline | [`experiment.json`](https://github.com/feiranzhao15077/QuadControl-Lab/blob/main/results/phase6_baseline/hover/experiment.json) | 10 s, 10,000 physics steps, final altitude 1.0 m, allocation failure 0, motor limiting 0 |
| Attitude-step baseline | [`experiment.json`](https://github.com/feiranzhao15077/QuadControl-Lab/blob/main/results/phase6_baseline/attitude_step/experiment.json) | Fixed +10° roll reference; benchmark observation; no allocation failure or motor limiting |
| A-10 deterministic response | [`experiment.json`](https://github.com/feiranzhao15077/QuadControl-Lab/blob/main/results/phase6_deterministic_robustness/A-10/experiment.json) | Peak error 9.9998°, final error 0.004746°, settling-like time 1.383 s |
| H-AR initial perturbation | [`experiment.json`](https://github.com/feiranzhao15077/QuadControl-Lab/blob/main/results/phase6_deterministic_robustness/H-AR/experiment.json) | Initial roll +2°; final error 0.00095°, settling-like time 0.665 s |
| T-PP/T-RP torque disturbance | [`T-PP`](https://github.com/feiranzhao15077/QuadControl-Lab/blob/main/results/phase6_deterministic_disturbance/T-PP/experiment.json) · [`T-RP`](https://github.com/feiranzhao15077/QuadControl-Lab/blob/main/results/phase6_deterministic_disturbance/T-RP/experiment.json) | 0.05 N·m, 0.1 s Body FLU torque pulses; max error 1.603°, recovery 0.69 s |

## 🧭 ESTIMATED observation evidence

The estimated path is supported by committed source and regression tests:

```text
IdealImu
  → ImuMeasurement
  → MinimalAttitudeEstimator
  → ControllerObservation(source=ESTIMATED)
  → ConsumerObservationAdapter(ATTITUDE_INNER_LOOP)
  → ControllerPipeline
```

- [Observation path implementation](https://github.com/feiranzhao15077/QuadControl-Lab/blob/main/experiments/simulation/observation_inner_loop.py)
- [Inner-loop comparison harness](https://github.com/feiranzhao15077/QuadControl-Lab/blob/main/experiments/simulation/estimated_inner_loop.py)
- [Integration regression](https://github.com/feiranzhao15077/QuadControl-Lab/blob/main/tests/regression/test_estimated_inner_loop_integration.py)
- [Experiment regression](https://github.com/feiranzhao15077/QuadControl-Lab/blob/main/tests/regression/test_estimated_inner_loop_experiment.py)

The estimated helper intentionally records `state_was_integrated = false`. It demonstrates source provenance, partial validity, deterministic replay, and controller-boundary acceptance; it is not a persisted full-plant estimated-flight result.

## 🚫 Not included in this profile page

- Full source tree or duplicated implementation files
- Full `results/` CSV logs
- EKF, full position/velocity estimation, GPS, or sensor fusion
- HIL, real-vehicle, or real-flight evidence
- Untracked design drafts or local debugging files
