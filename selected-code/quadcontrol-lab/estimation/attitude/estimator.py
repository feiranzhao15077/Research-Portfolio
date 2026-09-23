"""Minimal deterministic quaternion attitude estimator.

This module intentionally estimates attitude and body angular velocity only. It consumes
the shared IMU measurement contract and never reads plant truth, simulation modules, or
controller modules.
"""

from __future__ import annotations

from math import atan2, hypot, isfinite

import numpy as np

from core.contracts import (
    ControllerObservation,
    FieldValidity,
    ImuMeasurement,
    ObservationProviderStatus,
    ObservationSource,
    ObservationValidity,
)
from core.state import QuadState
from core.state.quaternion import (
    quaternion_derivative,
    quaternion_from_euler_zyx,
    quaternion_multiply,
    quaternion_norm,
    rotate_world_to_body,
)
from estimation.model import EstimatorContractError

__all__ = ["MinimalAttitudeEstimator"]

_VECTOR_SIZE = 3
_GRAVITY_AXIS_WORLD = np.array([0.0, 0.0, 1.0], dtype=float)
_PARTIAL_VALIDITY = ObservationValidity(
    position=FieldValidity.INVALID,
    velocity=FieldValidity.INVALID,
    attitude=FieldValidity.VALID,
    angular_velocity=FieldValidity.VALID,
)


class MinimalAttitudeEstimator:
    """Explicit-lifecycle gyro propagator with gravity-direction correction.

    The state is ``q_wb = [w, x, y, z]`` plus a private gyro-bias vector. The bias
    adaptation law is deliberately absent in this minimal implementation; the private
    estimate is initialized to zero and cleared by :meth:`reset`. Acceleration is used
    only as a gravity-direction cue and is never integrated into position or velocity.

    The ``QuadState`` in the returned observation is a structural carrier. Its position
    and velocity fields are marked ``INVALID`` and are never exposed by
    ``ObservationAdapter``. They are not truth, estimates, or benchmark values.
    """

    def __init__(
        self,
        *,
        gravity_mps2: float = 9.80665,
        gravity_tolerance_mps2: float = 0.5,
        gravity_correction_gain_s: float = 3.0,
    ) -> None:
        self._gravity_mps2 = self._validate_positive(
            gravity_mps2, "gravity_mps2", allow_zero=False
        )
        self._gravity_tolerance_mps2 = self._validate_non_negative(
            gravity_tolerance_mps2, "gravity_tolerance_mps2"
        )
        self._gravity_correction_gain_s = self._validate_non_negative(
            gravity_correction_gain_s, "gravity_correction_gain_s"
        )
        self._quaternion_wb: np.ndarray | None = None
        self._gyro_bias_body_radps = np.zeros(_VECTOR_SIZE, dtype=float)
        self._previous_timestamp_s: float | None = None
        self._status = ObservationProviderStatus.UNINITIALIZED

    @property
    def status(self) -> ObservationProviderStatus:
        """Return ``UNINITIALIZED`` or ``READY`` for this minimal estimator."""
        return self._status

    def initialize(
        self,
        measurement: ImuMeasurement,
        time_s: float | None = None,
    ) -> ControllerObservation:
        """Initialize explicitly from one gravity-dominated IMU measurement.

        No gyro integration occurs during initialization because there is no previous
        timestamp. Roll and pitch are aligned to the measured gravity direction; yaw is
        set to the explicit zero-yaw datum because accelerometer gravity does not observe
        absolute heading.
        """
        if self._status is not ObservationProviderStatus.UNINITIALIZED:
            raise EstimatorContractError(
                "initialize requires UNINITIALIZED status; call reset before reinitializing"
            )
        timestamp = self._validate_measurement(measurement, time_s)
        specific_force = self._specific_force(measurement)
        force_norm = float(np.linalg.norm(specific_force))
        self._require_gravity_dominated(force_norm)

        self._quaternion_wb = self._gravity_aligned_quaternion(specific_force)
        self._previous_timestamp_s = timestamp
        self._status = ObservationProviderStatus.READY
        return self._make_observation(
            timestamp=timestamp,
            angular_velocity_body_radps=self._corrected_gyro(measurement),
        )

    def update(
        self,
        measurement: ImuMeasurement,
        time_s: float | None = None,
    ) -> ControllerObservation:
        """Propagate and gravity-correct one strictly newer IMU measurement.

        Propagation is explicit first-order quaternion integration followed by numerical
        normalization. The correction is a bounded body-frame gravity-direction update;
        it does not estimate translation or velocity.
        """
        if self._status is not ObservationProviderStatus.READY:
            raise EstimatorContractError(
                "update requires explicit initialize() while status is UNINITIALIZED"
            )
        timestamp = self._validate_measurement(measurement, time_s)
        assert self._previous_timestamp_s is not None
        dt_s = timestamp - self._previous_timestamp_s
        if not isfinite(dt_s) or dt_s <= 0.0:
            raise EstimatorContractError(
                "measurement timestamp must be strictly greater than the previous timestamp"
            )
        assert self._quaternion_wb is not None

        corrected_gyro = self._corrected_gyro(measurement)
        derivative = quaternion_derivative(self._quaternion_wb, corrected_gyro)
        propagated = self._quaternion_wb + dt_s * derivative
        propagated = self._normalize_quaternion(propagated)

        specific_force = self._specific_force(measurement)
        force_norm = float(np.linalg.norm(specific_force))
        corrected = self._apply_gravity_correction(
            propagated,
            specific_force,
            force_norm,
            dt_s,
        )

        # Commit only after every validation and numerical operation succeeds.
        self._quaternion_wb = corrected
        self._previous_timestamp_s = timestamp
        return self._make_observation(
            timestamp=timestamp,
            angular_velocity_body_radps=corrected_gyro,
        )

    def reset(self) -> None:
        """Clear quaternion, bias, timestamp, and lifecycle state."""
        self._quaternion_wb = None
        self._gyro_bias_body_radps = np.zeros(_VECTOR_SIZE, dtype=float)
        self._previous_timestamp_s = None
        self._status = ObservationProviderStatus.UNINITIALIZED

    def _validate_measurement(
        self,
        measurement: ImuMeasurement,
        time_s: float | None,
    ) -> float:
        if not isinstance(measurement, ImuMeasurement):
            raise EstimatorContractError("measurement must be an ImuMeasurement")
        measurement_time = float(measurement.time_s)
        if not isfinite(measurement_time) or measurement_time < 0.0:
            raise EstimatorContractError("measurement.timestamp must be finite and non-negative")
        if time_s is not None:
            if isinstance(time_s, bool) or not isinstance(time_s, (int, float)):
                raise EstimatorContractError("time_s must match measurement.time_s")
            supplied_time = float(time_s)
            if not isfinite(supplied_time) or supplied_time != measurement_time:
                raise EstimatorContractError(
                    "time_s must equal measurement.time_s; measurement time is authoritative"
                )
        return measurement_time

    @staticmethod
    def _specific_force(measurement: ImuMeasurement) -> np.ndarray:
        specific_force = np.asarray(measurement.specific_force_body_mps2, dtype=float)
        if specific_force.shape != (_VECTOR_SIZE,) or not np.all(np.isfinite(specific_force)):
            raise EstimatorContractError("specific force must be a finite Body FLU vector")
        force = np.array(specific_force, dtype=float, copy=True)
        if float(np.linalg.norm(force)) <= 0.0:
            raise EstimatorContractError("specific force norm must be positive")
        return force

    def _corrected_gyro(self, measurement: ImuMeasurement) -> np.ndarray:
        gyro = np.asarray(measurement.angular_velocity_body_radps, dtype=float)
        if gyro.shape != (_VECTOR_SIZE,) or not np.all(np.isfinite(gyro)):
            raise EstimatorContractError("angular velocity must be a finite Body FLU vector")
        return np.array(gyro - self._gyro_bias_body_radps, dtype=float, copy=True)

    def _require_gravity_dominated(self, force_norm: float) -> None:
        if abs(force_norm - self._gravity_mps2) > self._gravity_tolerance_mps2:
            raise EstimatorContractError(
                "initialization requires a gravity-dominated specific-force measurement"
            )

    def _gravity_aligned_quaternion(self, specific_force: np.ndarray) -> np.ndarray:
        unit_force = specific_force / float(np.linalg.norm(specific_force))
        roll = atan2(float(unit_force[1]), float(unit_force[2]))
        pitch = atan2(
            float(-unit_force[0]),
            hypot(float(unit_force[1]), float(unit_force[2])),
        )
        # Yaw is explicitly zero: gravity cannot observe absolute heading.
        return self._normalize_quaternion(quaternion_from_euler_zyx(roll, pitch, 0.0))

    def _apply_gravity_correction(
        self,
        quaternion_wb: np.ndarray,
        specific_force: np.ndarray,
        force_norm: float,
        dt_s: float,
    ) -> np.ndarray:
        if abs(force_norm - self._gravity_mps2) > self._gravity_tolerance_mps2:
            # Translational acceleration is not treated as a gravity observation.
            return quaternion_wb

        measured_up_body = specific_force / force_norm
        predicted_up_body = rotate_world_to_body(quaternion_wb, _GRAVITY_AXIS_WORLD)
        alignment_error_body = np.cross(measured_up_body, predicted_up_body)
        correction_fraction = min(1.0, self._gravity_correction_gain_s * dt_s)
        correction_vector = correction_fraction * alignment_error_body
        delta_quaternion = self._normalize_quaternion(
            np.concatenate(([1.0], 0.5 * correction_vector))
        )
        return self._normalize_quaternion(
            quaternion_multiply(quaternion_wb, delta_quaternion)
        )

    def _make_observation(
        self,
        *,
        timestamp: float,
        angular_velocity_body_radps: np.ndarray,
    ) -> ControllerObservation:
        assert self._quaternion_wb is not None
        # QuadState is a structural carrier. Invalid position/velocity values are not
        # estimates and ObservationAdapter refuses to expose this partial observation.
        carrier = QuadState(
            position_world_m=np.zeros(_VECTOR_SIZE, dtype=float),
            velocity_world_mps=np.zeros(_VECTOR_SIZE, dtype=float),
            attitude_wb=np.array(self._quaternion_wb, dtype=float, copy=True),
            angular_velocity_body_radps=np.array(
                angular_velocity_body_radps,
                dtype=float,
                copy=True,
            ),
        )
        return ControllerObservation(
            state=carrier,
            timestamp=timestamp,
            validity=False,
            source=ObservationSource.ESTIMATED,
            field_validity=_PARTIAL_VALIDITY,
        )

    @staticmethod
    def _normalize_quaternion(quaternion: np.ndarray) -> np.ndarray:
        norm = quaternion_norm(quaternion)
        if not isfinite(norm) or norm <= 0.0:
            raise EstimatorContractError("quaternion normalization requires a finite non-zero norm")
        return np.array(quaternion / norm, dtype=float, copy=True)

    @staticmethod
    def _validate_positive(value: float, name: str, *, allow_zero: bool) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{name} must be a real scalar")
        result = float(value)
        if not isfinite(result) or (result < 0.0 if allow_zero else result <= 0.0):
            comparator = ">= 0" if allow_zero else "> 0"
            raise ValueError(f"{name} must be finite and {comparator}")
        return result

    @staticmethod
    def _validate_non_negative(value: float, name: str) -> float:
        return MinimalAttitudeEstimator._validate_positive(value, name, allow_zero=True)
