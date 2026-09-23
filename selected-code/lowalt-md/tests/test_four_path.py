import numpy as np
from src.geometry import SceneGeometry
from src.four_path import four_path_echo


def scene_and_point():
    return SceneGeometry(np.array([0., 0., 5.]), np.array([200., 0., 20.])), np.array([[[200., 2., 20.]]])


def test_m3_t1_zero_gamma_reduces_to_dd():
    scene, pos = scene_and_point()
    total, d = four_path_echo(pos, scene, 10e9, 1., 0., "H")
    assert np.allclose(d["E_DR"], 0)
    assert np.allclose(d["E_RD"], 0)
    assert np.allclose(d["E_RR"], 0)
    assert np.allclose(total[0], d["E_DD"][0].sum())


def test_m3_t2_reciprocal_dr_rd_naturally_match():
    scene, pos = scene_and_point()
    _, d = four_path_echo(pos, scene, 10e9, 6., .01, "H")
    assert np.allclose(d["L_DR_m"], d["L_RD_m"])
    assert np.allclose(d["E_DR"], d["E_RD"])


def test_m3_t3_rr_has_two_reflection_factor():
    scene, pos = scene_and_point()
    _, d = four_path_echo(pos, scene, 10e9, 6., .01, "H")
    assert np.allclose(d["reflection_factor_RR"], d["gamma_tx"] * d["gamma_rx"])
    assert np.allclose(d["reflection_factor_RR"], d["gamma_tx"] ** 2)


def test_m3_t4_complete_path_phase():
    scene, pos = scene_and_point()
    _, d = four_path_echo(pos, scene, 10e9, 6., .01, "H")
    k = 2*np.pi/(299792458.0/10e9)
    for name in ("DD", "DR", "RD", "RR"):
        field = d[f"E_{name}"][0, 0]
        expected_phase = -k * d[f"L_{name}_m"][0, 0]
        # Remove the known scattering/reflection-factor phase, then compare
        # the propagation phase modulo 2pi.
        factor = d[f"reflection_factor_{name}"][0, 0]
        assert np.allclose(field/abs(field) / (factor/abs(factor)), np.exp(1j*expected_phase))


def test_m3_t5_amplitude_spreading():
    scene, pos = scene_and_point()
    _, d = four_path_echo(pos, scene, 10e9, 6., .01, "H")
    rd, rr = d["R_d_tx_m"][0,0], d["R_r_tx_m"][0,0]
    assert np.isclose(d["geometric_spreading_DD"][0,0], 1/(rd*rd))
    assert np.isclose(d["geometric_spreading_DR"][0,0], 1/(rd*rr))
    assert np.isclose(d["geometric_spreading_RD"][0,0], 1/(rr*rd))
    assert np.isclose(d["geometric_spreading_RR"][0,0], 1/(rr*rr))


def test_m3_t6_continuity():
    scene = SceneGeometry(np.array([0.,0.,5.]), np.array([200.,0.,20.]))
    t = np.linspace(0, 1/40, 128, endpoint=False)
    pos = np.zeros((t.size,1,3)); pos[:,0,0] = 200 + .3*np.cos(2*np.pi*40*t); pos[:,0,1] = .3*np.sin(2*np.pi*40*t); pos[:,0,2] = 20
    _, d = four_path_echo(pos, scene, 10e9, 6., .01)
    for name in ("DD", "DR", "RD", "RR"):
        assert np.isfinite(d[f"L_{name}_m"]).all()
        assert np.isfinite(d[f"magnitude_{name}"]).all()
        assert np.isfinite(d[f"phase_{name}_unwrapped_rad"]).all()
        assert np.max(np.abs(np.diff(d[f"L_{name}_m"][:,0]))) < 1.0
