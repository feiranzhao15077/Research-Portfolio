"""Sensor interface and the V1 ideal IMU (Phase 6-1).

THE FROZEN PRINCIPLE
--------------------
**A controller must never read the true state.** Its only state input is a *measurement*,
produced here:

    true state -> sensor model (bias, noise, sample/hold) -> measurement -> controller

In V1 the noise parameters are zero, so the measurement equals the truth and the chain is
numerically transparent. The **structure** is nonetheless the sensor chain, so that
switching robustness on is a parameter change rather than a redesign of the control loop.
Implementing the sensors as a pass-through that the controller reads directly would make
that switch a refactor - and would quietly establish "the controller sees truth" as the
architecture.

THE PARAMETERS ALREADY EXIST
----------------------------
``configs/quad_params.yaml`` already carries ``estimation.imu_gyro_noise_std_radps``,
``imu_accel_noise_std_mps2`` and ``imu_bias_random_walk`` (all ``0.0`` in the baseline),
so enabling noise needs **no schema change and no new key**. What is missing is an
estimator, so read the current state honestly: V1 is "ideal sensors plus optional noise",
**not** "has an estimator".

TWO DISCIPLINES THAT ARE EASY TO GET WRONG
------------------------------------------
1. **The accelerometer measures specific force**, ``R_wb^T (a_true - g_w)``, expressed in
   Body FLU - not absolute acceleration. Getting this wrong flips the sign of gravity in
   the measurement, which is the classic IMU error and looks plausible in a plot.
2. **A zero standard deviation must not consume random numbers.** If it did, the same seed
   would produce different noise sequences depending on an unrelated setting, and two runs
   that should be comparable would silently diverge.

WHAT PHASE 6 MUST FILL IN
-------------------------
GPS / magnetometer, measurement delay and dropout, and the estimator itself. The bias
random walk is implemented here because it is pure bookkeeping with no physics content.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

import numpy as np

from core.contracts.measurement import ImuMeasurement

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.state import QuadState

__all__ = ["IdealImu", "ImuMeasurement", "SensorModelProtocol"]

_VECTOR_SIZE = 3


def _validate_time_s(time_s: float) -> float:
    """Return a valid simulation timestamp without consulting a host clock."""
    if not np.isfinite(time_s) or time_s < 0.0:
        raise ValueError(f"time_s must be finite and >= 0, got {time_s!r}")
    return float(time_s)


@runtime_checkable
class SensorModelProtocol(Protocol):
    """What the scheduler requires of the sensor layer.

    ``acceleration_world_mps2`` is supplied explicitly because the 13-dimensional state
    (C-5) carries position, velocity, attitude and angular velocity - **not** linear
    acceleration, and ``QuadState`` deliberately exposes no such property. The scheduler
    has the value to hand from the state derivative, so the sensor takes it as an argument
    rather than reaching for a state field that does not exist. Omitting it means "not
    accelerating", which is the honest default for a hover scenario and lets a gyro-only
    test run without a dynamics layer at all.
    """

    def sample(
        self,
        *,
        state: QuadState,
        time_s: float,
        acceleration_world_mps2: np.ndarray | None = None,
    ) -> ImuMeasurement:
        """Produce the measurement a controller would receive at ``time_s``."""


class IdealImu:
    """Gyro + accelerometer with optional bias random walk and Gaussian noise.

    With the baseline parameters (all standard deviations ``0.0``) this is numerically
    identical to the true state, which is what makes V1 comparable with the verification
    layer's ideal feedback. It is still a *sensor model*: the controller receives an
    :class:`ImuMeasurement`, never a ``QuadState``.

    RANDOMNESS
    ----------
    The generator is local and seeded from ``SimulationParams.random_seed``; global RNG
    state is never touched, so two runs cannot influence each other through it. The bias is
    this object's own state and can be reset explicitly, which is what a "recalibrate
    mid-run" scenario will need.
    """

    def __init__(
        self,
        *,
        gyro_noise_std_radps: float = 0.0,
        accel_noise_std_mps2: float = 0.0,
        bias_random_walk: float = 0.0,
        gravity_mps2: float = 9.80665,
        random_seed: int = 0,
    ) -> None:
        for name, value in (
            ("gyro_noise_std_radps", gyro_noise_std_radps),
            ("accel_noise_std_mps2", accel_noise_std_mps2),
            ("bias_random_walk", bias_random_walk),
        ):
            if not np.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and >= 0, got {value!r}")
        if not np.isfinite(gravity_mps2) or gravity_mps2 <= 0.0:
            raise ValueError(f"gravity_mps2 must be finite and > 0, got {gravity_mps2!r}")

        self._gyro_std = float(gyro_noise_std_radps)
        self._accel_std = float(accel_noise_std_mps2)
        self._bias_walk = float(bias_random_walk)
        self._gravity = float(gravity_mps2)
        self._generator = np.random.default_rng(random_seed)
        self._gyro_bias = np.zeros(_VECTOR_SIZE, dtype=float)

    # ------------------------------------------------------------------ access
    @property
    def gyro_bias_radps(self) -> np.ndarray:
        """Current gyro bias estimate - the sensor's state, never the controller's."""
        frozen = self._gyro_bias.copy()
        frozen.setflags(write=False)
        return frozen

    @property
    def noise_enabled(self) -> bool:
        """Whether any stochastic term is active; ``False`` means the sensor is transparent."""
        return self._gyro_std > 0.0 or self._accel_std > 0.0 or self._bias_walk > 0.0

    # ------------------------------------------------------------------ sample
    def sample(
        self,
        *,
        state: QuadState,
        time_s: float,
        acceleration_world_mps2: np.ndarray | None = None,
    ) -> ImuMeasurement:
        """Measure the true state, adding the bias and any configured noise.

        ``acceleration_world_mps2`` is the vehicle's true linear acceleration, which the
        caller obtains from the state derivative; see :class:`SensorModelProtocol` for why
        it is not read from the state. When omitted it is taken as zero.
        """
        sample_time_s = _validate_time_s(time_s)
        if acceleration_world_mps2 is None:
            true_acceleration = np.zeros(_VECTOR_SIZE, dtype=float)
        else:
            true_acceleration = np.asarray(acceleration_world_mps2, dtype=float)
            if true_acceleration.shape != (_VECTOR_SIZE,):
                raise ValueError(
                    f"acceleration_world_mps2 must have {_VECTOR_SIZE} components, "
                    f"got shape {true_acceleration.shape}"
                )
            if not np.all(np.isfinite(true_acceleration)):
                raise ValueError("acceleration_world_mps2 contains a non-finite component")

        # The accelerometer reads specific force, so gravity is removed in the world frame
        # and the result rotated into the body frame. Doing this in the wrong order (or not
        # at all) is the classic IMU sign error.
        gravity_world = np.array([0.0, 0.0, -self._gravity], dtype=float)
        specific_force_world = true_acceleration - gravity_world
        specific_force_body = state.rotation_wb.T @ specific_force_world

        gyro = np.asarray(state.angular_velocity_body_radps, dtype=float) + self._gyro_bias
        accel = specific_force_body

        # A zero standard deviation must not consume randomness, or the same seed would
        # yield different sequences depending on an unrelated setting.
        if self._gyro_std > 0.0:
            gyro = gyro + self._generator.normal(0.0, self._gyro_std, size=_VECTOR_SIZE)
        if self._accel_std > 0.0:
            accel = accel + self._generator.normal(0.0, self._accel_std, size=_VECTOR_SIZE)

        return ImuMeasurement(
            time_s=sample_time_s,
            angular_velocity_body_radps=gyro,
            specific_force_body_mps2=accel,
        )

    def advance_bias(self, *, dt_s: float) -> None:
        """Advance bias over explicit simulation time; do not advance it while sampling.

        A disabled random walk is a deterministic no-op and consumes no random numbers.
        ``dt_s`` is still validated so an invalid simulation step cannot be hidden by a
        zero baseline parameter.
        """
        if not np.isfinite(dt_s) or dt_s <= 0.0:
            raise ValueError(f"dt_s must be finite and > 0, got {dt_s!r}")
        if self._bias_walk > 0.0:
            step = self._bias_walk * np.sqrt(dt_s)
            self._gyro_bias = self._gyro_bias + self._generator.normal(
                0.0, step, size=_VECTOR_SIZE
            )

    def reset(self) -> None:
        """Clear bias without rewinding RNG state.

        This represents an explicit re-calibration event. Replaying an entire simulation
        means constructing a new model with the same seed, not calling ``reset``.
        """
        self._gyro_bias = np.zeros(_VECTOR_SIZE, dtype=float)
