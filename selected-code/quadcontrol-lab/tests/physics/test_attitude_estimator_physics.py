"""Physics-contract tests for the minimal attitude estimator."""

from __future__ import annotations

import numpy as np
import pytest

from core.contracts import ImuMeasurement
from core.state.quaternion import (
    quaternion_derivative,
    quaternion_from_euler_zyx,
    quaternion_norm,
    rotate_world_to_body,
)
from estimation.attitude import MinimalAttitudeEstimator
from estimation.model import EstimatorContractError

GRAVITY = 9.80665


def _measurement(
    time_s: float,
    *,
    gyro: np.ndarray | None = None,
    specific_force: np.ndarray | None = None,
) -> ImuMeasurement:
    return ImuMeasurement(
        time_s=time_s,
        angular_velocity_body_radps=(
            np.zeros(3) if gyro is None else np.asarray(gyro, dtype=float)
        ),
        specific_force_body_mps2=(
            np.array([0.0, 0.0, GRAVITY])
            if specific_force is None
            else np.asarray(specific_force, dtype=float)
        ),
    )


def test_horizontal_static_measurement_aligns_gravity_direction() -> None:
    true_attitude = quaternion_from_euler_zyx(0.2, -0.1, 0.0)
    specific_force = GRAVITY * rotate_world_to_body(
        true_attitude,
        np.array([0.0, 0.0, 1.0]),
    )
    estimator = MinimalAttitudeEstimator()

    observation = estimator.initialize(
        _measurement(0.0, specific_force=specific_force)
    )
    estimated_up_body = rotate_world_to_body(
        observation.state.attitude_wb,
        np.array([0.0, 0.0, 1.0]),
    )

    np.testing.assert_allclose(estimated_up_body, specific_force / GRAVITY, atol=1e-12)


def test_constant_angular_rate_follows_explicit_quaternion_propagation() -> None:
    dt_s = 0.01
    omega = np.array([0.2, -0.1, 0.3])
    estimator = MinimalAttitudeEstimator(gravity_correction_gain_s=0.0)
    estimator.initialize(_measurement(0.0))

    expected = np.array([1.0, 0.0, 0.0, 0.0])
    for index in range(1, 6):
        expected = expected + dt_s * quaternion_derivative(expected, omega)
        expected = expected / np.linalg.norm(expected)
        observation = estimator.update(
            _measurement(index * dt_s, gyro=omega)
        )
        np.testing.assert_allclose(observation.state.attitude_wb, expected, atol=1e-14)
        np.testing.assert_allclose(observation.state.angular_velocity_body_radps, omega)


def test_quaternion_norm_remains_unit_after_propagation_and_correction() -> None:
    estimator = MinimalAttitudeEstimator()
    estimator.initialize(_measurement(0.0))

    for index in range(1, 101):
        observation = estimator.update(
            _measurement(index * 0.01, gyro=np.array([0.1, -0.05, 0.02]))
        )
        assert quaternion_norm(observation.state.attitude_wb) == pytest.approx(1.0)


def test_small_attitude_disturbance_has_gravity_recovery_trend() -> None:
    disturbed_attitude = quaternion_from_euler_zyx(0.2, 0.0, 0.0)
    disturbed_force = GRAVITY * rotate_world_to_body(
        disturbed_attitude,
        np.array([0.0, 0.0, 1.0]),
    )
    estimator = MinimalAttitudeEstimator(gravity_correction_gain_s=4.0)
    estimator.initialize(_measurement(0.0))

    errors: list[float] = []
    for index in range(1, 21):
        observation = estimator.update(
            _measurement(index * 0.01, specific_force=disturbed_force)
        )
        estimated_up = rotate_world_to_body(
            observation.state.attitude_wb,
            np.array([0.0, 0.0, 1.0]),
        )
        errors.append(float(np.linalg.norm(estimated_up - disturbed_force / GRAVITY)))

    assert errors[-1] < errors[0]


def test_reset_clears_attitude_history_for_identical_replay() -> None:
    estimator = MinimalAttitudeEstimator(gravity_correction_gain_s=0.0)
    estimator.initialize(_measurement(0.0))
    estimator.update(_measurement(0.01, gyro=np.array([0.2, 0.0, 0.0])))
    estimator.reset()

    replay = estimator.initialize(_measurement(0.0))

    np.testing.assert_allclose(replay.state.attitude_wb, [1.0, 0.0, 0.0, 0.0])
    np.testing.assert_allclose(replay.state.angular_velocity_body_radps, np.zeros(3))


def test_bias_state_is_private_and_deterministic() -> None:
    first = MinimalAttitudeEstimator(gravity_correction_gain_s=0.0)
    second = MinimalAttitudeEstimator(gravity_correction_gain_s=0.0)
    sequence = [
        _measurement(0.0),
        _measurement(0.01, gyro=np.array([0.1, 0.2, -0.1])),
        _measurement(0.02, gyro=np.array([0.1, 0.2, -0.1])),
    ]

    first_outputs = [
        first.initialize(sequence[0]),
        first.update(sequence[1]),
        first.update(sequence[2]),
    ]
    second_outputs = [
        second.initialize(sequence[0]),
        second.update(sequence[1]),
        second.update(sequence[2]),
    ]

    assert not hasattr(first_outputs[-1], "gyro_bias_body_radps")
    for left, right in zip(first_outputs, second_outputs, strict=True):
        np.testing.assert_array_equal(left.state.as_vector(), right.state.as_vector())


def test_out_of_order_timestamp_does_not_commit_a_new_state() -> None:
    estimator = MinimalAttitudeEstimator(gravity_correction_gain_s=0.0)
    estimator.initialize(_measurement(1.0))
    before = estimator.update(_measurement(1.1, gyro=np.array([0.1, 0.0, 0.0])))

    with pytest.raises(EstimatorContractError, match="strictly greater"):
        estimator.update(_measurement(1.05, gyro=np.array([0.5, 0.0, 0.0])))

    after = estimator.update(_measurement(1.2, gyro=np.array([0.1, 0.0, 0.0])))
    assert after.timestamp > before.timestamp
