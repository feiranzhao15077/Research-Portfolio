"""The attitude (outer) loop - implemented in Phase 3B-3b-2.

Implements the mathematics frozen by ``docs/control/attitude_controller_design.md``
(Phase 3B-2, rulings D-033/D-036/D-041/D-043) with the parameters frozen by
``docs/control/phase3b3a_decisions.md`` (D-045…D-051).

    q_e    = q_wb* (x) q_ref_wb        with q_e,w >= 0      (AT-E1, AT-S1/AT-S2)
    e_att  = 2 * vec(q_e)                                   (AT-E3)
    w_p    = Kp_att * e_att                                 (AT-C1)
    w_lim  = tilt-scaled xy, saturated z                    (AT-C4)
    w_cmd  = w_lim + omega_ref_ff                           (AT-C3)
    a_cmd  = alpha_ref                                      (AT-C4, zero if absent)

WHAT THIS LOOP DELIBERATELY DOES NOT HAVE
-----------------------------------------
* **No integral term and no integral state** (D-036). That is why it cannot need
  anti-windup, and why it does not accept ``AllocationFeedback`` at all: there is no
  accumulation to freeze (AT-36).
* **No derivative term and no rate feedback** (AT-21). Damping comes from the rate
  loop's own ``Kp e_w - Kd wdot``; a second rate path here would double-damp the
  cascade and make the tuning unattributable.
* **No reference model** (AT-40 / open item P3B2-O-3). The reference arrives as a
  trajectory; smoothing it is a separate concern.
* **No filters and no memory**: the output depends on the current inputs only
  (AT-6), so the same inputs always give the same command.

TWO DETAILS THAT ARE EASY TO GET WRONG (both asserted in the tests)
-------------------------------------------------------------------
1. **Composition order.** ``q_e = q_wb* (x) q_ref_wb`` gives the semantics
   ``b -> b_ref``; the reverse order gives ``b_ref -> b`` and flips the sign of the
   error. Hover tests are blind to it (AT-7).
2. **The rate feedforward depends on the measured rate.** Formula AT-N2 says
   ``e_att_dot = omega_ref - omega_b``, so the feedforward path needs ``omega_body``:
   it is a required argument, not an optional convenience (AT-5 note). It must not be
   used for feedback, though - see the module's AT-21 note above.

INJECTION ONLY (D-038)
----------------------
The controller reads no file, imports no parameter loader, and does not import
``core.dynamics``. It receives an :class:`~core.params.schema.AttitudeControlParams`
value object and converts ``max_tilt_rad`` to ``2*sin(theta/2)`` **at construction
time**; that derived bound is never stored in the configuration (AT-C4a/AT-39).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

import numpy as np

from controllers.reference import AttitudeCommand, AttitudeReference

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime dependency
    # D-038 / AT-3 / AT-41: ``controllers/**`` must not import ``core.params``. The
    # guard is what makes that true rather than merely intended, and it is free because
    # this module already uses ``from __future__ import annotations``: the annotation
    # below is never evaluated at run time. Importing the package eagerly would pull in
    # ``core.params.loader`` transitively (``core/params/__init__.py`` re-exports it),
    # putting the parameter-loading machinery on the control kernel's import graph.
    from core.params import AttitudeControlParams

__all__ = [
    "ATTITUDE_AXES",
    "AttitudeControlStatus",
    "AttitudeController",
    "AttitudeSaturationFlag",
    "attitude_error_body_rad",
    "quaternion_conjugate",
    "quaternion_multiply",
    "shortest_path_quaternion",
]

#: Axis order is frozen to x -> roll, y -> pitch, z -> yaw (coordinate_convention.md).
ATTITUDE_AXES = ("roll", "pitch", "yaw")

#: Storage length of a quaternion, scalar-first [w, x, y, z] (C-12).
QUATERNION_SIZE = 4
#: Length of a body-frame vector.
VECTOR_SIZE = 3

#: Guard for the tilt ratio's denominator (AT-C4): a numerical detail, not a
#: design constant. It only has to be far below any physically meaningful tilt error.
_EPSILON_TILT = 1e-12

#: Tolerance used when the caller's quaternion looks non-unit: the platform
#: integrates on S^3 and renormalises per step, so a small radial drift is expected
#: and must be tolerated rather than rejected (state_definition.md §2, S-2).
_UNIT_QUATERNION_TOLERANCE = 1e-9


class AttitudeSaturationFlag(StrEnum):
    """Which attitude-loop limits were active (AT-C4c: limiting must be observable).

    Reported, never used to branch the control law - the law itself is a pure
    algebraic map (AT-6).
    """

    #: The tilt component of the command was scaled to ``e_sin_max`` (AT-C4b).
    TILT_LIMIT = "TILT_LIMIT"
    #: The yaw component of the command hit ``max_yaw_rate_radps`` (AT-C4b).
    YAW_RATE_LIMIT = "YAW_RATE_LIMIT"


@dataclass(frozen=True, slots=True)
class AttitudeControlStatus:
    """Read-only snapshot of what the loop did on the last step (A-03 / AT-37).

    The loop has no accumulator, so what is worth reporting is the geometry
    (``|e_att|``, ``|e_att,xy|``), the derived bound actually in force, whether the
    reference quaternion needed renormalising, and which limits engaged.
    """

    #: ``|e_att|`` in the sine convention, i.e. ``2|sin(theta/2)|`` (AT-E4/AT-8).
    attitude_error_magnitude: float
    #: ``|e_att,xy|``, the tilt part of the error (AT-C4).
    tilt_error_magnitude: float
    #: The derived tilt bound in force: ``2 sin(max_tilt_rad / 2)`` (AT-C4a).
    tilt_error_bound: float
    saturated: tuple[AttitudeSaturationFlag, ...]
    #: True when the caller-supplied quaternion was off the unit sphere by more than
    #: the accepted tolerance and was renormalised defensively (AC-4 / AC-6).
    quaternion_renormalised: bool
    #: Number of completed ``update`` calls.
    step_count: int


# ---------------------------------------------------------------------------
# Quaternion algebra (Hamilton, scalar-first) - C-11/C-12/C-14/C-15
# ---------------------------------------------------------------------------
def quaternion_conjugate(q: np.ndarray) -> np.ndarray:
    """``q* = [w, -x, -y, -z]`` (C-15). For a unit quaternion this is the inverse."""
    array = np.asarray(q, dtype=float).reshape(-1)
    if array.shape != (QUATERNION_SIZE,):
        raise ValueError(f"quaternion must have {QUATERNION_SIZE} components (C-12)")
    return np.array([array[0], -array[1], -array[2], -array[3]], dtype=float)


def quaternion_multiply(lhs: np.ndarray, rhs: np.ndarray) -> np.ndarray:
    """Hamilton product ``lhs (x) rhs`` (formula C-14), scalar-first.

    Written out explicitly rather than as a matrix product so the Hamilton sign
    convention is visible at the point of use; a LaTeX-looking helper would hide the
    one thing this function exists to get right.
    """
    a = np.asarray(lhs, dtype=float).reshape(-1)
    b = np.asarray(rhs, dtype=float).reshape(-1)
    if a.shape != (QUATERNION_SIZE,) or b.shape != (QUATERNION_SIZE,):
        raise ValueError(f"both quaternions must have {QUATERNION_SIZE} components (C-12)")
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return np.array(
        [
            aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
        ],
        dtype=float,
    )


def shortest_path_quaternion(q_error: np.ndarray) -> np.ndarray:
    """Return the representative of ``+/- q_error`` with a non-negative scalar part.

    ``q`` and ``-q`` are the same rotation (S^3 double-covers SO(3)), so the choice
    only decides whether the error is read as a rotation of ``theta`` or of
    ``theta - 2*pi``. Requiring ``q_e,w >= 0`` selects the shorter one (AT-S1/AT-S2).

    At exactly ``q_e,w = 0`` (a 180 degree error) both signs are equally short, so the
    frozen tie-break applies: make the largest-magnitude vector component positive,
    smallest index first on a tie (AT-S3'). It is deterministic and independent of
    floating-point comparison order, which is what keeps the command from jittering
    near the boundary.

    Note that the tie-break changes the *sign of the error vector as a whole*, never
    its axis: negating ``q_e`` negates ``vec(q_e)``, and the axis ``vec/|vec|`` is
    unchanged (AT-12). It is therefore a convention statement, not a correction.
    """
    array = np.asarray(q_error, dtype=float).reshape(-1)
    if array.shape != (QUATERNION_SIZE,):
        raise ValueError(f"quaternion must have {QUATERNION_SIZE} components (C-12)")
    scalar = array[0]
    if scalar > 0.0:
        return array.copy()
    if scalar < 0.0:
        return -array
    # Exactly zero: deterministic tie-break on the largest vector component.
    vector = array[1:]
    largest = int(np.argmax(np.abs(vector)))
    if vector[largest] < 0.0:
        return -array
    return array.copy()


def attitude_error_body_rad(
    attitude_wb: np.ndarray, attitude_ref_wb: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(e_att, q_e)`` for the frozen error definition (AT-E1, AT-E3, AT-S4).

    The order of operations is the frozen one: build ``q_e``, apply the shortest
    path, and only then extract ``e_att`` (AT-S4). Doing it the other way round would
    discard the ``q_e,w`` information the tie-break needs (AT-11).

    Both inputs are taken as unit quaternions; the caller is responsible for that
    (AC-4 clause 2), and :class:`AttitudeController` renormalises defensively and
    reports when it had to.
    """
    attitude = np.asarray(attitude_wb, dtype=float).reshape(-1)
    reference = np.asarray(attitude_ref_wb, dtype=float).reshape(-1)
    if attitude.shape != (QUATERNION_SIZE,) or reference.shape != (QUATERNION_SIZE,):
        raise ValueError(f"both quaternions must have {QUATERNION_SIZE} components (C-12)")
    q_error = quaternion_multiply(quaternion_conjugate(attitude), reference)
    q_error = shortest_path_quaternion(q_error)
    return 2.0 * q_error[1:], q_error


class AttitudeController:
    """Proportional attitude loop with feedforward and separated tilt/yaw limiting.

    CONSTRUCTION (injection only, D-038)
    ------------------------------------
    ``config`` - ``control.attitude`` as a typed value object (5 frozen keys).
    The derived tilt bound ``e_sin_max = 2 sin(max_tilt_rad / 2)`` is computed here,
    once, and kept as a private scalar: it is a *derived quantity* and is neither
    stored in the parameter file nor exposed as a configuration field
    (AT-C4a / AT-39 / D-043).

    No file is read, no path is accepted, and no default is substituted for a
    missing argument (contract C7).
    """

    def __init__(self, *, config: AttitudeControlParams) -> None:
        self._kp = np.array(
            [config.kp_att_roll, config.kp_att_pitch, config.kp_att_yaw], dtype=float
        )
        self._max_tilt_rad = float(config.max_tilt_rad)
        self._max_yaw_rate = float(config.max_yaw_rate_radps)
        # AT-C4a: the bound lives in the same sine space as the error, so no
        # arc-sine and no angle round-trip is needed anywhere in this loop.
        self._tilt_bound = 2.0 * float(np.sin(self._max_tilt_rad / 2.0))

        self._flags: tuple[AttitudeSaturationFlag, ...] = ()
        self._renormalised = False
        self._error_magnitude = 0.0
        self._tilt_magnitude = 0.0
        self._step_count = 0

    # ------------------------------------------------------------------ public
    def update(
        self,
        *,
        attitude_wb: np.ndarray,
        omega_body_radps: np.ndarray,
        reference: AttitudeReference,
    ) -> AttitudeCommand:
        """One control step. Returns the reference for the rate loop.

        ``attitude_wb``
            Current attitude quaternion ``q_wb``, Body FLU -> World ENU, scalar-first
            ``[w, x, y, z]`` (C-12).
        ``omega_body_radps``
            Measured/estimated body angular velocity, Body FLU, rad/s. It is part of
            the frozen signature (AT-5) and is validated, but the proportional law
            below does not consume it: using it would be the rate feedback that AT-21
            forbids, and the reference rate the AT-N2 coupling is about arrives
            explicitly in ``reference.omega_ref_ff_body_radps``.
        ``reference``
            The desired trajectory: ``q_ref_wb`` plus optional angular velocity and
            angular acceleration feedforwards (AT-I4).

        The returned :class:`AttitudeCommand` always carries both fields: the rate
        reference after limiting and feedforward, and the acceleration feedforward -
        **explicitly zero when the reference does not provide one**, never "the last
        value" (RC-FF3 / AT-24).
        """
        attitude = self._unit_quaternion(attitude_wb, "attitude_wb")
        # AT-5 note: accepted and validated as part of the frozen signature, but the
        # purely proportional law below (AT-C1/AT-C4) does not consume it. It is
        # deliberately NOT used for damping - that would be the D term AT-21 forbids -
        # and the reference rate that the AT-N2 coupling is about arrives explicitly in
        # ``reference.omega_ref_ff_body_radps``.
        _ = self._body_vector(omega_body_radps, "omega_body_radps")
        reference_attitude = self._unit_quaternion(reference.q_ref_wb, "q_ref_wb")

        flags: list[AttitudeSaturationFlag] = []

        # --- error (AT-E1/AT-E3/AT-S4) -------------------------------------
        error, _q_error = attitude_error_body_rad(attitude, reference_attitude)
        self._error_magnitude = float(np.linalg.norm(error))
        self._tilt_magnitude = float(np.linalg.norm(error[:2]))

        # --- proportional term (AT-C1) -------------------------------------
        omega_proportional = self._kp * error

        # --- tilt limiting: direction-preserving scale of xy (AT-C4, AT-C4b) --
        tilt = omega_proportional[:2]
        tilt_norm = float(np.linalg.norm(tilt))
        if tilt_norm > 0.0:
            ratio = min(1.0, self._tilt_bound / max(self._tilt_magnitude, _EPSILON_TILT))
            limited_tilt = tilt * ratio
            # Report only a real engagement: at a ratio of exactly 1.0 nothing changed.
            if ratio < 1.0:
                flags.append(AttitudeSaturationFlag.TILT_LIMIT)
        else:
            limited_tilt = tilt

        # --- yaw limiting: per-component saturation (AT-C4b) ----------------
        yaw = float(omega_proportional[2])
        limited_yaw = float(np.clip(yaw, -self._max_yaw_rate, self._max_yaw_rate))
        if limited_yaw != yaw:
            flags.append(AttitudeSaturationFlag.YAW_RATE_LIMIT)

        omega_p = np.array([limited_tilt[0], limited_tilt[1], limited_yaw], dtype=float)

        # --- feedforward (AT-C3, AT-24) -------------------------------------
        # Both feedforwards come from the reference and are added unchanged. The
        # reference RATE is never re-derived by differencing the reference quaternion:
        # RC-U10 forbids differentiating a reference, and the caller has already
        # stated the rate it means.
        if reference.omega_ref_ff_body_radps is None:
            omega_cmd = omega_p
        else:
            omega_cmd = omega_p + reference.omega_ref_ff_body_radps

        if reference.alpha_ref_body_radps2 is None:
            alpha_cmd = np.zeros(VECTOR_SIZE, dtype=float)
        else:
            alpha_cmd = np.array(reference.alpha_ref_body_radps2, dtype=float, copy=True)

        # The measured rate is consumed only through the caller's stated feedforward
        # (AT-N2); it must never feed a damping term here, so it is not used again.
        self._flags = tuple(flags)
        self._step_count += 1

        return AttitudeCommand(
            omega_ref_body_radps=omega_cmd,
            alpha_ref_body_radps2=alpha_cmd,
        )

    def status(self) -> AttitudeControlStatus:
        """Read-only snapshot of the loop's geometry and limit engagement."""
        return AttitudeControlStatus(
            attitude_error_magnitude=self._error_magnitude,
            tilt_error_magnitude=self._tilt_magnitude,
            tilt_error_bound=self._tilt_bound,
            saturated=self._flags,
            quaternion_renormalised=self._renormalised,
            step_count=self._step_count,
        )

    @property
    def tilt_error_bound(self) -> float:
        """The derived bound actually in force, ``2 sin(max_tilt_rad / 2)`` (AT-C4a)."""
        return self._tilt_bound

    @property
    def max_tilt_rad(self) -> float:
        """The configured tilt bound in radians (injected, D-043)."""
        return self._max_tilt_rad

    # --------------------------------------------------------------- internals
    def _unit_quaternion(self, value: np.ndarray, name: str) -> np.ndarray:
        """Validate a quaternion and put it on ``S^3``, recording when it was off.

        The state layer already renormalises every integration step, so a *slightly*
        non-unit quaternion is a known numerical artefact rather than an error, and a
        degenerate one cannot be repaired at all. A material deviation is reported
        through :meth:`status` instead of being corrected in silence.
        """
        array = np.asarray(value, dtype=float).reshape(-1)
        if array.shape != (QUATERNION_SIZE,):
            raise ValueError(f"{name} must have {QUATERNION_SIZE} components, scalar-first (C-12)")
        if not np.all(np.isfinite(array)):
            raise ValueError(f"{name} contains a non-finite component")
        norm = float(np.linalg.norm(array))
        if norm <= 0.0:
            raise ValueError(
                f"{name} has zero norm: no attitude can be recovered from it (C-13)"
            )
        if abs(norm - 1.0) > _UNIT_QUATERNION_TOLERANCE:
            self._renormalised = True
        return array / norm

    @staticmethod
    def _body_vector(value: np.ndarray, name: str) -> np.ndarray:
        array = np.asarray(value, dtype=float).reshape(-1)
        if array.shape != (VECTOR_SIZE,):
            raise ValueError(f"{name} must have {VECTOR_SIZE} components in Body FLU")
        if not np.all(np.isfinite(array)):
            raise ValueError(f"{name} contains a non-finite component")
        return array
