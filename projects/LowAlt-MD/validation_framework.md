# LowAlt-MD validation framework

_Validation organization for the Physics Model v2 display package._

---

## 🧪 Validation layers

| Layer | Question | Display interpretation |
| --- | --- | --- |
| Core, F6, M0, M1 | Are the baseline semantics and restricted property implemented consistently? | PASS within the stated scope |
| M2, M2.5, M3 | Can element-wise path effects and the full-target power ledger be audited? | Mechanism evidence with explicit proxy limits |
| R2 Track A/B | Does recognition remain after an energy-control protocol? | Track A contains an energy shortcut; Track B retains structural information but is environment-dependent |
| R2.1 | Are real-label scores above the same-feature permutation null? | Negative-control closure completed |
| R3 | Does matched-budget multi-environment training improve unseen-height generalization? | No stable positive advantage in the tested design |

## 📡 Controlled recognition audit

Track A retains raw amplitude and envelope information and is not treated as pure structural micro-Doppler evidence. Track B uses `α_k = 1/√N`, final RMS normalization and structural features to reduce the obvious energy shortcut; this does not prove that every shortcut has been removed.

R2.1 reuses the frozen feature and split and permutes only training labels. The displayed real-label scores exceed the corresponding permutation nulls, supporting a signal-above-null statement without proving absence of leakage or uniqueness of the physical mechanism.

## 🎯 Matched-budget generalization boundary

R3 matches both unique latent-state budget and waveform budget: each class uses 30 shared latent states and 60 training waveforms, with a shared unseen 80 m test set. The frozen wording is:

> Under simultaneous unique latent-state and waveform budget matching, simple 20 m + 40 m multi-environment training did not show a stable generalization advantage over the best single-environment baseline on the unseen 80 m environment; some model and distance conditions incurred a performance cost.

This result is bounded to the current Physics Model v2, feature set, 20/40→80 m design and simple joint training protocol. It must not be generalized to “multi-environment training is ineffective”.

## ⚠️ Evidence boundary

The portfolio does not import raw CSV/JSON ledgers, full feature matrices, binary caches or experiment scripts. The source project remains the audit entry for full numerical reproduction.
