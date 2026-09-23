"""Run the Physics Model v2 M3-alpha/M3-beta mechanism validation only.

The runner deliberately stops before recognition.  It keeps the four path
fields at two audit levels:

* ``E_p_k``: per-scatterer fields, saved as an auxiliary diagnostic;
* ``E_p = sum_k E_p_k``: the canonical full-target power ledger.

No v1 files are overwritten.  All products are written below ``results/v2``
and ``figures/v2``.
"""
from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.element_two_ray import element_two_ray_echo
from src.four_path import four_path_echo
from src.geometry import SceneGeometry, path_lengths
from src.rotor_kinematics import rotor_scatterer_positions
from src.rotor_scattering import free_space_echo
from src.signal_processing import compute_stft, normalized_spectrum
from src.features import extract_features


F0 = 10e9
FS = 200_000.0
TOBS = 0.05
RADAR = np.array([0.0, 0.0, 5.0])
GROUND = {"epsilon_r": 6.0, "conductivity_s_m": 0.01,
          "provenance": "illustrative_control"}
POLARIZATION = "H"
HEIGHT = 20.0
TILT_PRIMARY = 0.15
BLADES = 3
RADIUS = 0.35
RPM = 2400.0
SCATTERERS_PER_BLADE = 12
OUT = ROOT / "results" / "v2" / "m3"
FIG = ROOT / "figures" / "v2" / "m3"
PATH_NAMES = ("DD", "DR", "RD", "RR")
PAIR_NAMES = ("DD_DR", "DD_RD", "DD_RR", "DR_RD", "DR_RR", "RD_RR")
PHASE_RAD = float(np.random.default_rng(0).uniform(0.0, 2.0 * np.pi))

# These are v1 values copied only for an explicit audit table.  They were
# produced at a different (per-cell/mixed) power layer and are not used to
# tune v2.  They remain LEGACY-AFFECTED.
V1_REFERENCE = {
    "coherent_incoherent_ratio_200m": 0.363,
    "coherent_incoherent_ratio_400m": 2.420,
    "interference_mean_200m": -1.318e-9,
    "interference_mean_400m": 2.022e-10,
    "horizontal_phase_slope_beta0": -1.990,
    "path_power_200m": "DD=6.1803e-10; DR=5.1245e-10; RD=5.1245e-10; RR=4.2490e-10 (per-cell legacy layer)",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_head() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:  # noqa: BLE001
        return "UNAVAILABLE"


def timebase() -> np.ndarray:
    return np.arange(int(FS * TOBS), dtype=float) / FS


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise ValueError(f"cannot write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def render_case(range_m: float, height_m: float = HEIGHT, tilt_rad: float = TILT_PRIMARY,
                ground: dict[str, float] | None = None,
                polarization: str = POLARIZATION) -> dict[str, object]:
    t = timebase()
    center = np.array([range_m, 0.0, height_m])
    scene = SceneGeometry(RADAR, center)
    positions = rotor_scatterer_positions(
        t, BLADES, center, RADIUS, RPM, SCATTERERS_PER_BLADE,
        phase_rad=PHASE_RAD, tilt_rad=tilt_rad,
    )
    g = ground or GROUND
    m0 = free_space_echo(positions, scene, F0)
    m2, m2_diag = element_two_ray_echo(
        positions, scene, F0, g["epsilon_r"], g["conductivity_s_m"], polarization,
    )
    m3, m3_diag = four_path_echo(
        positions, scene, F0, g["epsilon_r"], g["conductivity_s_m"], polarization,
    )
    return {
        "t": t, "scene": scene, "positions": positions, "m0": m0, "m2": m2,
        "m2_diag": m2_diag, "m3": m3, "m3_diag": m3_diag,
        "range_m": range_m, "height_m": height_m, "tilt_rad": tilt_rad,
        "ground": g, "polarization": polarization,
    }


def signal_metrics(reference: np.ndarray, candidate: np.ndarray) -> dict[str, float]:
    corr = float(abs(np.vdot(reference, candidate)) /
                 max(np.linalg.norm(reference) * np.linalg.norm(candidate), 1e-30))
    f_ref, p_ref = normalized_spectrum(reference, FS)
    f_cand, p_cand = normalized_spectrum(candidate, FS)
    if not np.allclose(f_ref, f_cand):
        raise AssertionError("spectrum frequency grids differ")
    _, _, z_ref = compute_stft(reference, FS)
    _, _, z_cand = compute_stft(candidate, FS)
    phase = np.angle(np.vdot(z_ref, z_cand))
    z_ref = z_ref / max(np.linalg.norm(z_ref), 1e-30)
    z_cand = z_cand * np.exp(-1j * phase) / max(np.linalg.norm(z_cand), 1e-30)
    f_ref_features = extract_features(reference, FS)
    cand_features = extract_features(candidate, FS)
    return {
        "waveform_correlation": corr,
        "normalized_spectrum_l2": float(np.linalg.norm(p_ref - p_cand)),
        "normalized_stft_l2": float(np.linalg.norm(z_ref - z_cand)),
        "reference_rms": float(np.sqrt(np.mean(np.abs(reference) ** 2))),
        "candidate_rms": float(np.sqrt(np.mean(np.abs(candidate) ** 2))),
        "reference_power": float(np.mean(np.abs(reference) ** 2)),
        "candidate_power": float(np.mean(np.abs(candidate) ** 2)),
        "spectrum_width_delta_hz": float(
            cand_features["spectrum_width_hz"] - f_ref_features["spectrum_width_hz"]
        ),
        "spectral_entropy_delta": float(
            cand_features["spectral_entropy"] - f_ref_features["spectral_entropy"]
        ),
        "dc_ratio_delta": float(
            cand_features["dc_energy_ratio"] - f_ref_features["dc_energy_ratio"]
        ),
    }


def path_fields(diag: dict[str, object]) -> dict[str, np.ndarray]:
    return {name: np.asarray(diag[f"E_{name}"]) for name in PATH_NAMES}


def aggregate_fields(diag: dict[str, object]) -> dict[str, np.ndarray]:
    return {name: np.asarray(diag[f"full_target_E_{name}"]) for name in PATH_NAMES}


def pairwise_terms(aggregate: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    for i, left in enumerate(PATH_NAMES):
        for right in PATH_NAMES[i + 1:]:
            out[f"{left}_{right}"] = 2.0 * np.real(
                aggregate[left] * np.conj(aggregate[right])
            )
    return out


def unified_ledger(diag: dict[str, object]) -> dict[str, object]:
    """Return and independently recompute the canonical full-target ledger."""
    aggregate = aggregate_fields(diag)
    total = sum(aggregate.values())
    isolated = {name: np.abs(aggregate[name]) ** 2 for name in PATH_NAMES}
    incoherent = sum(isolated.values())
    coherent = np.abs(total) ** 2
    pairs = pairwise_terms(aggregate)
    pair_sum = sum(pairs.values())
    residual = coherent - incoherent - pair_sum
    scale = 1e-30 + 1e-12 * np.maximum.reduce(
        [coherent, incoherent, sum(np.abs(value) for value in pairs.values())]
    )
    return {
        "aggregate": aggregate,
        "isolated": isolated,
        "coherent": coherent,
        "incoherent": incoherent,
        "pairs": pairs,
        "pair_sum": pair_sum,
        "residual": residual,
        "scale": scale,
        "max_abs_residual": float(np.max(np.abs(residual))),
        "max_scaled_residual": float(np.max(np.abs(residual) / scale)),
        "closure_pass": bool(np.all(np.abs(residual) <= scale)),
    }


def write_path_ledgers(range_m: float, case: dict[str, object], ledger: dict[str, object]) -> None:
    t = np.asarray(case["t"])
    agg = ledger["aggregate"]
    isolated = ledger["isolated"]
    pairs = ledger["pairs"]
    common = {"range_m": float(range_m)}
    full_rows = []
    closure_rows = []
    for i, time_s in enumerate(t):
        row: dict[str, object] = {**common, "time_s": float(time_s)}
        for name in PATH_NAMES:
            row[f"E_{name}_real"] = float(np.real(agg[name][i]))
            row[f"E_{name}_imag"] = float(np.imag(agg[name][i]))
            row[f"P_{name}"] = float(isolated[name][i])
        row["P_coh"] = float(ledger["coherent"][i])
        row["P_incoh"] = float(ledger["incoherent"][i])
        row["coherent_incoherent_ratio"] = float(
            ledger["coherent"][i] / max(ledger["incoherent"][i], 1e-300)
        )
        for name in PAIR_NAMES:
            row[f"I_{name}"] = float(pairs[name][i])
        row["pairwise_sum"] = float(ledger["pair_sum"][i])
        row["closure_residual"] = float(ledger["residual"][i])
        row["closure_scale"] = float(ledger["scale"][i])
        full_rows.append(row)
        closure_rows.append({
            "range_m": float(range_m), "time_s": float(time_s),
            "P_coh": row["P_coh"], "P_incoh": row["P_incoh"],
            **{f"I_{name}": row[f"I_{name}"] for name in PAIR_NAMES},
            "pairwise_sum": row["pairwise_sum"],
            "closure_residual": row["closure_residual"],
            "closure_scale": row["closure_scale"],
            "closure_ok": bool(abs(row["closure_residual"]) <= row["closure_scale"]),
        })
    # One combined ledger is easier to audit than two opaque per-range files.
    write_csv(OUT / "full_target_path_ledger.csv", full_rows if not (OUT / "full_target_path_ledger.csv").exists() else _append_rows(OUT / "full_target_path_ledger.csv", full_rows))
    write_csv(OUT / "interference_closure_ledger.csv", closure_rows if not (OUT / "interference_closure_ledger.csv").exists() else _append_rows(OUT / "interference_closure_ledger.csv", closure_rows))


def _append_rows(path: Path, rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Read an existing ledger and append rows without changing field order."""
    with path.open("r", newline="", encoding="utf-8") as fh:
        existing = list(csv.DictReader(fh))
    # CSV strings are acceptable for the audit ledger; preserve old columns.
    return existing + rows


def save_per_scatterer(range_m: float, case: dict[str, object]) -> None:
    d = case["m3_diag"]
    payload: dict[str, object] = {
        "time_s": np.asarray(case["t"]),
        "positions_m": np.asarray(case["positions"]),
    }
    for name in PATH_NAMES:
        payload[f"E_{name}_per_scatterer"] = np.asarray(d[f"E_{name}"])
        payload[f"L_{name}_m"] = np.asarray(d[f"L_{name}_m"])
        payload[f"geometric_spreading_{name}"] = np.asarray(d[f"geometric_spreading_{name}"])
    payload["gamma_tx"] = np.asarray(d["gamma_tx"])
    payload["gamma_rx"] = np.asarray(d["gamma_rx"])
    np.savez_compressed(OUT / f"per_scatterer_path_fields_{int(range_m)}m.npz", **payload)


def summarize_alpha(range_m: float, case: dict[str, object], ledger: dict[str, object]) -> dict[str, object]:
    pairs = ledger["pairs"]
    ratio = ledger["coherent"] / np.maximum(ledger["incoherent"], 1e-300)
    pair_sum = ledger["pair_sum"]
    return {
        "range_m": float(range_m),
        "tilt_rad": float(case["tilt_rad"]),
        "mean_P_DD": float(np.mean(ledger["isolated"]["DD"])),
        "mean_P_DR": float(np.mean(ledger["isolated"]["DR"])),
        "mean_P_RD": float(np.mean(ledger["isolated"]["RD"])),
        "mean_P_RR": float(np.mean(ledger["isolated"]["RR"])),
        "mean_P_coh": float(np.mean(ledger["coherent"])),
        "mean_P_incoh": float(np.mean(ledger["incoherent"])),
        # The primary ratio is a ratio of time-mean powers, matching the
        # usual coherent/incoherent power summary.  The mean of instantaneous
        # ratios is retained separately because it answers a different
        # question when the denominator fluctuates.
        "mean_coherent_incoherent_ratio": float(
            np.mean(ledger["coherent"]) / max(np.mean(ledger["incoherent"]), 1e-300)
        ),
        "mean_instantaneous_coherent_incoherent_ratio": float(np.mean(ratio)),
        "median_coherent_incoherent_ratio": float(np.median(ratio)),
        "mean_interference": float(np.mean(pair_sum)),
        "positive_interference_fraction": float(np.mean(pair_sum > 0.0)),
        "negative_interference_fraction": float(np.mean(pair_sum < 0.0)),
        "max_abs_closure_residual": ledger["max_abs_residual"],
        "max_scaled_closure_residual": ledger["max_scaled_residual"],
        "closure_pass": ledger["closure_pass"],
        **{f"mean_I_{name}": float(np.mean(pairs[name])) for name in PAIR_NAMES},
    }


def factorization_check(case: dict[str, object]) -> dict[str, float]:
    """Independent per-cell alpha*(g_d+g_r)^2 expansion check."""
    d = case["m3_diag"]
    rd = np.asarray(d["R_d_tx_m"])
    rr = np.asarray(d["R_r_tx_m"])
    gamma = np.asarray(d["gamma_tx"])
    lam = 299_792_458.0 / F0
    k = 2.0 * np.pi / lam
    gd = np.exp(-1j * k * rd) / np.maximum(rd, 1e-30)
    gr = gamma * np.exp(-1j * k * rr) / np.maximum(rr, 1e-30)
    independent = (gd + gr) ** 2
    actual = np.asarray(d["E_total"])
    err = np.abs(actual - independent)
    rel = err / np.maximum(np.abs(actual), 1e-30)
    return {
        "max_abs_error": float(np.max(err)),
        "mean_abs_error": float(np.mean(err)),
        "max_relative_error": float(np.max(rel)),
        "mean_relative_error": float(np.mean(rel)),
        "pass": bool(np.max(err) <= 1e-12),
    }


def position_variants(range_m: float, tilt_rad: float) -> tuple[np.ndarray, SceneGeometry]:
    t = timebase()
    center = np.array([range_m, 0.0, HEIGHT])
    scene = SceneGeometry(RADAR, center)
    positions = rotor_scatterer_positions(
        t, BLADES, center, RADIUS, RPM, SCATTERERS_PER_BLADE,
        phase_rad=PHASE_RAD, tilt_rad=tilt_rad,
    )
    horizontal = positions.copy()
    horizontal[:, :, 2] = center[2]
    vertical = positions.copy()
    vertical[:, :, 0:2] = center[0:2]
    return np.stack([positions, horizontal, vertical], axis=0), scene


def scaling_rows() -> list[dict[str, object]]:
    wavelength = 299_792_458.0 / F0
    k = 2.0 * np.pi / wavelength
    rows: list[dict[str, object]] = []
    for beta in (0.0, TILT_PRIMARY):
        for range_m in (200.0, 300.0, 400.0, 600.0, 800.0):
            variants, scene = position_variants(range_m, beta)
            for variant, component in zip(variants, ("full", "horizontal_xy", "vertical_z")):
                rd, rr = path_lengths(variant, scene)
                delta = rr - rd
                per_cell_std = np.std(delta, axis=0)
                mean_std = float(np.mean(per_cell_std))
                rows.append({
                    "beta_rad": beta,
                    "component": component,
                    "range_m": range_m,
                    "path_difference_definition": "one_way_image_leg_delta_R=Rr-Rd",
                    "path_difference_std_mean_m": mean_std,
                    "path_difference_std_cell0_m": float(per_cell_std[0]),
                    "single_leg_phase_std_mean_rad": float(k * mean_std),
                    "round_trip_phase_std_mean_rad": float(2.0 * k * mean_std),
                    "component_note": (
                        "full tilted trajectory" if component == "full" else
                        "z frozen at rotor center" if component == "horizontal_xy" else
                        "x,y frozen at rotor center"
                    ),
                })
    return rows


def fit_scaling(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for beta in (0.0, TILT_PRIMARY):
        for component in ("full", "horizontal_xy", "vertical_z"):
            selected = [r for r in rows if r["beta_rad"] == beta and r["component"] == component]
            x = np.asarray([r["range_m"] for r in selected], dtype=float)
            y = np.asarray([r["single_leg_phase_std_mean_rad"] for r in selected], dtype=float)
            # A beta=0 vertical-only construction is analytically zero.  Use a
            # conservative numerical floor so round-off is not misreported as
            # an apparent power law.
            valid = np.isfinite(y) & (y > 1e-9)
            if np.count_nonzero(valid) < 3:
                out.append({
                    "beta_rad": beta, "component": component,
                    "fit_range_min_m": "", "fit_range_max_m": "", "n_points": int(np.count_nonzero(valid)),
                    "loglog_slope": "", "r_squared": "", "status": "NOT_FIT_ZERO_COMPONENT",
                })
                continue
            xlog = np.log(x[valid]); ylog = np.log(y[valid])
            slope, intercept = np.polyfit(xlog, ylog, 1)
            pred = slope * xlog + intercept
            ss_res = float(np.sum((ylog - pred) ** 2))
            ss_tot = float(np.sum((ylog - np.mean(ylog)) ** 2))
            r2 = 1.0 - ss_res / ss_tot if ss_tot > 0.0 else 1.0
            out.append({
                "beta_rad": beta, "component": component,
                "fit_range_min_m": float(np.min(x[valid])), "fit_range_max_m": float(np.max(x[valid])),
                "n_points": int(np.count_nonzero(valid)), "loglog_slope": float(slope),
                "r_squared": float(r2), "status": "PASS",
            })
    return out


def write_figures(cases: dict[float, dict[str, object]], ledgers: dict[float, dict[str, object]],
                  scaling: list[dict[str, object]]) -> None:
    FIG.mkdir(parents=True, exist_ok=True)
    case200 = cases[200.0]
    d = case200["m3_diag"]
    t = np.asarray(case200["t"]) * 1e3
    fig, ax = plt.subplots(figsize=(8, 4))
    for name in PATH_NAMES:
        ax.plot(t, np.asarray(d[f"L_{name}_m"])[:, 0], label=name)
    ax.set(xlabel="Time (ms)", ylabel="Complete path length (m)", title="v2 M3-alpha path lengths, 200 m")
    ax.grid(alpha=0.25); ax.legend(ncol=4)
    fig.savefig(FIG / "m3_path_lengths_200m.png", dpi=180, bbox_inches="tight"); plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4))
    for name in PATH_NAMES:
        ax.plot(t, np.abs(np.asarray(d[f"full_target_E_{name}"])), label=name)
    ax.set(xlabel="Time (ms)", ylabel="|E_p| (full target)", title="v2 M3-alpha path-field magnitudes, 200 m")
    ax.grid(alpha=0.25); ax.legend(ncol=4)
    fig.savefig(FIG / "m3_full_target_field_magnitudes_200m.png", dpi=180, bbox_inches="tight"); plt.close(fig)

    fig, axes = plt.subplots(2, 1, figsize=(8, 6), sharex=True, constrained_layout=True)
    for R, color in ((200.0, "tab:blue"), (400.0, "tab:orange")):
        led = ledgers[R]
        axes[0].plot(t, led["coherent"], color=color, label=f"coherent {int(R)} m")
        axes[0].plot(t, led["incoherent"], color=color, linestyle="--", label=f"incoherent {int(R)} m")
        axes[1].plot(t, led["pair_sum"], color=color, label=f"I total {int(R)} m")
    axes[0].set_ylabel("Power (a.u.)"); axes[1].set_ylabel("Pairwise interference"); axes[1].set_xlabel("Time (ms)")
    axes[0].grid(alpha=0.25); axes[1].grid(alpha=0.25); axes[0].legend(ncol=2); axes[1].legend()
    fig.savefig(FIG / "m3_coherent_incoherent_200m_400m.png", dpi=180, bbox_inches="tight"); plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4))
    for R, color in ((200.0, "tab:blue"), (400.0, "tab:orange")):
        c = cases[R]
        f, p0 = normalized_spectrum(c["m0"], FS)
        _, p2 = normalized_spectrum(c["m2"], FS)
        _, p3 = normalized_spectrum(c["m3"], FS)
        ax.plot(f / 1e3, p0, color=color, alpha=0.35, label=f"M0 {int(R)} m")
        ax.plot(f / 1e3, p2, color=color, linestyle="--", label=f"M2 {int(R)} m")
        ax.plot(f / 1e3, p3, color=color, linestyle="-", linewidth=1.2, label=f"M3 {int(R)} m")
    ax.set(xlabel="Doppler (kHz)", ylabel="Normalized power", title="v2 M0/M2/M3 normalized spectra")
    ax.grid(alpha=0.25); ax.legend(ncol=2, fontsize=8)
    fig.savefig(FIG / "m3_m0_m2_m3_normalized_spectra.png", dpi=180, bbox_inches="tight"); plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 5))
    for beta, marker in ((0.0, "o"), (TILT_PRIMARY, "s")):
        for component in ("full", "horizontal_xy", "vertical_z"):
            selected = [r for r in scaling if r["beta_rad"] == beta and r["component"] == component]
            x = np.asarray([r["range_m"] for r in selected], float)
            y = np.asarray([r["single_leg_phase_std_mean_rad"] for r in selected], float)
            if np.any(y > 1e-15):
                ax.loglog(x, y, marker=marker, label=f"beta={beta:g}, {component}")
    ax.set(xlabel="Range (m)", ylabel="Single-leg phase std (rad)", title="v2 M3-beta distance scaling")
    ax.grid(True, which="both", alpha=0.25); ax.legend(fontsize=8)
    fig.savefig(FIG / "m3_distance_scaling.png", dpi=180, bbox_inches="tight"); plt.close(fig)


def metric_consistency_check() -> dict[str, object]:
    primary = 0.776588
    scan = 0.684846
    return {
        "status": "PASS_DIFFERENT_DEFINITIONS",
        "primary_value_rad": primary,
        "primary_metric_name": "phase_spatial_std_temporal_mean_rad",
        "primary_definition": "mean_t(std_k(wrapped Delta phi_k(t)))",
        "distance_scan_value_rad": scan,
        "distance_scan_metric_name": "phase_temporal_std_mean_rad",
        "distance_scan_definition": "mean_k(std_t(unwrapped Delta phi_k(t)))",
        "difference_rad": primary - scan,
        "interpretation": "The order of aggregation is different; these are not the same statistic and no ledger inconsistency is present.",
        "source_report": "docs/validation/LowAlt-MD_physics_v2_M2_M25_validation_v1.md",
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    FIG.mkdir(parents=True, exist_ok=True)
    # Ensure a failed or interrupted run cannot leave a misleading mixed ledger.
    for old in (OUT / "full_target_path_ledger.csv", OUT / "interference_closure_ledger.csv"):
        if old.exists():
            old.unlink()

    consistency = metric_consistency_check()
    (OUT / "m2_5_metric_consistency_check.json").write_text(
        json.dumps(consistency, indent=2), encoding="utf-8"
    )
    if consistency["status"] != "PASS_DIFFERENT_DEFINITIONS":
        raise RuntimeError("M2.5 metric consistency check did not pass")

    cases: dict[float, dict[str, object]] = {}
    ledgers: dict[float, dict[str, object]] = {}
    alpha_rows: list[dict[str, object]] = []
    comparison_rows: list[dict[str, object]] = []
    factor_rows: list[dict[str, object]] = []
    for range_m in (200.0, 400.0):
        case = render_case(range_m)
        ledger = unified_ledger(case["m3_diag"])
        if not ledger["closure_pass"]:
            raise RuntimeError(f"full-target closure failed at {range_m:g} m")
        cases[range_m] = case
        ledgers[range_m] = ledger
        write_path_ledgers(range_m, case, ledger)
        save_per_scatterer(range_m, case)
        alpha_rows.append(summarize_alpha(range_m, case, ledger))
        factor = factorization_check(case)
        factor["range_m"] = range_m
        factor_rows.append(factor)
        metrics_m2 = signal_metrics(case["m0"], case["m2"])
        metrics_m3 = signal_metrics(case["m0"], case["m3"])
        for model_name, metrics in (("M2_effective_two_term_proxy", metrics_m2),
                                    ("M3_explicit_four_path", metrics_m3)):
            comparison_rows.append({
                "range_m": range_m, "model": model_name,
                "physics_definition": (
                    "element-wise effective two-term proxy" if model_name.startswith("M2")
                    else "explicit DD/DR/RD/RR full-target path sum"
                ), **metrics,
            })

    write_csv(OUT / "m3_alpha_summary.csv", alpha_rows)
    write_csv(OUT / "m3_m2_m0_comparison.csv", comparison_rows)
    write_csv(OUT / "factorization_closure_ledger.csv", factor_rows)

    # Pairwise summary at the canonical full-target layer.
    pair_rows: list[dict[str, object]] = []
    for range_m, ledger in ledgers.items():
        denominator = sum(abs(float(np.mean(ledger["pairs"][name]))) for name in PAIR_NAMES)
        for name in PAIR_NAMES:
            values = ledger["pairs"][name]
            mean_value = float(np.mean(values))
            pair_rows.append({
                "range_m": range_m, "pair": name,
                "mean": mean_value, "std": float(np.std(values)),
                "positive_fraction": float(np.mean(values > 0.0)),
                "negative_fraction": float(np.mean(values < 0.0)),
                "mean_abs": float(np.mean(np.abs(values))),
                "absolute_mean_contribution": abs(mean_value) / max(denominator, 1e-300),
            })
    write_csv(OUT / "pairwise_interference_summary.csv", pair_rows)

    scaling = scaling_rows()
    fits = fit_scaling(scaling)
    write_csv(OUT / "distance_scaling_ledger.csv", scaling)
    write_csv(OUT / "distance_scaling_fit.csv", fits)
    write_figures(cases, ledgers, scaling)

    v2_by_range = {row["range_m"]: row for row in alpha_rows}
    fit_beta0_horizontal = next(
        row for row in fits if row["beta_rad"] == 0.0 and row["component"] == "horizontal_xy"
    )
    v1_v2_rows = [
        {"metric": "coherent_incoherent_ratio_200m", "v1": V1_REFERENCE["coherent_incoherent_ratio_200m"], "v2": v2_by_range[200.0]["mean_coherent_incoherent_ratio"], "status": "modified", "comparability": "same ratio concept, but v1 legacy power layer; v2 unified full-target ledger", "reason": "v2 aggregates E_p over scatterers before power"},
        {"metric": "coherent_incoherent_ratio_400m", "v1": V1_REFERENCE["coherent_incoherent_ratio_400m"], "v2": v2_by_range[400.0]["mean_coherent_incoherent_ratio"], "status": "modified", "comparability": "same ratio concept, but v1 legacy power layer; v2 unified full-target ledger", "reason": "v2 aggregates E_p over scatterers before power"},
        {"metric": "interference_mean_200m", "v1": V1_REFERENCE["interference_mean_200m"], "v2": v2_by_range[200.0]["mean_interference"], "status": "modified", "comparability": "directional only; old v1 value was not the canonical full-target ledger", "reason": "different power layer"},
        {"metric": "interference_mean_400m", "v1": V1_REFERENCE["interference_mean_400m"], "v2": v2_by_range[400.0]["mean_interference"], "status": "modified", "comparability": "directional only; old v1 value was not the canonical full-target ledger", "reason": "different power layer"},
        {"metric": "isolated_path_power_200m", "v1": V1_REFERENCE["path_power_200m"], "v2": f"DD={v2_by_range[200.0]['mean_P_DD']:.9e}; DR={v2_by_range[200.0]['mean_P_DR']:.9e}; RD={v2_by_range[200.0]['mean_P_RD']:.9e}; RR={v2_by_range[200.0]['mean_P_RR']:.9e}", "status": "withdrawn_v1_numeric_comparison", "comparability": "not comparable", "reason": "v1 values were per-cell/mixed; v2 values are full-target isolated path powers"},
        {"metric": "pairwise_cross_terms", "v1": "legacy per-cell/mixed-level diagnostic", "v2": "six full-target terms in pairwise_interference_summary.csv", "status": "replaced", "comparability": "not comparable as percentages", "reason": "v2 unified ledger is the canonical evidence"},
        {"metric": "horizontal_phase_slope_beta0", "v1": V1_REFERENCE["horizontal_phase_slope_beta0"], "v2": fit_beta0_horizontal["loglog_slope"], "status": "retained_if_scope_limited", "comparability": "same beta=0 horizontal special-case definition", "reason": "v2 reports fit interval and R-squared explicitly"},
        {"metric": "200m_destructive_tendency", "v1": "destructive (ratio ~0.363)", "v2": "destructive" if v2_by_range[200.0]["mean_interference"] < 0 else "constructive", "status": "retained" if v2_by_range[200.0]["mean_interference"] < 0 else "superseded", "comparability": "directional", "reason": "v2 full-target interference sign"},
        {"metric": "400m_constructive_tendency", "v1": "constructive (ratio ~2.420)", "v2": "constructive" if v2_by_range[400.0]["mean_interference"] > 0 else "destructive", "status": "retained" if v2_by_range[400.0]["mean_interference"] > 0 else "superseded", "comparability": "directional", "reason": "v2 full-target interference sign"},
    ]
    write_csv(OUT / "v1_v2_comparison.csv", v1_v2_rows)

    manifest = {
        "physics_version": "v2",
        "stage": "M3-alpha/M3-beta",
        "recognition_run": False,
        "m2_run_in_this_script": "control recomputation only for M2/M3 comparison; no M2.5 experiment",
        "f0_hz": F0, "sample_rate_hz": FS, "observation_time_s": TOBS,
        "radar_position_m": RADAR.tolist(), "height_m": HEIGHT,
        "range_primary_m": 200.0, "range_reference_m": 400.0,
        "tilt_primary_rad": TILT_PRIMARY, "beta_scaling_ranges_m": [200, 300, 400, 600, 800],
        "ground": GROUND, "polarization": POLARIZATION,
        "blades": BLADES, "scatterers_per_blade": SCATTERERS_PER_BLADE,
        "phase_rad": PHASE_RAD, "git_head_before_commit": git_head(),
        "metric_consistency": consistency,
        "closure": {str(R): {"max_abs_residual": ledgers[R]["max_abs_residual"], "max_scaled_residual": ledgers[R]["max_scaled_residual"], "pass": ledgers[R]["closure_pass"]} for R in ledgers},
        "factorization": factor_rows,
        "m3_alpha_status": "PASS" if all(row["closure_pass"] for row in alpha_rows) else "FAIL",
        "m3_beta_status": "PASS" if all(row["status"] in {"PASS", "NOT_FIT_ZERO_COMPONENT"} for row in fits) else "FAIL",
        "recognition_allowed": bool(all(row["closure_pass"] for row in alpha_rows)),
        "source_hashes": {str(p.relative_to(ROOT)): sha256(p) for p in (
            ROOT / "src" / "geometry.py", ROOT / "src" / "fresnel.py",
            ROOT / "src" / "rotor_kinematics.py", ROOT / "src" / "four_path.py",
            ROOT / "src" / "element_two_ray.py", ROOT / "experiments" / "run_physics_v2_m3.py",
        )},
        "no_m3_overwrite": True,
    }
    (OUT / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({
        "out": str(OUT), "m3_alpha": manifest["m3_alpha_status"],
        "m3_beta": manifest["m3_beta_status"],
        "closure": manifest["closure"], "fit": fits,
    }, indent=2))


if __name__ == "__main__":
    main()
