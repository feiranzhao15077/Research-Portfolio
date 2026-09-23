"""Fresnel reflection coefficients for a conductive half-space."""
from __future__ import annotations

import numpy as np

EPS0 = 8.8541878128e-12


def complex_permittivity(epsilon_r: float, conductivity_s_m: float, frequency_hz: float) -> complex:
    """Return relative complex permittivity for the project ``exp(+jωt)`` convention.

    With ``exp(+jωt)``, a passive conductor is represented as
    ``epsilon_r - j*sigma/(ω*epsilon_0)``.  Frequency is in Hz and
    conductivity in S/m.
    """
    if not np.isfinite([epsilon_r, conductivity_s_m, frequency_hz]).all():
        raise ValueError("parameters must be finite")
    if epsilon_r <= 0 or conductivity_s_m < 0 or frequency_hz <= 0:
        raise ValueError("epsilon_r > 0, conductivity >= 0, frequency > 0 required")
    return complex(epsilon_r, -conductivity_s_m / (2 * np.pi * frequency_hz * EPS0))


def fresnel_coefficients(theta_rad: np.ndarray | float, epsilon_r: float, conductivity_s_m: float,
                        frequency_hz: float) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(Gamma_H, Gamma_V)`` for incidence angle from surface normal.

    In the x-z ground plane, H is perpendicular to the plane of incidence
    (TE/s polarization) and V is parallel to it (TM/p polarization).  The
    returned coefficients use the electric-field basis convention and the
    ``exp(+jωt)`` time convention.
    """
    theta = np.asarray(theta_rad, dtype=float)
    if np.any(~np.isfinite(theta)) or np.any((theta < 0) | (theta > np.pi / 2)):
        raise ValueError("theta must lie in [0, pi/2] radians")
    eps = complex_permittivity(epsilon_r, conductivity_s_m, frequency_hz)
    c = np.cos(theta)
    if epsilon_r == 1.0 and conductivity_s_m == 0.0:
        # Avoid grazing-angle cancellation when the two media are identical.
        z = np.zeros_like(theta, dtype=complex)
        return z, z.copy()
    u = np.sqrt(eps - np.sin(theta) ** 2 + 0j)
    gh = (c - u) / (c + u)
    gv = (eps * c - u) / (eps * c + u)
    return gh, gv


def fresnel_te(theta_from_normal_rad: np.ndarray | float, epsilon_r: float,
               conductivity_s_m: float, frequency_hz: float) -> np.ndarray:
    """TE (H/perpendicular) electric-field reflection coefficient."""
    return fresnel_coefficients(theta_from_normal_rad, epsilon_r, conductivity_s_m, frequency_hz)[0]


def fresnel_tm(theta_from_normal_rad: np.ndarray | float, epsilon_r: float,
               conductivity_s_m: float, frequency_hz: float) -> np.ndarray:
    """TM (V/parallel) electric-field reflection coefficient."""
    return fresnel_coefficients(theta_from_normal_rad, epsilon_r, conductivity_s_m, frequency_hz)[1]
