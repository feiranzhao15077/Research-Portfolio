"""STFT and spectral helpers."""
from __future__ import annotations

import numpy as np
from scipy.signal import stft


def compute_stft(signal: np.ndarray, sample_rate_hz: float, nperseg: int = 512,
                 noverlap: int | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x = np.asarray(signal, dtype=complex)
    if sample_rate_hz <= 0 or nperseg < 8 or nperseg > x.size:
        raise ValueError("invalid sample rate or segment length")
    if noverlap is None:
        noverlap = nperseg // 2
    f, t, z = stft(x, fs=sample_rate_hz, nperseg=nperseg, noverlap=noverlap,
                   return_onesided=False, boundary=None, padded=False)
    order = np.argsort(np.fft.fftshift(f))
    return np.fft.fftshift(f), t, np.fft.fftshift(z, axes=0)


def normalized_spectrum(signal: np.ndarray, sample_rate_hz: float) -> tuple[np.ndarray, np.ndarray]:
    f, _, z = compute_stft(signal, sample_rate_hz)
    p = np.mean(np.abs(z) ** 2, axis=1)
    p = p / max(np.sum(p), np.finfo(float).eps)
    return f, p

