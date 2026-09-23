"""Paired continuous/sparse rotor generators for the frozen stress test."""
import numpy as np

from .constants import C0
from .experiment_protocol import receiver_sigma
from .micro_doppler import rotor_echo
from .recognition_data import extract_features
from .robustness_diagnostics import channel_gains


def sparse_blade_echo(t, r_n, blade_len, n_blades, f_rot, beta, alpha,
                      carrier_hz, phase0_deg, scatter_rng, sparse_config):
    """Sparse point-scatterer rotor with class-independent scatter rules."""
    fractions = np.asarray(sparse_config["radial_fractions"], dtype=float)
    jitter = float(sparse_config["radial_jitter_fraction"])
    sigma = float(sparse_config["amplitude_lognormal_sigma"])
    if fractions.ndim != 1 or len(fractions) < 2:
        raise ValueError("at least two radial fractions required")
    if not np.all((fractions > jitter) & (fractions < 1.0 - jitter)):
        raise ValueError("radial fractions incompatible with jitter")
    if sigma < 0 or not sparse_config["class_conditional_parameters"] is False:
        raise ValueError("invalid or class-conditional sparse configuration")

    lam = C0 / carrier_hz
    echo = np.zeros_like(t, dtype=complex)
    positions = np.zeros((4, len(fractions)))
    weights = np.zeros((4, len(fractions)))
    active = np.zeros(4, dtype=bool)
    for blade in range(n_blades):
        phi = np.deg2rad(phase0_deg + 360.0 * blade / n_blades)
        radial = fractions + scatter_rng.uniform(-jitter, jitter, len(fractions))
        amplitude = scatter_rng.lognormal(mean=0.0, sigma=sigma, size=len(fractions))
        amplitude /= amplitude.mean()
        active[blade] = True
        positions[blade] = radial
        weights[blade] = amplitude
        p = np.sin(beta) * np.cos(2.0 * np.pi * f_rot * t + phi + alpha)
        for fraction, weight in zip(radial, amplitude):
            echo += (blade_len / len(fractions) * weight
                     * np.exp(-1j * 4.0 * np.pi
                              * (r_n + blade_len * fraction * p) / lam))
    return echo, positions, weights, active


def generate_paired_generator_bank(n_per_class, seed, scatter_seed, config, ranges):
    """Generate paired features sharing latent variables, noise and channels."""
    if n_per_class < 1:
        raise ValueError("positive sample count required")
    for key in ("rotation_hz", "length_m", "beta_deg"):
        bounds = np.asarray(ranges[key], dtype=float)
        if bounds.shape != (2,) or not np.all(np.isfinite(bounds)) or bounds[0] >= bounds[1]:
            raise ValueError("invalid parameter range: " + key)
    rng = np.random.default_rng(seed)
    scatter_rng = np.random.default_rng(scatter_seed)
    fs = float(config["sample_rate_hz"])
    t = np.arange(0.0, float(config["duration_s"]), 1.0 / fs)
    gains = channel_gains(config)
    n = 3 * n_per_class
    n_points = len(config["sparse_generator"]["radial_fractions"])
    result = dict(
        continuous_raw=np.empty((len(gains), n, 7)),
        sparse_raw=np.empty((len(gains), n, 7)),
        y=np.repeat(np.arange(3), n_per_class),
        latent=np.empty((n, 7)),
        noise=np.empty((n, len(t)), dtype=complex),
        noise_raw=np.empty((n, 7)),
        continuous_clean_power=np.empty(n),
        sparse_clean_power=np.empty(n),
        scatter_positions_fraction=np.zeros((n, 4, n_points)),
        scatter_weights=np.zeros((n, 4, n_points)),
        scatter_active=np.zeros((n, 4), dtype=bool),
    )
    sigma = receiver_sigma(config["reference_snr_db"])
    for row, label in enumerate(result["y"]):
        n_blades = int(label + 2)
        rotation = float(rng.uniform(*ranges["rotation_hz"]))
        length = float(rng.uniform(*ranges["length_m"]))
        beta = float(rng.uniform(*np.deg2rad(ranges["beta_deg"])))
        alpha = float(rng.uniform(0, 2 * np.pi))
        distance = float(rng.uniform(50, 200))
        phase = float(rng.uniform(0, 360))
        continuous = rotor_echo(t, distance, length, n_blades, rotation, beta,
                                alpha, 0.0, config["carrier_hz"], phase0_deg=phase)
        sparse, positions, weights, active = sparse_blade_echo(
            t, distance, length, n_blades, rotation, beta, alpha,
            config["carrier_hz"], phase, scatter_rng, config["sparse_generator"])
        noise = sigma * (rng.standard_normal(len(t)) + 1j * rng.standard_normal(len(t)))
        result["latent"][row] = n_blades, rotation, length, beta, alpha, distance, phase
        result["noise"][row] = noise
        result["noise_raw"][row] = extract_features(noise, fs)
        result["continuous_clean_power"][row] = np.mean(np.abs(continuous) ** 2)
        result["sparse_clean_power"][row] = np.mean(np.abs(sparse) ** 2)
        result["scatter_positions_fraction"][row] = positions
        result["scatter_weights"][row] = weights
        result["scatter_active"][row] = active
        for condition, gain in enumerate(gains):
            result["continuous_raw"][condition, row] = extract_features(
                gain * continuous + noise, fs)
            result["sparse_raw"][condition, row] = extract_features(
                gain * sparse + noise, fs)
    for name, array in result.items():
        if name != "noise" and not np.all(np.isfinite(array)):
            raise ValueError("nonfinite generated array: " + name)
    if not np.all(np.isfinite(result["noise"])):
        raise ValueError("nonfinite generated noise")
    return result
