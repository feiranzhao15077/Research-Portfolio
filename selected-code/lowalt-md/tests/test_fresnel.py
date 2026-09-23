import numpy as np
from src.fresnel import complex_permittivity, fresnel_coefficients, fresnel_te, fresnel_tm


def test_fresnel_limits_and_finiteness():
    eps = complex_permittivity(4.0, 0.0, 10e9)
    assert eps == 4 + 0j
    gh, gv = fresnel_coefficients(0.0, 4.0, 0.0, 10e9)
    assert np.isclose(gh, -1/3)
    assert np.isclose(gv, 1/3)
    assert np.isfinite(fresnel_coefficients(np.array([0., .3, 1.0]), 6, .01, 10e9)[0]).all()


def test_identical_media_have_zero_reflection():
    # The public half-space API is air over ground; epsilon_r=1, sigma=0 is identical media.
    gh, gv = fresnel_coefficients(np.linspace(0, np.pi/2 - 1e-6, 20), 1., 0., 10e9)
    assert np.max(np.abs(gh)) < 1e-12
    assert np.max(np.abs(gv)) < 1e-12


def test_tm_brewster_minimum_for_lossless_dielectric():
    theta_b = np.arctan(np.sqrt(4.0))
    assert abs(fresnel_tm(theta_b, 4., 0., 10e9)) < 1e-12


def test_passive_lossy_reflection_is_finite_and_bounded():
    angles = np.linspace(0, np.pi/2 - 1e-8, 1000)
    gh, gv = fresnel_coefficients(angles, 6., .01, 10e9)
    assert np.isfinite(gh).all() and np.isfinite(gv).all()
    assert np.max(np.abs(gh)) <= 1.0 + 1e-10
    assert np.max(np.abs(gv)) <= 1.0 + 1e-10
