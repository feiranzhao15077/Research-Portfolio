# LowAlt-MD physics framework

_Display-level summary of the Physics Model v2 mechanism chain._

---

## ⚙️ Baseline and mechanism layers

| Layer | Role | Current evidence |
| --- | --- | --- |
| F6 | Fresnel implementation semantics | Implementation check, not measured-material calibration |
| M0 | Free-space baseline | Controlled geometric and waveform baseline |
| M1 | Common complex-gain property | Normalized spectrum L2 `4.2423 × 10^-17`; correlation `1.0` |
| M2 | Element-wise two-term proxy | 200 m, `β = 0.15`, normalized-spectrum L2 `0.025246` |
| M2.5 | Phase and Fresnel heterogeneity diagnosis | Geometry phase variation is much larger in the current parameter range |
| M3 | Explicit DD/DR/RD/RR ledger | Full-target coherent power ledger closes for the displayed cases |

## 🔍 Mechanism interpretation

M1 establishes the restricted common-gain comparison: a nonzero, time-invariant, cell-common, noiseless complex gain preserves normalized spectral structure. M2 then introduces element-wise path-dependent weights, providing the mechanism bridge for normalized micro-Doppler change.

For the 200 m, `β = 0.15` case, the displayed phase diagnostics report spatial phase standard deviation `0.776588 rad` and temporal phase standard deviation `0.684846 rad`; the corresponding Fresnel phase spatial standard deviation is `3.23 × 10^-7 rad`. These are diagnostic comparisons under the current model, not single-factor causal proof.

## 📊 Four-path power ledger

M3 retains `DD`, `DR`, `RD` and `RR`, aggregates by scattering unit and then evaluates full-target coherent and incoherent power. The displayed coherent-to-incoherent ratios are `0.4311` at 200 m and `2.4181` at 400 m, indicating distance-dependent cancellation and reinforcement tendencies within the current configuration.

The corresponding closure residuals are `1.3235 × 10^-22` and `5.9557 × 10^-23`. These values support the ledger closure for the displayed cases; they are not a universal distance law.
