# -*- coding: utf-8 -*-
"""旋翼微多普勒回波模型（基于刘鲁涛 2025 旋翼回波时域积分模型）。

单个叶片上散射点 P（距转轴 l_P）的瞬时距离：
    R_P(t) = R_n + l_P·p(t),   p(t) = sinβ·cos(2π f_n t + φ_nm + α)
叶片积分回波：
    s_nm(t) = L·sinc[2π L p(t)/λ]·exp(-j4π[R_n + 0.5L·p(t)]/λ)
旋翼中心到雷达距离：
    R_n = sqrt(R0² + d² + 2·R0·d·sinβ·cos(α - θ_n))

理论量（用于验证）：
    最大微多普勒  f_d,max = 4π f_n L sinβ / λ
    多普勒带宽    B      = 8π f_n L sinβ / λ
    闪烁频率      M·f_n（M 个叶片）
    频谱梳状间隔  M·f_n

参考：刘鲁涛, 谢良正, 莫禹涵. 旋翼无人机雷达回波特征分析与参数估计方法[J].
国防科技大学学报, 2025, 47(2): 202-211.
"""

import numpy as np

from .constants import C0


def max_micro_doppler(f_rot, blade_len, beta, carrier_hz):
    """理论最大微多普勒频率 [Hz]：4π·f_rot·L·sinβ/λ。"""
    lam = C0 / carrier_hz
    return 4.0 * np.pi * f_rot * blade_len * np.sin(beta) / lam


def doppler_bandwidth(f_rot, blade_len, beta, carrier_hz):
    """理论多普勒带宽 [Hz]：8π·f_rot·L·sinβ/λ。"""
    return 2.0 * max_micro_doppler(f_rot, blade_len, beta, carrier_hz)


def rotor_center_range(r0, d, beta, alpha, theta_deg):
    """第 n 个旋翼中心到雷达的距离 R_n [m]。"""
    return np.sqrt(r0 ** 2 + d ** 2
                   + 2.0 * r0 * d * np.sin(beta)
                   * np.cos(alpha - np.deg2rad(theta_deg)))


def rotor_echo(t, r_n, blade_len, n_blades, f_rot, beta, alpha,
               theta_deg, carrier_hz, amp=1.0, phase0_deg=0.0):
    """单个旋翼的基带回波（M 个叶片积分求和）。"""
    lam = C0 / carrier_hz
    s = np.zeros_like(t, dtype=complex)
    for m in range(n_blades):
        phi = np.deg2rad(phase0_deg + 360.0 * m / n_blades)
        p = np.sin(beta) * np.cos(2.0 * np.pi * f_rot * t + phi + alpha)
        s += (amp * blade_len
              * np.sinc(2.0 * blade_len * p / lam)          # sin(x)/x, x=2πLp/λ
              * np.exp(-1j * 4.0 * np.pi
                       * (r_n + 0.5 * blade_len * p) / lam))
    return s


def point_scatterer_echo(t, r_n, l, f_rot, beta, alpha, theta_deg,
                         carrier_hz, amp=1.0, phase0_deg=0.0):
    """叶片上单个散射点（距转轴 l）的基带回波。

    幅度恒定，瞬时频率解析可验证：最大微多普勒 = 4π·f_rot·l·sinβ/λ。
    """
    lam = C0 / carrier_hz
    phi = np.deg2rad(phase0_deg)
    p = np.sin(beta) * np.cos(2.0 * np.pi * f_rot * t + phi + alpha)
    return amp * np.exp(-1j * 4.0 * np.pi * (r_n + l * p) / lam)


def max_inst_freq(x, fs, amp_thresh=0.05):
    """原始复数信号瞬时频率的最大绝对值 [Hz]。

    仅适用于幅度恒定的窄带点散射回波；幅度低于阈值的样本（相位噪声）
    会被剔除，避免包络零点处相位导数发散（叶片积分回波不适用本函数）。
    """
    mag = np.abs(x)
    th = amp_thresh * mag.max()
    valid = mag > th
    if not valid.any():
        return 0.0
    phase = np.unwrap(np.angle(x))
    f_inst = np.gradient(phase, 1.0 / fs) / (2.0 * np.pi)
    return float(np.max(np.abs(f_inst[valid]))) if valid.any() else 0.0


def body_echo(t, r0, carrier_hz, radial_velocity=0.0, amp=1.0):
    """机身基带回波（平动多普勒）。"""
    lam = C0 / carrier_hz
    return amp * np.exp(-1j * 4.0 * np.pi * (r0 + radial_velocity * t) / lam)


def quadrotor_echo(t, carrier_hz, r0=100.0, beta=np.pi / 3, alpha=0.0,
                   rotor_offset=0.205, blade_len=0.060, n_blades=2,
                   f_rot=(100.0, 100.0, 100.0, 100.0),
                   body_amp=1.0, blade_amp=0.08, radial_velocity=0.0,
                   rotor_theta_deg=(45.0, 135.0, 225.0, 315.0)):
    """四旋翼无人机（X 形构型，与 DroneModel 参数一致）总回波。"""
    s = body_echo(t, r0, carrier_hz, radial_velocity, body_amp)
    for n in range(4):
        r_n = rotor_center_range(r0, rotor_offset, beta, alpha,
                                 rotor_theta_deg[n])
        s += rotor_echo(t, r_n, blade_len, n_blades, f_rot[n],
                        beta, alpha, rotor_theta_deg[n], carrier_hz,
                        amp=blade_amp)
    return s


def stft(x, fs, win_len, hop, nfft=None):
    """短时傅里叶变换（复数基带信号，双边频谱）。返回 (t, f, S)。"""
    nfft = nfft or win_len
    win = np.hanning(win_len)
    n = len(x)
    n_frames = 1 + (n - win_len) // hop
    t = (np.arange(n_frames) * hop + win_len / 2.0) / fs
    f = np.fft.fftfreq(nfft, 1.0 / fs)
    S = np.empty((n_frames, nfft), dtype=complex)
    for i in range(n_frames):
        seg = x[i * hop:i * hop + win_len] * win
        S[i] = np.fft.fft(seg, nfft)
    return t, f, S


def spectrogram_db(x, fs, win_len, hop, nfft=None):
    """返回 (t, f, S_dB)，频率按 fftshift 排序便于显示。"""
    t, f, S = stft(x, fs, win_len, hop, nfft)
    Sdb = 10.0 * np.log10(np.abs(S) ** 2 + 1e-30)
    order = np.argsort(f)
    return t, f[order], Sdb[:, order]


def stft_max_doppler(x, fs, win_len, hop, threshold_db=-40.0):
    """从时频图中估计最大微多普勒（超出阈值的最远频率）。"""
    t, f, Sdb = spectrogram_db(x, fs, win_len, hop)
    ref = Sdb.max()
    active = Sdb > ref + threshold_db
    if not active.any():
        return 0.0
    return float(np.max(np.abs(f[active.any(axis=0)])))


def envelope_autocorr(x):
    """包络自相关（用于估计闪烁频率）。"""
    env = np.abs(x)
    env -= env.mean()
    ac = np.correlate(env, env, mode="full")[len(env) - 1:]
    return ac / (ac[0] + 1e-30)


def estimate_flash_freq(x, fs, min_hz=10.0, max_hz=5000.0):
    """由包络频谱峰值估计闪烁频率 [Hz]（理论值 M·f_rot）。"""
    env = np.abs(x) - np.mean(np.abs(x))
    mag = np.abs(np.fft.rfft(env))
    freqs = np.fft.rfftfreq(len(env), 1.0 / fs)
    mask = (freqs >= min_hz) & (freqs <= max_hz)
    if not mask.any():
        return 0.0
    return float(freqs[np.argmax(mag * mask)])


def fft_comb_spacing(x, fs, n_peaks=40, min_gap_hz=20.0):
    """从全时长 FFT 谱峰估计梳状谱间隔 [Hz]（理论值 M·f_rot）。"""
    n = len(x)
    X = np.fft.fft(x)
    freq = np.fft.fftfreq(n, 1.0 / fs)
    mag = np.abs(X)
    # 只保留正频率并做峰值检测（简单邻域极大）
    half = n // 2
    mag = mag[:half]
    freq = freq[:half]
    peaks = []
    gap = max(2, int(min_gap_hz / (fs / n)))
    for i in range(gap, half - gap):
        if mag[i] == mag[i - gap:i + gap + 1].max():
            peaks.append(i)
    if len(peaks) < 2:
        return 0.0
    # 取幅度最大的若干峰，求间隔的中位数
    mags = mag[peaks]
    top = np.argsort(mags)[-n_peaks:]
    top_peaks = np.sort(np.array(peaks)[top])
    diffs = np.diff(freq[top_peaks])
    diffs = diffs[diffs > 0.5 * np.median(diffs)]
    return float(np.median(diffs)) if len(diffs) else 0.0
