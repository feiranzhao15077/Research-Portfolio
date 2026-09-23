# -*- coding: utf-8 -*-
"""鞘套 × 旋翼微多普勒复合回波（创新方向 A）。

物理链：雷达波穿越等离子体鞘套（衰减+相移）→ 照射旋翼目标 →
微多普勒调制 → 再次穿越鞘套返回。窄带近似下复合回波：
    s_plasma(t) = T(f0)² · s_clean(t)
其中 T 为 1D Drude 平板单程透射复系数（解析 Airy 公式，已用 FDTD 验证），
平方表示往返两次穿越。

创新点：已读 27 篇文献（CNKI 定向检索）范围内未发现
"鞘套+旋翼微多普勒"组合场景；本模块给出窄带平板近似下的
复合回波模型与"透过鞘套识别"的退化规律（模型边界见技术报告 4.10）。

方向 C（时变鞘套）：电子密度随时间波动 fp(t) = fp0·(1 + m·sin(2π f_mod t))，
透射系数随之时变 T(t)，复合回波 s(t) = T(t)²·s_rotor(t)。
参照李小平（航空学报 2025）：信道相干时间 τ_c ≈ 1/f_mod（实验验证），
τ_c 相对旋翼闪烁周期（1/(M·f_rot)）的比值决定微多普勒特征能否存活。
"""

import numpy as np

from .constants import C0, ETA0
from .fdtd_plasma import plasma_eps_r, slab_t_r
from .micro_doppler import quadrotor_echo, rotor_echo


def slab_transmission(f, fp, nu, thickness):
    """单程透射复系数 T(f)（Drude 平板解析解，e^{-iωt} 约定）。"""
    omega = 2.0 * np.pi * f
    er = plasma_eps_r(omega, 2.0 * np.pi * fp, 2.0 * np.pi * nu)
    n = np.sqrt(er)
    k0 = omega / C0
    t, _ = slab_t_r(n, k0, thickness)
    return t


def round_trip_atten_db(f, fp, nu, thickness):
    """往返总衰减 [dB]：-10·log10(|T|⁴)。"""
    t = slab_transmission(f, fp, nu, thickness)
    return -10.0 * np.log10(abs(t) ** 4 + 1e-30)


def apply_sheath(s, carrier_hz, fp, nu, thickness):
    """把一段基带回波乘上鞘套往返因子 T(f0)²。"""
    t = slab_transmission(carrier_hz, fp, nu, thickness)
    return (t * t) * s


def sheath_rotor_echo(t, carrier_hz, fp, nu, thickness, r_n, blade_len,
                      n_blades, f_rot, beta, alpha, theta_deg,
                      amp=1.0, phase0_deg=0.0):
    """单旋翼回波穿过鞘套（T² 往返）。"""
    s = rotor_echo(t, r_n, blade_len, n_blades, f_rot, beta, alpha,
                   theta_deg, carrier_hz, amp=amp, phase0_deg=phase0_deg)
    return apply_sheath(s, carrier_hz, fp, nu, thickness)


def sheath_quadrotor_echo(t, carrier_hz, fp, nu, thickness, **kwargs):
    """四旋翼（含机身）回波穿过鞘套。"""
    s = quadrotor_echo(t, carrier_hz, **kwargs)
    return apply_sheath(s, carrier_hz, fp, nu, thickness)


def time_varying_transmission(t, carrier_hz, fp0, nu, thickness,
                              mod_depth=0.3, f_mod=50.0, phase0=0.0):
    """时变鞘套往返因子 T(t)²（fp 随时间正弦波动）。"""
    fp = fp0 * (1.0 + mod_depth * np.sin(2.0 * np.pi * f_mod * t + phase0))
    t_pass = slab_transmission(carrier_hz, fp, nu, thickness)
    return t_pass * t_pass


def apply_time_varying_sheath(s, t, carrier_hz, fp0, nu, thickness,
                              mod_depth=0.3, f_mod=50.0, phase0=0.0):
    """时变鞘套复合回波：s(t) = T(t)²·s_rotor(t)。"""
    return time_varying_transmission(t, carrier_hz, fp0, nu, thickness,
                                     mod_depth, f_mod, phase0) * s


def coherence_time(f_mod):
    """信道相干时间 [s] ≈ 1/f_mod（参照李小平 2025：与激励频率成反比）。"""
    return 1.0 / f_mod


def layered_transmission(f, fp_profile, nu, thickness, half_space=1.0):
    """分层等离子体平板的正入射透射复系数 T（ABCD 转移矩阵）。

    fp_profile : array_like，各层等离子体频率 [Hz]（从内到外排列）；
    thickness  : 每层厚度 [m]（等厚分层）；
    返回复透射系数（约定与 slab_t_r 一致，单层时完全重合）。
    """
    fp_profile = np.asarray(fp_profile, dtype=float)
    omega = 2.0 * np.pi * f
    k0 = omega / C0
    M = np.eye(2, dtype=complex)
    for fp in fp_profile:
        er = plasma_eps_r(omega, 2.0 * np.pi * fp, 2.0 * np.pi * nu)
        n = np.sqrt(er)
        eta = ETA0 / n
        kd = k0 * n * thickness
        c, s = np.cos(kd), np.sin(kd)
        Mj = np.array([[c, -1j * eta * s], [-1j / eta * s, c]])
        M = M @ Mj
    eta0 = ETA0 / half_space
    return 2.0 * eta0 / (M[0, 0] * eta0 + M[0, 1]
                         + M[1, 0] * eta0 ** 2 + M[1, 1] * eta0)
