# EMvision methodology

_Display-level method outline for the EMvision project._

---

## ⚙️ Analysis chain

The display materials organize the project around a three-factor C-G-P protocol: channel, generator and parameter. The current main attribution narrative uses a `2 × 2 × 2` factorial structure and examines main effects and interactions within the synthetic protocol.

```mermaid
flowchart LR
    accTitle: EMvision analysis chain
    accDescr: The display workflow moves from a controlled synthetic protocol through echo generation, feature extraction, attribution audit, and bounded reporting.

    protocol[📋 Define C-G-P protocol] --> echo_generation[⚙️ Generate synthetic echoes]
    echo_generation --> feature_chain[📊 Build micro-Doppler features]
    feature_chain --> attribution_audit[🔍 Audit effects and interactions]
    attribution_audit --> bounded_report[📝 Report bounded claims]

    classDef process fill:#dbeafe,stroke:#2563eb,stroke-width:2px,color:#1e3a5f
    classDef audit fill:#fef9c3,stroke:#ca8a04,stroke-width:2px,color:#713f12
    classDef output fill:#dcfce7,stroke:#16a34a,stroke-width:2px,color:#14532d

    class protocol,echo_generation,feature_chain process
    class attribution_audit audit
    class bounded_report output
```

## 🧪 Control logic

- Use a pure-noise negative control as a reference for the recognition task
- Separate within-condition performance from cross-condition behavior
- Include fixed-budget resource-allocation diagnostics and a third-generator stress test as supplementary evidence
- Keep synthetic-protocol results separate from claims about independent full-wave or measured validation

## ⚠️ Interpretation boundary

The methodology supports an auditable synthetic experiment. It does not, by itself, establish a measured radar result, a universal generalization law or an independent CST single-station validation result.
