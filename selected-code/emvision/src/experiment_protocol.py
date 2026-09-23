"""Shared, explicit synthetic receiver and run-level statistics protocol.

Power is in the arbitrary squared-amplitude units of the rotor model, not watts.
The reference is fixed before sampling and is independent of labels and signals.
"""
import numpy as np

PROTOCOL_VERSION = "2026-08-28-fixed-receiver-v1"
REFERENCE_POWER = 1e-3
HRRP_REFERENCE_AMPLITUDE = 0.5
RECORDED_SUMMARIES: list[dict[str, object]] = []


def receiver_sigma(snr_db, reference_power=REFERENCE_POWER):
    """Per-real-component noise SD, relative to a fixed reference power."""
    if not np.isfinite(reference_power) or reference_power <= 0:
        raise ValueError("reference_power must be finite and positive")
    return np.sqrt(reference_power / (2 * 10.0**(snr_db / 10.0)))


def sample_sd(values):
    values = np.asarray(values, dtype=float)
    if values.ndim != 1 or len(values) < 2 or not np.all(np.isfinite(values)):
        raise ValueError("at least two finite run-level observations required")
    sd = float(np.std(values, ddof=1))
    RECORDED_SUMMARIES.append(dict(values=values.tolist(), n=len(values),
                                  mean=float(values.mean()), sample_sd=sd))
    return sd


def mean_ci95(mean, sd, n):
    """Student-t interval for the prespecified 6/8-run protocols.
    Also supports 2..10 runs for small diagnostic experiments. Does not claim
    distribution-free coverage or include physical-model uncertainty.
    """
    critical = {2: 12.7062047364, 3: 4.3026527297, 4: 3.1824463053,
                5: 2.7764451052, 6: 2.5705818356, 7: 2.4469118511,
                8: 2.3646242516, 9: 2.3060041352, 10: 2.2621571629}
    if n not in critical:
        raise ValueError("t critical value not defined for this run count")
    half = critical[n]*sd/np.sqrt(n)
    return [float(mean-half), float(mean+half)]


def metadata():
    return dict(protocol_version=PROTOCOL_VERSION,
                noise_reference_power=REFERENCE_POWER,
                hrrp_reference_amplitude=HRRP_REFERENCE_AMPLITUDE,
                split="stratified_random_80_20_unless_explicit",
                training="separate_model_per_condition",
                sd_ddof=1, ci_method="Student-t, run-level, normality assumed",
                uncertainty_scope="simulation seeds only; not physical model uncertainty")
