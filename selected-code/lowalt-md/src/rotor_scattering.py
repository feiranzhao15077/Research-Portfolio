"""Free-space complex baseband rotor echo."""
from __future__ import annotations

import numpy as np

from .geometry import SceneGeometry, path_lengths


def free_space_echo(scatterer_positions_m: np.ndarray, scene: SceneGeometry, frequency_hz: float,
                    cell_weights: np.ndarray | None = None) -> np.ndarray:
    """Sum monostatic two-way point-scatterer returns."""
    pos = np.asarray(scatterer_positions_m, dtype=float)
    if pos.ndim != 3:
        raise ValueError("positions must have shape (time, cells, 3)")
    rd, _ = path_lengths(pos, scene)
    wavelength = 299_792_458.0 / frequency_hz
    amp = 1.0 / np.maximum(rd, 1e-9) ** 2
    if cell_weights is not None:
        w = np.asarray(cell_weights, dtype=float)
        if w.shape != (pos.shape[1],):
            raise ValueError("cell_weights shape mismatch")
        amp = amp * w[None, :]
    return np.sum(amp * np.exp(-1j * 4 * np.pi * rd / wavelength), axis=1)

