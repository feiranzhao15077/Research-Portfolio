# LowAlt-MD validation summary

_Public evidence summary for the LowAlt-MD research showcase._

---

## Validation chain

| Stage | Status | What was checked | Representative evidence |
| --- | --- | --- | --- |
| F6 / M0 / M1 | PASS | Fresnel semantics, free-space baseline, and restricted common-gain property | `docs/validation/LowAlt-MD_physics_v2_F6_M0_M1_validation_v1.md` |
| M2 / M2.5 | PASS | Element-wise non-common complex weights, path phase, and Fresnel heterogeneity | `docs/validation/LowAlt-MD_physics_v2_M2_M25_validation_v1.md` |
| M3-alpha / beta | PASS | Explicit `DD/DR/RD/RR`, full-target power closure, distance/component diagnostics | `docs/validation/LowAlt-MD_physics_v2_M3_validation_v1.md` |
| R2 Track A / B | PASS | Same-environment, cross-height, cross-ground, paired latent, and leakage audit | `docs/validation/LowAlt-MD_physics_v2_R2_validation_v1.md` |
| R2.1 | PASS | Four-combination label-permutation negative control on frozen R2 features | `docs/validation/LowAlt-MD_physics_v2_R2_1_permutation_validation_v1.md` |
| R3 | PASS as protocol/experiment | Matched unique latent-state and waveform budgets for 20/40→80 m | `docs/validation/LowAlt-MD_physics_v2_R3_validation_v1.md` |

## Quantitative evidence

| Check | Result | Interpretation |
| --- | ---: | --- |
| M1 normalized-spectrum L2 | `4.2423×10^-17` | Restricted common complex gain does not change normalized structure |
| M2 normalized-spectrum L2 | `0.025246` | Element-wise non-common weights can change normalized micro-Doppler structure |
| M2.5 path-phase spatial standard deviation | `0.776588 rad` | Geometry-induced phase heterogeneity is substantial in the primary case |
| M2.5 Fresnel-phase spatial standard deviation | `3.23×10^-7 rad` | Fresnel cell-to-cell phase variation is much smaller in the same case |
| M3 coherent/incoherent ratio | `0.4311` at 200 m; `2.4181` at 400 m | Current configuration changes between destructive and constructive tendency |
| M3 closure residual | `1.3235×10^-22` at 200 m; `5.9557×10^-23` at 400 m | Full-target power ledger closes numerically |

## Recognition and generalization

### Track A and Track B

- Track A keeps raw synthetic energy scaling and envelope features. Its near-perfect same-environment performance is not treated as pure micro-Doppler evidence.
- Track B uses scatterer-strength control, final waveform RMS normalization, and structural features. It retains above-chance structure information in selected same-environment settings, but remains environment-sensitive.

### R2.1 negative control

R2.1 reuses the frozen R2 noisy feature matrices and splits, and only permutes training labels. The four combinations all place real-label performance above their corresponding permutation null distributions. This supports label-dependent signal in the frozen features; it does not prove that all shortcut or leakage risks are absent.

### R3 negative result

Under matched unique latent-state and waveform budgets, simple 20 m+40 m training did not show a stable advantage over the best single-environment baseline at unseen 80 m. At 200 m, the multi−best-single paired difference was `−0.0322` for Logistic Regression and `−0.0434` for the one-hidden-layer MLP. At 400 m, the corresponding differences were `−0.0158` and `−0.0222`.

The correct scope is the current Physics Model v2, current structural features, simple classifiers, and the fixed 20/40→80 m protocol. The result does not establish that multi-environment training or domain adaptation is generally ineffective.

## Physics Model v2 correction record

| Problem found | Correction | Consequence |
| --- | --- | --- |
| Fresnel incidence geometry used an incomplete vertical term | Mirror-radar geometry uses the radar height in the incidence-angle construction | F6 and downstream reflected terms were revalidated |
| Tilt and spin operators were ordered inconsistently | Fixed-tilt rotor uses `Ry(β)Rz(ωt)p0` | Rotor normal is time-invariant and rigid-body distances are preserved |
| Per-scatterer and full-target power levels could be conflated | Aggregate `E_p=Σ_k E_{p,k}` before coherent/incoherent accounting | M3 path ledger and pairwise closure become auditable |
| R2.1 could be confused with a fresh rendering experiment | Reuse frozen R2 noisy features and splits; permute only training labels | The permutation result is a reproducible negative control |
| Earlier R3 budget could mix waveform count and latent diversity | Match both budgets and use common latent identities across training arms | The v2 R3 conclusion replaces the affected v1 conclusion |

## Scope and limitations

- This is a synthetic, flat-ground, far-field-oriented study.
- Ground A/B are `illustrative_control` settings, not calibrated terrain labels.
- The scatterer is an isotropic point proxy, not a calibrated blade RCS model.
- There is no rough-ground diffuse scattering, antenna pattern, measured radar validation, or full target–background electromagnetic solve.
- v1 artifacts are retained for history; affected v1 quantitative results are superseded by v2 and are not silently mixed into this summary.

## Provenance

- Original repository: <https://github.com/feiranzhao15077/LowAlt-MD>
- Current scientific freeze: [`docs/final/LowAlt-MD_physics_v2_scientific_status_freeze_v1.md`](https://github.com/feiranzhao15077/LowAlt-MD/blob/master/docs/final/LowAlt-MD_physics_v2_scientific_status_freeze_v1.md)
- v2 quantitative sources: `results/v2/m2_m25/`, `results/v2/m3/`, `results/v2/r2/`, `results/v2/r2_1/`, `results/v2/r3/`
- Current freeze commit: `8268caa`
