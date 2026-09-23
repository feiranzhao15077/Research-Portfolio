"""Minimal, auditable monostatic DD/DR/RD/RR path ledger (M3-alpha).

This module models propagation paths only.  Scatterer weights are isotropic
surrogates; no real target bistatic RCS or PO/SBR solver is implied.
"""
from __future__ import annotations

import numpy as np

from .geometry import SceneGeometry, incidence_angle_rad, path_lengths
from .fresnel import fresnel_coefficients


def four_path_echo(
    scatterer_positions_m: np.ndarray,
    scene: SceneGeometry,
    frequency_hz: float,
    epsilon_r: float,
    conductivity_s_m: float,
    polarization: str = "H",
    scatterer_weights: np.ndarray | None = None,
) -> tuple[np.ndarray, dict[str, object]]:
    """Return M3-alpha total echo and unmerged path diagnostics.

    Positions have shape ``(time, cells, 3)`` and SI metres.  ``R_d_tx`` and
    ``R_d_rx`` are direct Tx/cell and cell/Rx leg distances; ``R_r_tx`` and
    ``R_r_rx`` are corresponding image-method legs.  In the reciprocal
    monostatic geometry they are numerically equal, but remain separate in the
    bookkeeping.  Each path uses ``exp(-j*k*L_path)`` with complete path
    length, and factors ``1, Γ_rx, Γ_tx, Γ_txΓ_rx`` for DD/DR/RD/RR.
    """
    pos = np.asarray(scatterer_positions_m, dtype=float)
    if pos.ndim != 3 or pos.shape[-1] != 3:
        raise ValueError("positions must have shape (time, cells, 3)")
    if frequency_hz <= 0 or not np.isfinite(frequency_hz):
        raise ValueError("frequency_hz must be finite and positive")
    pol = polarization.upper()
    if pol not in {"H", "V"}:
        raise ValueError("polarization must be H or V")
    n_cells = pos.shape[1]
    if scatterer_weights is None:
        weights = np.ones(n_cells, dtype=complex)
    else:
        weights = np.asarray(scatterer_weights, dtype=complex)
        if weights.shape != (n_cells,):
            raise ValueError("scatterer_weights shape mismatch")

    rd, rr = path_lengths(pos, scene)
    theta = incidence_angle_rad(pos, scene)
    gh, gv = fresnel_coefficients(theta, epsilon_r, conductivity_s_m, frequency_hz)
    gamma_tx = gh if pol == "H" else gv
    gamma_rx = gamma_tx.copy()
    # Separate leg arrays are intentional, even though the current geometry is reciprocal.
    rd_tx, rd_rx, rr_tx, rr_rx = rd, rd.copy(), rr, rr.copy()
    lengths = {
        "L_DD_m": rd_tx + rd_rx,
        "L_DR_m": rd_tx + rr_rx,
        "L_RD_m": rr_tx + rd_rx,
        "L_RR_m": rr_tx + rr_rx,
    }
    lam = 299792458.0 / frequency_hz
    k = 2.0 * np.pi / lam
    spreading = {
        "DD": 1.0 / np.maximum(rd_tx * rd_rx, 1e-30),
        "DR": 1.0 / np.maximum(rd_tx * rr_rx, 1e-30),
        "RD": 1.0 / np.maximum(rr_tx * rd_rx, 1e-30),
        "RR": 1.0 / np.maximum(rr_tx * rr_rx, 1e-30),
    }
    factors = {"DD": np.ones_like(rd, dtype=complex), "DR": gamma_rx,
               "RD": gamma_tx, "RR": gamma_tx * gamma_rx}
    fields = {}
    for name in ("DD", "DR", "RD", "RR"):
        fields[name] = (weights[None, :] * spreading[name] * factors[name]
                        * np.exp(-1j * k * lengths[f"L_{name}_m"]))
    total = fields["DD"] + fields["DR"] + fields["RD"] + fields["RR"]
    diagnostics = {
        **lengths,
        "R_d_tx_m": rd_tx, "R_d_rx_m": rd_rx, "R_r_tx_m": rr_tx, "R_r_rx_m": rr_rx,
        "incidence_angle_rad": theta, "gamma_tx": gamma_tx, "gamma_rx": gamma_rx,
        "reflection_factor_DD": factors["DD"], "reflection_factor_DR": factors["DR"],
        "reflection_factor_RD": factors["RD"], "reflection_factor_RR": factors["RR"],
        "geometric_spreading_DD": spreading["DD"], "geometric_spreading_DR": spreading["DR"],
        "geometric_spreading_RD": spreading["RD"], "geometric_spreading_RR": spreading["RR"],
        "E_DD": fields["DD"], "E_DR": fields["DR"], "E_RD": fields["RD"], "E_RR": fields["RR"],
        "E_total": total,
    }
    for name in ("DD", "DR", "RD", "RR"):
        diagnostics[f"phase_{name}_rad"] = np.angle(fields[name])
        diagnostics[f"phase_{name}_unwrapped_rad"] = np.unwrap(np.angle(fields[name]), axis=0)
        diagnostics[f"magnitude_{name}"] = np.abs(fields[name])

    # Canonical v2 full-target power ledger.  Path fields are first summed
    # over scatterers; all coherent, incoherent and pairwise quantities below
    # live at the same (time,) level.
    path_names = ("DD", "DR", "RD", "RR")
    aggregate_fields = {name: np.sum(fields[name], axis=1) for name in path_names}
    full_target = np.sum(np.stack([aggregate_fields[name] for name in path_names], axis=0), axis=0)
    full_target_incoherent = np.sum(
        np.stack([np.abs(aggregate_fields[name]) ** 2 for name in path_names], axis=0), axis=0
    )
    pairwise = {}
    for i, left in enumerate(path_names):
        for right in path_names[i + 1:]:
            pairwise[f"{left}_{right}"] = 2.0 * np.real(
                aggregate_fields[left] * np.conj(aggregate_fields[right])
            )
    full_target_coherent = np.abs(full_target) ** 2
    full_target_pairwise = np.sum(np.stack(list(pairwise.values()), axis=0), axis=0)
    diagnostics.update({
        "full_target_E_DD": aggregate_fields["DD"],
        "full_target_E_DR": aggregate_fields["DR"],
        "full_target_E_RD": aggregate_fields["RD"],
        "full_target_E_RR": aggregate_fields["RR"],
        "full_target_E_total": full_target,
        "full_target_coherent_power": full_target_coherent,
        "full_target_incoherent_power": full_target_incoherent,
        "full_target_pairwise_interference": pairwise,
        "full_target_pairwise_sum": full_target_pairwise,
        "full_target_interference_diagnostic": full_target_coherent - full_target_incoherent,
        # Explicit names for the original per-cell diagnostic layer.
        "per_cell_coherent_power": np.abs(total) ** 2,
        "per_cell_incoherent_power": sum(np.abs(fields[n]) ** 2 for n in path_names),
    })
    diagnostics["per_cell_interference_diagnostic"] = (
        diagnostics["per_cell_coherent_power"] - diagnostics["per_cell_incoherent_power"]
    )
    # Legacy aliases are retained for v1 scripts only.  v2 reports/tests must
    # use the explicit full_target_* or per_cell_* names above.
    diagnostics["incoherent_power"] = diagnostics["per_cell_incoherent_power"]
    diagnostics["coherent_power"] = diagnostics["per_cell_coherent_power"]
    diagnostics["interference_diagnostic"] = diagnostics["per_cell_interference_diagnostic"]
    return np.sum(total, axis=1), diagnostics
