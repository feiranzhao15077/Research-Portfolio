"""The controller pipeline: the call boundary of the inner loops (Phase 3B-3b-3).

This module owns **orchestration only**. It adds no control law, no state, and no
mathematics: it freezes the order in which the already-frozen loops are called, what
crosses the boundary, and where the allocator's report re-enters the chain.

    x, reference, T_cmd, dt, feedback(k-1)
        |
        +-- attitude loop  ->  AttitudeCommand{omega_ref, alpha_ref}     (D-041)
        +-- rate loop      ->  Wrench{T, tau}                            (D-022/F3)
        |
        '-> caller: Wrench  ->  core/mixer.allocate  ->  feedback(k) -> next call

WHAT IS FROZEN HERE (and what is deliberately left to the caller)
----------------------------------------------------------------
1. **Order.** Attitude before rate, always. The reverse is not expressible: the rate
   loop's reference *is* the attitude loop's output (AT-38, D-033).
2. **Step lifecycle.** One ``step`` call does exactly one control cycle: attitude,
   then rate, then return. It never allocates, never integrates, and never advances
   time - those belong to the caller (mixer and integrator respectively). The pipeline
   therefore holds no state that needs resetting.
3. **``dt`` propagation.** ``dt`` is **not** chosen here. The rate controller already
   owns its period (injected once, D-035); the pipeline **requires the caller to state
   it** and **checks it against the controller** so that passing ``dt_physics_s``
   instead of ``dt_control_s`` fails loudly on the first step rather than silently
   mis-scaling both the integral and the derivative term.
4. **State reading.** Two fields are read and nothing else: ``attitude_wb`` and
   ``angular_velocity_body_radps``. The pipeline never writes to the state, never
   re-derives the quaternion from the rate, and never forwards position or velocity -
   those belong to the outer loop (Phase 4) and to the estimator (Phase 5).
5. **The attitude -> rate connection.** ``AttitudeCommand`` is passed to the rate loop
   unchanged, because the two contracts were designed to fit (D-041 / AT-38): the
   attitude loop emits exactly the ``omega_ref_body_radps`` and
   ``alpha_ref_body_radps2`` the rate loop consumes. No adapter, no field renaming.
6. **The feedback return point.** ``AllocationFeedback`` is routed to the **rate loop
   only**, which is the single consumer the anti-windup contract names (D-034 / AT-36),
   and it is passed straight through - the pipeline stores nothing - so the
   one-step lag of AC-22 is preserved exactly and no second delay is introduced. On
   failure the rate loop freezes its integral and still returns the wrench it computed
   (D-028); the pipeline does not intervene, because hiding or repairing that is
   forbidden (NF-1/FB-4).

WHAT THIS MODULE DELIBERATELY DOES NOT DO
-----------------------------------------
* **No ``allocate`` call.** The pipeline's output is a ``Wrench``, which *is* the
  mixer's input; invoking the mixer here would move an L2 concern into L3 and would
  force this boundary to know about the allocator's feasible set.
* **No thrust computation.** ``total_thrust_N`` arrives from the outer loop and is
  passed through the rate loop unchanged (RC-8).
* **No clock.** Time is never read; the caller supplies ``dt`` and the pipeline only
  checks it (``forbid_wall_clock``).
* **No attitude/position loop.** Position and altitude control are Phase 4 and are not
  part of this boundary.

DEPENDENCIES (D-038 / architecture rules A1, A4)
-------------------------------------------------
It imports the two loops, their shared contracts, and the state container. It reads no
file, imports no parameter loader, and does not import ``core.dynamics`` or
``core.motor``; the inertia tensor needed by the rate loop is injected into that loop
by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from controllers.attitude import AttitudeController, AttitudeSaturationFlag
from controllers.rate import RateController, SaturationFlag
from controllers.reference import AllocationFeedback, AttitudeCommand, AttitudeReference
from core.mixer import Wrench
from core.state import QuadState

__all__ = ["ControlStep", "ControllerPipeline", "PipelineStepError"]

#: Relative tolerance for comparing the caller's ``dt`` with the rate loop's period.
#: This is a numerical tolerance on a *comparison*, not a design constant: the two
#: values are expected to be the same number, so anything beyond float round-off means
#: a different quantity was passed.
_DT_RELATIVE_TOLERANCE = 1e-9


class PipelineStepError(ValueError):
    """A pipeline-level contract violation (not a control-law error).

    Raised when the caller supplies a time step that does not match the period the
    rate loop was constructed with. The failure is deliberately loud and early: a
    mismatched ``dt`` would silently mis-scale both the integral increment and the
    derivative term, producing wrong dynamics that look plausible.
    """


@dataclass(frozen=True, slots=True)
class ControlStep:
    """One control cycle's inputs and outputs, as a record (Phase 3B-3b-3).

    ``step_index`` is the pipeline's own cycle counter, used to align the recorded
    command with the experiment log. It is **not** a clock and not a time: the number
    of cycles is derived from ``dt`` and the simulation step, never from wall time.

    ``feedback_step_index`` echoes the allocator's own ``step_index`` when feedback was
    supplied, so a log can show the one-step offset (AC-22) explicitly instead of
    leaving it implicit.
    """

    step_index: int
    dt_control_s: float
    command: AttitudeCommand
    wrench: Wrench
    feedback_step_index: int | None
    attitude_saturated: tuple[AttitudeSaturationFlag, ...]
    rate_saturated: tuple[SaturationFlag, ...]


class ControllerPipeline:
    """Calls the attitude loop, then the rate loop, once per control cycle.

    CONSTRUCTION (injection only, D-038)
    ------------------------------------
    Both loops arrive already constructed, because each has its own injection
    requirements and neither is this module's business to assemble:

    * ``attitude`` - built from ``AttitudeControlParams``;
    * ``rate`` - built from ``RateControlParams`` **plus the inertia tensor**, whose
      composition site is an open design question (``P3B1-O-1``). Composing it here
      would answer that question by accident, and would put a ``core.dynamics``
      dependency (rule A4) into this module.

    The pipeline holds no other configuration: it has no gains, no limits, no period.
    """

    def __init__(self, *, attitude: AttitudeController, rate: RateController) -> None:
        self._attitude = attitude
        self._rate = rate
        self._step_count = 0

    # ------------------------------------------------------------------ public
    def step(
        self,
        *,
        state: QuadState,
        reference: AttitudeReference,
        total_thrust_N: float,
        dt_control_s: float,
        feedback: AllocationFeedback | None = None,
    ) -> ControlStep:
        """Run one control cycle and return the commanded wrench with its record.

        ``state``
            The 13-dimensional state, read only. Exactly two fields are consumed:
            ``attitude_wb`` and ``angular_velocity_body_radps``.
        ``reference``
            The desired attitude trajectory for this cycle (AT-I4).
        ``total_thrust_N``
            Collective thrust from the outer loop, passed through unchanged (RC-8).
        ``dt_control_s``
            The control period in seconds. Required from the caller and checked
            against the rate loop's injected period (see :meth:`_check_dt`).
        ``feedback``
            The allocator's report on the **previous** cycle, or ``None`` on the first
            one. It is forwarded to the rate loop and nowhere else (D-034 / AT-36).
        """
        dt = self._check_dt(dt_control_s)

        # --- 1. attitude loop: attitude -> rate reference (D-041) -----------
        command = self._attitude.update(
            attitude_wb=state.attitude_wb,
            omega_body_radps=state.angular_velocity_body_radps,
            reference=reference,
        )

        # --- 2. rate loop: reference -> physical wrench (D-022/F3) ----------
        # The command is passed unchanged: the contracts were designed to fit
        # (AT-38), so any adaptation here would mean one of them had drifted.
        wrench = self._rate.update(
            omega_body_radps=state.angular_velocity_body_radps,
            command=command,
            total_thrust_N=total_thrust_N,
            feedback=feedback,
        )

        attitude_status = self._attitude.status()
        rate_status = self._rate.status()
        record = ControlStep(
            step_index=self._step_count,
            dt_control_s=dt,
            command=command,
            wrench=wrench,
            feedback_step_index=(
                None if feedback is None else int(feedback.step_index)
            ),
            attitude_saturated=attitude_status.saturated,
            rate_saturated=rate_status.saturated,
        )
        self._step_count += 1
        return record

    @property
    def step_count(self) -> int:
        """Number of completed control cycles."""
        return self._step_count

    @property
    def dt_control_s(self) -> float:
        """The period the rate loop was constructed with (read-only).

        Exposed so a caller can derive ``control_rate_hz = 1 / dt`` for scheduling
        without reaching into the rate loop itself.
        """
        return self._rate.dt_control_s

    # --------------------------------------------------------------- internals
    def _check_dt(self, dt_control_s: float) -> float:
        """Validate the caller's step against the rate loop's injected period.

        WHY A CHECK AND NOT A SOURCE OF TRUTH
            ``dt`` has exactly one owner per loop, and for the loops that use it that
            owner is the injection site (D-035 derives it from ``control_rate_hz``).
            Letting the pipeline override or re-derive it would create a second source
            of truth for time - the same failure mode the parameter rules forbid for
            physical quantities. The pipeline's job is therefore to make a *wrong*
            ``dt`` impossible to pass unnoticed, which is what this does.
        """
        expected = self._rate.dt_control_s
        dt = float(dt_control_s)
        if not np.isfinite(dt) or dt <= 0.0:
            raise PipelineStepError(
                f"dt_control_s must be positive and finite, got {dt_control_s!r}"
            )
        if abs(dt - expected) > _DT_RELATIVE_TOLERANCE * expected:
            raise PipelineStepError(
                f"dt_control_s={dt!r} does not match the control period the rate loop was "
                f"built with ({expected!r}); pass dt_control_s = 1 / control_rate_hz, not "
                f"dt_physics_s (D-035)"
            )
        return dt
