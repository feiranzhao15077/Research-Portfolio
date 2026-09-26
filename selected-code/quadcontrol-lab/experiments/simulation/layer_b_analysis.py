"""Predeclared Layer B mechanism evaluators; no simulation or file I/O."""

from __future__ import annotations

from math import atan2, fsum, isfinite, pi
from typing import Any

import numpy as np

from core.state.quaternion import euler_zyx_from_quaternion, rotate_world_to_body
from estimation.attitude import AttitudeEstimatorDiagnosticSnapshot
from experiments.simulation.layer_a_controlled_input import (
    ROLL_BUDGET_NOT_APPLICABLE,
    LayerAFormulaCaseAudit,
    LayerAFormulaCheckAudit,
    audit_update_formula,
    roll_diagnostic,
)
from experiments.simulation.layer_b_diagnostic import LayerBDiagnosticEvent

_UP = np.array([0.0, 0.0, 1.0])


def aggregate_layer_b_formula(
    audits: tuple[LayerAFormulaCheckAudit, ...], *, gate_pass_count: int,
) -> LayerAFormulaCaseAudit:
    """Use frozen per-UPDATE audits and their case-audit record for a variable event count.

    Layer A's aggregation entry is fixed at 200 updates. Layer B has up to 999;
    only the count aggregation is adapted, never the five-field formula or tolerance.
    """
    if len(audits) != gate_pass_count or gate_pass_count < 0:
        raise ValueError("formula audit count differs from gate-pass UPDATE count")
    if any(
        audit.checked_field_count != 5 or audit.max_abs_residual is None
        or not isfinite(audit.max_abs_residual) for audit in audits
    ):
        raise ValueError("noncomputable Layer A formula audit")
    failures = sum(not audit.passed for audit in audits)
    return LayerAFormulaCaseAudit(
        checked_update_count=gate_pass_count, failure_count=failures,
        max_abs_residual=max(
            ((audit.max_abs_residual or 0.0) for audit in audits), default=0.0,
        ),
        passed=failures == 0,
    )


def _angle(left: np.ndarray, right: np.ndarray) -> float:
    return atan2(float(np.linalg.norm(np.cross(left, right))), float(np.dot(left, right)))


def evaluate_b1(events: tuple[LayerBDiagnosticEvent, ...]) -> dict[str, Any]:
    eligible = qualifying = 0
    for event in events[1:]:
        if event.correction_gate_pass is not True:
            continue
        truth = np.asarray(event.truth_q_wb)
        roll = euler_zyx_from_quaternion(truth)[0]
        if abs(roll) < 0.01:
            continue
        force = np.asarray(event.specific_force_body_mps2)
        norm = float(np.linalg.norm(force))
        if not isfinite(norm) or norm <= 0.0:
            return {"status": "UNRESOLVED", "eligible_count": eligible,
                    "simultaneous_pass_count": qualifying, "pass_fraction": None}
        if event.measured_specific_force_direction_body is None:
            return {"status": "UNRESOLVED", "eligible_count": eligible,
                    "simultaneous_pass_count": qualifying, "pass_fraction": None}
        measured = np.asarray(event.measured_specific_force_direction_body)
        measured_norm = float(np.linalg.norm(measured))
        if not np.all(np.isfinite(measured)) or not isfinite(measured_norm) or measured_norm <= 0:
            return {"status": "UNRESOLVED", "eligible_count": eligible,
                    "simultaneous_pass_count": qualifying, "pass_fraction": None}
        measured = measured / measured_norm
        eligible += 1
        truth_up = rotate_world_to_body(truth, _UP)
        if _angle(measured, _UP) <= 1e-10 and _angle(measured, truth_up) >= 0.01:
            qualifying += 1
    return {
        "status": "UNRESOLVED" if eligible == 0 else (
            "PASS" if 10 * qualifying >= 9 * eligible else "FAIL"
        ),
        "eligible_count": eligible,
        "simultaneous_pass_count": qualifying,
        "pass_fraction": qualifying / eligible if eligible else None,
    }


def _pure_roll(quaternion: np.ndarray) -> float | None:
    if quaternion.shape != (4,) or not np.all(np.isfinite(quaternion)):
        return None
    if abs(float(np.linalg.norm(quaternion)) - 1.0) > 1e-9:
        return None
    roll, pitch, yaw = euler_zyx_from_quaternion(quaternion)
    if not all(isfinite(x) for x in (roll, pitch, yaw)):
        return None
    if abs(pitch) >= 1e-6 or abs(yaw) >= 1e-6 or abs(roll) >= pi - 1e-6:
        return None
    return roll


def evaluate_roll_budget(
    events: tuple[LayerBDiagnosticEvent, ...],
    snapshots: tuple[AttitudeEstimatorDiagnosticSnapshot, ...],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reuse the frozen roll gate, then apply the 3.7.8 E/P/C budget exactly."""
    unresolved_b2 = {"status": "UNRESOLVED", "eligible_count": 0, "evaluated_count": 0,
                     "prefix_violation_count": None, "cumulative_signed_correction": None}
    unresolved_b3 = {"status": "UNRESOLVED", "roll_applicable": False,
                     "S_P": None, "S_C": None, "sum_abs_P": None, "sum_abs_C": None,
                     "closure_residual": None, "R_C": None, "terminal_roll_error": None}
    if len(events) != len(snapshots) or not events:
        return unresolved_b2, unresolved_b3
    t0_truth = _pure_roll(np.asarray(events[0].truth_q_wb))
    t0_after = _pure_roll(np.asarray(events[0].q_after_correction_wb))
    if t0_truth is None or t0_after is None:
        return unresolved_b2, unresolved_b3
    previous = (t0_truth, t0_after, t0_after)
    previous_truth, previous_after = t0_truth, t0_after
    p_values: list[float] = []
    c_values: list[float] = []
    first_gate: int | None = None
    terminal_error = t0_after - t0_truth
    for index, (event, snapshot) in enumerate(zip(events[1:], snapshots[1:], strict=True), 1):
        if event.correction_gate_pass is True and first_gate is None:
            first_gate = index
        roll = roll_diagnostic(np.asarray(event.truth_q_wb), snapshot, previous_rolls=previous)
        if roll == ROLL_BUDGET_NOT_APPLICABLE:
            return unresolved_b2, unresolved_b3
        correction = roll.correction_roll_increment_rad
        if event.correction_gate_pass is False and correction != 0.0:
            return unresolved_b2, unresolved_b3
        propagation = (
            (roll.predicted_roll_rad - previous_after)
            - (roll.truth_roll_rad - previous_truth)
        )
        p_values.append(propagation)
        c_values.append(correction)
        previous_truth = roll.truth_roll_rad
        previous_after = roll.corrected_roll_rad
        previous = (roll.truth_roll_rad, roll.predicted_roll_rad, roll.corrected_roll_rad)
        terminal_error = roll.estimator_roll_error_rad
    s_p, s_c = fsum(p_values), fsum(c_values)
    closure = abs((terminal_error - (t0_after - t0_truth)) - s_p - s_c)
    denominator = abs(s_c) + abs(s_p)
    ratio = abs(s_c) / denominator if denominator != 0.0 else None
    violations = (
        sum(fsum(c_values[:index]) >= 0.0 for index in range(first_gate, len(c_values) + 1))
        if first_gate is not None else None
    )
    b2 = {
        "status": "UNRESOLVED" if first_gate is None else (
            "PASS" if violations == 0 else "FAIL"
        ),
        "eligible_count": sum(event.correction_gate_pass is True for event in events[1:]),
        "evaluated_count": len(c_values) - first_gate + 1 if first_gate is not None else 0,
        "prefix_violation_count": violations,
        "cumulative_signed_correction": s_c,
    }
    b3 = {
        "status": "UNRESOLVED" if ratio is None or closure > 1e-9 else (
            "PASS" if terminal_error < -0.01 and s_c < 0 and ratio > 0.5 else "FAIL"
        ),
        "roll_applicable": True,
        "S_P": s_p, "S_C": s_c,
        "sum_abs_P": fsum(abs(value) for value in p_values),
        "sum_abs_C": fsum(abs(value) for value in c_values),
        "closure_residual": closure, "R_C": ratio,
        "terminal_roll_error": terminal_error,
    }
    return b2, b3


def evaluate_formula(
    snapshots: tuple[AttitudeEstimatorDiagnosticSnapshot, ...],
) -> dict[str, Any]:
    audits = tuple(audit_update_formula(snapshot) for snapshot in snapshots[1:]
                   if snapshot.correction_gate_pass is True)
    count = sum(snapshot.correction_gate_pass is True for snapshot in snapshots[1:])
    try:
        result = aggregate_layer_b_formula(audits, gate_pass_count=count)
    except ValueError:
        return {"status": "UNRESOLVED", "checked_update_count": count,
                "failure_count": None, "max_abs_residual": None, "passed": False}
    return {"status": "PASS" if result.passed else "UNRESOLVED",
            **result.to_schema_record()}


def final_status(*, equivalence: bool, event_integrity: bool, formula: dict[str, Any],
                 b1: dict[str, Any], b2: dict[str, Any], b3: dict[str, Any]) -> str:
    if not equivalence or not event_integrity or formula["status"] != "PASS":
        return "UNRESOLVED"
    statuses = (b1["status"], b2["status"], b3["status"])
    if "UNRESOLVED" in statuses:
        return "UNRESOLVED"
    return "SUPPORTED" if all(value == "PASS" for value in statuses) else "NOT_SUPPORTED"
