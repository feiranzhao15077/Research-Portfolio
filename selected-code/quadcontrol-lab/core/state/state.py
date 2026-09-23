"""The frozen 13-dimensional quadrotor state.

``docs/theory/state_definition.md`` freezes the core state as

    x = [p, v, q_wb, omega_b]^T in R^13            (formula S-1)

with

* ``p``  position, **World ENU**, metres                     (S-1)
* ``v``  velocity, **World ENU**, m/s                        (S-1)
* ``q_wb`` attitude quaternion ``[w, x, y, z]``, body -> world (S-1, C-11/C-12)
* ``omega_b`` angular velocity, **Body FLU**, rad/s          (S-1)

The state lives on ``R^3 x R^3 x S^3 x R^3`` -- dimension 12 embedded in R^13
(formula S-3), so the quaternion carries one redundant degree of freedom and must
be renormalised after every integration step (S-2; policy in
``configs/simulation_params.yaml``).

Euler angles, ``v_b``, ``omega_w`` and the altitude are **derived** (S-4..S-7') and
deliberately not stored: caching them would create a second source of truth that
can silently disagree with ``q_wb``.

There is no allocation-outcome field here. The N-8/N-11 rulings (D-024/D-028) put
feasibility information in the mixer's output contract, so adding such a field to
the state would change the frozen state definition (SD-6).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from core.state.errors import StateError
from core.state.quaternion import (
    QUATERNION_SIZE,
    VECTOR_SIZE,
    euler_zyx_from_quaternion,
    is_unit_quaternion,
    quaternion_norm,
    rotation_from_quaternion,
)

__all__ = ["STATE_DIMENSION", "QuadState"]

#: Frozen number of state components (S-1).
STATE_DIMENSION = 13

#: First index of each group inside the frozen state vector (S-1).
POSITION_SLICE = slice(0, 3)
VELOCITY_SLICE = slice(3, 6)
ATTITUDE_SLICE = slice(6, 10)
ANGULAR_VELOCITY_SLICE = slice(10, 13)


def _freeze_vector(value: np.ndarray, size: int, name: str) -> np.ndarray:
    """Validate a vector and return an immutable copy of it.

    Copying matters: without it a caller could keep a reference to the array it
    passed in and mutate the "immutable" state afterwards (contract C2 spirit).
    """
    array = np.asarray(value, dtype=float)
    if array.shape != (size,):
        raise StateError(f"{name} must have shape ({size},), got {array.shape}")
    if not np.all(np.isfinite(array)):
        raise StateError(f"{name} contains a non-finite component")
    frozen = np.array(array, dtype=float, copy=True)
    frozen.setflags(write=False)
    return frozen


@dataclass(frozen=True, slots=True)
class QuadState:
    """Immutable 13-dimensional state in the frozen frame conventions.

    Construction validates shapes and finiteness. A *slightly* non-unit quaternion
    is accepted on purpose - integration always leaves a small radial drift, and
    rejecting it here would make the renormalisation policy unimplementable. The
    drift is exposed through :attr:`quaternion_norm_error` so callers can monitor
    it (M-06) instead of having it hidden.
    """

    position_world_m: np.ndarray
    velocity_world_mps: np.ndarray
    attitude_wb: np.ndarray
    angular_velocity_body_radps: np.ndarray

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "position_world_m",
            _freeze_vector(self.position_world_m, VECTOR_SIZE, "position_world_m"),
        )
        object.__setattr__(
            self,
            "velocity_world_mps",
            _freeze_vector(self.velocity_world_mps, VECTOR_SIZE, "velocity_world_mps"),
        )
        object.__setattr__(
            self,
            "attitude_wb",
            _freeze_vector(self.attitude_wb, QUATERNION_SIZE, "attitude_wb"),
        )
        object.__setattr__(
            self,
            "angular_velocity_body_radps",
            _freeze_vector(
                self.angular_velocity_body_radps, VECTOR_SIZE, "angular_velocity_body_radps"
            ),
        )
        if quaternion_norm(self.attitude_wb) <= 0.0:
            raise StateError(
                "attitude_wb has zero norm: no attitude can be recovered from it (C-13)"
            )

    # ------------------------------------------------------- vector form (S-1)
    @classmethod
    def from_vector(cls, vector: np.ndarray) -> QuadState:
        """Build a state from the frozen 13-component vector ``[p, v, q, omega]``."""
        array = np.asarray(vector, dtype=float).reshape(-1)
        if array.shape != (STATE_DIMENSION,):
            raise StateError(
                f"state vector must have {STATE_DIMENSION} components (S-1), got {array.shape}"
            )
        return cls(
            position_world_m=array[POSITION_SLICE],
            velocity_world_mps=array[VELOCITY_SLICE],
            attitude_wb=array[ATTITUDE_SLICE],
            angular_velocity_body_radps=array[ANGULAR_VELOCITY_SLICE],
        )

    def as_vector(self) -> np.ndarray:
        """The frozen 13-component vector form (S-1)."""
        return np.concatenate(
            (
                self.position_world_m,
                self.velocity_world_mps,
                self.attitude_wb,
                self.angular_velocity_body_radps,
            )
        )

    # ---------------------------------------------- quaternion health (S-2/S-3)
    @property
    def quaternion_norm_error(self) -> float:
        """``| ||q|| - 1 |``: the radial drift that must stay bounded (M-06, S-41)."""
        return abs(quaternion_norm(self.attitude_wb) - 1.0)

    def is_unit_quaternion(self, *, tolerance: float = 1e-9) -> bool:
        """Whether the attitude satisfies the unit-norm constraint (S-2)."""
        return is_unit_quaternion(self.attitude_wb, tolerance=tolerance)

    def normalised(self) -> QuadState:
        """Return the same state with ``q`` renormalised onto ``S^3`` (S-2).

        This is the sanctioned way to remove radial drift. It must be called by the
        integrator, and the drift *before* the call is what
        ``quaternion_norm_warn_threshold`` monitors.
        """
        return QuadState(
            position_world_m=self.position_world_m,
            velocity_world_mps=self.velocity_world_mps,
            attitude_wb=self.attitude_wb / quaternion_norm(self.attitude_wb),
            angular_velocity_body_radps=self.angular_velocity_body_radps,
        )

    # ------------------------------------------------- derived quantities (S-4..S-7')
    @property
    def _unit_attitude(self) -> np.ndarray:
        """The stored attitude renormalised onto ``S^3``.

        The state layer, not the caller, owns the drift policy: ``QuadState``
        validates that the attitude is non-degenerate and is expected to be unit to
        within integration drift. The low-level algebra in
        :mod:`core.state.quaternion` stays strict (it raises for an arbitrary
        non-unit input), because that is a mathematical precondition, whereas here
        the drift is a known numerical artefact that must stay *observable*
        (:attr:`quaternion_norm_error`) instead of blocking the dynamics - the
        integrator has to evaluate the equations of motion before it can
        renormalise.
        """
        return self.attitude_wb / quaternion_norm(self.attitude_wb)

    @property
    def rotation_wb(self) -> np.ndarray:
        """``R_wb``, body -> world, from the stored attitude (formula C-9)."""
        return rotation_from_quaternion(self._unit_attitude)

    @property
    def altitude_m(self) -> float:
        """Altitude ``h = p_z``: the ENU up axis (formula S-7')."""
        return float(self.position_world_m[2])

    @property
    def euler_zyx_rad(self) -> tuple[float, float, float]:
        """ZYX intrinsic Euler angles ``(phi, theta, psi)`` in radians (formula S-4).

        A derived, display-and-input quantity only: it is never part of the state
        and must not be used as one (it has a singularity at ``theta = +-pi/2``).
        """
        return euler_zyx_from_quaternion(self._unit_attitude)

    @property
    def velocity_body_mps(self) -> np.ndarray:
        """``v_b = R_wb^T v``, body-frame velocity (formula S-5)."""
        return self.rotation_wb.T @ self.velocity_world_mps

    @property
    def angular_velocity_world_radps(self) -> np.ndarray:
        """``omega_w = R_wb omega_b`` (formula S-6).

        The dynamics use ``omega_b`` (formula C-19); this form exists for logging
        and for formula C-22. Mixing the two without declaring which is which is a
        classic attitude bug.
        """
        return self.rotation_wb @ self.angular_velocity_body_radps

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        roll, pitch, yaw = self.euler_zyx_rad
        return (
            f"QuadState(p={np.array2string(self.position_world_m, precision=4)}, "
            f"v={np.array2string(self.velocity_world_mps, precision=4)}, "
            f"euler_zyx=({roll:+.3f}, {pitch:+.3f}, {yaw:+.3f}) rad, "
            f"omega_b={np.array2string(self.angular_velocity_body_radps, precision=4)})"
        )


def stationary_state(*, altitude_m: float = 0.0) -> QuadState:
    """A level, motionless state at the given altitude.

    Trivial on purpose: the hover *equilibrium* is a statement about the dynamics
    and motors, not about the state container, so the equilibrium thrust belongs in
    ``core/dynamics`` tests, not here.
    """
    return QuadState(
        position_world_m=np.array([0.0, 0.0, altitude_m]),
        velocity_world_mps=np.zeros(3),
        attitude_wb=np.array([1.0, 0.0, 0.0, 0.0]),
        angular_velocity_body_radps=np.zeros(3),
    )
