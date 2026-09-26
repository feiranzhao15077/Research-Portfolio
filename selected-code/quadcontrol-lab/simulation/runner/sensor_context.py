"""Committed-state acceleration and sensor context for formal plant assembly.

These primitives carry plant information to an experiment's sensor callback. They
do not sample a sensor, update an estimator, advance time, or integrate a state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite

import numpy as np

from core.contracts.observation import ObservationSource
from core.state import QuadState
from simulation.clock import SimulationStep
from simulation.runner.assembly import PlantInput, SimulationPlant
from simulation.scheduler import CommittedState

__all__ = ["PlantAccelerationProvider", "SensorContext"]


@dataclass(frozen=True, slots=True)
class SensorContext:
    """Owned, immutable snapshot of one committed sensor boundary.

    Nested state and plant input are deep copies. Every exposed ndarray is backed
    by immutable bytes, so a caller cannot restore its write flag or mutate a
    snapshot through an earlier reference.
    """

    time_s: float
    physics_step_index: int
    dt_physics_s: float
    committed_state: CommittedState
    previous_held_plant_input: PlantInput
    acceleration_world_mps2: np.ndarray
    source: ObservationSource
    state: QuadState = field(init=False)
    committed_state_step_index: int = field(init=False)
    held_input_step_index: int = field(init=False)

    def __post_init__(self) -> None:
        if not isfinite(self.time_s) or self.time_s < 0.0:
            raise ValueError("sensor context time must be finite and non-negative")
        if type(self.physics_step_index) is not int or self.physics_step_index < 0:
            raise ValueError("sensor context physics step index must be a non-negative int")
        if not isfinite(self.dt_physics_s) or self.dt_physics_s <= 0.0:
            raise ValueError("sensor context physics period must be positive and finite")
        if self.time_s != self.physics_step_index * self.dt_physics_s:
            raise ValueError("sensor context time does not match its exact physics step")
        if not isinstance(self.committed_state, CommittedState):
            raise TypeError("sensor context requires a scheduler committed state")
        if self.committed_state.step_index != self.physics_step_index:
            raise ValueError("committed state step does not match the sensor step")
        if not isinstance(self.previous_held_plant_input, PlantInput):
            raise TypeError("sensor context requires the previous held PlantInput")
        expected_input_step = self.physics_step_index - 1
        if self.previous_held_plant_input.source_step_index != expected_input_step:
            raise ValueError("held input does not come from the preceding physics step")
        if self.source is not ObservationSource.ESTIMATED:
            raise ValueError("sensor context source must be ESTIMATED")
        input_value = self.previous_held_plant_input
        object.__setattr__(
            self,
            "previous_held_plant_input",
            PlantInput(
                rotor_step=input_value.rotor_step,
                rotor_forces=input_value.rotor_forces,
                source_step_index=input_value.source_step_index,
            ),
        )
        object.__setattr__(
            self, "state", QuadState.from_vector(self.committed_state.vector)
        )
        object.__setattr__(
            self, "committed_state_step_index", self.committed_state.step_index
        )
        object.__setattr__(self, "held_input_step_index", expected_input_step)
        acceleration = np.asarray(self.acceleration_world_mps2, dtype=float)
        if acceleration.shape != (3,) or not np.all(np.isfinite(acceleration)):
            raise ValueError("world acceleration must be a finite three-vector")
        object.__setattr__(
            self,
            "acceleration_world_mps2",
            np.frombuffer(acceleration.tobytes(), dtype=float),
        )


class PlantAccelerationProvider:
    """Read the World ENU linear derivative from the existing dynamics authority.

    Construction rejects every environment except exact ``NoDisturbance``, whose
    force evaluation does not change state, RNG, clock, or logs. The provider invokes
    neither the rotor update nor the integrator and retains no simulation state.
    """

    def __init__(self, *, plant: SimulationPlant) -> None:
        plant.require_pure_acceleration_environment()
        self._plant = plant

    def evaluate(
        self, *, step: SimulationStep, state: QuadState, held_input: PlantInput
    ) -> np.ndarray:
        """Evaluate ``dv_world/dt`` at a committed state under a preceding input."""
        derivative = np.asarray(
            self._plant.state_derivative(step, state.as_vector(), held_input),
            dtype=float,
        )
        if derivative.shape != (13,) or not np.all(np.isfinite(derivative)):
            raise ValueError("plant returned an invalid state derivative")
        acceleration = derivative[3:6]
        return np.frombuffer(acceleration.tobytes(), dtype=float)

    def context(
        self, *, step: SimulationStep, committed_state: CommittedState, held_input: PlantInput
    ) -> SensorContext:
        """Bind one committed state, held input, and pure acceleration evaluation."""
        if not isinstance(committed_state, CommittedState):
            raise TypeError("context requires a scheduler committed state")
        if committed_state.step_index != step.step_index:
            raise ValueError("committed state step does not match sensor step")
        if not isinstance(held_input, PlantInput):
            raise TypeError("context requires a PlantInput")
        if held_input.source_step_index != step.step_index - 1:
            raise ValueError("held input does not come from the preceding physics step")
        return SensorContext(
            time_s=step.time_s,
            physics_step_index=step.step_index,
            dt_physics_s=step.dt_physics_s,
            committed_state=committed_state,
            previous_held_plant_input=held_input,
            acceleration_world_mps2=self.evaluate(
                step=step,
                state=QuadState.from_vector(committed_state.vector),
                held_input=held_input,
            ),
            source=ObservationSource.ESTIMATED,
        )
