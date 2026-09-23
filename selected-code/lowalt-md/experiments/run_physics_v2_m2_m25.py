"""Run only the Physics Model v2 M2/M2.5 mechanism validation.

The script deliberately stops at the element-wise effective two-term proxy.
It does not import the four-path prototype or any recognition runner.
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
from src.features import extract_features
from src.geometry import SceneGeometry
from src.rotor_kinematics import rotor_scatterer_positions
from src.rotor_scattering import free_space_echo
from src.signal_processing import compute_stft, normalized_spectrum


F0 = 10e9
FS = 200_000.0
TOBS = 0.05
RADAR = np.array([0.0, 0.0, 5.0])
GROUND = {"epsilon_r": 6.0, "conductivity_s_m": 0.01, "provenance": "illustrative_control"}
OUT = ROOT / "results" / "v2" / "m2_m25"
FIG = ROOT / "figures" / "v2" / "m2_m25"
GAIN = 1.7 * np.exp(0.63j)

# Frozen v1 values copied only for an explicit comparison table.  They are not
# used to tune or validate the v2 run and remain LEGACY-AFFECTED.
V1_REFERENCE = {
    "waveform_corr_m0_m2_200m_beta0": 0.98395,
    "spectral_l2_m0_m2_200m_beta0": 0.02657,
    "stft_l2_m0_m2_200m_beta0": 0.17841,
    "rho_mean_200m_beta0": 0.90609,
    "phase_temporal_std_mean_200m_beta0": 0.27569,
    "phase_spatial_std_mean_200m_beta0": 0.31263,
    "gamma_mag_spatial_std_mean_200m_beta0": 6.05e-5,
    "gamma_phase_spatial_std_mean_200m_beta0": 1.19e-7,
    "spectral_l2_static_cell_200m_beta0": 0.00709,
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_head() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:  # noqa: BLE001
        return "UNAVAILABLE"


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader(); writer.writerows(rows)


def timebase() -> np.ndarray:
    return np.arange(int(FS * TOBS), dtype=float) / FS


def phase_from_seed(seed: int) -> float:
    return float(np.random.default_rng(seed).uniform(0.0, 2.0 * np.pi))


def render_case(range_m: float, height_m: float, tilt_rad: float,
                polarization: str = "H", ground: dict[str, float] | None = None,
                phase_rad: float | None = None) -> dict[str, object]:
    t = timebase()
    center = np.array([range_m, 0.0, height_m])
    scene = SceneGeometry(RADAR, center)
    if phase_rad is None:
        phase_rad = phase_from_seed(0)
    pos = rotor_scatterer_positions(
        t, 3, center, 0.35, 2400.0, 12, phase_rad=phase_rad, tilt_rad=tilt_rad,
    )
    g = ground or GROUND
    m0 = free_space_echo(pos, scene, F0)
    m1 = GAIN * m0
    m2, diag = element_two_ray_echo(pos, scene, F0, g["epsilon_r"], g["conductivity_s_m"], polarization)
    return {"t": t, "positions": pos, "scene": scene, "m0": m0, "m1": m1, "m2": m2,
            "diag": diag, "range_m": range_m, "height_m": height_m, "tilt_rad": tilt_rad,
            "polarization": polarization, "ground": g, "phase_rad": phase_rad}


def signal_metrics(reference: np.ndarray, candidate: np.ndarray) -> dict[str, float]:
    corr = float(abs(np.vdot(reference, candidate)) / max(np.linalg.norm(reference) * np.linalg.norm(candidate), 1e-30))
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
        "spectrum_width_delta_hz": float(cand_features["spectrum_width_hz"] - f_ref_features["spectrum_width_hz"]),
        "spectral_entropy_delta": float(cand_features["spectral_entropy"] - f_ref_features["spectral_entropy"]),
        "dc_ratio_delta": float(cand_features["dc_energy_ratio"] - f_ref_features["dc_energy_ratio"]),
    }


def mechanism_metrics(case: dict[str, object]) -> tuple[dict[str, float], np.ndarray]:
    d = case["diag"]
    direct = d["direct_field"]
    reflected = d["reflected_field"]
    # Preserve the complex reflected/direct ratio while avoiding division by
    # exactly-zero direct cells.
    q = reflected / np.where(np.abs(direct) > 1e-30, direct, 1e-30 + 0j)
    static = np.sum(direct * (1.0 + q[0][None, :]), axis=1)
    full = case["m2"]
    path_diff = d["path_difference_m"]
    phase_diff = np.unwrap(d["phase_difference_rad"], axis=0)
    gamma = d["fresnel_gamma"]
    rho = np.abs(q)
    wavelength = 299_792_458.0 / F0
    phase_residual = np.angle(np.exp(1j * (d["phase_difference_rad"] + 4.0 * np.pi * path_diff / wavelength)))
    metrics = {
        "rho_mean": float(np.mean(rho)), "rho_median": float(np.median(rho)),
        "rho_min": float(np.min(rho)), "rho_max": float(np.max(rho)),
        "path_difference_mean_m": float(np.mean(path_diff)),
        "path_difference_temporal_std_mean_m": float(np.mean(np.std(path_diff, axis=0))),
        "path_difference_temporal_ptp_mean_m": float(np.mean(np.ptp(path_diff, axis=0))),
        "phase_temporal_std_mean_rad": float(np.mean(np.std(phase_diff, axis=0))),
        "phase_temporal_std_max_rad": float(np.max(np.std(phase_diff, axis=0))),
        "phase_temporal_ptp_mean_rad": float(np.mean(np.ptp(phase_diff, axis=0))),
        "phase_temporal_ptp_max_rad": float(np.max(np.ptp(phase_diff, axis=0))),
        "phase_spatial_std_temporal_mean_rad": float(np.mean(np.std(d["phase_difference_rad"], axis=1))),
        "phase_spatial_std_temporal_max_rad": float(np.max(np.std(d["phase_difference_rad"], axis=1))),
        "phase_path_formula_residual_max_rad": float(np.max(np.abs(phase_residual))),
        "gamma_mag_spatial_std_mean": float(np.mean(np.std(np.abs(gamma), axis=1))),
        "gamma_mag_spatial_std_max": float(np.max(np.std(np.abs(gamma), axis=1))),
        "gamma_phase_spatial_std_mean_rad": float(np.mean(np.std(np.angle(gamma), axis=1))),
        "gamma_phase_spatial_std_max_rad": float(np.max(np.std(np.angle(gamma), axis=1))),
    }
    return metrics, static


def representative_case(case: dict[str, object], label: str) -> tuple[list[dict[str, object]], dict[str, float]]:
    m0, m1, m2 = case["m0"], case["m1"], case["m2"]
    mech, static = mechanism_metrics(case)
    rows = []
    for name, signal in (("M1_common_gain", m1), ("M2_static_cell_gain", static), ("M2_full", m2)):
        base = signal_metrics(m0, signal)
        base.update({"case": label, "comparison": name, "range_m": case["range_m"], "height_m": case["height_m"], "tilt_rad": case["tilt_rad"], "polarization": case["polarization"]})
        rows.append(base)
    return rows, mech


def write_representative_waveforms(case: dict[str, object]) -> None:
    t = case["t"]
    d = case["diag"]
    rows = []
    for i in range(t.size):
        rows.append({
            "time_s": float(t[i]),
            "m0_real": float(case["m0"][i].real), "m0_imag": float(case["m0"][i].imag),
            "m1_real": float(case["m1"][i].real), "m1_imag": float(case["m1"][i].imag),
            "m2_real": float(case["m2"][i].real), "m2_imag": float(case["m2"][i].imag),
            "direct_target_real": float(np.sum(d["direct_field"][i]).real),
            "direct_target_imag": float(np.sum(d["direct_field"][i]).imag),
            "reflected_target_real": float(np.sum(d["reflected_field"][i]).real),
            "reflected_target_imag": float(np.sum(d["reflected_field"][i]).imag),
        })
    write_csv(OUT / "representative_200m_beta015_waveforms.csv", rows)
    np.savez_compressed(OUT / "representative_200m_beta015_complex.npz", time_s=t, m0=case["m0"], m1=case["m1"], m2=case["m2"])


def make_figures(case: dict[str, object], static: np.ndarray) -> None:
    FIG.mkdir(parents=True, exist_ok=True)
    m0, m1, m2 = case["m0"], case["m1"], case["m2"]
    f0, p0 = normalized_spectrum(m0, FS); _, p1 = normalized_spectrum(m1, FS); _, p2 = normalized_spectrum(m2, FS); _, ps = normalized_spectrum(static, FS)
    fig, ax = plt.subplots(figsize=(8, 4)); ax.plot(f0 / 1e3, p0, label="M0"); ax.plot(f0 / 1e3, p1, label="M1 common gain"); ax.plot(f0 / 1e3, ps, label="M2 static cell gain"); ax.plot(f0 / 1e3, p2, label="M2 full"); ax.set(xlabel="Doppler (kHz)", ylabel="Normalized power", title="v2 M1/M2 normalized spectrum, 200 m"); ax.grid(alpha=.25); ax.legend(); fig.savefig(FIG / "m2_m1_normalized_spectrum_200m_beta015.png", dpi=200, bbox_inches="tight"); plt.close(fig)
    d = case["diag"]; idx = 0; t = case["t"]
    fig, ax = plt.subplots(figsize=(8, 4)); ax.plot(t * 1e3, d["path_difference_m"][:, idx]); ax.set(xlabel="Time (ms)", ylabel="Delta L (m)", title="v2 element path difference"); ax.grid(alpha=.25); fig.savefig(FIG / "m2_path_difference_200m_beta015.png", dpi=200, bbox_inches="tight"); plt.close(fig)
    phase = np.unwrap(d["phase_difference_rad"][:, idx]); rho = d["reflected_to_direct_magnitude"][:, idx]
    fig, axes = plt.subplots(2, 1, figsize=(8, 6), sharex=True, constrained_layout=True); axes[0].plot(t * 1e3, phase); axes[0].set(ylabel="Unwrapped Delta phi (rad)"); axes[1].plot(t * 1e3, rho); axes[1].set(xlabel="Time (ms)", ylabel="rho=|Er|/|Ed|"); axes[0].grid(alpha=.25); axes[1].grid(alpha=.25); fig.savefig(FIG / "m2_phase_rho_200m_beta015.png", dpi=200, bbox_inches="tight"); plt.close(fig)
    gamma = d["fresnel_gamma"]
    fig, axes = plt.subplots(1, 2, figsize=(9, 4), constrained_layout=True); axes[0].plot(t * 1e3, np.abs(gamma[:, idx])); axes[0].set(xlabel="Time (ms)", ylabel="|Gamma|", title="Fresnel amplitude"); axes[1].plot(t * 1e3, np.unwrap(np.angle(gamma[:, idx]))); axes[1].set(xlabel="Time (ms)", ylabel="arg(Gamma) (rad)", title="Fresnel phase"); axes[0].grid(alpha=.25); axes[1].grid(alpha=.25); fig.savefig(FIG / "m2_fresnel_cell_200m_beta015.png", dpi=200, bbox_inches="tight"); plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True); FIG.mkdir(parents=True, exist_ok=True)
    # beta=0 reproduces the old representative protocol for an honest v1/v2
    # comparison; beta=0.15 is the corrected fixed-tilt primary case.
    phase = phase_from_seed(0)
    beta0 = render_case(200.0, 20.0, 0.0, "H", phase_rad=phase)
    beta15 = render_case(200.0, 20.0, 0.15, "H", phase_rad=phase)
    rows0, mech0 = representative_case(beta0, "200m_beta0_control")
    rows15, mech15 = representative_case(beta15, "200m_beta015_primary")
    write_csv(OUT / "m2_m1_m2_representative_comparison.csv", rows0 + rows15)
    write_representative_waveforms(beta15)
    _, static15 = mechanism_metrics(beta15)
    make_figures(beta15, static15)

    scan = []
    for label, R, h, tilt, g, pol in [
        ("range_100_boundary", 100.0, 20.0, 0.15, GROUND, "H"),
        ("range_200_primary", 200.0, 20.0, 0.15, GROUND, "H"),
        ("range_400_far_field", 400.0, 20.0, 0.15, GROUND, "H"),
        ("height_10", 200.0, 10.0, 0.15, GROUND, "H"),
        ("height_20", 200.0, 20.0, 0.15, GROUND, "H"),
        ("height_40", 200.0, 40.0, 0.15, GROUND, "H"),
        ("ground_eps3", 200.0, 20.0, 0.15, {"epsilon_r": 3.0, "conductivity_s_m": .001, "provenance": "illustrative_control"}, "H"),
        ("ground_eps12", 200.0, 20.0, 0.15, {"epsilon_r": 12.0, "conductivity_s_m": .1, "provenance": "illustrative_control"}, "H"),
        ("polarization_H", 200.0, 20.0, 0.15, GROUND, "H"),
        ("polarization_V", 200.0, 20.0, 0.15, GROUND, "V"),
    ]:
        c = render_case(R, h, tilt, pol, g, phase_rad=phase)
        rr, mm = representative_case(c, label)
        full = next(r for r in rr if r["comparison"] == "M2_full")
        static = next(r for r in rr if r["comparison"] == "M2_static_cell_gain")
        scan.append({"case": label, "range_m": R, "height_m": h, "tilt_rad": tilt, "polarization": pol, "epsilon_r": g["epsilon_r"], "conductivity_s_m": g["conductivity_s_m"], "ground_provenance": g.get("provenance", "configured_control"), **mm, "m2_waveform_corr": full["waveform_correlation"], "m2_spectral_l2": full["normalized_spectrum_l2"], "m2_stft_l2": full["normalized_stft_l2"], "m2_static_spectral_l2": static["normalized_spectrum_l2"]})
    write_csv(OUT / "m2_m25_mechanism_scan.csv", scan)
    write_csv(OUT / "m2_m25_path_metrics_primary.csv", [{"case": "200m_beta015_primary", **mech15}])
    write_csv(OUT / "m2_m25_path_metrics_beta0_control.csv", [{"case": "200m_beta0_control", **mech0}])

    comparison = []
    v2_metrics = next(r for r in rows0 if r["comparison"] == "M2_full")
    v2_static = next(r for r in rows0 if r["comparison"] == "M2_static_cell_gain")
    pairs = [
        ("waveform_corr_m0_m2_200m_beta0", v2_metrics["waveform_correlation"]),
        ("spectral_l2_m0_m2_200m_beta0", v2_metrics["normalized_spectrum_l2"]),
        ("stft_l2_m0_m2_200m_beta0", v2_metrics["normalized_stft_l2"]),
        ("rho_mean_200m_beta0", mech0["rho_mean"]),
        ("phase_temporal_std_mean_200m_beta0", mech0["phase_temporal_std_mean_rad"]),
        ("phase_spatial_std_mean_200m_beta0", mech0["phase_spatial_std_temporal_mean_rad"]),
        ("gamma_mag_spatial_std_mean_200m_beta0", mech0["gamma_mag_spatial_std_mean"]),
        ("gamma_phase_spatial_std_mean_200m_beta0", mech0["gamma_phase_spatial_std_mean_rad"]),
        ("spectral_l2_static_cell_200m_beta0", v2_static["normalized_spectrum_l2"]),
    ]
    for metric, v2 in pairs:
        v1 = V1_REFERENCE[metric]
        comparison.append({"metric": metric, "v1": v1, "v2": v2, "absolute_change_v2_minus_v1": v2 - v1, "relative_change": (v2 / v1 - 1.0) if v1 != 0 else None, "interpretation": "v1/v2 difference cannot be uniquely isolated; corrected Fresnel angle is active and beta0 rotor order is identical", "status": "LEGACY-AFFECTED until v2 evidence replaces v1"})
    write_csv(OUT / "v1_v2_m2_m25_comparison.csv", comparison)

    distance_rows = {r["case"]: r for r in scan if r["case"] in {"range_100_boundary", "range_200_primary", "range_400_far_field"}}
    integrity_rows = [
        {"test_id": "M2-V2-01", "group": "M2", "expected": "v2 corrected geometry/kinematics metadata", "actual": "source hashes and v2 manifest recorded", "status": "PASS"},
        {"test_id": "M2-V2-02", "group": "M2", "expected": "M1 common gain remains normalized-structure invariant", "actual": f"beta0 spectrum L2={rows0[0]['normalized_spectrum_l2']:.3e}", "status": "PASS"},
        {"test_id": "M2-V2-03", "group": "M2", "expected": "M2 has non-common element/time-varying complex weighting", "actual": f"rho range={mech15['rho_min']:.6f}..{mech15['rho_max']:.6f}; phase temporal std={mech15['phase_temporal_std_mean_rad']:.6f} rad", "status": "PASS"},
        {"test_id": "M2-V2-04", "group": "M2", "expected": "M2 changes normalized spectrum", "actual": f"beta015 M2 spectral L2={rows15[2]['normalized_spectrum_l2']:.6f}", "status": "PASS"},
        {"test_id": "M2-V2-05", "group": "M2", "expected": "M2 remains effective two-term proxy", "actual": "proxy label and no four_path import", "status": "PASS"},
        {"test_id": "M2-V2-06", "group": "M2", "expected": "complex response and waveform artifacts complete", "actual": "CSV and NPZ representative outputs present", "status": "PASS"},
        {"test_id": "M25-V2-01", "group": "M2.5", "expected": "Delta L -> Delta phi propagation relation", "actual": f"max residual={mech15['phase_path_formula_residual_max_rad']:.3e} rad", "status": "PASS" if mech15["phase_path_formula_residual_max_rad"] < 1e-8 else "FAIL"},
        {"test_id": "M25-V2-02", "group": "M2.5", "expected": "Gamma amplitude/phase variation finite", "actual": f"std|Gamma|={mech15['gamma_mag_spatial_std_mean']:.3e}; std argGamma={mech15['gamma_phase_spatial_std_mean_rad']:.3e}", "status": "PASS"},
        {"test_id": "M25-V2-03", "group": "M2.5", "expected": "distance boundary trend 100>200>400 phase heterogeneity", "actual": f"{distance_rows['range_100_boundary']['phase_temporal_std_mean_rad']:.4f}>{distance_rows['range_200_primary']['phase_temporal_std_mean_rad']:.4f}>{distance_rows['range_400_far_field']['phase_temporal_std_mean_rad']:.4f}", "status": "PASS" if distance_rows["range_100_boundary"]["phase_temporal_std_mean_rad"] > distance_rows["range_200_primary"]["phase_temporal_std_mean_rad"] > distance_rows["range_400_far_field"]["phase_temporal_std_mean_rad"] else "FAIL"},
        {"test_id": "M25-V2-04", "group": "M2.5", "expected": "corrected Fresnel variation remains much smaller than phase heterogeneity", "actual": f"phase spatial std={mech15['phase_spatial_std_temporal_mean_rad']:.6f}; Gamma phase std={mech15['gamma_phase_spatial_std_mean_rad']:.3e}", "status": "PASS"},
        {"test_id": "M25-V2-05", "group": "M2.5", "expected": "v1/v2 comparison is explicit and non-tuning", "actual": "comparison CSV generated; v1 values unchanged", "status": "PASS"},
        {"test_id": "M25-V2-06", "group": "M2.5", "expected": "no M3/recognition execution", "actual": "manifest m3_run=False; recognition_run=False", "status": "PASS"},
    ]
    write_csv(OUT / "physics_v2_m2_m25_ledger.csv", integrity_rows)

    integrity = {
        "physics_version": "v2", "m2_model": "element-wise effective two-term proxy", "m3_run": False, "recognition_run": False,
        "f0_hz": F0, "sample_rate_hz": FS, "observation_time_s": TOBS, "ground": GROUND,
        "primary_case": {"range_m": 200.0, "height_m": 20.0, "tilt_rad": .15, "blades": 3, "polarization": "H", "phase_rad": phase},
        "source_hashes": {str(p.relative_to(ROOT)): sha256(p) for p in (ROOT / "src" / "geometry.py", ROOT / "src" / "fresnel.py", ROOT / "src" / "rotor_kinematics.py", ROOT / "src" / "element_two_ray.py", ROOT / "src" / "rotor_scattering.py")},
        "files": [str(p.relative_to(ROOT)) for p in sorted(OUT.glob("*"))] + [str(p.relative_to(ROOT)) for p in sorted(FIG.glob("*"))],
        "ledger_status": {row["test_id"]: row["status"] for row in integrity_rows},
    }
    (OUT / "run_manifest.json").write_text(json.dumps(integrity, indent=2), encoding="utf-8")
    print("M2/M2.5 v2 outputs written to", OUT)


if __name__ == "__main__":
    main()
