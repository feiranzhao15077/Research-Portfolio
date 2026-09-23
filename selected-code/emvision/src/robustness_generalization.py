"""Parameter-domain generation and baseline fitting for stage two.

Kept separate from the frozen stage-one generator so its archived source hashes
remain valid. Shared feature extraction, noise and physical models are reused.
"""
import numpy as np

from .experiment_protocol import receiver_sigma
from .micro_doppler import rotor_echo
from .recognition import MLP
from .recognition_data import extract_features
from .robustness_diagnostics import channel_gains, rms_normalize


def generate_domain_bank(n_per_class, seed, config, ranges, augmentation_seed=None):
    if n_per_class < 1:
        raise ValueError("positive sample count required")
    for key in ("rotation_hz", "length_m", "beta_deg"):
        if not (len(ranges[key]) == 2 and np.isfinite(ranges[key]).all()
                and ranges[key][0] < ranges[key][1]):
            raise ValueError("invalid parameter range: " + key)
    rng = np.random.default_rng(seed)
    aug_rng = np.random.default_rng(augmentation_seed)
    fs = config["sample_rate_hz"]
    t = np.arange(0.0, config["duration_s"], 1.0 / fs)
    gains = channel_gains(config)
    n = 3 * n_per_class
    result = dict(raw=np.empty((len(gains), n, 7)), rms=np.empty((len(gains), n, 7)),
                  y=np.repeat(np.arange(3), n_per_class), latent=np.empty((n, 7)),
                  noise_raw=np.empty((n, 7)), noise_rms=np.empty((n, 7)),
                  clean_power=np.empty(n), realized_noise_power=np.empty(n))
    if augmentation_seed is not None:
        result.update(aug_raw=np.empty((n, 7)), aug_attenuation_db=np.empty(n))
    sigma = receiver_sigma(config["reference_snr_db"])
    for row, label in enumerate(result["y"]):
        nb = int(label + 2)
        rotation = float(rng.uniform(*ranges["rotation_hz"]))
        length = float(rng.uniform(*ranges["length_m"]))
        beta = float(rng.uniform(*np.deg2rad(ranges["beta_deg"])))
        alpha = float(rng.uniform(0, 2 * np.pi))
        distance = float(rng.uniform(50, 200))
        phase = float(rng.uniform(0, 360))
        clean = rotor_echo(t, distance, length, nb, rotation, beta, alpha, 0.,
                           config["carrier_hz"], amp=1., phase0_deg=phase)
        noise = sigma * (rng.standard_normal(len(t)) + 1j*rng.standard_normal(len(t)))
        result["latent"][row] = nb, rotation, length, beta, alpha, distance, phase
        result["clean_power"][row] = np.mean(np.abs(clean)**2)
        result["realized_noise_power"][row] = np.mean(np.abs(noise)**2)
        result["noise_raw"][row] = extract_features(noise, fs)
        result["noise_rms"][row] = extract_features(rms_normalize(noise), fs)
        for ci, gain in enumerate(gains):
            observed = gain*clean + noise
            result["raw"][ci, row] = extract_features(observed, fs)
            result["rms"][ci, row] = extract_features(rms_normalize(observed), fs)
        if augmentation_seed is not None:
            attenuation = float(aug_rng.uniform(*config["augmentation_attenuation_db"]))
            observed = gains[0]*10**(-attenuation/20)*clean + noise
            result["aug_raw"][row] = extract_features(observed, fs)
            result["aug_attenuation_db"][row] = attenuation
    result["sample_snr_db"] = 10*np.log10(
        np.abs(gains[:, None])**2 * result["clean_power"][None, :] / (2*sigma**2))
    for name, array in result.items():
        if not np.all(np.isfinite(array)):
            raise ValueError("nonfinite generated array: " + name)
    return result


def fit_baseline(features, labels, kind, seed, config, columns=None):
    columns = np.arange(features.shape[1]) if columns is None else np.asarray(columns)
    x = features[:, columns]
    mean, sd = x.mean(0), x.std(0) + 1e-12
    if kind == "mlp":
        model = MLP(3, seed=seed, **config["models"][kind])
    elif kind == "svm":
        from sklearn.svm import SVC
        model = SVC(**config["models"][kind])
    elif kind == "rf":
        from sklearn.ensemble import RandomForestClassifier
        model = RandomForestClassifier(random_state=seed, **config["models"][kind])
    else:
        raise ValueError("unknown classifier: " + kind)
    model.fit((x-mean)/sd, labels)
    return dict(model=model, mean=mean, sd=sd, columns=columns)


def predict_baseline(fitted, features):
    return fitted["model"].predict((features[:, fitted["columns"]]-fitted["mean"])/fitted["sd"])
