"""Shared, unexecuted configuration for a future IDEAL/ESTIMATED comparison.

Both arms are assembled from one configuration. This module does not run either
arm, write results, or change the existing benchmark experiment entry point.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from pathlib import Path

import numpy as np

from controllers import ControllerPipeline
from controllers.attitude import AttitudeController
from controllers.rate import RateController
from controllers.reference import AttitudeReference
from core.contracts import (
    ActuatorDecisionContext,
    ControllerObservation,
    ObservationSource,
    V1ActuatorPolicy,
)
from core.contracts.observation_adapter import ObservationAdapter
from core.dynamics import inertia_tensor_body_kgm2
from core.mixer import allocate, rotor_positions_body_m
from core.motor import hover_rotor_speed_squared_radps2
from core.params import (
    IntegratorMethod,
    QuadParams,
    SimulationParams,
    load_params,
    load_simulation_params,
)
from core.state import QuadState, quaternion_from_euler_zyx, stationary_state
from estimation.attitude import MinimalAttitudeEstimator
from experiments.simulation.baseline import (
    ATTITUDE_STEP_ROLL_RAD,
    INITIAL_ALTITUDE_M,
    BaselineScenario,
)
from experiments.simulation.estimated_attitude_loop import EstimatedAttitudeLoopExperiment
from simulation.clock import SimulationClock
from simulation.dynamics import RigidBodyDynamics
from simulation.environment import NoDisturbance
from simulation.integrator import RungeKutta4Integrator
from simulation.motors import FirstOrderRotorModel, RotorCommand, RotorDynamicsSpec, RotorState
from simulation.runner import SimulationPlant, SimulationRunner, SimulationSpec, V1ClosedLoopRunner
from simulation.sensors import IdealImu

__all__ = [
    "PairedExperimentArm",
    "PairedExperimentConfig",
    "assert_pair_parity",
    "build_initial_state",
    "build_paired_experiments",
    "load_paired_config",
]


@dataclass(frozen=True, slots=True)
class PairedExperimentConfig:
    """One source for both arms' physical, numerical, and scenario settings."""

    params: QuadParams
    simulation_params: SimulationParams
    scenario: BaselineScenario
    initial_altitude_m: float = INITIAL_ALTITUDE_M

    def __post_init__(self) -> None:
        if not isinstance(self.params, QuadParams):
            raise TypeError("params must be QuadParams")
        if not isinstance(self.simulation_params, SimulationParams):
            raise TypeError("simulation_params must be SimulationParams")
        if not isinstance(self.scenario, BaselineScenario):
            raise TypeError("scenario must be a BaselineScenario")
        if not isfinite(self.initial_altitude_m):
            raise ValueError("initial altitude must be finite")
        if self.simulation_params.integrator_method is not IntegratorMethod.RK4:
            raise ValueError("paired experiment requires the existing RK4 integrator")
        if not self.simulation_params.integrator_fixed_step:
            raise ValueError("paired experiment requires a fixed physics timestep")
        if self.params.control.attitude is None or self.params.control.rate is None:
            raise ValueError("paired experiment requires attitude and rate controller settings")
        # Validate the common multi-rate schedule before either arm is assembled.
        SimulationClock(
            dt_physics_s=self.simulation_params.dt_physics_s,
            dt_control_s=1.0 / self.simulation_params.control_rate_hz,
            dt_sensor_s=1.0 / self.simulation_params.control_rate_hz,
            duration_s=self.simulation_params.duration_s,
        )

    @property
    def spec(self) -> SimulationSpec:
        control_period = 1.0 / self.simulation_params.control_rate_hz
        return SimulationSpec(
            dt_physics_s=self.simulation_params.dt_physics_s,
            dt_control_s=control_period,
            dt_sensor_s=control_period,
            duration_s=self.simulation_params.duration_s,
        )

    @property
    def seed(self) -> int:
        return self.simulation_params.random_seed


def load_paired_config(
    *,
    scenario: BaselineScenario,
    quad_params_path: str | Path = "configs/quad_params.yaml",
    simulation_params_path: str | Path = "configs/simulation_params.yaml",
) -> PairedExperimentConfig:
    """Load each existing parameter file once for both future experiment arms."""
    return PairedExperimentConfig(
        params=load_params(quad_params_path),
        simulation_params=load_simulation_params(simulation_params_path),
        scenario=scenario,
    )


def build_initial_state(config: PairedExperimentConfig) -> QuadState:
    """Construct the one initial plant state shared by both prepared arms."""
    return stationary_state(altitude_m=config.initial_altitude_m)


def _build_reference(config: PairedExperimentConfig) -> AttitudeReference:
    roll = 0.0 if config.scenario is BaselineScenario.HOVER else ATTITUDE_STEP_ROLL_RAD
    return AttitudeReference(q_ref_wb=quaternion_from_euler_zyx(roll, 0.0, 0.0))


def _build_controller(config: PairedExperimentConfig) -> ControllerPipeline:
    attitude_settings = config.params.control.attitude
    rate_settings = config.params.control.rate
    assert attitude_settings is not None and rate_settings is not None
    return ControllerPipeline(
        attitude=AttitudeController(config=attitude_settings),
        rate=RateController(
            config=rate_settings,
            inertia_kgm2=inertia_tensor_body_kgm2(config.params),
            dt_control_s=config.spec.dt_control_s,
        ),
    )


def _build_plant(config: PairedExperimentConfig) -> SimulationPlant:
    params = config.params
    hover_speed_squared = hover_rotor_speed_squared_radps2(params)
    initial_rotors = RotorState.from_command(
        RotorCommand(np.full(params.frame.rotor_count, hover_speed_squared))
    )
    return SimulationPlant(
        params=params,
        rotor_model=FirstOrderRotorModel(spec=RotorDynamicsSpec(tau_m_s=0.0)),
        initial_rotor_state=initial_rotors,
        rotor_positions_body_m=rotor_positions_body_m(params),
        dynamics=RigidBodyDynamics(),
        environment=NoDisturbance(),
    )


def _build_runner(config: PairedExperimentConfig) -> SimulationRunner:
    return SimulationRunner(
        integrator=RungeKutta4Integrator(
            renormalise_quaternion=config.simulation_params.renormalise_quaternion
        )
    )


@dataclass(frozen=True, slots=True)
class PairedExperimentArm:
    """Prepared arm and its locked run inputs; construction performs no simulation."""

    source: ObservationSource
    runner: V1ClosedLoopRunner | EstimatedAttitudeLoopExperiment
    config: PairedExperimentConfig
    spec: SimulationSpec
    initial_state: QuadState
    seed: int
    plant: SimulationPlant
    controller: ControllerPipeline
    reference: AttitudeReference


def assert_pair_parity(ideal: PairedExperimentArm, estimated: PairedExperimentArm) -> None:
    """Fail before execution if the prepared arms differ beyond observation source."""
    if ideal.source is not ObservationSource.IDEAL_BENCHMARK:
        raise ValueError("first arm must use IDEAL_BENCHMARK")
    if estimated.source is not ObservationSource.ESTIMATED:
        raise ValueError("second arm must use ESTIMATED")
    if not isinstance(ideal.runner, V1ClosedLoopRunner) or not isinstance(
        estimated.runner, EstimatedAttitudeLoopExperiment
    ):
        raise ValueError("paired arms must use the expected runner types")
    if ideal.config is not estimated.config:
        raise ValueError("paired arms must share one configuration object")
    config = ideal.config
    if ideal.spec != estimated.spec or ideal.spec != config.spec:
        raise ValueError("paired arms must use the same timestep and horizon")
    expected_state = build_initial_state(config).as_vector()
    if not (
        np.array_equal(ideal.initial_state.as_vector(), estimated.initial_state.as_vector())
        and np.array_equal(ideal.initial_state.as_vector(), expected_state)
    ):
        raise ValueError("paired arms must use the same initial state")
    if ideal.seed != estimated.seed or ideal.seed != config.seed:
        raise ValueError("paired arms must use the same random seed")
    if ideal.plant._params is not config.params or estimated.plant._params is not config.params:
        raise ValueError("paired arms must use the same plant parameters")
    if ideal.runner._plant is not ideal.plant or estimated.runner._plant is not estimated.plant:
        raise ValueError("runner plant differs from its prepared arm")
    if (
        ideal.runner._controller is not ideal.controller
        or estimated.runner._controller is not estimated.controller
    ):
        raise ValueError("runner controller differs from its prepared arm")
    if (
        ideal.runner._reference is not ideal.reference
        or estimated.runner._reference is not estimated.reference
    ):
        raise ValueError("runner reference differs from its prepared arm")
    if ideal.runner._allocator is not allocate:
        raise ValueError("IDEAL mixer differs from the shared mixer")
    if (
        type(ideal.runner._policy) is not V1ActuatorPolicy
        or type(estimated.runner._policy) is not V1ActuatorPolicy
    ):
        raise ValueError("paired arms must use the same actuator policy")
    expected_hover = hover_rotor_speed_squared_radps2(config.params)
    ideal_speeds = ideal.plant.rotor_state.rotor_speed_squared_radps2
    estimated_speeds = estimated.plant.rotor_state.rotor_speed_squared_radps2
    if not (
        np.array_equal(ideal_speeds, estimated_speeds)
        and np.allclose(
            ideal_speeds,
            expected_hover,
            rtol=1e-14,
            atol=0.0,
        )
    ):
        raise ValueError("paired arms must use the configured initial rotor state")
    ideal_integrator = ideal.runner._runner._integrator
    estimated_integrator = estimated.runner._runner._integrator
    if not isinstance(ideal_integrator, RungeKutta4Integrator) or not isinstance(
        estimated_integrator, RungeKutta4Integrator
    ):
        raise ValueError("paired arms must use RK4")
    if (
        ideal_integrator.renormalise_quaternion
        != estimated_integrator.renormalise_quaternion
        or ideal_integrator.renormalise_quaternion
        != config.simulation_params.renormalise_quaternion
    ):
        raise ValueError("paired RK4 settings differ")
    for field in ("_kp", "_ki", "_kd", "_inertia"):
        left = getattr(ideal.controller._rate, field)
        right = getattr(estimated.controller._rate, field)
        if not np.array_equal(left, right):
            raise ValueError("paired controller rate settings differ")
    if not np.array_equal(ideal.controller._attitude._kp, estimated.controller._attitude._kp):
        raise ValueError("paired controller attitude settings differ")
    for field in ("_max_tilt_rad", "_max_yaw_rate"):
        if getattr(ideal.controller._attitude, field) != getattr(
            estimated.controller._attitude, field
        ):
            raise ValueError("paired controller attitude settings differ")
    for field in (
        "_integral_limit",
        "_u_max",
        "_torque_limit",
        "_enable_rate_ff",
        "_enable_gyro_ff",
    ):
        if getattr(ideal.controller._rate, field) != getattr(estimated.controller._rate, field):
            raise ValueError("paired controller rate settings differ")
    for arm in (ideal, estimated):
        if arm.controller.dt_control_s != config.spec.dt_control_s:
            raise ValueError("controller period differs from the shared configuration")
        if arm.plant._rotor_model.tau_m_s != 0.0:
            raise ValueError("paired actuator configuration must be quasi-static")
        if type(arm.plant._environment) is not NoDisturbance:
            raise ValueError("paired plant configuration requires NoDisturbance")
        if not np.array_equal(arm.reference.q_ref_wb, _build_reference(config).q_ref_wb):
            raise ValueError("paired arms must use the same reference")


def build_paired_experiments(
    config: PairedExperimentConfig,
    *,
    estimated_diagnostics_enabled: bool = False,
) -> tuple[PairedExperimentArm, PairedExperimentArm]:
    """Prepare two fresh arms from one source without running either arm."""
    if not isinstance(config, PairedExperimentConfig):
        raise TypeError("config must be PairedExperimentConfig")
    if type(estimated_diagnostics_enabled) is not bool:
        raise TypeError("estimated_diagnostics_enabled must be bool")
    spec = config.spec
    initial_state = build_initial_state(config)
    reference = _build_reference(config)
    thrust = config.params.body.mass_kg * config.params.environment.gravity_mps2

    ideal_plant = _build_plant(config)
    ideal_controller = _build_controller(config)
    ideal = PairedExperimentArm(
        source=ObservationSource.IDEAL_BENCHMARK,
        runner=V1ClosedLoopRunner(
            runner=_build_runner(config),
            controller=ideal_controller,
            plant=ideal_plant,
            params=config.params,
            attitude_reference=reference,
            collective_thrust_N=thrust,
            observation_factory=lambda state, timestamp: ControllerObservation(
                state=state,
                timestamp=timestamp,
                validity=True,
                source=ObservationSource.IDEAL_BENCHMARK,
            ),
            observation_adapter=ObservationAdapter(),
            allocator=allocate,
            actuator_policy=V1ActuatorPolicy(
                command_factory=lambda values: RotorCommand(
                    rotor_speed_squared_radps2=values
                )
            ),
            actuator_context_factory=lambda step_index, time_s: ActuatorDecisionContext(
                step_index=step_index, time_s=time_s
            ),
        ),
        config=config,
        spec=spec,
        initial_state=initial_state,
        seed=config.seed,
        plant=ideal_plant,
        controller=ideal_controller,
        reference=reference,
    )

    estimated_plant = _build_plant(config)
    estimated_controller = _build_controller(config)
    estimated = PairedExperimentArm(
        source=ObservationSource.ESTIMATED,
        runner=EstimatedAttitudeLoopExperiment(
            runner=_build_runner(config),
            plant=estimated_plant,
            controller=estimated_controller,
            params=config.params,
            attitude_reference=reference,
            collective_thrust_N=thrust,
            sensor=IdealImu(
                gyro_noise_std_radps=0.0,
                accel_noise_std_mps2=0.0,
                bias_random_walk=0.0,
                gravity_mps2=config.params.environment.gravity_mps2,
                random_seed=config.seed,
            ),
            estimator=MinimalAttitudeEstimator(
                gravity_mps2=config.params.environment.gravity_mps2,
                diagnostics_enabled=estimated_diagnostics_enabled,
            ),
            random_seed=config.seed,
        ),
        config=config,
        spec=spec,
        initial_state=initial_state,
        seed=config.seed,
        plant=estimated_plant,
        controller=estimated_controller,
        reference=reference,
    )
    assert_pair_parity(ideal, estimated)
    return ideal, estimated
