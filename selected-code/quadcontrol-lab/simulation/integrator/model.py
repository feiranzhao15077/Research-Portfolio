"""The formal integrator: a pure state-advance function (Phase 6-1).

WHY THIS IS A SEPARATE MODULE, AND NOT THE VERIFICATION RK4
-----------------------------------------------------------
Ruling ``P3B3C-O-1`` is closed with "RK4 remains verification-only", and **D-054①** adds
that Phase 6 must implement the real integrator **independently**: lifting the harness
loop would carry a verification shortcut into the product. So this module exists on its
own, and:

* it does **not** import ``experiments/`` (nor may it ever);
* it makes **no quadrotor assumption** - it advances whatever the caller supplies as
  ``dx/dt = f(x)``, and knows nothing about quaternions, thrust or torque;
* its behaviour is pinned by ``SimulationParams`` (method, step policy, renormalisation),
  which the caller injects.

The verification RK4 and this integrator may agree numerically on a scenario. That would
be reassuring but is **not** the point: the point is that they can disagree, because they
are different code. A shared implementation could not disagree with itself.

IMPLEMENTED POLICIES
--------------------
:class:`ExplicitEulerIntegrator` remains the transparent first-order policy used to test
the Phase 6-1 plumbing. :class:`RungeKutta4Integrator` is the formal fixed-step policy
selected by the frozen baseline. Both consume an injected derivative and contain no
quadrotor dynamics, controller, scheduler or file access. Quaternion renormalisation is
an explicit numerical guard after the state advance; the pre-normalisation drift remains
observable in :class:`IntegratorStepResult` and is not presented as a correction to the
underlying model.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np

__all__ = [
    "STATE_DIMENSION",
    "DerivativeFn",
    "ExplicitEulerIntegrator",
    "IntegratorProtocol",
    "IntegratorStepResult",
    "RungeKutta4Integrator",
]

#: Length of the state vector ``[p_w, v_w, q_wb, omega_b]`` (13, C-5).
STATE_DIMENSION = 13

#: The state derivative as a plain vector. A callable rather than ``(x, t)`` because
#: fixed-step integration of an autonomous system needs no explicit time argument; a
#: time-varying force enters through the closure, which keeps this signature honest about
#: what the integrator actually consumes.
DerivativeFn = Callable[[np.ndarray], np.ndarray]


@dataclass(frozen=True, slots=True)
class IntegratorStepResult:
    """One advance: the new state plus the observables a log must be able to record.

    ``quaternion_norm_error_before`` is the drift measured **before** any renormalisation,
    because that is the quantity ``M-06`` exists to make observable; reporting only the
    post-normalisation error would hide exactly the numerical degradation the monitor is
    for.
    """

    state: np.ndarray
    quaternion_norm_error_before: float
    quaternion_norm_error_after: float

    def __post_init__(self) -> None:
        frozen = np.array(np.asarray(self.state, dtype=float), dtype=float, copy=True)
        frozen.setflags(write=False)
        object.__setattr__(self, "state", frozen)


@runtime_checkable
class IntegratorProtocol(Protocol):
    """What the scheduler requires of an integrator.

    Kept minimal on purpose: the scheduler only ever needs "advance this state by this
    period". Anything else (adaptivity, event detection) is not in V1 and adding it here
    would let the scheduler depend on machinery it does not use.
    """

    def step(
        self, *, derivative: DerivativeFn, state: np.ndarray, dt_s: float
    ) -> IntegratorStepResult:
        """Advance ``state`` by ``dt_s``."""


def _validated_state(state: np.ndarray) -> np.ndarray:
    vector = np.array(np.asarray(state, dtype=float), dtype=float, copy=True)
    if vector.shape != (STATE_DIMENSION,):
        raise ValueError(
            f"state must have {STATE_DIMENSION} components (C-5), got shape {vector.shape}"
        )
    if not np.all(np.isfinite(vector)):
        raise ValueError("state must contain only finite values")
    if float(np.linalg.norm(vector[6:10])) <= 0.0:
        raise ValueError("state quaternion must have non-zero norm")
    return vector


def _validated_dt(dt_s: float) -> float:
    dt = float(dt_s)
    if not np.isfinite(dt) or dt <= 0.0:
        raise ValueError(f"dt_s must be positive and finite, got {dt_s!r}")
    return dt


def _evaluate_derivative(
    derivative: DerivativeFn, state: np.ndarray, *, stage: str
) -> np.ndarray:
    slope = np.array(
        np.asarray(derivative(np.array(state, dtype=float, copy=True)), dtype=float),
        dtype=float,
        copy=True,
    )
    if slope.shape != (STATE_DIMENSION,):
        raise ValueError(
            f"the derivative must have {STATE_DIMENSION} components at {stage}, "
            f"got shape {slope.shape}"
        )
    if not np.all(np.isfinite(slope)):
        raise ValueError(f"the derivative must contain only finite values at {stage}")
    return slope


def _step_result(advanced: np.ndarray, *, renormalise_quaternion: bool) -> IntegratorStepResult:
    if not np.all(np.isfinite(advanced)):
        raise ValueError("the advanced state must contain only finite values")

    quaternion = advanced[6:10]
    norm = float(np.linalg.norm(quaternion))
    before = abs(norm - 1.0)
    if renormalise_quaternion:
        if norm <= 0.0:
            raise ValueError("the advanced quaternion has zero norm and cannot be normalised")
        advanced[6:10] = quaternion / norm
    after = float(abs(np.linalg.norm(advanced[6:10]) - 1.0))

    return IntegratorStepResult(
        state=advanced,
        quaternion_norm_error_before=before,
        quaternion_norm_error_after=after,
    )


class ExplicitEulerIntegrator:
    """First-order forward Euler: ``x_{k+1} = x_k + dt * f(x_k)``.

    Deliberately minimal and deliberately not the intended production method. Its value in
    Phase 6-1 is that it is the one policy whose behaviour is verifiable by hand, so the
    *plumbing* (clock, scheduler, state shape, drift reporting) can be tested before the
    numerics are chosen. The numerical method is a decision for the implementation phase,
    guided by ``SimulationParams.integrator_method``.
    """

    def __init__(self, *, renormalise_quaternion: bool = True) -> None:
        self._renormalise = bool(renormalise_quaternion)

    @property
    def renormalise_quaternion(self) -> bool:
        return self._renormalise

    def step(
        self, *, derivative: DerivativeFn, state: np.ndarray, dt_s: float
    ) -> IntegratorStepResult:
        vector = _validated_state(state)
        dt = _validated_dt(dt_s)
        slope = _evaluate_derivative(derivative, vector, stage="Euler")
        advanced = vector + dt * slope
        return _step_result(
            advanced,
            renormalise_quaternion=self._renormalise,
        )


class RungeKutta4Integrator:
    """Classical four-stage fixed-step Runge-Kutta integrator.

    The implementation is a general numerical state advance over an injected autonomous
    derivative. It does not know how the derivative was produced and therefore contains
    no dynamics, motor, controller, scheduler or timing policy. The caller owns ``dt_s``.

    Quaternion renormalisation is applied only after the complete RK4 advance. The
    unnormalised result is measured first so numerical drift remains visible to logs and
    tests rather than being hidden by the guard.
    """

    def __init__(self, *, renormalise_quaternion: bool = True) -> None:
        self._renormalise = bool(renormalise_quaternion)

    @property
    def renormalise_quaternion(self) -> bool:
        return self._renormalise

    def step(
        self, *, derivative: DerivativeFn, state: np.ndarray, dt_s: float
    ) -> IntegratorStepResult:
        vector = _validated_state(state)
        dt = _validated_dt(dt_s)

        k1 = _evaluate_derivative(derivative, vector, stage="RK4 k1")
        k2 = _evaluate_derivative(derivative, vector + 0.5 * dt * k1, stage="RK4 k2")
        k3 = _evaluate_derivative(derivative, vector + 0.5 * dt * k2, stage="RK4 k3")
        k4 = _evaluate_derivative(derivative, vector + dt * k3, stage="RK4 k4")

        advanced = vector + (dt / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)
        return _step_result(
            advanced,
            renormalise_quaternion=self._renormalise,
        )
