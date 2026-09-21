# EM-Trace Engineering Evidence Audit

This page is a compact, public-facing evidence map for the EM-Trace showcase. It preserves the distinction between a completed workflow step, a corrected interpretation, a validated engineering capability, and a result that is not numerically stable enough for a quantitative claim.

## Status vocabulary

| Status | Meaning in this profile |
| --- | --- |
| `COMPLETED` | The implementation or documentation task was carried out. |
| `CORRECTED` | An earlier interpretation or extraction convention was found to be wrong and replaced in the current workflow. |
| `VALIDATED` | The method or contract has direct supporting evidence; this does not imply numerical convergence. |
| `NOT_STABLE` | The result remains sensitive to mesh, domain, or the unfinished end-to-end backward signal chain. |

## Direction and polarization audit

**Claim:** the correct single-station backward observation for the saved incidence is θ=90°, φ=180°, with (E_{\mathrm{global},y}=-E_\phi) for the recorded polarization basis.

**Evidence:** the [direction-convention review](https://github.com/feiranzhao15077/EM-Trace/blob/main/docs/validation/EM-Trace_direction_convention_review_v1.md) and the [backward-field validation](https://github.com/feiranzhao15077/EM-Trace/blob/main/docs/validation/EM-Trace_phase3B1_backward_field_validation_v1.md).

**Correction:** historical φ=0° files were forward-direction samples despite the old `backscatter` naming. They are retained as audit history but are not valid backward results.

## Mesh sensitivity audit (M1)

All registered L7/L10/L13/L16 paired runs reported solver success, but each stopped at its configured maximum adaptive-pass boundary. The audit therefore separates solver completion from numerical convergence.

| Comparison | Frozen observation |
| --- | --- |
| P0 vs P180 at L13 | Complex difference 1.329%; RCS difference 0.0637 dB (registered gate passes at this layer). |
| P0 vs P180 at L16 | Complex difference 3.8938%; RCS difference 0.34359 dB (complex gate passes, RCS gate fails). |
| Single-pose stability | Neither P0 nor P180 met the pre-registered stability threshold through L16. |

**Interpretation:** the values may be shown as engineering baselines, not high-precision converged RCS or complex fields. The [M1 closeout](https://github.com/feiranzhao15077/EM-Trace/blob/main/docs/validation/EM-Trace_M1_closeout_and_stage7_scope_v1.md) records the resulting Stage 7 scope limitation.

## Calculation-domain audit (A3)

The audit keeps four concepts separate: boundary-condition type, physical calculation-box extent, target-to-boundary spacing, and the `OpenAddSpaceFactor` configuration parameter. The latter is not itself a wavelength-based physical spacing.

| Control | Frozen observation |
| --- | --- |
| body-only: automatic domain vs unified full-reference domain | RCS change 16.809791 dB; complex-field change 85.807734%; A3 gate fails. |
| body+arms: automatic domain vs unified full-reference domain | RCS change 1.371822 dB; complex-field change 33.134155%; A3 gate fails. |
| Structural ablation interpretation | Heterogeneous-domain quantitative increments are withdrawn; unified-domain rows remain engineering-baseline structure-response comparisons. |

The [A3 E2 final report](https://github.com/feiranzhao15077/EM-Trace/blob/main/docs/validation/EM-Trace_A3_E2_domain_consistency_final_v1.md) explicitly avoids assigning the 16.8 dB difference to boundary reflection alone or separating domain and mesh effects without evidence.

## Signal-chain boundary

The [M1 closeout and Stage 7 scope](https://github.com/feiranzhao15077/EM-Trace/blob/main/docs/validation/EM-Trace_M1_closeout_and_stage7_scope_v1.md) freezes Stage 7 as an **angle-snapshot-driven micro-motion signal-generation workflow / engineering-chain demonstration**. The 20 Hz setting is consistent with the preset 180° periodic mapping; it is not an independent blade-frequency validation. The remaining 48 angles and a new STFT campaign are not restored.

## Traceability and historical corrections

| Item | Public status |
| --- | --- |
| Old φ=0° “backscatter” extraction | `INVALID_DIRECTION` / `SUPERSEDED`; see the direction review. |
| Heterogeneous-domain body-only → body+arms → full quantitative increments | `SUPERSEDED`; do not use as exact structural contributions. |
| Unconverged high-precision wording | `NOT_CONVERGED`; replaced by engineering-baseline wording. |
| Stage 8B log recovery | Byte-exact historical archives were recovered from EM-Trace commits `22044e378f09f5e17a3fb74cb2ce42306537bb58` and `15030aa843b253595945965cb5cbf1502ce8a599`; ambiguous current log copies remain excluded from case provenance. |

The [A5 traceability closeout](https://github.com/feiranzhao15077/EM-Trace/blob/main/docs/validation/EM-Trace_A5_traceability_closeout_v1.md) and [artifact manifest v2](https://github.com/feiranzhao15077/EM-Trace/blob/main/docs/validation/EM-Trace_artifact_manifest_v2.csv) are the source of the public evidence mapping.

## Source index

- [Scientific status freeze](https://github.com/feiranzhao15077/EM-Trace/blob/main/docs/final/EM-Trace_scientific_status_freeze_v1.md)
- [Final project summary](https://github.com/feiranzhao15077/EM-Trace/blob/main/docs/reports/EM-Trace_final_project_summary_v2.md)
- [Direction convention review](https://github.com/feiranzhao15077/EM-Trace/blob/main/docs/validation/EM-Trace_direction_convention_review_v1.md)
- [Backward complex-field validation](https://github.com/feiranzhao15077/EM-Trace/blob/main/docs/validation/EM-Trace_phase3B1_backward_field_validation_v1.md)
- [M1 mesh-sensitivity closeout](https://github.com/feiranzhao15077/EM-Trace/blob/main/docs/validation/EM-Trace_M1_closeout_and_stage7_scope_v1.md)
- [A3 domain-consistency final report](https://github.com/feiranzhao15077/EM-Trace/blob/main/docs/validation/EM-Trace_A3_E2_domain_consistency_final_v1.md)
- [A5 traceability closeout](https://github.com/feiranzhao15077/EM-Trace/blob/main/docs/validation/EM-Trace_A5_traceability_closeout_v1.md)
- [Scientific status freeze commit](https://github.com/feiranzhao15077/EM-Trace/commit/e58dd6d)
