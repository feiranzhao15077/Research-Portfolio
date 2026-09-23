"""Quadrotor rigid-body dynamics - the frozen state equation.

Implements ``docs/theory/rigid_body_dynamics.md`` formulas D-5, D-6, D-11, D-12,
D-20 and the frozen quaternion kinematics C-19:

    pdot      = v
    vdot      = (1/m) ( T R_wb[:,2] + m g_w + f_d )
    qdot_wb   = 0.5 q_wb (x) [0, omega_b]
    omegadot  = I^-1 ( tau_b - omega_b x (I omega_b) + tau_d )

This module is a **pure function of its arguments**: it reads no file, holds no
state, and performs no integration (the integrator is a separate concern and is not
part of Phase 2). Everything it needs arrives through ``params`` and the explicit
physical inputs, which is what makes the hover / free-fall / conservation
invariants testable in isolation.

Inputs and outputs are always ``float64`` and always in the frozen frames:
``p``/``v``/``f_d`` in World ENU, ``q_wb`` body -> world, ``omega_b``/``tau`` in
Body FLU (``docs/theory/coordinate_convention.md``).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from core.params import QuadParams
from core.state import (
    STATE_DIMENSION,
    QuadState,
    quaternion_derivative,
    skew_symmetric,
)

__all__ = [
    "StateDerivative",
    "hover_collective_thrust_N",
    "inertia_tensor_body_kgm2",
    "state_derivative",
]

_GRAVITY_DIRECTION_WORLD = np.array([0.0, 0.0, -1.0])


@dataclass(frozen=True, slots=True)
class StateDerivative:
    """Time derivative of the 13-dimensional state (formula D-17).

    Each field carries its unit and frame in the name, matching the state
    convention (``docs/theory/state_definition.md``):

    * ``position_dot_world_mps`` - ``pdot``, World ENU, m/s
    * ``velocity_dot_world_mps2`` - ``vdot``, World ENU, m/s^2
    * ``attitude_dot_wb_per_s`` - ``qdot_wb``, 1/s
    * ``angular_velocity_dot_body_radps2`` - ``omegadot_b``, Body FLU, rad/s^2
    """

    position_dot_world_mps: np.ndarray
    velocity_dot_world_mps2: np.ndarray
    attitude_dot_wb_per_s: np.ndarray
    angular_velocity_dot_body_radps2: np.ndarray

    def as_vector(self) -> np.ndarray:
        """The derivative in the frozen ``[p, v, q, omega]`` component order (S-1)."""
        return np.concatenate(
            (
                self.position_dot_world_mps,
                self.velocity_dot_world_mps2,
                self.attitude_dot_wb_per_s,
                self.angular_velocity_dot_body_radps2,
            )
        )


def inertia_tensor_body_kgm2(params: QuadParams) -> np.ndarray:
    """The inertia tensor about the CoM, Body FLU, kg*m^2 (formula D-1).

    ``I = [[Ixx, -Ixy, -Ixz], [-Ixy, Iyy, -Iyz], [-Ixz, -Iyz, Izz]]``

    The minus signs are part of the tensor definition, and ``configs/quad_params.yaml``
    stores the *products* ``Ixy``/``Ixz``/``Iyz`` themselves, so they must not be
    negated a second time.
    """
    inertia = params.body.inertia_kgm2
    products = params.body.inertia_products_kgm2
    return np.array(
        [
            [inertia.Ixx, -products.Ixy, -products.Ixz],
            [-products.Ixy, inertia.Iyy, -products.Iyz],
            [-products.Ixz, -products.Iyz, inertia.Izz],
        ],
        dtype=float,
    )


def _gravity_world_mps2(params: QuadParams) -> np.ndarray:
    """``g_w = [0, 0, -g]`` in World ENU (formula C-2).

    Gravity is read from the parameter set: no module may hardcode it
    (``docs/parameter_management.md`` §3).
    """
    return _GRAVITY_DIRECTION_WORLD * params.environment.gravity_mps2


def hover_collective_thrust_N(params: QuadParams) -> float:
    """Collective thrust required to hover, ``T = m g`` (formula D-20)."""
    return params.body.mass_kg * params.environment.gravity_mps2


def state_derivative(
    *,
    state: QuadState,
    params: QuadParams,
    total_thrust_N: float,
    torque_body_Nm: np.ndarray,
    force_dist_world_N: np.ndarray | None = None,
    torque_dist_body_Nm: np.ndarray | None = None,
) -> StateDerivative:
    """Evaluate ``xdot = f(x, u, d)`` (formula D-17).

    Parameters
    ----------
    state:
        Current 13-dimensional state.
    params:
        Validated vehicle parameters (mass, inertia, gravity). Injected, never
        read from disk here (rule A7).
    total_thrust_N:
        Collective rotor thrust, acting along ``+z_b`` (formula C-4), N.
    torque_body_Nm:
        Aerodynamic body torque produced by the rotors, Body FLU, N*m
        (formula D-14). Any feedforward or compensation is the caller's concern.
    force_dist_world_N:
        External disturbance force, World ENU, N (assumption A-05 uses zeros).
    torque_dist_body_Nm:
        External disturbance torque, Body FLU, N*m.

    Returns
    -------
    StateDerivative
        The derivative in the frozen component order.
    """
    mass_kg = params.body.mass_kg
    inertia = inertia_tensor_body_kgm2(params)
    omega_b = state.angular_velocity_body_radps

    force_world = total_thrust_N * state.rotation_wb[:, 2] + mass_kg * _gravity_world_mps2(params)
    if force_dist_world_N is not None:
        force_world = force_world + np.asarray(force_dist_world_N, dtype=float)

    total_torque = np.asarray(torque_body_Nm, dtype=float)
    if torque_dist_body_Nm is not None:
        total_torque = total_torque + np.asarray(torque_dist_body_Nm, dtype=float)

    # D-12: omegadot = I^-1 (tau - omega x (I omega)). The cross product is the
    # gyroscopic coupling; dropping it would make wide-angle manoeuvres wrong while
    # leaving every hover test passing, which is exactly why it is tested directly.
    gyroscopic = np.cross(omega_b, inertia @ omega_b)
    angular_acceleration = np.linalg.solve(inertia, total_torque - gyroscopic)

    return StateDerivative(
        position_dot_world_mps=state.velocity_world_mps,
        velocity_dot_world_mps2=force_world / mass_kg,
        attitude_dot_wb_per_s=quaternion_derivative(state.attitude_wb, omega_b),
        angular_velocity_dot_body_radps2=angular_acceleration,
    )


def body_rate_derivative_body_radps2(
    *,
    angular_velocity_body_radps: np.ndarray,
    torque_body_Nm: np.ndarray,
    params: QuadParams,
) -> np.ndarray:
    """Euler-equation right-hand side alone (formula D-12), for unit testing.

    A thin extraction of the rotational part of :func:`state_derivative`, kept so a
    test or a controller can reason about the rotational dynamics without building a
    full :class:`~core.state.QuadState`.
    """
    inertia = inertia_tensor_body_kgm2(params)
    omega_b = np.asarray(angular_velocity_body_radps, dtype=float)
    torque = np.asarray(torque_body_Nm, dtype=float)
    return np.linalg.solve(inertia, torque - np.cross(omega_b, inertia @ omega_b))


def angular_velocity_cross_matrix(angular_velocity_body_radps: np.ndarray) -> np.ndarray:
    """``[omega_b]x``, the matrix form used in formulas C-10 and D-10."""
    return skew_symmetric(np.asarray(angular_velocity_body_radps, dtype=float))


#: The derivative vector has the same frozen length as the state (S-1).
DERIVATIVE_DIMENSION = STATE_DIMENSION
