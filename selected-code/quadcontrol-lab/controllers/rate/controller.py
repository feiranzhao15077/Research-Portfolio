"""The three-axis rate controller (Phase 3B-3b-1).

FROZEN MATHEMATICS - see ``docs/control/rate_controller_design.md``
------------------------------------------------------------------
Every formula implemented here is named after the document that froze it, so a
reviewer can check the code against the ruling without reading the whole design.

    RC-E1    e_w = omega_ref - omega                       (both Body FLU)
    RC-U4'   u_cmd = Kp*e_w + Ki*xi - Kd*omega_dot_meas
    RC-U5/6  u = u_cmd + alpha_ref                          (alpha_ref optional)
    RC-U13   u is limited to +/- u_max per axis
    RC-U7    tau = I @ u + omega x (I @ omega)
    RC-AW1/2 xi += dt * e_w * g,  g = 1 iff feedback is confirmed and realised
    RC-I3    xi is projected onto [-integral_limit, +integral_limit]^3

WHY THE CONTROL LAW IS WRITTEN IN THIS FORM
-------------------------------------------
Substituting RC-U7 into the frozen rigid-body equation D-12
(``I @ omega_dot = tau - omega x (I @ omega) + tau_d``) leaves ``omega_dot = u``:
the inertia cancels **exactly**, in closed form, for any inertia tensor. That
identity is the single most valuable test of this module (R1-V-02) because it
fails loudly if the gain convention, the gyroscopic term's sign, or the order of
operations drifts - while a hover test would still pass.

WHAT IS DELIBERATELY ABSENT
---------------------------
* **No integrator reset**, ever, from inside the controller. The integral is
  frozen (not zeroed) when the allocator reports a shortfall, and only an explicit
  ``reset_integral()`` call clears it (RC-AW4 / RC-I5). Zeroing on saturation is
  forbidden because it makes a temporary saturation look like a solved steady
  state, which produces a second overshoot on recovery.
* **No filters.** No derivative low-pass, no notch, no yaw filter (N-10/X-08).
  The platform has no noise model yet (assumption A-12), so a filter would add a
  phase lag and a new internal state with no target to improve.
* **No failure-reason branch.** ``feedback.failure_reason`` is recorded and
  reported, never tested (RC-AW0). The only control branch is ``allocation_ok``
  together with the continuous ``alpha``.
* **No torque unit conversion or normalisation.** One ``torque_limit`` guard is
  applied - a declared controller-side bound, not a mixer clip.

DEPENDENCIES (D-038 / architecture rules A1, A4)
-------------------------------------------------
The module imports only ``numpy``, the ``Wrench`` output contract and - for typing
only - the injected configuration type, which is behind a ``TYPE_CHECKING`` guard so
that ``core.params`` is **not** imported at run time (Phase 5-1, closing ``P5-O-1``).
It reads no file, imports no parameter loader, and does **not** import
``core.dynamics``: the inertia tensor arrives as an injected array, so the caller
decides where it is composed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

import numpy as np

from controllers.rate.config import epsilon_alpha
from controllers.reference import AllocationFeedback, AttitudeCommand
from core.mixer import Wrench

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime dependency
    # D-038: ``controllers/**`` must not import ``core.params``. Keeping the type behind
    # this guard makes the injection-only rule structural instead of conventional, and
    # costs nothing because the module already uses ``from __future__ import
    # annotations``. An eager import would also drag in ``core.params.loader``, since
    # ``core/params/__init__.py`` re-exports ``ParamLoader``.
    from core.params import RateControlParams

__all__ = [
    "RATE_AXES",
    "FeedforwardFlag",
    "RateControlStatus",
    "RateController",
    "SaturationFlag",
]

#: Axis order is frozen to x -> roll, y -> pitch, z -> yaw (coordinate_convention.md).
RATE_AXES = ("roll", "pitch", "yaw")

#: Vector length of every body-frame quantity in this module.
VECTOR_SIZE = 3

#: Shape of the injected inertia tensor.
INERTIA_SHAPE = (VECTOR_SIZE, VECTOR_SIZE)


class SaturationFlag(StrEnum):
    """Which limits were active. Reported, never used to branch the control law.

    The three limit flags come from the controller's own declared bounds; the two
    allocation flags are statements about the **allocator's** report on the previous
    step. They are kept apart because conflating "I limited myself" with "the
    vehicle could not do it" would hide exactly the distinction ruling D-028
    requires be visible.
    """

    #: The command acceleration hit ``u_max_body_radps2``.
    OUTPUT_LIMIT = "OUTPUT_LIMIT"
    #: The integral state hit ``integral_limit_body_rad`` (RC-I7: numerical guard).
    INTEGRAL_LIMIT = "INTEGRAL_LIMIT"
    #: The commanded torque hit ``torque_limit_body_Nm``.
    TORQUE_LIMIT = "TORQUE_LIMIT"
    #: Last allocation was scaled by SA-3: part of the request was not delivered.
    ALLOCATION_SCALED = "ALLOCATION_SCALED"
    #: Last allocation failed outright; no rotor command was produced at all.
    ALLOCATION_FAILED = "ALLOCATION_FAILED"


class FeedforwardFlag(StrEnum):
    """Which feedforward channels were active (RC-FF2: closing must be observable).

    Reported separately from :class:`SaturationFlag` on purpose: an enabled
    feedforward is a *configuration* statement, not a saturation event, and mixing
    the two would make "no flags" ambiguous.
    """

    #: FF-1: the reference rate participates in the error (D-039).
    RATE_FF = "RATE_FF"
    #: FF-3: ``omega x (I omega)`` is added to the torque (D-039).
    GYRO_FF = "GYRO_FF"


_RATE_FF_ENABLED = FeedforwardFlag.RATE_FF
_GYRO_FF_ENABLED = FeedforwardFlag.GYRO_FF


@dataclass(frozen=True, slots=True)
class RateControlStatus:
    """Read-only snapshot of the controller's internal state (RC-I4 / A-03).

    Everything a log or a verification needs to prove that anti-windup behaved:
    the integral itself, whether its gate was open this step, the scale factor the
    allocator reported, and which limits were active. Without this the S-37
    contract ("the control layer must not keep integrating after a failure") would
    be unverifiable from the outside.
    """

    integral_body_rad: np.ndarray
    #: The accumulation gate of this step: 1.0 open, 0.0 frozen (RC-AW2).
    accumulation_gate: float
    #: ``alpha`` from the most recent feedback, or ``None`` before the first one.
    allocation_alpha: float | None
    #: ``step_index`` from the most recent feedback, or ``None``.
    feedback_step_index: int | None
    #: ``failure_reason`` of the most recent feedback, or ``None``.
    failure_reason: str | None
    saturated: tuple[SaturationFlag, ...]
    #: Which feedforward channels were active on the last step (RC-FF2: observable).
    feedforward_enabled: tuple[FeedforwardFlag, ...]
    #: Number of completed ``update`` calls.
    step_count: int


class RateController:
    """Three-axis body-rate PID with saturation-aware integration.

    CONSTRUCTION (injection only, D-038)
    ------------------------------------
    ``config``   - ``control.rate`` as a typed value object (14 frozen keys).
    ``inertia_kgm2`` - the 3x3 inertia tensor about the CoM, Body FLU, kg*m^2.
                   It is injected as an array and never composed here, so this
                   module needs no opinion about where it comes from. (The
                   platform already has one composition site, formula RC-U2;
                   the caller or its fixture uses that and passes the result.)
    ``dt_control_s`` - the control period in seconds, derived from
                   ``control_rate_hz`` by ruling D-035 and injected by the driver.

    No file is read, no path is accepted, and no default value is substituted for
    a missing argument (contract C7).
    """

    def __init__(
        self,
        *,
        config: RateControlParams,
        inertia_kgm2: np.ndarray,
        dt_control_s: float,
    ) -> None:
        inertia = np.asarray(inertia_kgm2, dtype=float)
        if inertia.shape != INERTIA_SHAPE:
            raise ValueError(
                f"inertia_kgm2 must be a {INERTIA_SHAPE[0]}x{INERTIA_SHAPE[1]} body-frame "
                f"tensor, got shape {inertia.shape}"
            )
        if not np.all(np.isfinite(inertia)):
            raise ValueError("inertia_kgm2 contains a non-finite component")
        if not dt_control_s > 0.0:
            raise ValueError(
                f"dt_control_s must be > 0, got {dt_control_s!r}; it is derived from "
                f"control_rate_hz (D-035), never guessed"
            )

        self._inertia = inertia
        self._dt = float(dt_control_s)

        # Gains are copied out of the value object one scalar at a time: the
        # controller holds numbers, not a reference to a configuration object it
        # could later consult for "one more parameter" (RC-P2).
        self._kp = np.array(
            [config.kp_rate_roll, config.kp_rate_pitch, config.kp_rate_yaw], dtype=float
        )
        self._ki = np.array(
            [config.ki_rate_roll, config.ki_rate_pitch, config.ki_rate_yaw], dtype=float
        )
        self._kd = np.array(
            [config.kd_rate_roll, config.kd_rate_pitch, config.kd_rate_yaw], dtype=float
        )
        self._integral_limit = float(config.integral_limit_body_rad)
        self._u_max = float(config.u_max_body_radps2)
        self._torque_limit = float(config.torque_limit_body_Nm)
        self._enable_rate_ff = bool(config.enable_rate_ff)
        self._enable_gyro_ff = bool(config.enable_gyro_ff)

        # The only mutable state in the controller.
        self._integral = np.zeros(VECTOR_SIZE, dtype=float)
        self._previous_omega: np.ndarray | None = None
        self._gate = 0.0
        self._alpha: float | None = None
        self._feedback_step_index: int | None = None
        self._failure_reason: str | None = None
        self._flags: tuple[SaturationFlag, ...] = ()
        self._step_count = 0

    # ------------------------------------------------------------------ public
    def update(
        self,
        *,
        omega_body_radps: np.ndarray,
        command: AttitudeCommand,
        total_thrust_N: float,
        feedback: AllocationFeedback | None = None,
    ) -> Wrench:
        """One control step. Returns the commanded wrench.

        ``omega_body_radps``
            Measured/estimated body angular velocity, Body FLU, rad/s.
        ``command``
            Reference from ``controllers/attitude`` (D-041). Both of its fields
            are Body FLU.
        ``total_thrust_N``
            Collective thrust, passed through unchanged. The rate loop shapes
            torque only: rewriting ``T`` here would be a silent modification of the
            control demand (FB-4), and thrust belongs to the outer loop.
        ``feedback``
            The allocator's report on the **previous** step, or ``None`` on the
            first step. ``None`` is treated as unconfirmed, never as success
            (RC-AW3 / AC-20).

        The call order of the returned ``Wrench`` is fixed: PID, then the
        acceleration feedforward, then the acceleration limit, then ``I``, then
        the gyroscopic feedforward, then the torque limit (RC-U15).
        """
        omega = self._as_body_vector(omega_body_radps, "omega_body_radps")
        thrust = float(total_thrust_N)

        # --- FF-1 and the error (RC-E1) ------------------------------------
        # enable_rate_ff gates the REFERENCE RATE only (D-039). With it off the
        # error degenerates to -omega, i.e. the loop becomes rate damping with no
        # reference: that is what the switch means, and it is why the switch must
        # not be used to "turn the attitude loop off" (AT-25).
        reference = command.omega_ref_body_radps if self._enable_rate_ff else np.zeros(VECTOR_SIZE)
        error = reference - omega

        # --- D term from the measured rate (RC-U9) -------------------------
        if self._previous_omega is None:
            omega_dot_measured = np.zeros(VECTOR_SIZE)
        else:
            omega_dot_measured = (omega - self._previous_omega) / self._dt

        # --- gate, control law, and the integral update (RC-AW1..RC-AW4, RC-I3) --
        # ORDER IS LOAD-BEARING. Formula RC-U4' uses the CURRENT integral ``xi(k)``
        # while RC-I3 produces ``xi(k+1)``; the updated value must therefore be
        # computed for the next step but NOT used in this step's control law. Using
        # the new value here would make the output depend on a state that, by the
        # document's own index, belongs to the next instant - a one-step lead that no
        # frozen formula asks for and that R1-V-07's increment identity would expose.
        self._gate = self._accumulation_gate(feedback)
        u_cmd = self._kp * error + self._ki * self._integral - self._kd * omega_dot_measured
        u_cmd, u_limited = self._limit_vector(u_cmd, self._u_max)

        flags: list[SaturationFlag] = []
        if u_limited:
            flags.append(SaturationFlag.OUTPUT_LIMIT)

        if self._gate > 0.0:
            candidate = self._integral + self._dt * error
            projected, integral_limited = self._project_integral(candidate)
            if integral_limited:
                flags.append(SaturationFlag.INTEGRAL_LIMIT)
            self._integral = projected
        # else: the integral keeps its last value exactly - no decay, no zeroing.

        if feedback is not None:
            self._alpha = float(feedback.alpha)
            self._feedback_step_index = int(feedback.step_index)
            self._failure_reason = feedback.failure_reason
            if not feedback.allocation_ok:
                flags.append(SaturationFlag.ALLOCATION_FAILED)
            elif feedback.alpha < 1.0 - epsilon_alpha:
                flags.append(SaturationFlag.ALLOCATION_SCALED)
        else:
            self._alpha = None
            self._feedback_step_index = None
            self._failure_reason = None

        # --- feedforward and torque (RC-U5, RC-U6, RC-U7) ------------------
        u = u_cmd
        if command.alpha_ref_body_radps2 is not None:
            u = u + command.alpha_ref_body_radps2

        torque = self._inertia @ u
        if self._enable_gyro_ff:
            torque = torque + np.cross(omega, self._inertia @ omega)
        torque, torque_limited = self._limit_vector(torque, self._torque_limit)
        if torque_limited:
            flags.append(SaturationFlag.TORQUE_LIMIT)

        # --- bookkeeping ---------------------------------------------------
        self._previous_omega = omega.copy()
        self._flags = tuple(flags)
        self._step_count += 1

        return Wrench(total_thrust_N=thrust, torque_body_Nm=torque)

    def status(self) -> RateControlStatus:
        """Read-only snapshot of the integral and the saturation bookkeeping."""
        integral = self._integral.copy()
        integral.setflags(write=False)
        enabled = []
        if self._enable_rate_ff:
            enabled.append(_RATE_FF_ENABLED)
        if self._enable_gyro_ff:
            enabled.append(_GYRO_FF_ENABLED)
        return RateControlStatus(
            integral_body_rad=integral,
            accumulation_gate=self._gate,
            allocation_alpha=self._alpha,
            feedback_step_index=self._feedback_step_index,
            failure_reason=self._failure_reason,
            saturated=self._flags,
            feedforward_enabled=tuple(enabled),
            step_count=self._step_count,
        )

    def reset_integral(self) -> None:
        """Clear the integral state.

        Explicit only (RC-I5). The controller never calls this itself, and in
        particular does not clear on saturation: a scenario or the experiment layer
        decides when a reset is meaningful, so that "the integrator was cleared"
        is always an event someone asked for rather than something the controller
        did behind the log's back.
        """
        self._integral = np.zeros(VECTOR_SIZE, dtype=float)

    @property
    def dt_control_s(self) -> float:
        """The injected control period (read-only)."""
        return self._dt

    @property
    def inertia_kgm2(self) -> np.ndarray:
        """The injected inertia tensor (read-only view of a frozen array)."""
        return self._inertia

    # --------------------------------------------------------------- internals
    def _accumulation_gate(self, feedback: AllocationFeedback | None) -> float:
        """The RC-AW2 gate: 1.0 only for a confirmed, fully realised allocation.

        A single scalar for all three axes (RC-I6): SA-3 scaling is a global event
        and an ``AllocationFailure`` carries no per-axis information, so a per-axis
        gate would have to invent one.
        """
        if feedback is None:
            return 0.0
        if not feedback.allocation_ok:
            return 0.0
        return 1.0 if feedback.alpha >= 1.0 - epsilon_alpha else 0.0

    @staticmethod
    def _as_body_vector(value: np.ndarray, name: str) -> np.ndarray:
        array = np.asarray(value, dtype=float).reshape(-1)
        if array.shape != (VECTOR_SIZE,):
            raise ValueError(f"{name} must have {VECTOR_SIZE} components in Body FLU")
        if not np.all(np.isfinite(array)):
            raise ValueError(f"{name} contains a non-finite component")
        return array

    @staticmethod
    def _limit_vector(vector: np.ndarray, limit: float) -> tuple[np.ndarray, bool]:
        """Per-component symmetric limit; reports whether it engaged."""
        limited = np.clip(vector, -limit, limit)
        return limited, bool(np.any(limited != vector))

    def _project_integral(self, candidate: np.ndarray) -> tuple[np.ndarray, bool]:
        """Project the integral back onto the box (RC-I3), reporting engagement.

        This is a numerical guard on the integral *state*, not the anti-windup
        mechanism (RC-I7): the mechanism is the gate above. It reports so the
        projection cannot happen silently.
        """
        projected = np.clip(candidate, -self._integral_limit, self._integral_limit)
        return projected, bool(np.any(projected != candidate))
