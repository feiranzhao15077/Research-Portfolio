"""Layer B synthetic fixtures and a 20-step test-only diagnostic probe."""

from __future__ import annotations

import copy
import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from core.state.quaternion import quaternion_from_euler_zyx
from experiments.simulation.baseline import BaselineScenario
from experiments.simulation.environment_fingerprint import EnvironmentFingerprint
from experiments.simulation.estimated_attitude_loop import EstimatedAttitudeLoopExperiment
from experiments.simulation.layer_b_analysis import (
    evaluate_b1,
    evaluate_formula,
    evaluate_roll_budget,
    final_status,
)
from experiments.simulation.layer_b_assembler import assemble_layer_b_result
from experiments.simulation.layer_b_diagnostic import (
    collect_diagnostic_events,
    validate_event_sequence,
)
from experiments.simulation.layer_b_equivalence import (
    ROW_FIELDS,
    LayerBEquivalenceAudit,
    LayerBReplayEquivalenceChecker,
)
from experiments.simulation.layer_b_identity import (
    PARENT_ENVIRONMENT,
    PARENT_HASHES,
    build_descriptor,
    canonical_bytes,
    identity_for,
)
from experiments.simulation.layer_b_schema import validate_layer_b_result
from experiments.simulation.layer_b_writer import LayerBResultWriter, validate_published_layer_b
from experiments.simulation.paired_configuration import (
    PairedExperimentConfig,
    build_paired_experiments,
    load_paired_config,
)

HEAD = "a" * 40


def _fingerprint() -> EnvironmentFingerprint:
    return EnvironmentFingerprint(
        "3.11.9", "Windows-10-10.0.26200-SP0",
        (("numpy", "2.4.6"), ("PyYAML", "6.0.3"), ("scipy", "1.17.1")),
    )


def _failed_record() -> dict[str, Any]:
    descriptor = build_descriptor(execution_commit=HEAD, environment_fingerprint=_fingerprint())
    return {
        "schema_version": descriptor.result_schema_version,
        "experiment_id": descriptor.experiment_id,
        "execution_commit": HEAD,
        "protocol_commit": descriptor.protocol_commit,
        "instrumentation_commit": descriptor.instrumentation_commit,
        "diagnostic_schema_version": descriptor.diagnostic_schema_version,
        "parent_experiment_id": descriptor.parent_experiment_id,
        "parent_protocol_commit": descriptor.parent_protocol_commit,
        "parent_config_hash": descriptor.parent_config_hash,
        "parent_environment_fingerprint": PARENT_ENVIRONMENT,
        "parent_manifest_sha256": PARENT_HASHES["manifest.json"],
        "parent_ideal_sha256": PARENT_HASHES["IDEAL_BENCHMARK.json"],
        "parent_estimated_sha256": PARENT_HASHES["ESTIMATED_ATTITUDE_LOOP.json"],
        "environment_fingerprint": descriptor.payload()["environment_fingerprint"],
        "criterion_identity": descriptor.criterion_identity,
        "replay_equivalence": {
            "checked_row_count": 0, "checked_field_count": 0, "mismatch_count": 1,
            "first_mismatch_location": "trajectory.length",
            "per_field_mismatch": {"trajectory": 1}, "passed": False,
        },
        "replay_shared_record": {
            "metadata": {}, "experiment": {}, "initialization": {}, "audit": {}, "trajectory": [],
        },
        "diagnostic_events": [], "formula_audit": {"status": "UNRESOLVED"},
        "event_integrity": False,
        "B1_result": {"status": "UNRESOLVED"},
        "B2_result": {"status": "UNRESOLVED"},
        "B3_result": {"status": "UNRESOLVED"},
        "final_status": "UNRESOLVED",
    }


def test_identity_deterministic_sensitive_and_immutable() -> None:
    descriptor = build_descriptor(execution_commit=HEAD, environment_fingerprint=_fingerprint())
    payload = descriptor.payload()
    assert identity_for(payload) == descriptor.experiment_id
    assert identity_for(dict(reversed(list(payload.items())))) == descriptor.experiment_id
    for key in payload:
        changed = copy.deepcopy(payload)
        changed[key] = str(changed[key]) + "x"
        assert identity_for(changed) != descriptor.experiment_id
        with pytest.raises(ValueError):
            identity_for({name: value for name, value in payload.items() if name != key})
    with pytest.raises(FrozenInstanceError):
        field = "execution_commit"
        setattr(descriptor, field, "b" * 40)
    with pytest.raises(ValueError):
        replace(descriptor, experiment_id="phase3-layer-b-invalid")


def test_schema_and_atomic_writer_failure_record(tmp_path: Path) -> None:
    record = _failed_record()
    validate_layer_b_result(record)
    root = tmp_path / "outside"
    path = LayerBResultWriter(root).publish(record)
    assert {item.name for item in path.iterdir()} == {
        "manifest.json", "replay.json", "diagnostics.json", "analysis.json",
    }
    assert validate_published_layer_b(path) == json.loads(canonical_bytes(record))
    with pytest.raises(FileExistsError):
        LayerBResultWriter(root).publish(record)
    broken = copy.deepcopy(record)
    del broken["parent_config_hash"]
    with pytest.raises(ValueError):
        validate_layer_b_result(broken)
    altered = copy.deepcopy(record)
    altered["experiment_id"] += "x"
    with pytest.raises(ValueError):
        validate_layer_b_result(altered)


def _synthetic_parent() -> dict[str, Any]:
    row: dict[str, Any] = dict.fromkeys(ROW_FIELDS)
    row["controller_output"] = {"wrench": {"torque_body_Nm": [0.0, 0.0, 0.0]}}
    row["mixer_output"] = {"achieved_wrench": {"total_thrust_N": 1.0}}
    row["actuator_command"] = {"rotor_speed_squared_radps2": [1.0] * 4}
    row["observation_source"] = "ESTIMATED"
    row["timestamp"] = 0.001
    row["state_index"] = 1
    row["attitude_truth"] = [1.0, 0.0, 0.0, 0.0]
    row["attitude"] = [1.0, 0.0, 0.0, 0.0]
    rows = [{**row, "controller_output": row["controller_output"] if i < 1000 else None}
            for i in range(10000)]
    return {
        "trajectory": rows, "metadata": dict.fromkeys((
            "configuration_hash", "plant_hash", "controller_hash", "actuator_hash",
            "initial_state_hash", "reference_hash", "quad_params_sha256",
            "simulation_params_sha256", "environment_fingerprint", "dirty_state",
        ), "fixed"),
        "initialization": {"source": "ESTIMATED"},
        "experiment": {"seed": 0},
        "audit": {"fallback_count": 0, "rejection_count": 0, "source_transition": []},
    }


@pytest.mark.parametrize("field", [
    "timestamp", "state_index", "attitude_truth", "attitude", "controller_output",
    "mixer_output", "actuator_command", "observation_source", "count",
])
def test_equivalence_detects_nested_and_count_mismatch(field: str) -> None:
    parent = _synthetic_parent()
    replay = {**parent, "trajectory": list(parent["trajectory"])}
    assert LayerBReplayEquivalenceChecker.compare(parent, replay).passed
    if field == "count":
        replay["trajectory"].pop()
    else:
        row = copy.deepcopy(replay["trajectory"][5])
        if field == "controller_output":
            row[field]["wrench"]["torque_body_Nm"][0] = 1.0
        elif field == "mixer_output":
            row[field]["achieved_wrench"]["total_thrust_N"] = 2.0
        elif field == "actuator_command":
            row[field]["rotor_speed_squared_radps2"][0] = 2.0
        elif field in ("attitude_truth", "attitude"):
            row[field][0] = 0.5
        elif field == "observation_source":
            row[field] = "IDEAL_BENCHMARK"
        else:
            row[field] += 1
        replay["trajectory"][5] = row
    audit = LayerBReplayEquivalenceChecker.compare(parent, replay)
    assert not audit.passed and audit.mismatch_count > 0
    assert audit.first_mismatch_location is not None
    if field != "count":
        assert any(field in key for key, _count in audit.per_field_mismatch)


def test_status_gate() -> None:
    passed = {"status": "PASS"}
    failed = {"status": "FAIL"}
    unresolved = {"status": "UNRESOLVED"}
    assert final_status(equivalence=True, event_integrity=True, formula=passed,
                        b1=passed, b2=passed, b3=passed) == "SUPPORTED"
    assert final_status(equivalence=True, event_integrity=True, formula=passed,
                        b1=failed, b2=passed, b3=passed) == "NOT_SUPPORTED"
    assert final_status(equivalence=False, event_integrity=True, formula=passed,
                        b1=passed, b2=passed, b3=passed) == "UNRESOLVED"
    assert final_status(equivalence=True, event_integrity=True, formula=passed,
                        b1=unresolved, b2=passed, b3=passed) == "UNRESOLVED"


def test_short_diagnostic_probe_is_test_only_and_nonintrusive() -> None:
    root = Path(__file__).resolve().parents[2]
    original = load_paired_config(
        scenario=BaselineScenario.ATTITUDE_STEP,
        quad_params_path=root / "configs/quad_params.yaml",
        simulation_params_path=root / "configs/simulation_params.yaml",
    )
    config = PairedExperimentConfig(
        params=original.params,
        simulation_params=replace(original.simulation_params, duration_s=0.02),
        scenario=original.scenario,
    )
    _ideal_off, off_arm = build_paired_experiments(config)
    _ideal_on, on_arm = build_paired_experiments(config, estimated_diagnostics_enabled=True)
    assert isinstance(off_arm.runner, EstimatedAttitudeLoopExperiment)
    assert isinstance(on_arm.runner, EstimatedAttitudeLoopExperiment)
    off = off_arm.runner.run(spec=off_arm.spec, initial_state=off_arm.initial_state)
    on = on_arm.runner.run(spec=on_arm.spec, initial_state=on_arm.initial_state)
    assert len(off.simulation.outcomes) == len(on.simulation.outcomes) == 20
    assert len(off.controls) == len(on.controls) == 2
    assert not off.diagnostic_events
    events = collect_diagnostic_events(on)
    assert len(events) == len(on.diagnostic_events) == 2
    validate_event_sequence(events, full_horizon=False)
    assert events[0].event_kind == "INITIALIZE" and events[1].event_kind == "UPDATE"
    assert len(off.observations) == len(on.observations) == 2
    assert len(off.contexts) == len(on.contexts) == 2
    assert [item.sampled for item in off.simulation.outcomes] == [
        item.sampled for item in on.simulation.outcomes
    ]
    assert np.array_equal(off.simulation.final_state, on.simulation.final_state)
    for left, right in zip(off.controls, on.controls, strict=True):
        assert np.array_equal(left.wrench.torque_body_Nm, right.wrench.torque_body_Nm)
    snapshots = tuple(item.snapshot for item in on.diagnostic_events)
    assert evaluate_formula(snapshots)["status"] == "PASS"
    assert evaluate_b1(events)["status"] in ("PASS", "FAIL", "UNRESOLVED")
    b2, b3 = evaluate_roll_budget(events, snapshots)
    assert b2["status"] in ("PASS", "FAIL", "UNRESOLVED")
    assert b3["status"] in ("PASS", "FAIL", "UNRESOLVED")
    synthetic = assemble_layer_b_result(
        descriptor=build_descriptor(execution_commit=HEAD, environment_fingerprint=_fingerprint()),
        replay_shared_record=_failed_record()["replay_shared_record"],
        events=events, snapshots=snapshots,
        equivalence=LayerBEquivalenceAudit(0, 0, 1, "TEST_ONLY", (("test", 1),), False),
        test_only=True,
    )
    assert synthetic["final_status"] == "UNRESOLVED"


def _short_probe() -> tuple[Any, Any]:
    root = Path(__file__).resolve().parents[2]
    original = load_paired_config(
        scenario=BaselineScenario.ATTITUDE_STEP,
        quad_params_path=root / "configs/quad_params.yaml",
        simulation_params_path=root / "configs/simulation_params.yaml",
    )
    config = PairedExperimentConfig(
        params=original.params,
        simulation_params=replace(original.simulation_params, duration_s=0.02),
        scenario=original.scenario,
    )
    _ideal, arm = build_paired_experiments(config, estimated_diagnostics_enabled=True)
    assert isinstance(arm.runner, EstimatedAttitudeLoopExperiment)
    result = arm.runner.run(spec=arm.spec, initial_state=arm.initial_state)
    snapshots = tuple(item.snapshot for item in result.diagnostic_events)
    return collect_diagnostic_events(result), snapshots


def test_b1_exact_threshold_and_no_eligible_sample() -> None:
    events, _snapshots = _short_probe()
    assert evaluate_b1(events)["status"] == "UNRESOLVED"
    event = replace(
        events[1], truth_q_wb=tuple(quaternion_from_euler_zyx(0.2, 0.0, 0.0)),
        specific_force_body_mps2=(0.0, 0.0, 9.80665),
        measured_specific_force_direction_body=(0.0, 0.0, 1.0),
        correction_gate_pass=True,
    )
    passed = evaluate_b1((events[0], event))
    assert passed["status"] == "PASS" and passed["eligible_count"] == 1
    failed = evaluate_b1((events[0], replace(
        event, measured_specific_force_direction_body=(0.0, 0.1, 0.995),
    )))
    assert failed["status"] == "FAIL"


def test_b2_b3_budget_direction_and_roll_gate() -> None:
    events, snapshots = _short_probe()
    truth = tuple(quaternion_from_euler_zyx(0.02, 0.0, 0.0))
    q_pred = quaternion_from_euler_zyx(0.02, 0.0, 0.0)
    q_negative = quaternion_from_euler_zyx(-0.02, 0.0, 0.0)
    event = replace(events[1], truth_q_wb=truth, correction_gate_pass=True)
    snap = replace(snapshots[1], q_pred_wb=q_pred, q_after_correction_wb=q_negative)
    b2, b3 = evaluate_roll_budget((events[0], event), (snapshots[0], snap))
    assert b2["status"] == b3["status"] == "PASS"
    assert b3["S_C"] < 0 and b3["R_C"] > 0.5 and b3["closure_residual"] <= 1e-9
    positive = replace(snap, q_after_correction_wb=quaternion_from_euler_zyx(0.04, 0.0, 0.0))
    b2_failed, b3_failed = evaluate_roll_budget(
        (events[0], event), (snapshots[0], positive),
    )
    assert b2_failed["status"] == b3_failed["status"] == "FAIL"
    no_correction = replace(snap, q_after_correction_wb=q_pred)
    b2_zero, b3_zero = evaluate_roll_budget(
        (events[0], event), (snapshots[0], no_correction),
    )
    assert b2_zero["status"] == "FAIL" and b3_zero["status"] == "UNRESOLVED"
    pitched = replace(snap, q_pred_wb=quaternion_from_euler_zyx(0.0, 0.1, 0.0))
    b2_unresolved, b3_unresolved = evaluate_roll_budget(
        (events[0], event), (snapshots[0], pitched),
    )
    assert b2_unresolved["status"] == b3_unresolved["status"] == "UNRESOLVED"


def test_formula_failure_and_runner_preflight_rejection(monkeypatch: Any, tmp_path: Path) -> None:
    from experiments.simulation import layer_b_runner as runner

    _events, snapshots = _short_probe()
    measured = np.array(snapshots[1].measured_specific_force_direction_body)
    measured[0] += 1e-6
    altered = replace(snapshots[1], measured_specific_force_direction_body=measured)
    assert evaluate_formula((snapshots[0], altered))["status"] == "UNRESOLVED"
    monkeypatch.setattr(runner, "_source_identity", lambda: ("b" * 40, False))
    with pytest.raises(ValueError, match="clean source commit"):
        runner.run_formal_layer_b(
            expected_head=HEAD, expected_environment_sha256=_fingerprint().sha256,
            parent_directory=tmp_path, output_root=tmp_path,
        )
    monkeypatch.setattr(runner, "_source_identity", lambda: (HEAD, True))
    with pytest.raises(ValueError, match="clean source commit"):
        runner.run_formal_layer_b(
            expected_head=HEAD, expected_environment_sha256=_fingerprint().sha256,
            parent_directory=tmp_path, output_root=tmp_path,
        )


def test_formal_dry_mode_stops_after_preflight(monkeypatch: Any, tmp_path: Path) -> None:
    from experiments.simulation import layer_b_runner as runner

    gate = object()
    calls = 0

    def preflight(**_kwargs: Any) -> object:
        nonlocal calls
        calls += 1
        return gate

    monkeypatch.setattr(runner, "preflight_formal_layer_b", preflight)
    result = runner.run_formal_layer_b(
        expected_head=HEAD, expected_environment_sha256=_fingerprint().sha256,
        parent_directory=tmp_path, output_root=tmp_path, dry_run=True,
    )
    assert result is gate
    assert calls == 1
