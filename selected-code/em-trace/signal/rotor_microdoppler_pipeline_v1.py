"""Build the Stage 7A angle-to-time-to-STFT verification dataset and figure.

This pipeline deliberately distinguishes the available 15-degree CST snapshot
grid from the recommended 3-degree production grid.  Temporal interpolation is
used only to exercise the processing chain; it cannot recover angular harmonics
that were not sampled by CST.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import stft
from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = REPO_ROOT / "results/raw/rotor_microdoppler_pipeline_v1"
FIGURE_ROOT = REPO_ROOT / "figures"
INPUT_CSV = RAW_ROOT / "complex_scattering_snapshots.csv"

RPM = 600.0
ROTATION_HZ = RPM / 60.0
BLADE_COUNT = 2
BLADE_PASS_HZ = BLADE_COUNT * ROTATION_HZ
SAMPLE_RATE_HZ = 2048.0
DURATION_S = 2.0
WINDOW_SAMPLES = 512
OVERLAP_SAMPLES = 384
NFFT = 2048
R_TIP_M = 0.112
C0_MPS = 299_792_458.0
CARRIER_HZ = 10.0e9
WAVELENGTH_M = C0_MPS / CARRIER_HZ
TIP_SPEED_MPS = 2.0 * np.pi * ROTATION_HZ * R_TIP_M
TIP_DOPPLER_BOUND_HZ = 2.0 * TIP_SPEED_MPS / WAVELENGTH_M
RECOMMENDED_ANGLE_STEP_DEG = 3.0


def load_snapshots() -> dict[str, np.ndarray]:
    data = np.genfromtxt(INPUT_CSV, delimiter=",", names=True, dtype=None, encoding="utf-8")
    return {
        "angle_deg": np.asarray(data["rotor_angle_deg"], dtype=float),
        "scattering_re_m": np.asarray(data["scattering_re_m"], dtype=float),
        "scattering_im_m": np.asarray(data["scattering_im_m"], dtype=float),
        "rcs_dbsm": np.asarray(data["rcs_dbsm"], dtype=float),
        "phase_deg": np.asarray(data["ephi_phase_deg"], dtype=float),
    }


def periodic_linear_interpolate(
    angle_query_deg: np.ndarray,
    angle_deg: np.ndarray,
    scattering: np.ndarray,
) -> np.ndarray:
    """Interpolate a two-blade 180-degree periodic complex response."""
    if not np.allclose(angle_deg, np.arange(0.0, 181.0, 15.0)):
        raise ValueError("Expected the frozen Stage 6B 0:15:180 degree grid")
    endpoint_mean = 0.5 * (scattering[0] + scattering[-1])
    values = scattering.copy()
    values[0] = endpoint_mean
    values[-1] = endpoint_mean
    wrapped = np.mod(angle_query_deg, 180.0)
    real = np.interp(wrapped, angle_deg, values.real)
    imag = np.interp(wrapped, angle_deg, values.imag)
    return real + 1j * imag


def save_time_series(time_s: np.ndarray, angle_deg: np.ndarray, signal: np.ndarray) -> None:
    path = RAW_ROOT / "complex_time_series.csv"
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            ["time_s", "rotor_angle_deg_mod_180", "scattering_re_m", "scattering_im_m", "magnitude_m", "phase_rad"]
        )
        for t, theta, sample in zip(time_s, angle_deg, signal, strict=True):
            writer.writerow([t, theta, sample.real, sample.imag, abs(sample), np.angle(sample)])


def save_harmonics(freq_hz: np.ndarray, mean_power: np.ndarray) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    peak_power = float(np.max(mean_power))
    for order in range(-10, 11):
        target = order * BLADE_PASS_HZ
        index = int(np.argmin(np.abs(freq_hz - target)))
        rows.append(
            {
                "harmonic_order": order,
                "target_frequency_hz": target,
                "nearest_bin_hz": float(freq_hz[index]),
                "relative_power_db": float(10.0 * np.log10(max(mean_power[index], 1e-30) / peak_power)),
            }
        )
    with (RAW_ROOT / "harmonic_summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return rows


def make_figure(
    snapshots: dict[str, np.ndarray],
    time_s: np.ndarray,
    signal: np.ndarray,
    freq_hz: np.ndarray,
    stft_time_s: np.ndarray,
    power_db: np.ndarray,
) -> dict[str, object]:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.titlesize": 11,
            "axes.labelsize": 10,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "savefig.facecolor": "white",
            "svg.fonttype": "none",
        }
    )
    fig = plt.figure(figsize=(8.0, 8.2), constrained_layout=True)
    grid = fig.add_gridspec(3, 1, height_ratios=[1.0, 1.0, 1.5])

    ax0 = fig.add_subplot(grid[0])
    scattering = snapshots["scattering_re_m"] + 1j * snapshots["scattering_im_m"]
    ax0.plot(snapshots["angle_deg"], np.abs(scattering), "o-", color="#0072B2", lw=1.6, ms=4)
    ax0.set(xlabel="Rotor angle (deg)", ylabel=r"$|a(\theta)|$ (m)", title="(a) Complex CST snapshot magnitude")
    ax0.grid(True, alpha=0.25)

    ax1 = fig.add_subplot(grid[1])
    shown = time_s <= 0.30
    ax1.plot(time_s[shown], signal.real[shown], color="#0072B2", lw=1.2, label="Real")
    ax1.plot(time_s[shown], signal.imag[shown], color="#D55E00", lw=1.2, ls="--", label="Imaginary")
    ax1.set(xlabel="Time (s)", ylabel="Scattering amplitude (m)", title="(b) Mapped complex baseband signal at 600 rpm")
    ax1.grid(True, alpha=0.25)
    ax1.legend(frameon=False, ncol=2, loc="upper right")

    ax2 = fig.add_subplot(grid[2])
    view = np.abs(freq_hz) <= 250.0
    mesh = ax2.pcolormesh(stft_time_s, freq_hz[view], power_db[view], shading="auto", cmap="viridis", vmin=-45, vmax=0)
    raw_snapshot_nyquist_hz = 0.5 * (360.0 / 15.0) * ROTATION_HZ
    ax2.axhspan(raw_snapshot_nyquist_hz, 250.0, color="white", alpha=0.10)
    ax2.axhspan(-250.0, -raw_snapshot_nyquist_hz, color="white", alpha=0.10)
    ax2.axhline(BLADE_PASS_HZ, color="white", lw=0.8, ls="--", alpha=0.9)
    ax2.axhline(-BLADE_PASS_HZ, color="white", lw=0.8, ls="--", alpha=0.9)
    ax2.axhline(raw_snapshot_nyquist_hz, color="#D55E00", lw=0.9, ls=":")
    ax2.axhline(-raw_snapshot_nyquist_hz, color="#D55E00", lw=0.9, ls=":")
    ax2.set(xlabel="Time (s)", ylabel="Doppler frequency (Hz)", title="(c) STFT of mean-removed signal (pipeline verification only)")
    colorbar = fig.colorbar(mesh, ax=ax2, pad=0.02)
    colorbar.set_label("Relative power (dB)")

    png = FIGURE_ROOT / "rotor_microdoppler_pipeline_v1.png"
    svg = FIGURE_ROOT / "rotor_microdoppler_pipeline_v1.svg"
    FIGURE_ROOT.mkdir(parents=True, exist_ok=True)
    fig.savefig(png, dpi=300, bbox_inches="tight")
    fig.savefig(svg, bbox_inches="tight")
    plt.close(fig)
    with Image.open(png) as raster:
        width_px, height_px = raster.size
        dpi = raster.info.get("dpi", (None, None))
        mode = raster.mode
    manifest = {
        "figure_id": "rotor_microdoppler_pipeline_v1",
        "png": str(png.relative_to(REPO_ROOT)),
        "svg": str(svg.relative_to(REPO_ROOT)),
        "png_width_px": width_px,
        "png_height_px": height_px,
        "png_dpi": list(dpi),
        "png_mode": mode,
        "png_sha256": hashlib.sha256(png.read_bytes()).hexdigest(),
        "svg_sha256": hashlib.sha256(svg.read_bytes()).hexdigest(),
        "data_source": str(INPUT_CSV.relative_to(REPO_ROOT)),
        "visual_encoding": "raw CST snapshot magnitude, mapped complex I/Q, and two-sided STFT relative power",
        "integrity_note": "No angular smoothing is used; real and imaginary parts are piecewise-linearly interpolated only for pipeline verification.",
    }
    (RAW_ROOT / "figure_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def main() -> int:
    RAW_ROOT.mkdir(parents=True, exist_ok=True)
    snapshots = load_snapshots()
    scattering = snapshots["scattering_re_m"] + 1j * snapshots["scattering_im_m"]
    sample_count = int(round(DURATION_S * SAMPLE_RATE_HZ))
    time_s = np.arange(sample_count, dtype=float) / SAMPLE_RATE_HZ
    angle_deg = np.mod(6.0 * RPM * time_s, 180.0)
    signal = periodic_linear_interpolate(angle_deg, snapshots["angle_deg"], scattering)
    signal_mean_removed = signal - np.mean(signal)

    freq_hz, stft_time_s, spectrum = stft(
        signal_mean_removed,
        fs=SAMPLE_RATE_HZ,
        window="hann",
        nperseg=WINDOW_SAMPLES,
        noverlap=OVERLAP_SAMPLES,
        nfft=NFFT,
        detrend=False,
        return_onesided=False,
        boundary=None,
        padded=False,
    )
    freq_hz = np.fft.fftshift(freq_hz)
    spectrum = np.fft.fftshift(spectrum, axes=0)
    power = np.abs(spectrum) ** 2
    power_db = 10.0 * np.log10(np.maximum(power / np.max(power), 1e-12))
    mean_power = np.mean(power, axis=1)

    save_time_series(time_s, angle_deg, signal)
    harmonics = save_harmonics(freq_hz, mean_power)
    np.savez_compressed(
        RAW_ROOT / "stft_data.npz",
        frequency_hz=freq_hz,
        time_s=stft_time_s,
        complex_spectrum=spectrum,
        relative_power_db=power_db,
    )
    figure_manifest = make_figure(snapshots, time_s, signal, freq_hz, stft_time_s, power_db)

    angular_nyquist_limit_deg = np.degrees(WAVELENGTH_M / (4.0 * R_TIP_M))
    available_unique_per_revolution = 360.0 / 15.0
    recommended_unique_per_revolution = 360.0 / RECOMMENDED_ANGLE_STEP_DEG
    config = {
        "pipeline_id": "rotor_microdoppler_pipeline_v1",
        "status": "processing-chain verification; not alias-free final micro-Doppler",
        "source_complex_snapshots": str(INPUT_CSV.relative_to(REPO_ROOT)),
        "source_angle_range_deg": [0, 180],
        "source_angle_step_deg": 15,
        "two_blade_period_deg": 180,
        "endpoint_policy": "average complex samples at 0 and 180 degrees before periodic linear interpolation",
        "interpolation": "piecewise linear on real and imaginary components",
        "rpm": RPM,
        "rotation_frequency_hz": ROTATION_HZ,
        "blade_count": BLADE_COUNT,
        "blade_pass_frequency_hz": BLADE_PASS_HZ,
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "duration_s": DURATION_S,
        "time_mapping": "theta_deg(t) = mod(6 * rpm * t, 180)",
        "stft": {
            "window": "Hann",
            "window_samples": WINDOW_SAMPLES,
            "window_duration_s": WINDOW_SAMPLES / SAMPLE_RATE_HZ,
            "overlap_samples": OVERLAP_SAMPLES,
            "overlap_fraction": OVERLAP_SAMPLES / WINDOW_SAMPLES,
            "nfft": NFFT,
            "bin_spacing_hz": SAMPLE_RATE_HZ / NFFT,
            "nominal_window_resolution_hz": SAMPLE_RATE_HZ / WINDOW_SAMPLES,
            "mean_removed": True,
            "two_sided": True,
        },
        "physics_bounds": {
            "carrier_frequency_hz": CARRIER_HZ,
            "wavelength_m": WAVELENGTH_M,
            "tip_radius_m": R_TIP_M,
            "tip_speed_mps": TIP_SPEED_MPS,
            "monostatic_tip_doppler_bound_hz": TIP_DOPPLER_BOUND_HZ,
            "angular_nyquist_step_limit_deg": angular_nyquist_limit_deg,
        },
        "sampling_assessment": {
            "available_unique_samples_per_revolution": available_unique_per_revolution,
            "available_grid_alias_free": False,
            "recommended_angle_step_deg": RECOMMENDED_ANGLE_STEP_DEG,
            "recommended_unique_samples_per_revolution": recommended_unique_per_revolution,
            "recommended_unique_cst_snapshots_over_0_to_180": 60,
            "recommended_inclusive_cst_angles": 61,
        },
        "harmonics": harmonics,
        "figure_manifest": figure_manifest,
        "limitations": [
            "Temporal resampling does not restore angular harmonics absent from the 15 degree CST grid.",
            "The generated STFT validates software/data flow only and is not a final blade-tip Doppler measurement.",
            "No body, additional rotors, multipath, complex material, or measured noise is included.",
        ],
    }
    (RAW_ROOT / "pipeline_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(config, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
