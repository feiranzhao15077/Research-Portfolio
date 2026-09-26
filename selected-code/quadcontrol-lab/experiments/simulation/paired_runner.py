"""Locked paired execution entry; constructing it never starts a simulation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.contracts import ObservationSource
from estimation.model import EstimatorContractError
from experiments.simulation.estimated_attitude_loop import (
    EstimatedAttitudeLoopExperiment,
    EstimatedObservationRejected,
)
from experiments.simulation.formal_result_schema import FormalFailureRecord
from experiments.simulation.formal_result_writer import FormalResultWriter
from experiments.simulation.formal_run_preflight import (
    FormalRunPreflight,
    FormalRunPreflightError,
)
from experiments.simulation.paired_configuration import (
    PairedExperimentArm,
    assert_pair_parity,
)
from experiments.simulation.paired_logging import (
    PairedAudit,
    PairedFailureEvent,
    PairedLog,
    PairedObservationAuditError,
    adapt_estimated_log,
    adapt_ideal_log,
)
from experiments.simulation.paired_run_descriptor import (
    PairRunDescriptor,
    assert_code_provenance,
    assert_descriptor_pair,
)
from simulation.runner import V1ClosedLoopRunner

__all__ = ["PairedRunFailure", "PairedRunResult", "PairedRunner"]


class PairedRunFailure(RuntimeError):
    """Failure record retained when either arm or its log validation aborts."""

    def __init__(
        self, message: str, *, audit: PairedAudit, completed_ideal: PairedLog | None = None
    ) -> None:
        super().__init__(message)
        self.audit = audit
        self.completed_ideal = completed_ideal


@dataclass(frozen=True, slots=True)
class PairedRunResult:
    ideal: PairedLog
    estimated: PairedLog
    ideal_descriptor: PairRunDescriptor
    estimated_descriptor: PairRunDescriptor


class PairedRunner:
    """Run the two existing assemblies with no caller-overridable run arguments."""

    def __init__(
        self,
        *,
        ideal: PairedExperimentArm,
        estimated: PairedExperimentArm,
        ideal_descriptor: PairRunDescriptor,
        estimated_descriptor: PairRunDescriptor,
    ) -> None:
        assert_descriptor_pair(ideal, estimated, ideal_descriptor, estimated_descriptor)
        self._ideal = ideal
        self._estimated = estimated
        self._ideal_descriptor = ideal_descriptor
        self._estimated_descriptor = estimated_descriptor
        self._has_run = False
        self._formal_execution_active = False

    def preflight(self) -> None:
        """Recheck hashes, fresh state, and actual assemblies before any run call."""
        if self._has_run:
            raise ValueError("paired runner is single-use")
        assert_pair_parity(self._ideal, self._estimated)
        assert_descriptor_pair(
            self._ideal,
            self._estimated,
            self._ideal_descriptor,
            self._estimated_descriptor,
        )
        if self._ideal.controller.step_count or self._estimated.controller.step_count:
            raise ValueError("paired controllers must be fresh")
        if not isinstance(self._ideal.runner, V1ClosedLoopRunner) or not isinstance(
            self._estimated.runner, EstimatedAttitudeLoopExperiment
        ):
            raise TypeError("paired runner types changed")
        if self._ideal.runner._has_run or self._estimated.runner._has_run:
            raise ValueError("paired arms must be unused")

    def preflight_formal(self) -> None:
        """Arm the strict formal gate without executing either experiment arm."""
        self.preflight()
        FormalRunPreflight.validate(
            self._ideal,
            self._estimated,
            self._ideal_descriptor,
            self._estimated_descriptor,
        )

    def run_formal(self, *, output_root: Path) -> Path:
        """Enforce the formal gate, then persist either a pair or its failure audit."""
        writer = FormalResultWriter.begin_pair(
            output_root=output_root,
            ideal=self._ideal_descriptor,
            estimated=self._estimated_descriptor,
        )
        estimated_runner = self._estimated.runner
        try:
            self.preflight_formal()
            if (
                not isinstance(estimated_runner, EstimatedAttitudeLoopExperiment)
                or not estimated_runner.formal_preflight_armed
            ):
                raise FormalRunPreflightError("formal estimator gate was not armed")
        except Exception as error:
            writer.record_preflight_failure(
                phase="formal_preflight",
                reason=f"{type(error).__name__}: {error}",
            )
            raise

        self._formal_execution_active = True
        try:
            result = self.run()
        except PairedRunFailure as error:
            event = error.audit.failure_event
            if event is None:
                raise ValueError("paired failure lacks a source and phase") from error
            if error.completed_ideal is not None:
                writer.record_arm_success(
                    source=ObservationSource.IDEAL_BENCHMARK,
                    log=error.completed_ideal,
                )
            descriptor = (
                self._ideal_descriptor
                if event.arm is ObservationSource.IDEAL_BENCHMARK
                else self._estimated_descriptor
            )
            writer.record_arm_failure(
                record=FormalFailureRecord(descriptor=descriptor, audit=error.audit),
                initialization_provenance=(
                    estimated_runner.initialization_provenance
                    if event.arm is ObservationSource.ESTIMATED else None
                ),
            )
            raise
        except Exception as error:
            writer.record_preflight_failure(
                phase="run_boundary",
                reason=f"{type(error).__name__}: {error}",
            )
            raise
        finally:
            self._formal_execution_active = False

        for source, log, descriptor, provenance in (
            (ObservationSource.IDEAL_BENCHMARK, result.ideal, self._ideal_descriptor, None),
            (
                ObservationSource.ESTIMATED, result.estimated,
                self._estimated_descriptor, estimated_runner.initialization_provenance,
            ),
        ):
            try:
                writer.record_arm_success(
                    source=source, log=log, initialization_provenance=provenance,
                )
            except Exception as error:
                writer.record_arm_failure(record=FormalFailureRecord(
                    descriptor=descriptor,
                    audit=PairedAudit(
                        failure_reason=f"{type(error).__name__}: {error}",
                        failure_event=PairedFailureEvent(
                            source, "result_logging", None, None, None, None,
                        ),
                    ),
                ))
                raise
        return writer.finalize_pair()

    def run(self) -> PairedRunResult:
        """Execute only the locked inputs, fail closed, and return symmetric in-memory logs."""
        estimated_runner = self._estimated.runner
        if (
            isinstance(estimated_runner, EstimatedAttitudeLoopExperiment)
            and estimated_runner.formal_preflight_armed
            and not self._formal_execution_active
        ):
            raise ValueError("formal-armed pair must use run_formal for persistence")
        self.preflight()
        self._has_run = True
        ideal_arm = self._ideal
        estimated_arm = self._estimated
        ideal_descriptor = self._ideal_descriptor
        estimated_descriptor = self._estimated_descriptor
        assert isinstance(ideal_arm.runner, V1ClosedLoopRunner)
        assert isinstance(estimated_arm.runner, EstimatedAttitudeLoopExperiment)
        arm = ideal_descriptor.observation_source
        stage = "ideal_run"
        ideal_log: PairedLog | None = None
        try:
            ideal_raw = ideal_arm.runner.run(
                spec=ideal_arm.spec,
                initial_state=ideal_arm.initial_state,
                random_seed=ideal_descriptor.seed,
                quad_params_sha256=ideal_descriptor.quad_params_sha256,
                simulation_params_sha256=ideal_descriptor.simulation_params_sha256,
                git_commit=ideal_descriptor.code_revision,
            )
            stage = "ideal_log_validation"
            ideal_log = adapt_ideal_log(
                ideal_raw,
                descriptor=ideal_descriptor,
                initial_state=ideal_arm.initial_state,
            )
            assert_code_provenance(ideal_descriptor)
            arm = estimated_descriptor.observation_source
            stage = "estimated_run"
            estimated_raw = estimated_arm.runner.run(
                spec=estimated_arm.spec,
                initial_state=estimated_arm.initial_state,
                quad_params_sha256=estimated_descriptor.quad_params_sha256,
                simulation_params_sha256=estimated_descriptor.simulation_params_sha256,
                git_commit=estimated_descriptor.code_revision,
            )
            stage = "estimated_log_validation"
            estimated_log = adapt_estimated_log(
                estimated_raw,
                descriptor=estimated_descriptor,
            )
            stage = "postflight"
            assert_code_provenance(estimated_descriptor)
        except Exception as error:
            observation_failure = isinstance(
                error,
                (
                    EstimatedObservationRejected,
                    EstimatorContractError,
                    PairedObservationAuditError,
                ),
            )
            event = error.failure_event if isinstance(error, PairedObservationAuditError) else None
            if event is None and arm is estimated_descriptor.observation_source:
                boundary = estimated_arm.runner.last_event
                if boundary is not None and stage == "estimated_run":
                    event = PairedFailureEvent(
                        arm, boundary.stage, boundary.step_index, boundary.timestamp,
                        boundary.sample_index, boundary.observed_source,
                    )
            if event is None:
                event = PairedFailureEvent(arm, stage, None, None, None, None)
            audit = PairedAudit(
                invalid_observation_count=(
                    error.invalid_observation_count
                    if isinstance(error, PairedObservationAuditError)
                    else int(observation_failure)
                ),
                rejected_observation_count=int(observation_failure),
                fallback_count=0,
                source_transition=(
                    (error.source_transition,)
                    if isinstance(error, PairedObservationAuditError)
                    else (event.observed_source is not None and event.observed_source is not arm,)
                ),
                failure_reason=f"{type(error).__name__}: {error}",
                failure_event=event,
            )
            raise PairedRunFailure(
                "paired experiment failed without fallback", audit=audit,
                completed_ideal=ideal_log,
            ) from error
        return PairedRunResult(
            ideal=ideal_log,
            estimated=estimated_log,
            ideal_descriptor=ideal_descriptor,
            estimated_descriptor=estimated_descriptor,
        )
