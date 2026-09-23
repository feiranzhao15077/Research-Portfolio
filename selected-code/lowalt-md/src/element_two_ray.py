"""Moving-scatterer two-ray echo with per-cell Fresnel coefficients."""
from __future__ import annotations

import numpy as np

from .geometry import SceneGeometry, incidence_angle_rad, path_lengths
from .fresnel import fresnel_coefficients


def element_two_ray_echo(scatterer_positions_m: np.ndarray, scene: SceneGeometry, frequency_hz: float,
                         epsilon_r: float, conductivity_s_m: float, polarization: str = "H",
                         reflection_scale: complex = 1.0) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    pos = np.asarray(scatterer_positions_m, dtype=float)
    if pos.ndim != 3 or pos.shape[-1] != 3:
        raise ValueError("positions must have shape (time, cells, 3)")
    pol = polarization.upper()
    if pol not in {"H", "V"}:
        raise ValueError("polarization must be H or V")
    """Return M2 echo and per-cell diagnostics.

    ``rd`` and ``rr`` are one-way equivalent distances: ``rr`` is the
    image-method distance for one radar-to-target leg.  For monostatic
    narrowband propagation, both direct and reflected returns therefore use
    the two-way phase ``exp(-j*4*pi*R/lambda)``.  The reflected term models
    one specular ground interaction on each leg of the monostatic path.
    """
    rd, rr = path_lengths(pos, scene)
    theta = incidence_angle_rad(pos, scene)
    gh, gv = fresnel_coefficients(theta, epsilon_r, conductivity_s_m, frequency_hz)
    gamma = gh if pol == "H" else gv
    wavelength = 299_792_458.0 / frequency_hz
    ad = 1.0 / np.maximum(rd, 1e-9) ** 2
    ar = 1.0 / np.maximum(rr, 1e-9) ** 2
    direct = ad * np.exp(-1j * 4 * np.pi * rd / wavelength)
    reflected = reflection_scale * gamma * ar * np.exp(-1j * 4 * np.pi * rr / wavelength)
    echo = np.sum(direct + reflected, axis=1)
    phi_d = -4 * np.pi * rd / wavelength
    phi_r = -4 * np.pi * rr / wavelength
    diagnostics = {"direct_range_m": rd, "reflected_range_m": rr, "path_difference_m": rr - rd,
                   "incidence_angle_rad": theta, "fresnel_gamma": gamma,
                   "direct_phase_rad": phi_d, "reflected_phase_rad": phi_r,
                   "phase_difference_rad": phi_r - phi_d,
                   "direct_field": direct, "reflected_field": reflected,
                   "reflected_to_direct_magnitude": np.abs(reflected) / np.maximum(np.abs(direct), 1e-30)}
    return echo, diagnostics
