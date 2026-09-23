# -*- coding: utf-8 -*-
"""微多普勒目标识别数据集生成。

每类目标 = 一个旋翼（叶片数不同），随机化转速/叶片长度/俯仰角/载频/距离，
并叠加高斯白噪声（随机 SNR）。标签 = 叶片数（2/3/4）。
特征（可解释、对齐文献）：
  1. 包络闪烁频率（envelope FFT 峰，≈M·f_rot，Hz/1000）
  2. 多普勒带宽（频谱支撑 -40 dB，Hz/1000）
  3. 包络均值 / 标准差 / 峰度
  4. 频谱熵（正频功率归一化）
  5. 直流能量占比（机身/静态散射占比）
"""

import numpy as np

from .micro_doppler import rotor_echo
from .experiment_protocol import REFERENCE_POWER

FS = 200_000


def _pos_spectrum(x, fs):
    """复数基带信号的正频频谱（幅度）与对应频率。"""
    n = len(x)
    X = np.fft.fft(x)[:(n + 1) // 2]
    freqs = np.fft.fftfreq(n, 1.0 / fs)[:(n + 1) // 2]
    return X, freqs


def _envelope_flash_freq(x, fs, min_hz=10.0, max_hz=1200.0):
    env = np.abs(x) - np.mean(np.abs(x))
    mag = np.abs(np.fft.rfft(env))
    freqs = np.fft.rfftfreq(len(env), 1.0 / fs)
    mask = (freqs >= min_hz) & (freqs <= max_hz)
    if not mask.any():
        return 0.0
    return float(freqs[np.argmax(mag * mask)])


def _spectrum_support(x, fs, thr_db=-40.0):
    """正频侧幅度超过阈值的最远频率（多普勒带宽的一半）。"""
    X, freqs = _pos_spectrum(x, fs)
    X = np.abs(X)
    thr = X.max() * 10.0 ** (thr_db / 20.0)
    idx = np.where(X > thr)[0]
    return float(freqs[idx[-1]]) if len(idx) else 0.0


def _spectral_entropy(x):
    X, _ = _pos_spectrum(x, 1.0)
    X = np.abs(X) ** 2
    p = X[1:] / (X[1:].sum() + 1e-30)
    return float(-np.sum(p * np.log(p + 1e-30)) / np.log(len(p)))


def extract_features(s, fs=FS):
    """从一段基带回波提取 7 维特征向量。"""
    env = np.abs(s)
    X, _ = _pos_spectrum(s, 1.0)
    pw = np.abs(X) ** 2
    return np.array([
        _envelope_flash_freq(s, fs) / 1e3,          # kHz
        _spectrum_support(s, fs) / 1e3,             # kHz
        env.mean(),
        env.std(),
        float(((env - env.mean()) ** 4).mean() / (env.std() ** 4 + 1e-30)),
        _spectral_entropy(s),
        float(pw[0] / (pw.sum() + 1e-30)),
    ])


def add_noise(s, snr_db, rng=None, reference_power=REFERENCE_POWER):
    """叠加高斯白噪声（复信号），相对固定参考功率的标称 SNR = snr_db dB。

    rng 传入 numpy Generator 时使用该生成器（推荐），None 时创建默认生成器，
    避免依赖未设种子的全局 np.random 状态。
    """
    if rng is None:
        rng = np.random.default_rng()
    if not np.isfinite(reference_power) or reference_power <= 0:
        raise ValueError("reference_power must be positive and finite")
    noise_pow = reference_power / (10.0 ** (snr_db / 10.0))
    sigma = np.sqrt(noise_pow / 2.0)
    return s + sigma * (rng.standard_normal(len(s))
                        + 1j * rng.standard_normal(len(s)))


def generate_dataset(n_per_class, n_blades_list=(2, 3, 4), seed=0,
                     t_dur=0.05, snr_db_range=(5.0, 30.0),
                     fc_list=(10e9, 24e9, 35e9), f_rot_range=(60.0, 120.0),
                     blade_len_range=(0.05, 0.12)):
    """生成带标签数据集。返回 (X, y, meta)。"""
    rng = np.random.default_rng(seed)
    fs = FS
    t = np.arange(0.0, t_dur, 1.0 / fs)
    n = len(t)
    n_class = len(n_blades_list)
    X = np.empty((n_per_class * n_class, 7))
    y = np.empty(n_per_class * n_class, dtype=int)
    meta = []
    for ci, nb in enumerate(n_blades_list):
        for i in range(n_per_class):
            fc = float(rng.choice(fc_list))
            f_rot = float(rng.uniform(*f_rot_range))
            blade_len = float(rng.uniform(*blade_len_range))
            beta = float(rng.uniform(np.deg2rad(30), np.deg2rad(90)))
            alpha = float(rng.uniform(0, 2 * np.pi))
            r0 = float(rng.uniform(50.0, 200.0))
            phase0 = float(rng.uniform(0, 360))
            snr = float(rng.uniform(*snr_db_range))
            s = rotor_echo(t, r0, blade_len, nb, f_rot, beta, alpha,
                           0.0, fc, amp=1.0, phase0_deg=phase0)
            s = add_noise(s, snr, rng=rng)
            row = ci * n_per_class + i
            X[row] = extract_features(s, fs)
            y[row] = ci            # 类别索引 0/1/2
            meta.append(dict(n_blades=nb, f_rot=f_rot, blade_len=blade_len,
                             beta=beta, fc=fc, snr=snr, r0=r0))
    return X, y, meta


def train_test_split(X, y, test_frac=0.2, seed=0):
    """分层随机划分训练/测试集。"""
    rng = np.random.default_rng(seed)
    X, y = np.asarray(X), np.asarray(y)
    if len(X) != len(y) or not 0 < test_frac < 1:
        raise ValueError("incompatible data or invalid test fraction")
    train_idx, test_idx = [], []
    for label in np.unique(y):
        idx = rng.permutation(np.flatnonzero(y == label))
        if len(idx) < 2:
            raise ValueError("each class requires at least two samples")
        n_test = min(len(idx)-1, max(1, int(round(len(idx)*test_frac))))
        test_idx.extend(idx[:n_test])
        train_idx.extend(idx[n_test:])
    train_idx = rng.permutation(train_idx)
    test_idx = rng.permutation(test_idx)
    return X[train_idx], y[train_idx], X[test_idx], y[test_idx]


def standardize(X_train, X_test):
    """z-score 标准化（用训练集统计量）。"""
    mu = X_train.mean(axis=0)
    sd = X_train.std(axis=0) + 1e-12
    return (X_train - mu) / sd, (X_test - mu) / sd, (mu, sd)
