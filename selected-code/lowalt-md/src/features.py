"""Small, interpretable micro-Doppler feature set."""
from __future__ import annotations

import numpy as np
from scipy.signal import fftconvolve

from .signal_processing import compute_stft


def extract_features(signal: np.ndarray, sample_rate_hz: float,
                     normalize_rms: bool = False,
                     rms_floor: float = 1e-12) -> dict[str, float]:
    x = np.asarray(signal, dtype=complex)
    if normalize_rms:
        rms = float(np.sqrt(np.mean(np.abs(x) ** 2)))
        if rms > rms_floor:
            x = x / rms
    f, _, z = compute_stft(x, sample_rate_hz)
    p = np.mean(np.abs(z) ** 2, axis=1)
    p = p / max(p.sum(), np.finfo(float).eps)
    mean_f = float(np.sum(f * p))
    width = float(np.sqrt(np.sum((f - mean_f) ** 2 * p)))
    entropy = float(-np.sum(p * np.log(p + 1e-15)) / np.log(p.size))
    env = np.abs(x)
    # Count dominant envelope cycles as a simple flicker proxy.
    centered = env - env.mean()
    # FFT autocorrelation is mathematically equivalent to np.correlate here,
    # but avoids O(n²) runtime during paired R1 generation.
    ac = fftconvolve(centered, centered[::-1], mode="full")[len(centered)-1:]
    peaks = np.where((ac[1:-1] > ac[:-2]) & (ac[1:-1] >= ac[2:]))[0] + 1
    flicker = float(sample_rate_hz / peaks[0]) if peaks.size and peaks[0] > 0 else 0.0
    return {"spectrum_width_hz": width, "spectral_entropy": entropy,
            "envelope_mean": float(env.mean()), "envelope_std": float(env.std()),
            "flicker_frequency_hz": flicker,
            "dc_energy_ratio": float(p[np.argmin(np.abs(f))])}
