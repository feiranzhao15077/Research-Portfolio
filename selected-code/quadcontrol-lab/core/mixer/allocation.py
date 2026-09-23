"""Control allocation (mixing) - the inverse of the rotor force map.

Implements ``docs/theory/mixer_model.md``:

* X-2      the requested wrench ``w = [T, tau_x, tau_y, tau_z]^T``
* X-6/X-7  the allocation matrix ``w = A n``, **linear in** ``n = omega^2``
* X-8/X-9  the structural split ``A = D M`` with ``M M^T = 4I``  (X-10)
* X-12..X-14 the analytic inverse ``n = A^-1 w``
* X-15/X-16 hover allocation
* X-17..X-19 the feasible box and the realisability criterion
* X-20..X-24 the frozen SA-3 feasibility policy (rulings AS-3 / AS-4, D-021 / D-024)

The final feasibility ruling (N-8) is implemented literally:

1. upper-bound violation only -> **SA-3 global scaling** is allowed;
2. a component below ``omega_min^2`` -> attempt positive proportional scaling;
3. if scaling cannot repair it -> return an :class:`AllocationFailure` carrying
   ``requested_wrench``, ``raw_allocation_n0``, ``violation_index`` and
   ``failure_reason``;
4. **never** clip and **never** silently modify the request (FB-1..FB-4).

Consequently :func:`allocate` does **not** return a bare ``n`` vector: it returns
either an :class:`AllocationResult` or an :class:`AllocationFailure`, so a caller
cannot ignore the failure branch by accident.

IMPLEMENTATION CLARIFICATION CL-3 (explicit, not silent)
--------------------------------------------------------
Two related numerical points, both documented rather than hidden:

1. **Comparisons** against the feasible box use :data:`ALLOCATION_TOLERANCE_RADPS2`.
   Without it, the round-off of the ``w -> n0 -> w`` chain would make a component
   that should be exactly zero come back as ``-1e-11`` and be reported as
   ``NEGATIVE_COMPONENT`` - a perfectly realisable command declared unrealisable.

2. **The success path enforces its own contract.** After scaling, components that
   sit within the tolerance of a box bound are snapped exactly onto it, so every
   :class:`AllocationResult` satisfies ``n in [omega_min^2, omega_max^2]`` exactly
   (formula X-17). That matters because :func:`core.motor.rotor_forces` rejects a
   negative ``n`` outright, so a residual ``-7.8e-11`` would make an accepted
   allocation unusable downstream.

   This is **not** the clipping that FB-1 forbids. FB-1 forbids *repairing an
   unrepairable request* by clamping individual components; here the request was
   repaired by SA-3 scaling and only the rounding residue is removed. The change is
   bounded by ``ALLOCATION_TOLERANCE_RADPS2`` (i.e. ~5e-13 rad/s at any realistic
   speed), it can never turn an infeasible request into a success - those already
   returned :class:`AllocationFailure` - and it never alters a component that lies
   more than the tolerance inside the box.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from core.mixer.geometry import rotor_positions_body_m
from core.motor import rotor_spin_signs
from core.params import QuadParams

__all__ = [
    "ALLOCATION_TOLERANCE_RADPS2",
    "AllocationFailure",
    "AllocationFailureReason",
    "AllocationResult",
    "Wrench",
    "allocate",
    "inverse_mixing_matrix",
    "is_wrench_realisable",
    "mixing_matrix",
    "wrench_from_rotor_speed_squared",
]

#: Absolute tolerance for ``n = omega^2`` comparisons, in (rad/s)^2.
#: Chosen far above the round-off of a double-precision matrix product at typical
#: magnitudes (~1e-9 relative) and far below any physically meaningful rotor-speed
#: difference. See clarification CL-3 in the module docstring.
ALLOCATION_TOLERANCE_RADPS2 = 1e-9


class AllocationFailureReason(StrEnum):
    """The frozen ``failure_reason`` enumeration (``mixer_model.md`` §6.4.2)."""

    #: At least one component is negative: a rotor would have to spin backwards.
    #: Scaling is sign-preserving, so this is unrepairable (property P-2).
    NEGATIVE_COMPONENT = "NEGATIVE_COMPONENT"
    #: ``omega_min > 0`` and some component is exactly zero; ``alpha * 0 = 0`` can
    #: never reach a positive idle floor.
    ZERO_COMPONENT_BELOW_IDLE_FLOOR = "ZERO_COMPONENT_BELOW_IDLE_FLOOR"
    #: The lower and upper bounds demand contradictory scale factors.
    EMPTY_SCALING_INTERVAL = "EMPTY_SCALING_INTERVAL"


@dataclass(frozen=True, slots=True)
class Wrench:
    """The requested aerodynamic wrench ``w`` (formula X-2).

    ``total_thrust_N`` acts along ``+z_b``; ``torque_body_Nm`` is in Body FLU.
    """

    total_thrust_N: float
    torque_body_Nm: np.ndarray

    def as_vector(self) -> np.ndarray:
        return np.array(
            [
                float(self.total_thrust_N),
                float(self.torque_body_Nm[0]),
                float(self.torque_body_Nm[1]),
                float(self.torque_body_Nm[2]),
            ]
        )

    @classmethod
    def from_vector(cls, vector: np.ndarray) -> Wrench:
        array = np.asarray(vector, dtype=float).reshape(-1)
        if array.shape != (4,):
            raise ValueError(f"wrench must have 4 components [T, tx, ty, tz], got {array.shape}")
        return cls(total_thrust_N=float(array[0]), torque_body_Nm=array[1:].copy())


@dataclass(frozen=True, slots=True)
class AllocationResult:
    """A successful allocation: the frozen success-path report (``mixer_model.md`` §6.3.4).

    ``status`` is ``"exact"`` when ``alpha == 1`` (the request was realisable as
    given) and ``"scaled"`` when the SA-3 policy adjusted it. A ``"scaled"`` result
    is **not** a failure, but the caller must use ``achieved_wrench`` - not
    ``requested_wrench`` - when computing metrics, otherwise the delivered control
    effort is misreported (principle FP-3).
    """

    status: str
    rotor_speed_squared_radps2: np.ndarray
    alpha: float
    requested_wrench: Wrench
    raw_allocation_n0: np.ndarray
    achieved_wrench: Wrench
    violation_index: tuple[int, ...]

    @property
    def was_scaled(self) -> bool:
        return self.alpha != 1.0


@dataclass(frozen=True, slots=True)
class AllocationFailure:
    """A request that cannot be realised, with all four mandatory fields.

    Ruling AS-4: the allocator **returns this instead of any** ``n``. There is
    deliberately no ``rotor_speed_squared_radps2`` attribute, so a caller cannot
    reach for a command that would misrepresent the vehicle's capability.
    """

    requested_wrench: Wrench
    raw_allocation_n0: np.ndarray
    violation_index: tuple[int, ...]
    failure_reason: AllocationFailureReason


def mixing_matrix(params: QuadParams) -> np.ndarray:
    """The frozen allocation matrix ``A`` (formula X-7), with ``w = A n``.

    Rows are ``[T, tau_x, tau_y, tau_z]``; columns are rotors in frozen numbering.
    Built from geometry and motor coefficients - never authored by hand
    (``parameter_management.md`` §6)::

        T     = k_T sum_i n_i                      (M-5)
        tau_x =  k_T sum_i y_i n_i                 (D-16a)
        tau_y = -k_T sum_i x_i n_i                 (D-16b)
        tau_z = -k_Q sum_i s_i n_i                 (D-16c with M-6)
    """
    thrust_coefficient = params.motor.thrust_coefficient_kT_N_per_radps2
    torque_coefficient = params.motor.torque_coefficient_kQ_Nm_per_radps2
    x_body = rotor_positions_body_m(params)[:, 0]
    y_body = rotor_positions_body_m(params)[:, 1]
    return np.vstack(
        (
            thrust_coefficient * np.ones(params.frame.rotor_count),
            thrust_coefficient * y_body,
            -thrust_coefficient * x_body,
            -torque_coefficient * rotor_spin_signs(params),
        )
    )


def inverse_mixing_matrix(params: QuadParams) -> np.ndarray:
    """``A^-1``: the analytic inverse of the frozen matrix (formula X-12).

    ``A`` is square and full rank (formula X-11), so the inverse is unique and the
    allocation is a single closed-form matrix product - no iteration, no solver.
    """
    return np.linalg.inv(mixing_matrix(params))


def wrench_from_rotor_speed_squared(
    rotor_speed_squared_radps2: np.ndarray, params: QuadParams
) -> Wrench:
    """The forward map ``w = A n`` (formula X-6).

    This is the same map :func:`allocate` inverts, which is what makes the S-42
    round-trip test meaningful: it is an independent expression of the positive
    model, not a second copy of the inverse.
    """
    return Wrench.from_vector(
        mixing_matrix(params) @ np.asarray(rotor_speed_squared_radps2, dtype=float)
    )


def _limit_box(params: QuadParams) -> tuple[float, float]:
    """The feasible box ``[omega_min^2, omega_max^2]`` (formula X-17), (rad/s)^2."""
    return params.motor.omega_min_radps**2, params.motor.omega_max_radps**2


def _snap_onto_box(n: np.ndarray, lower: float, upper: float) -> np.ndarray:
    """Snap round-off residue exactly onto the box bounds (clarification CL-3).

    Only components already within :data:`ALLOCATION_TOLERANCE_RADPS2` of a bound are
    touched, so a genuinely out-of-range command is never repaired here.
    """
    tolerance = ALLOCATION_TOLERANCE_RADPS2
    snapped = np.where(np.abs(n - lower) <= tolerance, lower, n)
    return np.where(np.abs(snapped - upper) <= tolerance, upper, snapped)


def is_wrench_realisable(requested_wrench: Wrench, params: QuadParams) -> bool:
    """Whether ``w`` is directly realisable, i.e. ``A^-1 w in N`` (formula X-19)."""
    lower, upper = _limit_box(params)
    n0 = inverse_mixing_matrix(params) @ requested_wrench.as_vector()
    return bool(
        np.all(n0 >= lower - ALLOCATION_TOLERANCE_RADPS2)
        and np.all(n0 <= upper + ALLOCATION_TOLERANCE_RADPS2)
    )


def allocate(
    *, requested_wrench: Wrench, params: QuadParams
) -> AllocationResult | AllocationFailure:
    """Allocate the requested wrench to rotor speeds, applying the frozen SA-3 policy.

    Follows the six-step algorithm of ``mixer_model.md`` §6.3.3 exactly. The return
    type is deliberately a union: the failure branch carries no rotor command.

    Parameters
    ----------
    requested_wrench:
        The wrench the controller asks for: collective thrust along ``+z_b`` plus a
        body torque in Body FLU (formula X-2).
    params:
        Validated vehicle parameters. Injected, never read from disk here.

    Returns
    -------
    AllocationResult | AllocationFailure
        Never a bare array (ruling AS-4).
    """
    lower, upper = _limit_box(params)
    tolerance = ALLOCATION_TOLERANCE_RADPS2
    requested = requested_wrench.as_vector()
    n0 = inverse_mixing_matrix(params) @ requested

    # Step 1 - already feasible: nothing to adjust.
    if np.all(n0 >= lower - tolerance) and np.all(n0 <= upper + tolerance):
        command = _snap_onto_box(n0, lower, upper)
        return AllocationResult(
            status="exact",
            rotor_speed_squared_radps2=command,
            alpha=1.0,
            requested_wrench=requested_wrench,
            raw_allocation_n0=n0,
            achieved_wrench=wrench_from_rotor_speed_squared(command, params),
            violation_index=(),
        )

    # Step 2 - negative component: unrepairable, scaling preserves the sign (P-2).
    negative = np.flatnonzero(n0 < -tolerance)
    if negative.size:
        return AllocationFailure(
            requested_wrench=requested_wrench,
            raw_allocation_n0=n0,
            violation_index=tuple(int(i) for i in negative),
            failure_reason=AllocationFailureReason.NEGATIVE_COMPONENT,
        )

    # Step 3 - a component at (numerically) zero cannot be scaled up to a positive
    # idle floor, because alpha * 0 = 0.
    if lower > 0.0:
        zeros = np.flatnonzero(np.abs(n0) <= tolerance)
        if zeros.size:
            return AllocationFailure(
                requested_wrench=requested_wrench,
                raw_allocation_n0=n0,
                violation_index=tuple(int(i) for i in zeros),
                failure_reason=AllocationFailureReason.ZERO_COMPONENT_BELOW_IDLE_FLOOR,
            )

    # Step 4 - the feasible scale interval from the per-component constraints (X-22).
    # Components at (numerically) zero impose no constraint when the floor is zero,
    # since alpha * 0 = 0 satisfies it for every alpha.
    positive = n0 > tolerance
    positive_index = np.flatnonzero(positive)
    if positive_index.size == 0:
        # Every component is zero and the idle floor is zero, so step 1 would have
        # accepted it. Reaching here means the bounds are inconsistent.
        return AllocationFailure(
            requested_wrench=requested_wrench,
            raw_allocation_n0=n0,
            violation_index=(),
            failure_reason=AllocationFailureReason.EMPTY_SCALING_INTERVAL,
        )
    alpha_lo = float(np.max(lower / n0[positive]))
    alpha_hi = float(np.min(upper / n0[positive]))

    # Step 5 - contradictory bounds: no uniform scale factor works.
    if alpha_lo > alpha_hi:
        return AllocationFailure(
            requested_wrench=requested_wrench,
            raw_allocation_n0=n0,
            violation_index=tuple(int(i) for i in positive_index),
            failure_reason=AllocationFailureReason.EMPTY_SCALING_INTERVAL,
        )

    # Step 6 - accept the scale factor closest to "no change" inside the interval.
    alpha_star = min(max(1.0, alpha_lo), alpha_hi)
    command = _snap_onto_box(alpha_star * n0, lower, upper)
    repaired = tuple(int(i) for i in np.flatnonzero((n0 < lower) | (n0 > upper)))
    return AllocationResult(
        status="scaled" if alpha_star != 1.0 else "exact",
        rotor_speed_squared_radps2=command,
        alpha=alpha_star,
        requested_wrench=requested_wrench,
        raw_allocation_n0=n0,
        achieved_wrench=wrench_from_rotor_speed_squared(command, params),
        violation_index=repaired,
    )
