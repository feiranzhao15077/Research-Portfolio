"""Paired synthetic diagnostics; no fitting on test observations or labels.

The legacy latent/noise RNG draw order is preserved so the unprocessed arm can
be checked against demo_sheath_microdoppler._gen_sheath_set. New experimental
outputs are kept separate from the manuscript's historical results.
"""
from dataclasses import dataclass
import hashlib

import numpy as np

from .experiment_protocol import receiver_sigma
from .micro_doppler import rotor_echo
from .recognition import MLP, confusion_matrix
from .recognition_data import extract_features
from .sheath_microdoppler import slab_transmission

FEATURE_NAMES = (
    "flash_khz", "positive_support_khz", "envelope_mean", "envelope_sd",
    "kurtosis", "spectral_entropy", "dc_fraction",
)
LATENT_NAMES = ("blade_count", "rotation_hz", "blade_length_m", "beta_rad",
                "alpha_rad", "range_m", "phase_deg")


def rms_normalize(observation):
    """Normalize using this observed record only, robust to very small scales."""
    x = np.asarray(observation, dtype=complex)
    if x.ndim != 1 or x.size == 0 or not np.all(np.isfinite(x)):
        raise ValueError("expected a nonempty finite one-dimensional record")
    peak = np.abs(x).max()
    if peak == 0:
        return np.zeros_like(x)
    scaled = x / peak
    return scaled / np.sqrt(np.mean(np.abs(scaled)**2))


def channel_gains(config):
    return np.asarray([
        slab_transmission(config["carrier_hz"], fp * 1e9,
                          config["collision_hz"], config["thickness_m"])**2
        for fp in config["fp_ghz"]
    ], dtype=complex)


def generate_bank(n_per_class, seed, config, augmentation_seed=None,
                  include_scaled=False, signal_gain=1.0):
    """One latent/noise record per row, shared across conditions and arms.

    Augmentation replaces the channel once per training row, independently of
    the latent/noise RNG. It does not add records or use test-set statistics.
    Sample SNR uses expected receiver noise power, not a per-record noise fit.
    """
    if n_per_class < 1:
        raise ValueError("n_per_class must be positive")
    rng = np.random.default_rng(seed)
    aug_rng = np.random.default_rng(augmentation_seed)
    fs = config["sample_rate_hz"]
    t = np.arange(0.0, config["duration_s"], 1.0 / fs)
    gains = channel_gains(config)
    if config["fp_ghz"][0] != 0 or abs(gains[0]) == 0:
        raise ValueError("first condition must be a nonzero vacuum baseline")
    n = 3 * n_per_class
    nc = len(gains)
    out = dict(raw=np.empty((nc, n, 7)), rms=np.empty((nc, n, 7)),
               y=np.repeat(np.arange(3), n_per_class), latent=np.empty((n, 7)),
               noise_raw=np.empty((n, 7)), noise_rms=np.empty((n, 7)),
               clean_power=np.empty(n), realized_noise_power=np.empty(n))
    if include_scaled:
        out.update(scaled_raw=np.empty((nc, n, 7)),
                   scaled_rms=np.empty((nc, n, 7)))
    if augmentation_seed is not None:
        out.update(aug_raw=np.empty((n, 7)), aug_attenuation_db=np.empty(n))
    sigma = receiver_sigma(config["reference_snr_db"])
    for row, label in enumerate(out["y"]):
        nb = int(label + 2)
        f_rot = float(rng.uniform(60.0, 120.0))
        blade_len = float(rng.uniform(0.05, 0.12))
        beta = float(rng.uniform(np.deg2rad(30), np.deg2rad(90)))
        alpha = float(rng.uniform(0, 2 * np.pi))
        r0 = float(rng.uniform(50.0, 200.0))
        phase0 = float(rng.uniform(0, 360))
        clean = signal_gain * rotor_echo(
            t, r0, blade_len, nb, f_rot, beta, alpha, 0.0,
            config["carrier_hz"], amp=1.0, phase0_deg=phase0)
        noise = sigma * (rng.standard_normal(len(t))
                         + 1j * rng.standard_normal(len(t)))
        out["latent"][row] = nb, f_rot, blade_len, beta, alpha, r0, phase0
        out["clean_power"][row] = np.mean(np.abs(clean)**2)
        out["realized_noise_power"][row] = np.mean(np.abs(noise)**2)
        out["noise_raw"][row] = extract_features(noise, fs)
        out["noise_rms"][row] = extract_features(rms_normalize(noise), fs)
        for ci, gain in enumerate(gains):
            obs = gain * clean + noise
            out["raw"][ci, row] = extract_features(obs, fs)
            out["rms"][ci, row] = extract_features(rms_normalize(obs), fs)
            if include_scaled:
                whole_scaled = (gain / gains[0]) * (gains[0] * clean + noise)
                out["scaled_raw"][ci, row] = extract_features(whole_scaled, fs)
                out["scaled_rms"][ci, row] = extract_features(
                    rms_normalize(whole_scaled), fs)
        if augmentation_seed is not None:
            attenuation = float(aug_rng.uniform(*config["augmentation_attenuation_db"]))
            aug_obs = gains[0] * 10**(-attenuation / 20) * clean + noise
            out["aug_attenuation_db"][row] = attenuation
            out["aug_raw"][row] = extract_features(aug_obs, fs)
    with np.errstate(divide="ignore"):
        out["sample_snr_db"] = 10 * np.log10(
            np.abs(gains[:, None])**2 * out["clean_power"][None, :]
            / (2 * sigma**2))
    # -inf is a valid SNR only in the signal_gain=0 diagnostic.
    for key, value in out.items():
        if key != "sample_snr_db" and not np.all(np.isfinite(value)):
            raise ValueError("nonfinite generated array: " + key)
    return out


@dataclass
class FrozenClassifier:
    model: MLP
    mean: np.ndarray
    sd: np.ndarray
    columns: np.ndarray

    def predict(self, features):
        x = np.asarray(features)[:, self.columns]
        return self.model.predict((x - self.mean) / self.sd)

    def arrays(self):
        result = {key: getattr(self.model, key) for key in ("W1", "b1", "W2", "b2")}
        result.update(mean=self.mean, sd=self.sd, columns=self.columns)
        return result

    def digest(self):
        digest = hashlib.sha256()
        for name, array in sorted(self.arrays().items()):
            digest.update(name.encode())
            digest.update(str(array.shape).encode())
            digest.update(str(array.dtype).encode())
            digest.update(np.ascontiguousarray(array).tobytes())
        return digest.hexdigest()


def fit_frozen(features, labels, seed, mlp_config, columns=None):
    x = np.asarray(features)
    columns = np.arange(x.shape[1]) if columns is None else np.asarray(columns)
    x = x[:, columns]
    mean = x.mean(axis=0)
    sd = x.std(axis=0) + 1e-12  # identical to the historical protocol
    model = MLP(3, seed=seed, **mlp_config).fit((x - mean) / sd, labels)
    return FrozenClassifier(model, mean, sd, columns)


def classification_metrics(labels, prediction):
    y, p = np.asarray(labels), np.asarray(prediction)
    if (y.ndim != 1 or p.shape != y.shape or not len(y)
            or not np.all(np.isin(y, [0, 1, 2]))
            or not np.all(np.isin(p, [0, 1, 2]))):
        raise ValueError("expected equally sized nonempty three-class labels")
    cm = confusion_matrix(y.astype(int), p.astype(int), 3)
    tp = cm.diagonal().astype(float)
    support, predicted = cm.sum(axis=1), cm.sum(axis=0)
    recall = np.divide(tp, support, out=np.zeros(3), where=support != 0)
    f1 = np.divide(2 * tp, support + predicted, out=np.zeros(3),
                   where=(support + predicted) != 0)
    return dict(accuracy=float(tp.sum()/cm.sum()), macro_f1=float(f1.mean()),
                recall=recall.tolist(), confusion_matrix=cm.tolist())
