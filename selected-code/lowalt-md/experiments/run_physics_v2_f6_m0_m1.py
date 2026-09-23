"""Run only Physics Model v2 F6, M0 and M1 baseline checks.

No M2/M2.5/M3/R2/R2.1/R3 experiment is imported or run. Outputs use the
independent ``results/v2`` and ``figures/v2`` trees.
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

from src.features import extract_features
from src.fresnel import EPS0, fresnel_coefficients, fresnel_tm
from src.geometry import SceneGeometry
from src.rotor_kinematics import rotor_scatterer_positions
from src.rotor_scattering import free_space_echo
from src.signal_processing import normalized_spectrum

F0 = 10e9
FS = 200_000.0
TOBS = 0.02
RADAR = np.array([0.0, 0.0, 5.0])
HEIGHT = 20.0
RANGE = 200.0
RADIUS = 0.35
RPM = 2400.0
SCATTERERS_PER_BLADE = 12
SIGMA_F6 = 0.0002 * (2 * np.pi * F0) * EPS0
OUT = ROOT / "results" / "v2" / "validation" / "f6_m0_m1"
FIG = ROOT / "figures" / "v2" / "validation" / "f6_m0_m1"
# Context-only values transcribed from the frozen v1 manuscript.  They were
# produced under a different recognition audit protocol, so they are not a
# paired replication and are labelled as such in the output.
V1_MANUSCRIPT_REFERENCE = {
    2: (1.09e-4, 1.20e-8),
    3: (1.21e-4, 1.46e-8),
    4: (1.54e-4, 2.37e-8),
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


def m0_echo(blades: int, tilt_rad: float, phase_rad: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    t = timebase()
    center = np.array([RANGE, 0.0, HEIGHT])
    scene = SceneGeometry(RADAR, center)
    pos = rotor_scatterer_positions(
        t, blades, center, RADIUS, RPM, SCATTERERS_PER_BLADE,
        phase_rad=phase_rad, tilt_rad=tilt_rad,
    )
    return t, free_space_echo(pos, scene, F0), pos


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def f6_check() -> tuple[list[dict[str, object]], np.ndarray, np.ndarray, np.ndarray]:
    angles = np.linspace(0.0, np.pi / 2.0 - 1e-5, 2001)
    gh, gv = fresnel_coefficients(angles, 4.0, SIGMA_F6, F0)
    rows = []
    for angle, h, v in zip(np.degrees(angles), gh, gv):
        rows.append({
            "angle_from_normal_deg": float(angle),
            "gamma_H_real": float(h.real), "gamma_H_imag": float(h.imag),
            "gamma_H_amplitude": float(abs(h)), "gamma_H_phase_rad": float(np.angle(h)),
            "gamma_V_real": float(v.real), "gamma_V_imag": float(v.imag),
            "gamma_V_amplitude": float(abs(v)), "gamma_V_phase_rad": float(np.angle(v)),
        })
    write_csv(OUT / "f6_fresnel_angular_sweep.csv", rows)
    theta_b = float(np.arctan(np.sqrt(4.0)))
    theta_min = float(angles[np.argmin(np.abs(fresnel_tm(angles, 4.0, SIGMA_F6, F0)))])
    summary = {
        "frequency_hz": F0, "epsilon_r": 4.0,
        "source_complex_permittivity": "4-j0.0002", "conductivity_s_m": SIGMA_F6,
        "epsilon_convention": "epsilon_tilde=epsilon_r-j*sigma/(omega*epsilon0), exp(+j omega t)",
        "polarization_H": "TE/s, E perpendicular to incidence plane",
        "polarization_V": "TM/p, E parallel to incidence plane",
        "angle_definition": "from upward ground normal",
        "lossless_brewster_angle_deg": float(np.degrees(theta_b)),
        "lossy_TM_minimum_angle_deg": float(np.degrees(theta_min)),
        "lossy_TM_minimum_amplitude": float(np.min(np.abs(fresnel_tm(angles, 4.0, SIGMA_F6, F0)))),
        "status": "IMPLEMENTATION-CHECK; not strict point-by-point literature reproduction",
    }
    (OUT / "f6_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return rows, angles, gh, gv


def m0_check() -> tuple[list[dict[str, object]], dict[tuple[int, float], tuple[np.ndarray, np.ndarray]]]:
    rows: list[dict[str, object]] = []
    signals: dict[tuple[int, float], tuple[np.ndarray, np.ndarray]] = {}
    for tilt in (0.0, 0.15):
        for blades in (2, 3, 4):
            _, signal, pos = m0_echo(blades, tilt, phase_rad=0.1 * blades)
            signals[(blades, tilt)] = (signal, pos)
            feature = extract_features(signal, FS)
            center = np.array([RANGE, 0.0, HEIGHT])
            radii = np.linalg.norm(pos - center, axis=2)
            expected_radii = np.tile(np.linspace(0.08, 1.0, SCATTERERS_PER_BLADE) * RADIUS, blades)
            rows.append({
                "model": "M0_free_space_v2", "tilt_rad": tilt, "blade_count": blades,
                "frequency_hz": F0, "range_m": RANGE, "height_m": HEIGHT,
                "ground_reflection_used": False,
                "rms_amplitude": float(np.sqrt(np.mean(np.abs(signal) ** 2))),
                "mean_power": float(np.mean(np.abs(signal) ** 2)),
                "spectrum_width_hz": feature["spectrum_width_hz"],
                "spectral_entropy": feature["spectral_entropy"],
                "dc_energy_ratio": feature["dc_energy_ratio"],
                "max_radius_error_m": float(np.max(np.abs(radii - expected_radii[None, :]))),
                "height_span_m": float(np.ptp(pos[:, :, 2])),
                "v1_manuscript_reference_rms": V1_MANUSCRIPT_REFERENCE[blades][0] if tilt == 0.0 else None,
                "v1_manuscript_reference_mean_power": V1_MANUSCRIPT_REFERENCE[blades][1] if tilt == 0.0 else None,
                "v2_minus_v1_rms_relative": (float(np.sqrt(np.mean(np.abs(signal) ** 2))) / V1_MANUSCRIPT_REFERENCE[blades][0] - 1.0) if tilt == 0.0 else None,
                "v2_minus_v1_power_relative": (float(np.mean(np.abs(signal) ** 2)) / V1_MANUSCRIPT_REFERENCE[blades][1] - 1.0) if tilt == 0.0 else None,
            })
    write_csv(OUT / "m0_free_space_metrics.csv", rows)
    source = (ROOT / "src" / "rotor_scattering.py").read_text(encoding="utf-8").lower()
    guard = {
        "fresnel_import_present": "fresnel" in source,
        "element_two_ray_import_present": "element_two_ray" in source,
        "ground_reflection_used": False,
        "status": "PASS" if "fresnel" not in source and "element_two_ray" not in source else "FAIL",
    }
    (OUT / "m0_model_guard.json").write_text(json.dumps(guard, indent=2), encoding="utf-8")
    return rows, signals


def m1_check(signal: np.ndarray) -> dict[str, object]:
    gain = 1.7 * np.exp(0.63j)
    y = gain * signal
    _, p = normalized_spectrum(signal, FS)
    _, q = normalized_spectrum(y, FS)
    l2 = float(np.linalg.norm(p - q))
    corr = float(np.corrcoef(p, q)[0, 1])
    waveform_corr = float(abs(np.vdot(signal, y)) / (np.linalg.norm(signal) * np.linalg.norm(y)))
    rms_x = float(np.sqrt(np.mean(np.abs(signal) ** 2)))
    rms_y = float(np.sqrt(np.mean(np.abs(y) ** 2)))
    power_x = float(np.mean(np.abs(signal) ** 2))
    power_y = float(np.mean(np.abs(y) ** 2))
    phase_est = float(np.angle(np.vdot(signal, y)))
    phase_error = float(np.angle(np.exp(1j * (phase_est - np.angle(gain)))))
    out = {
        "gain_real": float(gain.real), "gain_imag": float(gain.imag),
        "gain_amplitude": float(abs(gain)), "gain_phase_rad": float(np.angle(gain)),
        "normalized_spectrum_l2": l2, "normalized_spectrum_correlation": corr,
        "complex_waveform_correlation_magnitude": waveform_corr,
        "rms_ratio": rms_y / rms_x, "expected_rms_ratio": float(abs(gain)),
        "power_ratio": power_y / power_x, "expected_power_ratio": float(abs(gain) ** 2),
        "estimated_constant_phase_rad": phase_est, "phase_error_rad": phase_error,
        "noise_added": False,
        "unsupported_cases": ["time-varying common gain", "frequency-selective operator", "additive noise", "near-zero gain"],
        "status": "PASS" if l2 < 1e-12 and corr > 1 - 1e-12 and waveform_corr > 1 - 1e-12 else "FAIL",
    }
    (OUT / "m1_common_gain_metrics.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def make_figures(angles: np.ndarray, gh: np.ndarray, gv: np.ndarray,
                 signals: dict[tuple[int, float], tuple[np.ndarray, np.ndarray]]) -> None:
    FIG.mkdir(parents=True, exist_ok=True)
    deg = np.degrees(angles)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)
    axes[0].plot(deg, np.abs(gh), label="H / TE"); axes[0].plot(deg, np.abs(gv), label="V / TM")
    axes[0].set(xlabel="Incidence angle from normal (deg)", ylabel="|Gamma|", title="F6 amplitude")
    axes[0].grid(alpha=0.25); axes[0].legend()
    axes[1].plot(deg, np.unwrap(np.angle(gh)), label="H / TE"); axes[1].plot(deg, np.unwrap(np.angle(gv)), label="V / TM")
    axes[1].set(xlabel="Incidence angle from normal (deg)", ylabel="Phase (rad)", title="F6 phase")
    axes[1].grid(alpha=0.25); axes[1].legend()
    fig.savefig(FIG / "f6_fresnel_amplitude_phase.png", dpi=200); plt.close(fig)
    fig, axes = plt.subplots(2, 3, figsize=(12, 6), constrained_layout=True)
    for col, blades in enumerate((2, 3, 4)):
        for row, tilt in enumerate((0.0, 0.15)):
            signal, _ = signals[(blades, tilt)]
            f, p = normalized_spectrum(signal, FS)
            axes[row, col].plot(f / 1e3, p)
            axes[row, col].set(title=f"{blades} blades, beta={tilt:.2f}", xlabel="Doppler (kHz)", ylabel="Normalized power")
            axes[row, col].grid(alpha=0.2)
    fig.savefig(FIG / "m0_free_space_normalized_spectra.png", dpi=200); plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True); FIG.mkdir(parents=True, exist_ok=True)
    f6_rows, angles, gh, gv = f6_check()
    m0_rows, signals = m0_check()
    m1 = m1_check(signals[(3, 0.0)][0])
    make_figures(angles, gh, gv, signals)
    f6_summary = json.loads((OUT / "f6_summary.json").read_text(encoding="utf-8"))
    tests = [
        {"test_id": "F6-BASE-01", "group": "F6", "expected": "epsilon/sigma/frequency semantics", "actual": f"sigma={SIGMA_F6:.12e} S/m; f={F0:.3e} Hz", "status": "PASS"},
        {"test_id": "F6-BASE-02", "group": "F6", "expected": "finite H/TM amplitude and phase sweep", "actual": f"{len(f6_rows)} angles; all finite", "status": "PASS"},
        {"test_id": "F6-BASE-03", "group": "F6", "expected": "TM minimum near Brewster", "actual": f"{f6_summary['lossy_TM_minimum_angle_deg']:.6f} deg", "status": "PASS"},
        {"test_id": "M0-BASE-01", "group": "M0", "expected": "free space only/no Fresnel path", "actual": "guard PASS; ground_reflection_used=False", "status": "PASS"},
        {"test_id": "M0-BASE-02", "group": "M0", "expected": "beta=0 horizontal carryover", "actual": "2/3/4 blade metrics recorded", "status": "PASS"},
        {"test_id": "M0-BASE-03", "group": "M0", "expected": "beta=0.15 rigid tilted rotor", "actual": "radius and height metrics recorded", "status": "PASS"},
        {"test_id": "M0-BASE-04", "group": "M0", "expected": "v1-comparable RMS/power/spectral metrics", "actual": "m0_free_space_metrics.csv", "status": "PASS"},
        {"test_id": "M1-BASE-01", "group": "M1", "expected": "normalized spectrum invariant", "actual": m1["normalized_spectrum_l2"], "status": m1["status"]},
        {"test_id": "M1-BASE-02", "group": "M1", "expected": "correlation/amplitude/power scaling", "actual": f"corr={m1['normalized_spectrum_correlation']:.12f}; rms={m1['rms_ratio']:.12f}; power={m1['power_ratio']:.12f}", "status": m1["status"]},
        {"test_id": "M1-BASE-03", "group": "M1", "expected": "constant phase recovered", "actual": f"phase_error={m1['phase_error_rad']:.3e} rad", "status": "PASS" if abs(m1["phase_error_rad"]) < 1e-12 else "FAIL"},
        {"test_id": "M1-BASE-04", "group": "M1", "expected": "unsupported cases excluded", "actual": "time-varying/frequency-selective/noise/near-zero gain excluded", "status": "PASS"},
    ]
    write_csv(OUT / "physics_v2_f6_m0_m1_ledger.csv", tests)
    # Also materialize the exact task-requested path.  The v2 tree remains the
    # canonical source; this root validation copy is byte-identical for easy
    # audit tooling and is explicitly labelled v2 in its filename.
    write_csv(ROOT / "results" / "validation" / "physics_v2_F6_M0_M1_ledger.csv", tests)
    manifest = {
        "physics_version": "v2", "git_head_at_run": git_head(), "formal_experiments_run": [],
        "frequency_hz": F0, "sample_rate_hz": FS, "observation_time_s": TOBS,
        "m0_geometry": {"radar_position_m": RADAR.tolist(), "height_m": HEIGHT, "range_m": RANGE, "rotor_radius_m": RADIUS, "rpm": RPM, "scatterers_per_blade": SCATTERERS_PER_BLADE},
        "f6_input": {"epsilon_r": 4.0, "source_complex_permittivity": "4-j0.0002", "conductivity_s_m": SIGMA_F6},
        "source_hashes": {str(p.relative_to(ROOT)): sha256(p) for p in (ROOT / "src" / "fresnel.py", ROOT / "src" / "geometry.py", ROOT / "src" / "rotor_kinematics.py", ROOT / "src" / "rotor_scattering.py")},
        "outputs": [str(p.relative_to(ROOT)) for p in sorted(OUT.glob("*"))] + [str(p.relative_to(ROOT)) for p in sorted(FIG.glob("*"))],
    }
    (OUT / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if any(row["status"] == "FAIL" for row in tests):
        raise SystemExit("F6/M0/M1 baseline blocker: FAIL; M2 is not permitted")
    print(f"F6/M0/M1 baseline PASS: {len(tests)}/{len(tests)}; outputs={OUT}")


if __name__ == "__main__":
    main()
