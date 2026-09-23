"""Quaternion and rotation utilities - the frozen attitude conventions.

Every function here implements a numbered formula from
``docs/theory/coordinate_convention.md``. The conventions are FROZEN:

* quaternion storage ``[w, x, y, z]`` (scalar first), Hamilton product
* ``q_wb`` means **body -> world**
* ``R_wb = R(q_wb)`` maps ``v_b`` to ``v_w``; ``R_bw = R_wb.T``
* attitude kinematics ``qdot = 0.5 * q (x) [0, omega_b]`` with ``omega_b`` in the
  **body** frame

Nothing in this module knows about parameters, dynamics or control: it is pure
attitude algebra, which is why it can be verified against closed-form identities.
"""

from __future__ import annotations

import numpy as np

from core.state.errors import QuaternionError

__all__ = [
    "QUATERNION_SIZE",
    "VECTOR_SIZE",
    "euler_zyx_from_quaternion",
    "identity_quaternion",
    "is_unit_quaternion",
    "quaternion_conjugate",
    "quaternion_derivative",
    "quaternion_from_euler_zyx",
    "quaternion_from_rotation",
    "quaternion_inverse",
    "quaternion_multiply",
    "quaternion_norm",
    "rotate_body_to_world",
    "rotate_world_to_body",
    "rotation_from_quaternion",
    "skew_symmetric",
]

QUATERNION_SIZE = 4
VECTOR_SIZE = 3

#: Default tolerance for the unit-norm requirement (C-13).
UNIT_NORM_TOLERANCE = 1e-9

_IDENTITY = np.array([1.0, 0.0, 0.0, 0.0], dtype=float)


def _as_quaternion(quaternion: np.ndarray, *, name: str = "quaternion") -> np.ndarray:
    array = np.asarray(quaternion, dtype=float)
    if array.shape != (QUATERNION_SIZE,):
        raise QuaternionError(f"{name} must have shape (4,), got {array.shape}")
    if not np.all(np.isfinite(array)):
        raise QuaternionError(f"{name} contains a non-finite component")
    return array


def _as_vector(vector: np.ndarray, *, name: str = "vector") -> np.ndarray:
    array = np.asarray(vector, dtype=float)
    if array.shape != (VECTOR_SIZE,):
        raise QuaternionError(f"{name} must have shape (3,), got {array.shape}")
    if not np.all(np.isfinite(array)):
        raise QuaternionError(f"{name} contains a non-finite component")
    return array


def quaternion_norm(quaternion: np.ndarray) -> float:
    """Euclidean norm ``||q||`` (formula C-13)."""
    return float(np.linalg.norm(_as_quaternion(quaternion)))


def is_unit_quaternion(quaternion: np.ndarray, *, tolerance: float = UNIT_NORM_TOLERANCE) -> bool:
    """Whether ``| ||q|| - 1 | <= tolerance`` (formula C-13)."""
    return abs(quaternion_norm(quaternion) - 1.0) <= tolerance


def _require_unit(quaternion: np.ndarray, *, name: str = "quaternion") -> np.ndarray:
    array = _as_quaternion(quaternion, name=name)
    error = abs(float(np.linalg.norm(array)) - 1.0)
    if error > UNIT_NORM_TOLERANCE:
        raise QuaternionError(
            f"{name} is not a unit quaternion: | ||q|| - 1 | = {error:.3e} exceeds "
            f"{UNIT_NORM_TOLERANCE:.1e} (C-13). Renormalise it explicitly (S-2) "
            f"rather than letting this layer hide the drift."
        )
    return array


def quaternion_multiply(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """Hamilton product ``left (x) right`` (formula C-14).

    ``q_wc = q_wb (x) q_bc`` composes rotations, so the indices adjacent to the
    operator cancel (formula C-17). A swapped argument order is a real bug, not a
    sign convention: it produces the inverse composite.
    """
    w1, x1, y1, z1 = _as_quaternion(left, name="left")
    w2, x2, y2, z2 = _as_quaternion(right, name="right")
    return np.array(
        [
            w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
            w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
            w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        ],
        dtype=float,
    )


def quaternion_conjugate(quaternion: np.ndarray) -> np.ndarray:
    """``q* = [w, -x, -y, -z]`` (formula C-15)."""
    w, x, y, z = _as_quaternion(quaternion)
    return np.array([w, -x, -y, -z], dtype=float)


def quaternion_inverse(quaternion: np.ndarray) -> np.ndarray:
    """``q^-1 = q* / ||q||^2`` (formula C-16)."""
    array = _as_quaternion(quaternion)
    squared = float(array @ array)
    if squared <= 0.0:
        raise QuaternionError("cannot invert a zero quaternion")
    return quaternion_conjugate(array) / squared


def rotation_from_quaternion(quaternion: np.ndarray) -> np.ndarray:
    """``R_wb(q)``, the frozen explicit form (formula C-9).

    Returns the **body -> world** rotation matrix, so ``v_w = R_wb @ v_b``
    (formula C-5) and ``R_bw = R_wb.T`` (formula C-6).
    """
    w, x, y, z = _require_unit(quaternion)
    return np.array(
        [
            [1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - w * z), 2.0 * (x * z + w * y)],
            [2.0 * (x * y + w * z), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - w * x)],
            [2.0 * (x * z - w * y), 2.0 * (y * z + w * x), 1.0 - 2.0 * (x * x + y * y)],
        ],
        dtype=float,
    )


def quaternion_from_rotation(rotation: np.ndarray) -> np.ndarray:
    """Invert formula C-9: recover ``q`` from a rotation matrix.

    Returns the representative with ``w >= 0``; ``-q`` is the same attitude
    (double cover), so callers must compare up to sign.
    """
    matrix = np.asarray(rotation, dtype=float)
    if matrix.shape != (3, 3):
        raise QuaternionError(f"rotation must have shape (3, 3), got {matrix.shape}")
    if not np.all(np.isfinite(matrix)):
        raise QuaternionError("rotation contains a non-finite entry")

    trace = float(matrix[0, 0] + matrix[1, 1] + matrix[2, 2])
    if trace > 0.0:
        scale = 2.0 * np.sqrt(trace + 1.0)
        quaternion = np.array(
            [
                0.25 * scale,
                (matrix[2, 1] - matrix[1, 2]) / scale,
                (matrix[0, 2] - matrix[2, 0]) / scale,
                (matrix[1, 0] - matrix[0, 1]) / scale,
            ]
        )
    elif matrix[0, 0] > matrix[1, 1] and matrix[0, 0] > matrix[2, 2]:
        scale = 2.0 * np.sqrt(1.0 + matrix[0, 0] - matrix[1, 1] - matrix[2, 2])
        quaternion = np.array(
            [
                (matrix[2, 1] - matrix[1, 2]) / scale,
                0.25 * scale,
                (matrix[0, 1] + matrix[1, 0]) / scale,
                (matrix[0, 2] + matrix[2, 0]) / scale,
            ]
        )
    elif matrix[1, 1] > matrix[2, 2]:
        scale = 2.0 * np.sqrt(1.0 + matrix[1, 1] - matrix[0, 0] - matrix[2, 2])
        quaternion = np.array(
            [
                (matrix[0, 2] - matrix[2, 0]) / scale,
                (matrix[0, 1] + matrix[1, 0]) / scale,
                0.25 * scale,
                (matrix[1, 2] + matrix[2, 1]) / scale,
            ]
        )
    else:
        scale = 2.0 * np.sqrt(1.0 + matrix[2, 2] - matrix[0, 0] - matrix[1, 1])
        quaternion = np.array(
            [
                (matrix[1, 0] - matrix[0, 1]) / scale,
                (matrix[0, 2] + matrix[2, 0]) / scale,
                (matrix[1, 2] + matrix[2, 1]) / scale,
                0.25 * scale,
            ]
        )

    quaternion = quaternion / np.linalg.norm(quaternion)
    if quaternion[0] < 0.0:
        quaternion = -quaternion
    return quaternion


def rotate_body_to_world(quaternion: np.ndarray, vector_body: np.ndarray) -> np.ndarray:
    """``v_w = R_wb v_b`` (formulas C-5 / C-18)."""
    return rotation_from_quaternion(quaternion) @ _as_vector(vector_body, name="vector_body")


def rotate_world_to_body(quaternion: np.ndarray, vector_world: np.ndarray) -> np.ndarray:
    """``v_b = R_bw v_w = R_wb^T v_w`` (formula C-6)."""
    return rotation_from_quaternion(quaternion).T @ _as_vector(vector_world, name="vector_world")


def skew_symmetric(vector: np.ndarray) -> np.ndarray:
    """``[u]x`` with ``[u]x v = u x v`` (formula C-10)."""
    u_x, u_y, u_z = _as_vector(vector)
    return np.array([[0.0, -u_z, u_y], [u_z, 0.0, -u_x], [-u_y, u_x, 0.0]], dtype=float)


def quaternion_derivative(quaternion: np.ndarray, angular_velocity_body: np.ndarray) -> np.ndarray:
    """``qdot_wb = 0.5 * q_wb (x) [0, omega_b]`` (formula C-19).

    ``angular_velocity_body`` is expressed in the **body** frame. Passing a
    world-frame rate here is the single most common attitude bug; formula C-21
    gives the conversion when a world rate is what you actually have.
    """
    omega = _as_vector(angular_velocity_body, name="angular_velocity_body")
    return 0.5 * quaternion_multiply(_as_quaternion(quaternion), np.concatenate(([0.0], omega)))


def quaternion_from_euler_zyx(roll_rad: float, pitch_rad: float, yaw_rad: float) -> np.ndarray:
    """ZYX intrinsic Euler angles to quaternion (formula C-23 / C-24).

    ``R_wb = Rz(psi) Ry(theta) Rx(phi)``. Euler angles are a *derived* output of
    the platform (formula S-4); this helper exists for input, tests and plots, not
    as an internal state representation.
    """
    half_roll, half_pitch, half_yaw = roll_rad / 2.0, pitch_rad / 2.0, yaw_rad / 2.0
    q_roll = np.array([np.cos(half_roll), np.sin(half_roll), 0.0, 0.0])
    q_pitch = np.array([np.cos(half_pitch), 0.0, np.sin(half_pitch), 0.0])
    q_yaw = np.array([np.cos(half_yaw), 0.0, 0.0, np.sin(half_yaw)])
    return quaternion_multiply(quaternion_multiply(q_yaw, q_pitch), q_roll)


def euler_zyx_from_quaternion(quaternion: np.ndarray) -> tuple[float, float, float]:
    """Quaternion to ZYX intrinsic Euler angles ``(phi, theta, psi)`` (formula S-4).

    Only valid outside the gimbal-lock singularity at ``theta = +-pi/2``; the core
    state never uses Euler angles, so the singularity cannot reach the dynamics.
    """
    w, x, y, z = _require_unit(quaternion)
    sin_pitch = 2.0 * (w * y - z * x)
    clamped = float(np.clip(sin_pitch, -1.0, 1.0))
    pitch = float(np.arcsin(clamped))
    if abs(clamped) > 1.0 - 1e-12:
        # Gimbal lock: roll and yaw are not separable. Return a canonical split.
        roll = float(np.arctan2(2.0 * (x * y + w * z), 1.0 - 2.0 * (y * y + z * z)))
        return roll, pitch, 0.0
    roll = float(np.arctan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y)))
    yaw = float(np.arctan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z)))
    return roll, pitch, yaw


def identity_quaternion() -> np.ndarray:
    """The attitude ``q = [1, 0, 0, 0]``, i.e. ``R_wb = I``."""
    return _IDENTITY.copy()
