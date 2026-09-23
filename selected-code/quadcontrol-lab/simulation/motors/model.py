"""Motor/rotor interface: command -> rotor speed -> thrust and torque (Phase 6-1).

THE CHAIN
---------
    core/mixer        Wrench -> n_cmd = omega^2        (frozen contract, CF-11 / D-022)
    simulation/motors n_cmd -> omega                   (rotor dynamics; THIS module)
    core/motor        omega -> RotorForces             (quasi-static mapping, frozen)
    simulation/dynamics composes the delivered forces into a wrench

``core/motor`` owns the **mapping**; this module owns the **rotor dynamics**. Neither may
do the other's job: restating the mapping would create a second copy of frozen physics,
and putting the lag inside ``core/motor`` would put time into a module that has none.

THE FIRST-ORDER MODEL, AND WHY IT IS DISABLED BY DEFAULT
--------------------------------------------------------
The intended rotor model is ``tau_m * domega/dt = omega_cmd - omega``. It is **supported by
this interface but must default to off**, because:

* **CF-9** froze the quasi-static motor with ``first_order_time_constant_s = 0.0`` as a
  *hard* constraint (rule V13). Enabling a lag by default would contradict a frozen
  ruling on the day the module is created.
* No parameter currently carries a lag constant, and adding one would change
  ``configs/simulation_params.yaml`` and therefore require a ``baseline_id`` bump (SEP-3).

So the lag is a **capability**, switched on by supplying a non-zero :attr:`tau_m_s`. The
robustness scenario "motor delay" (design document §9.2) is what will switch it on.

THE SQUARE ROOT, AND WHERE IT BELONGS
-------------------------------------
The mixer emits ``n = omega^2`` and CF-11 defers the square root to ``core/motor``. But a
lag acts on ``omega``, not on ``omega^2``, so a rotor-dynamics layer must recover
``omega_cmd`` itself. The division of labour frozen here is:

* ``simulation/motors`` converts the **command** (``n_cmd -> omega_cmd``) and integrates;
* ``core/motor`` performs the **physical mapping** (``omega -> thrust``), unchanged.

The two never overlap: one concerns what was asked for, the other what the hardware gives.
Registered as ``P6-0-O-3`` for confirmation.

PHASE 6-3-A SCOPE
-----------------
This module currently defines only the command, state, timing specification, step result
and protocol. It intentionally does **not** implement ``RotorModel`` or advance the
first-order equation. The timing specification exposes the validation that a future
dynamic step must call, while keeping construction independent of an as-yet-unknown
physics period.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import numpy as np

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.motor import SaturationResult
    from core.params import QuadParams

from core.motor import saturate_rotor_speed_squared

__all__ = [
    "FirstOrderRotorModel",
    "MotorCommandError",
    "RotorCommand",
    "RotorDynamicsSpec",
    "RotorModelProtocol",
    "RotorState",
    "RotorStateError",
    "RotorStepResult",
    "RotorTimingError",
]


class MotorCommandError(ValueError):
    """A rotor command that cannot be interpreted (a contract violation)."""


class RotorStateError(ValueError):
    """A rotor state that cannot represent physical non-negative rotor speeds."""


class RotorTimingError(ValueError):
    """A rotor time constant or step that violates the dynamic interface contract."""


@dataclass(frozen=True, slots=True)
class RotorDynamicsSpec:
    """Injected timing semantics for a future rotor-dynamics implementation.

    ``tau_m_s == 0.0`` is the exact quasi-static branch frozen by CF-9. No epsilon is
    used and no division by ``tau_m_s`` occurs on that branch. A positive value selects
    the future first-order dynamic path. Construction validates only the time constant;
    :meth:`validate_step` validates ``dt_s`` when a consumer actually advances a step.
    """

    tau_m_s: float = 0.0

    def __post_init__(self) -> None:
        tau = float(self.tau_m_s)
        if not math.isfinite(tau) or tau < 0.0:
            raise RotorTimingError(f"tau_m_s must be finite and >= 0, got {self.tau_m_s!r}")
        object.__setattr__(self, "tau_m_s", tau)

    @property
    def quasi_static(self) -> bool:
        """Whether the exact ``tau_m_s == 0.0`` branch is selected."""
        return self.tau_m_s == 0.0

    def validate_step(self, dt_s: float) -> None:
        """Validate timing at the future dynamic-step call site.

        The explicit equality branch is deliberate: the quasi-static mode must never be
        approximated with an epsilon or implemented through a division-by-zero guard.
        """
        dt = float(dt_s)
        if not math.isfinite(dt) or dt <= 0.0:
            raise RotorTimingError(f"dt_s must be positive and finite, got {dt_s!r}")
        if self.tau_m_s == 0.0:
            return
        ratio = dt / self.tau_m_s
        if ratio >= 1.0:
            raise RotorTimingError(
                f"dynamic rotor stepping requires dt_s / tau_m_s < 1, got {ratio!r}"
            )


@dataclass(frozen=True, slots=True)
class RotorCommand:
    """The mixer's output for one step: squared rotor speeds ``n = omega^2`` (CF-11).

    Carrying ``omega^2`` rather than ``omega`` keeps the frozen mixer contract intact and
    makes the im/possible distinction explicit: a negative component is not a speed, it is
    a request for a rotor to run backwards, which SA-3 already refuses upstream. If one
    arrives anyway it is an error, not something to clip - clipping would hide the very
    failure the allocator is supposed to report.
    """

    rotor_speed_squared_radps2: np.ndarray

    def __post_init__(self) -> None:
        array = np.asarray(self.rotor_speed_squared_radps2, dtype=float)
        if array.ndim != 1 or array.size == 0:
            raise MotorCommandError(
                "rotor commands must be a non-empty one-dimensional array, "
                f"got shape {array.shape}"
            )
        if not np.all(np.isfinite(array)):
            raise MotorCommandError("rotor commands contain a non-finite component")
        if np.any(array < 0.0):
            raise MotorCommandError(
                "rotor commands are squared speeds and cannot be negative; a negative value "
                "means the allocation should have failed (SA-3), not that it should be clipped"
            )
        frozen = np.array(array, dtype=float, copy=True)
        frozen.setflags(write=False)
        object.__setattr__(self, "rotor_speed_squared_radps2", frozen)

    @property
    def rotor_speed_radps(self) -> np.ndarray:
        """``omega_cmd`` recovered from ``n``; a command conversion, not the physical mapping."""
        return np.sqrt(self.rotor_speed_squared_radps2)

    @property
    def rotor_count(self) -> int:
        return int(self.rotor_speed_squared_radps2.size)


@dataclass(frozen=True, slots=True)
class RotorState:
    """The rotor speeds the vehicle actually has, in rad/s.

    This is the state the motor layer exists to carry: the mixer describes what is wanted,
    ``core/motor`` maps a speed to a force, and only this object knows the speed that is
    actually present once the lag has been integrated.
    """

    rotor_speed_radps: np.ndarray

    def __post_init__(self) -> None:
        array = np.asarray(self.rotor_speed_radps, dtype=float)
        if array.ndim != 1 or array.size == 0:
            raise RotorStateError(
                "rotor speeds must be a non-empty one-dimensional array, "
                f"got shape {array.shape}"
            )
        if not np.all(np.isfinite(array)):
            raise RotorStateError("rotor speeds contain a non-finite component")
        if np.any(array < 0.0):
            raise RotorStateError("rotor speeds must be non-negative")
        frozen = np.array(array, dtype=float, copy=True)
        frozen.setflags(write=False)
        object.__setattr__(self, "rotor_speed_radps", frozen)

    @property
    def rotor_speed_squared_radps2(self) -> np.ndarray:
        """``omega^2``, the form ``core.motor`` and the mixer both speak."""
        return self.rotor_speed_radps**2

    @property
    def rotor_count(self) -> int:
        return int(self.rotor_speed_radps.size)

    @classmethod
    def from_command(cls, command: RotorCommand) -> RotorState:
        """Construct the exact quasi-static state ``omega = omega_cmd``.

        This expresses the ``tau_m_s == 0.0`` interface semantics without implementing
        a dynamic model or involving a time step.
        """
        return cls(rotor_speed_radps=command.rotor_speed_radps)

    @classmethod
    def at_rest(cls, *, rotor_count: int = 4) -> RotorState:
        """The only state a scenario may start from without stating an initial condition.

        Zero is honest: ``omega_min`` is 0 in the baseline, so "at rest" is reachable and
        does not smuggle in a hover guess. Four rotors remain the scenario default, while
        the state type and constructor support any positive rotor count.
        """
        if isinstance(rotor_count, bool) or not isinstance(rotor_count, int) or rotor_count < 1:
            raise RotorStateError(f"rotor_count must be a positive integer, got {rotor_count!r}")
        return cls(rotor_speed_radps=np.zeros(rotor_count, dtype=float))


@dataclass(frozen=True, slots=True)
class RotorStepResult:
    """One rotor-dynamics step: the new speed, selected path and saturation facts.

    Forces and torques deliberately do not appear here. ``simulation/motors`` owns only
    rotor-state evolution; a later dynamics layer may pass the resulting squared speeds
    to the frozen ``core.motor`` physical mapping.
    """

    state: RotorState
    #: True when the quasi-static path was taken (``tau_m_s == 0``), so a log can show
    #: whether rotor dynamics were active at all.
    quasi_static: bool
    #: Limitation requested from ``core.motor`` while updating the rotor state. This is
    #: motor-state limiting only: it is not mixer allocation failure, controller
    #: saturation, or a force/torque output. ``core.motor.rotor_forces`` may report its
    #: own input-window status independently when a later layer evaluates this state.
    saturation: SaturationResult


@runtime_checkable
class RotorModelProtocol(Protocol):
    """What the scheduler requires of the motor layer."""

    @property
    def tau_m_s(self) -> float:
        """The first-order lag constant; ``0.0`` selects the quasi-static path (CF-9)."""

    def step(
        self, *, command: RotorCommand, state: RotorState, params: QuadParams, dt_s: float
    ) -> RotorStepResult:
        """Advance rotor speed state by ``dt_s`` without calculating forces or torques."""


class FirstOrderRotorModel:
    """Advance rotor speeds with an optional first-order lag.

    For ``tau_m_s > 0`` this uses the explicit Euler discretisation

    ``omega_next = omega + (dt / tau_m) * (omega_cmd - omega)``.

    It is not the continuous system's exact exponential response. ``tau_m_s == 0.0`` is
    an exact, separate quasi-static branch: it neither reads the old speed values nor
    divides by the time constant. Physical speed limits are requested from the frozen
    :func:`core.motor.saturate_rotor_speed_squared` interface; no bounds are restated here.
    """

    def __init__(self, *, spec: RotorDynamicsSpec) -> None:
        self._spec = spec

    @property
    def tau_m_s(self) -> float:
        return self._spec.tau_m_s

    def step(
        self, *, command: RotorCommand, state: RotorState, params: QuadParams, dt_s: float
    ) -> RotorStepResult:
        self._spec.validate_step(dt_s)
        expected_count = int(params.frame.rotor_count)
        if command.rotor_count != expected_count or state.rotor_count != expected_count:
            raise RotorStateError(
                "rotor count mismatch: "
                f"command={command.rotor_count}, state={state.rotor_count}, "
                f"params={expected_count}"
            )

        omega_cmd = command.rotor_speed_radps
        if self._spec.tau_m_s == 0.0:
            raw_next = omega_cmd
            quasi_static = True
        else:
            alpha = float(dt_s) / self._spec.tau_m_s
            raw_next = state.rotor_speed_radps + alpha * (
                omega_cmd - state.rotor_speed_radps
            )
            quasi_static = False

        saturation = saturate_rotor_speed_squared(raw_next**2, params)
        if saturation.was_limited:
            next_state = RotorState(rotor_speed_radps=np.sqrt(saturation.clamped_radps2))
        else:
            # Preserve the exact quasi-static command bits when no physical limit acts.
            next_state = RotorState(rotor_speed_radps=raw_next)

        return RotorStepResult(
            state=next_state,
            quasi_static=quasi_static,
            saturation=saturation,
        )
