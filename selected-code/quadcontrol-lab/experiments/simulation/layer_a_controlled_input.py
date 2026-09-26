"""Frozen Layer A inputs and offline checks; no plant or controller dependency.

Importing this module does not run the 201-event mechanism comparison. The
controlled run is an explicit caller action reserved for Phase 3.7.7b.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from itertools import accumulate
from math import isfinite, pi
from typing import Literal

import numpy as np

from core.contracts import ImuMeasurement
from core.state.quaternion import (
    euler_zyx_from_quaternion,
    quaternion_from_euler_zyx,
    rotate_world_to_body,
)
from estimation.attitude import AttitudeEstimatorDiagnosticSnapshot, MinimalAttitudeEstimator

CaseId = Literal["LAYER_A1_GRAVITY_CONSISTENT", "LAYER_A2_BODY_Z_DIRECTION"]
LAYER_A1_GRAVITY_CONSISTENT: CaseId = "LAYER_A1_GRAVITY_CONSISTENT"
LAYER_A2_BODY_Z_DIRECTION: CaseId = "LAYER_A2_BODY_Z_DIRECTION"
ROLL_BUDGET_NOT_APPLICABLE: Literal["ROLL_BUDGET_NOT_APPLICABLE"] = (
    "ROLL_BUDGET_NOT_APPLICABLE"
)
INSTRUMENTATION_COMMIT = "64bb6f1bd7dd0103bc03a1317dbd8d4a286bd8b1"
SCHEMA_VERSION = "phase3.7.6a.estimator.v1"
DT_S = 0.01
UPDATE_COUNT = 200
SAMPLE_COUNT = UPDATE_COUNT + 1
HORIZON_S = 2.0
ROLL_RATE_RADPS = 0.2
GRAVITY_MPS2 = 9.80665
NORM_TOLERANCE_MPS2 = 0.5
CORRECTION_GAIN_S = 3.0
SEED_METADATA = 0
_UP_WORLD = np.array([0.0, 0.0, 1.0])


@dataclass(frozen=True, slots=True)
class LayerADescriptor:
    case_id: CaseId
    instrumentation_commit: str = INSTRUMENTATION_COMMIT
    schema_version: str = SCHEMA_VERSION
    dt_s: float = DT_S
    horizon_s: float = HORIZON_S
    sample_count: int = SAMPLE_COUNT
    angular_rate_body_radps: tuple[float, float, float] = (ROLL_RATE_RADPS, 0.0, 0.0)
    gravity_mps2: float = GRAVITY_MPS2
    norm_tolerance_mps2: float = NORM_TOLERANCE_MPS2
    correction_gain_s: float = CORRECTION_GAIN_S
    seed_metadata: int = SEED_METADATA
    truth_trajectory: str = "q_wb=EulerZYX(roll=0.2*t,pitch=0,yaw=0)"
    specific_force_rule: str = ""

    def __post_init__(self) -> None:
        if self.case_id not in (
            "LAYER_A1_GRAVITY_CONSISTENT", "LAYER_A2_BODY_Z_DIRECTION"
        ):
            raise ValueError("unknown Layer A case")
        rule = (
            "g*R(q_truth)^T*e_z" if self.case_id == "LAYER_A1_GRAVITY_CONSISTENT"
            else "g*e_z"
        )
        if self.specific_force_rule and self.specific_force_rule != rule:
            raise ValueError("case and specific-force rule disagree")
        object.__setattr__(self, "specific_force_rule", rule)
        if (self.dt_s, self.horizon_s, self.sample_count) != (DT_S, HORIZON_S, SAMPLE_COUNT):
            raise ValueError("Layer A timing is frozen")
        if (self.gravity_mps2, self.norm_tolerance_mps2, self.correction_gain_s) != (
            GRAVITY_MPS2, NORM_TOLERANCE_MPS2, CORRECTION_GAIN_S
        ):
            raise ValueError("Layer A estimator configuration is frozen")
        if (
            self.instrumentation_commit != INSTRUMENTATION_COMMIT
            or self.schema_version != SCHEMA_VERSION
            or self.angular_rate_body_radps != (ROLL_RATE_RADPS, 0.0, 0.0)
            or self.seed_metadata != SEED_METADATA
            or self.truth_trajectory != "q_wb=EulerZYX(roll=0.2*t,pitch=0,yaw=0)"
        ):
            raise ValueError("Layer A identity and trajectory are frozen")

    @property
    def input_sha256(self) -> str:
        encoded = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class LayerASample:
    index: int
    measurement: ImuMeasurement
    truth_q_wb: np.ndarray

    def __post_init__(self) -> None:
        q = np.asarray(self.truth_q_wb, dtype=float)
        if q.shape != (4,) or not np.all(np.isfinite(q)):
            raise ValueError("truth quaternion must be finite and four-dimensional")
        object.__setattr__(self, "truth_q_wb", np.frombuffer(q.tobytes(), dtype=float))


def layer_a_sample(case_id: CaseId, index: int) -> LayerASample:
    """Build one frozen synthetic measurement; truth stays outside estimator API."""
    LayerADescriptor(case_id)  # Reject unknown cases before generating data.
    if type(index) is not int or not 0 <= index <= UPDATE_COUNT:
        raise ValueError("sample index must be an integer in [0, 200]")
    time_s = DT_S * index
    truth_q = quaternion_from_euler_zyx(ROLL_RATE_RADPS * time_s, 0.0, 0.0)
    if case_id == "LAYER_A1_GRAVITY_CONSISTENT":
        force = GRAVITY_MPS2 * rotate_world_to_body(truth_q, _UP_WORLD)
    else:
        force = np.array([0.0, 0.0, GRAVITY_MPS2])
    return LayerASample(
        index=index,
        measurement=ImuMeasurement(
            time_s=time_s,
            angular_velocity_body_radps=np.array([ROLL_RATE_RADPS, 0.0, 0.0]),
            specific_force_body_mps2=force,
        ),
        truth_q_wb=truth_q,
    )


def run_layer_a_case(
    case_id: CaseId,
) -> tuple[tuple[LayerASample, AttitudeEstimatorDiagnosticSnapshot], ...]:
    """Explicit 201-event execution entry; do not call before Phase 3.7.7b."""
    estimator = MinimalAttitudeEstimator(
        gravity_mps2=GRAVITY_MPS2,
        gravity_tolerance_mps2=NORM_TOLERANCE_MPS2,
        gravity_correction_gain_s=CORRECTION_GAIN_S,
        diagnostics_enabled=True,
    )
    records: list[tuple[LayerASample, AttitudeEstimatorDiagnosticSnapshot]] = []
    for index in range(SAMPLE_COUNT):
        sample = layer_a_sample(case_id, index)
        if index == 0:
            estimator.initialize(sample.measurement)
        else:
            estimator.update(sample.measurement)
        snapshot = estimator.diagnostic_snapshot
        if snapshot is None:
            raise RuntimeError("diagnostic snapshot missing")
        records.append((sample, snapshot))
    return tuple(records)


@dataclass(frozen=True, slots=True)
class LayerAFormulaCheckAudit:
    """Five-field checker result; None residual means the check was not computable."""

    passed: bool
    max_abs_residual: float | None
    checked_field_count: int
    failed_field_count: int
    invalid_reason: str | None = None

    def __post_init__(self) -> None:
        if self.checked_field_count not in (0, 5):
            raise ValueError("checker audit covers exactly five fields or is invalid")
        if not 0 <= self.failed_field_count <= self.checked_field_count:
            raise ValueError("failed field count is outside checked fields")
        if self.checked_field_count == 0:
            if self.passed or self.max_abs_residual is not None or not self.invalid_reason:
                raise ValueError("invalid checker audit has no numeric residual")
        elif (
            self.max_abs_residual is None
            or not isfinite(self.max_abs_residual)
            or self.max_abs_residual < 0.0
            or self.invalid_reason is not None
            or self.passed != (self.failed_field_count == 0)
            or self.passed != (self.max_abs_residual <= 1e-10)
        ):
            raise ValueError("computed checker audit fields disagree")


@dataclass(frozen=True, slots=True)
class LayerAFormulaCaseAudit:
    """Aggregate of exactly 200 valid UPDATE checks for one frozen case."""

    checked_update_count: int
    failure_count: int
    max_abs_residual: float
    passed: bool

    def to_schema_record(self) -> dict[str, int | float | bool]:
        return {
            "checked_update_count": self.checked_update_count,
            "failure_count": self.failure_count,
            "max_abs_residual": self.max_abs_residual,
            "pass": self.passed,
        }


def _invalid_formula_audit(reason: str) -> LayerAFormulaCheckAudit:
    return LayerAFormulaCheckAudit(
        passed=False,
        max_abs_residual=None,
        checked_field_count=0,
        failed_field_count=0,
        invalid_reason=reason,
    )


def audit_update_formula(snapshot: AttitudeEstimatorDiagnosticSnapshot) -> LayerAFormulaCheckAudit:
    """Single source of the frozen formula and its five absolute residuals.

    Vector/scalar residual is max(abs(actual-expected)). The quaternion residual
    is min(max(abs(q-expected)), max(abs(q+expected))). A non-computable check
    fails with no numeric residual; it cannot enter a case aggregate.
    """
    if snapshot.event_kind != "UPDATE" or snapshot.correction_gate_pass is not True:
        return _invalid_formula_audit("UPDATE_WITH_APPLIED_GATE_REQUIRED")
    if any(
        getattr(snapshot, key) is None
        for key in (
            "q_pred_wb",
            "predicted_up_body",
            "measured_specific_force_direction_body",
            "alignment_error_body",
            "correction_fraction",
            "delta_q_body",
            "dt_s",
        )
    ):
        return _invalid_formula_audit("MISSING_CHECKED_FIELD")
    assert snapshot.q_pred_wb is not None
    assert snapshot.predicted_up_body is not None
    assert snapshot.measured_specific_force_direction_body is not None
    assert snapshot.alignment_error_body is not None
    assert snapshot.correction_fraction is not None
    assert snapshot.delta_q_body is not None
    assert snapshot.dt_s is not None
    try:
        force = np.asarray(snapshot.specific_force_body_mps2, dtype=float)
        force_norm = float(np.linalg.norm(force))
        if (
            not isfinite(force_norm)
            or force_norm <= 0.0
            or not isfinite(snapshot.specific_force_norm)
            or abs(force_norm - snapshot.specific_force_norm) > 1e-10
            or abs(force_norm - GRAVITY_MPS2) > NORM_TOLERANCE_MPS2
        ):
            return _invalid_formula_audit("SPECIFIC_FORCE_NORM_PRECONDITION_FAILED")
        measured = force / force_norm
        predicted = rotate_world_to_body(snapshot.q_pred_wb, _UP_WORLD)
        residual = np.cross(measured, predicted)
        fraction = min(1.0, CORRECTION_GAIN_S * snapshot.dt_s)
        expected_delta = np.concatenate(([1.0], 0.5 * fraction * residual))
        expected_delta /= np.linalg.norm(expected_delta)
        comparisons = (
            (np.asarray(snapshot.measured_specific_force_direction_body), measured),
            (np.asarray(snapshot.predicted_up_body), predicted),
            (np.asarray(snapshot.alignment_error_body), residual),
            (np.asarray([snapshot.correction_fraction]), np.asarray([fraction])),
        )
        field_residuals = [
            float(np.max(np.abs(actual - expected))) for actual, expected in comparisons
        ]
        actual_delta = np.asarray(snapshot.delta_q_body)
        quaternion_residual = min(
            float(np.max(np.abs(actual_delta - expected_delta))),
            float(np.max(np.abs(actual_delta + expected_delta))),
        )
        field_residuals.append(quaternion_residual)
        if len(field_residuals) != 5 or not all(isfinite(value) for value in field_residuals):
            return _invalid_formula_audit("NONFINITE_CHECKED_FIELD")
    except (TypeError, ValueError, FloatingPointError):
        return _invalid_formula_audit("NONCOMPUTABLE_CHECKED_FIELD")
    failed = sum(value > 1e-10 for value in field_residuals)
    return LayerAFormulaCheckAudit(
        passed=failed == 0,
        max_abs_residual=max(field_residuals),
        checked_field_count=5,
        failed_field_count=failed,
    )


def check_update_formula(snapshot: AttitudeEstimatorDiagnosticSnapshot) -> bool:
    """Compatibility bool API: delegate to the one frozen formula implementation."""
    return audit_update_formula(snapshot).passed


def aggregate_formula_audits(
    audits: tuple[LayerAFormulaCheckAudit, ...],
    *,
    update_event_count: int,
) -> LayerAFormulaCaseAudit:
    """Reject incomplete/nonfinite UPDATE audit sets rather than hiding events."""
    if update_event_count != UPDATE_COUNT or len(audits) != update_event_count:
        raise ValueError("case checker aggregation requires exactly 200 UPDATE events")
    if any(
        not isinstance(audit, LayerAFormulaCheckAudit)
        or audit.checked_field_count != 5
        or audit.max_abs_residual is None
        or not isfinite(audit.max_abs_residual)
        for audit in audits
    ):
        raise ValueError("case checker aggregation received an invalid audit")
    failures = sum(not audit.passed for audit in audits)
    maximum = max(audit.max_abs_residual for audit in audits if audit.max_abs_residual is not None)
    return LayerAFormulaCaseAudit(
        checked_update_count=len(audits),
        failure_count=failures,
        max_abs_residual=maximum,
        passed=failures == 0,
    )


@dataclass(frozen=True, slots=True)
class RollDiagnostic:
    truth_roll_rad: float
    predicted_roll_rad: float
    corrected_roll_rad: float
    correction_roll_increment_rad: float
    estimator_roll_error_rad: float


def roll_diagnostic(
    truth_q_wb: np.ndarray,
    snapshot: AttitudeEstimatorDiagnosticSnapshot,
    *,
    previous_rolls: tuple[float, float, float] | None = None,
) -> RollDiagnostic | Literal["ROLL_BUDGET_NOT_APPLICABLE"]:
    """Euler audit only for pure roll, away from wrapping and singularities."""
    if snapshot.q_pred_wb is None:
        return ROLL_BUDGET_NOT_APPLICABLE
    quaternions = (truth_q_wb, snapshot.q_pred_wb, snapshot.q_after_correction_wb)
    angles = tuple(euler_zyx_from_quaternion(q) for q in quaternions)
    if any(
        not all(isfinite(value) for value in triplet)
        or abs(triplet[1]) >= 1e-6 or abs(triplet[2]) >= 1e-6
        or abs(triplet[0]) >= pi - 1e-6
        for triplet in angles
    ):
        return ROLL_BUDGET_NOT_APPLICABLE
    rolls = tuple(triplet[0] for triplet in angles)
    if previous_rolls is not None and any(
        abs(current - previous) >= pi
        for current, previous in zip(rolls, previous_rolls, strict=True)
    ):
        return ROLL_BUDGET_NOT_APPLICABLE
    if abs(rolls[2] - rolls[1]) >= pi:
        return ROLL_BUDGET_NOT_APPLICABLE
    return RollDiagnostic(
        truth_roll_rad=rolls[0],
        predicted_roll_rad=rolls[1],
        corrected_roll_rad=rolls[2],
        correction_roll_increment_rad=rolls[2] - rolls[1],
        estimator_roll_error_rad=rolls[2] - rolls[0],
    )


@dataclass(frozen=True, slots=True)
class LayerACriterionInputs:
    initialization_identical: bool
    gyro_identical: bool
    force_norm_identical: bool
    timestamps_identical: bool
    estimator_parameters_identical: bool
    a2_formula_consistent: bool
    a2_roll_correction_increments_rad: tuple[float, ...] | None
    a1_terminal_attitude_error_rad: float
    a2_terminal_attitude_error_rad: float


@dataclass(frozen=True, slots=True)
class LayerACriterionAudit:
    input_parity_pass: bool
    formula_consistency_pass: bool
    sustained_negative_correction_pass: bool
    terminal_error_gap_pass: bool
    passed: bool


def audit_layer_a_criteria(inputs: LayerACriterionInputs) -> LayerACriterionAudit:
    """One source for the predeclared Layer A component and combined rules.

    "Sustained negative accumulation" means each cumulative sum after an
    UPDATE remains strictly negative. No result data is loaded by this rule.
    """
    values = (inputs.a1_terminal_attitude_error_rad, inputs.a2_terminal_attitude_error_rad)
    if any(not isfinite(value) or value < 0.0 for value in values):
        raise ValueError("terminal attitude errors must be finite and non-negative")
    increments = inputs.a2_roll_correction_increments_rad
    if increments is not None and (
        len(increments) != UPDATE_COUNT or any(not isfinite(value) for value in increments)
    ):
        raise ValueError("A2 requires 200 finite UPDATE correction increments")
    parity_pass = (
        inputs.initialization_identical
        and inputs.gyro_identical
        and inputs.force_norm_identical
        and inputs.timestamps_identical
        and inputs.estimator_parameters_identical
    )
    sustained_negative = (
        increments is not None and all(total < 0.0 for total in accumulate(increments))
    )
    terminal_gap = values[1] - values[0] >= 0.01
    return LayerACriterionAudit(
        input_parity_pass=parity_pass,
        formula_consistency_pass=inputs.a2_formula_consistent,
        sustained_negative_correction_pass=sustained_negative,
        terminal_error_gap_pass=terminal_gap,
        passed=parity_pass and inputs.a2_formula_consistent and sustained_negative and terminal_gap,
    )


def evaluate_layer_a_criteria(inputs: LayerACriterionInputs) -> bool:
    """Compatibility bool API; all criterion math lives in the audit function."""
    return audit_layer_a_criteria(inputs).passed
