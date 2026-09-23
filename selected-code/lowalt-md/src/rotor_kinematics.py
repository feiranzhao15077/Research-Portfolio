"""Deterministic discrete-scatterer rotor kinematics."""
from __future__ import annotations

import numpy as np


def rotor_scatterer_positions(time_s: np.ndarray, blade_count: int, rotor_center_m: np.ndarray,
                              rotor_radius_m: float, rotor_speed_rpm: float,
                              scatterers_per_blade: int = 12, phase_rad: float = 0.0,
                              tilt_rad: float = 0.0, motion_scale: float = 1.0) -> np.ndarray:
    """Return positions with shape (n_time, blade_count*scatterers_per_blade, 3)."""
    t = np.asarray(time_s, dtype=float)
    center = np.asarray(rotor_center_m, dtype=float)
    if center.shape != (3,) or blade_count not in (2, 3, 4) or rotor_radius_m <= 0:
        raise ValueError("invalid rotor geometry")
    if scatterers_per_blade < 1 or rotor_speed_rpm < 0:
        raise ValueError("invalid scatterer count or speed")
    radial = np.linspace(0.08, 1.0, scatterers_per_blade) * rotor_radius_m
    omega = 2 * np.pi * rotor_speed_rpm / 60.0
    base = np.arange(blade_count)[:, None] * (2 * np.pi / blade_count)
    phi = base + radial[None, :] * 0.0
    local = np.zeros((blade_count, scatterers_per_blade, 3), dtype=float)
    local[..., 0] = radial[None, :] * np.cos(phi)
    local[..., 1] = radial[None, :] * np.sin(phi)
    # The frozen attitude is applied after the body-frame spin.  This is
    # p(t) = c + Ry(beta) Rz(omega*t + phase) p0, so the rotor normal is
    # time invariant.  motion_scale=0 is a frozen-rotor gate.
    cy, sy = np.cos(tilt_rad), np.sin(tilt_rad)
    rot_y = np.array([[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]])
    out = np.empty((t.size, blade_count * scatterers_per_blade, 3), dtype=float)
    for i, ti in enumerate(t):
        angle = motion_scale * omega * ti + phase_rad
        cz, sz = np.cos(angle), np.sin(angle)
        rot_z = np.array([[cz, -sz, 0.0], [sz, cz, 0.0], [0.0, 0.0, 1.0]])
        body_rotated = local @ rot_z.T
        out[i] = center + (body_rotated @ rot_y.T).reshape(-1, 3)
    return out
