"""Controller observation contracts frozen by D-064.

This module defines shared vocabulary only.  It does not read plant truth, consume sensor
measurements, estimate state, evaluate staleness, call a controller, or own simulation time.

``ControllerObservation.state`` intentionally reuses :class:`core.state.QuadState` as its
numerical structure.  The field is a controller-facing observation, not a declaration that
the value is plant truth; ``source`` preserves that provenance explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Protocol, TypeVar, runtime_checkable

from core.state import QuadState

__all__ = [
    "ATTITUDE_INNER_LOOP",
    "FULL_STATE_OUTER_LOOP",
    "ConsumerObservationProfile",
    "ConsumerObservationRequirements",
    "ControllerObservation",
    "FieldValidity",
    "ObservationField",
    "ObservationProviderProtocol",
    "ObservationProviderStatus",
    "ObservationSource",
    "ObservationValidity",
    "observation_satisfies",
]

MeasurementT = TypeVar("MeasurementT", contravariant=True)


class ObservationSource(StrEnum):
    """Explicit provenance of a controller-facing observation (D-064)."""

    ESTIMATED = "ESTIMATED"
    IDEAL_BENCHMARK = "IDEAL_BENCHMARK"
    TEST_FAKE = "TEST_FAKE"


class FieldValidity(StrEnum):
    """Validity of one state field in a controller observation."""

    INVALID = "INVALID"
    VALID = "VALID"


class ObservationField(StrEnum):
    """State fields that may be required by a downstream consumer."""

    POSITION = "position"
    VELOCITY = "velocity"
    ATTITUDE = "attitude"
    ANGULAR_VELOCITY = "angular_velocity"


class ConsumerObservationProfile(StrEnum):
    """Named consumer profiles frozen by the D-068 boundary proposal."""

    ATTITUDE_INNER_LOOP = "ATTITUDE_INNER_LOOP"
    FULL_STATE_OUTER_LOOP = "FULL_STATE_OUTER_LOOP"


@dataclass(frozen=True, slots=True)
class ObservationValidity:
    """Field-level validity carried alongside the unified :class:`QuadState`.

    The state container remains a complete ``QuadState`` for structural compatibility,
    but a provider may mark only the fields it actually estimates as valid.  Values of
    fields marked ``INVALID`` are not estimates and must never be exposed to a
    controller by the observation adapter.
    """

    position: FieldValidity
    velocity: FieldValidity
    attitude: FieldValidity
    angular_velocity: FieldValidity

    def __post_init__(self) -> None:
        for name in ("position", "velocity", "attitude", "angular_velocity"):
            value = getattr(self, name)
            if not isinstance(value, FieldValidity):
                raise TypeError(f"{name} must be a FieldValidity")

    @classmethod
    def all_valid(cls) -> ObservationValidity:
        """Return the full-state validity used by the V1 benchmark path."""
        return cls(
            position=FieldValidity.VALID,
            velocity=FieldValidity.VALID,
            attitude=FieldValidity.VALID,
            angular_velocity=FieldValidity.VALID,
        )

    @classmethod
    def all_invalid(cls) -> ObservationValidity:
        """Return the default field status for a legacy invalid observation."""
        return cls(
            position=FieldValidity.INVALID,
            velocity=FieldValidity.INVALID,
            attitude=FieldValidity.INVALID,
            angular_velocity=FieldValidity.INVALID,
        )

    @property
    def is_complete(self) -> bool:
        """Whether every state field is explicitly valid."""
        return all(
            value is FieldValidity.VALID
            for value in (
                self.position,
                self.velocity,
                self.attitude,
                self.angular_velocity,
            )
        )


@dataclass(frozen=True, slots=True)
class ConsumerObservationRequirements:
    """Fields a specific consumer requires from a controller observation.

    This contract is deliberately separate from :class:`ObservationValidity`:
    providers report what they can supply, while a consumer boundary declares what it
    needs.  A partial observation can therefore satisfy an attitude-only consumer
    without becoming a complete state observation.
    """

    profile: ConsumerObservationProfile
    position: bool
    velocity: bool
    attitude: bool
    angular_velocity: bool

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ConsumerObservationProfile):
            raise TypeError("profile must be a ConsumerObservationProfile")
        for name in ("position", "velocity", "attitude", "angular_velocity"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be a bool")

    @classmethod
    def attitude_inner_loop(cls) -> ConsumerObservationRequirements:
        """Return the current attitude/rate inner-loop requirement profile."""
        return cls(
            profile=ConsumerObservationProfile.ATTITUDE_INNER_LOOP,
            position=False,
            velocity=False,
            attitude=True,
            angular_velocity=True,
        )

    @classmethod
    def full_state_outer_loop(cls) -> ConsumerObservationRequirements:
        """Return the complete state requirement for future outer-loop consumers."""
        return cls(
            profile=ConsumerObservationProfile.FULL_STATE_OUTER_LOOP,
            position=True,
            velocity=True,
            attitude=True,
            angular_velocity=True,
        )

    @property
    def required_fields(self) -> frozenset[ObservationField]:
        """Return the explicitly required fields without consulting an observation."""
        return frozenset(
            field
            for field in ObservationField
            if bool(getattr(self, field.value))
        )


ATTITUDE_INNER_LOOP = ConsumerObservationRequirements.attitude_inner_loop()
FULL_STATE_OUTER_LOOP = ConsumerObservationRequirements.full_state_outer_loop()


def observation_satisfies(
    observation: ControllerObservation,
    requirements: ConsumerObservationRequirements,
) -> bool:
    """Return whether an observation supplies every field required by a consumer.

    The aggregate observation validity is intentionally not used as a rejection gate.
    It describes complete-state validity; this function evaluates only the selected
    consumer's required fields.  Source provenance is preserved by the observation and
    does not alter this pure compatibility decision.
    """
    if not isinstance(observation, ControllerObservation):
        raise TypeError("observation must be a ControllerObservation")
    if not isinstance(requirements, ConsumerObservationRequirements):
        raise TypeError(
            "requirements must be a ConsumerObservationRequirements"
        )
    field_validity = observation.field_validity
    if field_validity is None:  # pragma: no cover - ControllerObservation normalizes it
        return False
    return all(
        getattr(field_validity, field.value) is FieldValidity.VALID
        for field in requirements.required_fields
    )


class ObservationProviderStatus(StrEnum):
    """Lifecycle states for a future observation provider.

    This enum and :meth:`can_transition_to` define the lifecycle contract only.  They do
    not implement a provider or advance any provider state.

    ``RESET`` is an explicit transitional state.  A reset provider returns to
    ``UNINITIALIZED`` before it can become ``READY`` again.  Self-transitions are allowed
    for stable statuses so repeated provider events do not require artificial state
    changes; repeated reset is idempotent.
    """

    UNINITIALIZED = "UNINITIALIZED"
    READY = "READY"
    STALE = "STALE"
    INVALID = "INVALID"
    RESET = "RESET"

    def can_transition_to(self, next_status: ObservationProviderStatus) -> bool:
        """Return whether ``self -> next_status`` is allowed by the frozen lifecycle."""
        if not isinstance(next_status, ObservationProviderStatus):
            raise TypeError("next_status must be an ObservationProviderStatus")
        return next_status in _ALLOWED_PROVIDER_TRANSITIONS[self]


@runtime_checkable
class ObservationProviderProtocol(Protocol[MeasurementT]):
    """Measurement-to-observation boundary frozen by D-066.

    A provider owns only the transformation from an explicit measurement to a committed
    controller observation. Initialization is an explicit lifecycle operation; calling
    ``update`` before successful initialization must fail or return ``None`` and must not
    initialize implicitly. Implementations may return a partial observation whose
    ``field_validity`` identifies exactly which state fields are estimated. They may hold
    estimator state, but they must not read plant truth, call a controller, advance
    simulation time, or silently fall back to ``IDEAL_BENCHMARK``. ``None`` represents
    the initialization case where no observation payload exists yet.

    ``measurement.time_s`` is the only authoritative observation timestamp. The optional
    ``time_s`` argument is retained as a compatibility check for callers that already
    pass scheduler time; implementations must reject a mismatch and must never generate
    an alternative timestamp.

    A provider may report ``UNINITIALIZED`` with ``None`` before initialization, or
    ``READY`` with either a valid or an invalid observation for a processed measurement.
    A provider-level ``INVALID`` lifecycle status remains distinct from an invalid
    observation payload and never authorizes a source fallback.
    """

    @property
    def status(self) -> ObservationProviderStatus:
        """Return the provider lifecycle status."""

    def initialize(
        self,
        measurement: MeasurementT,
        time_s: float | None = None,
    ) -> ControllerObservation | None:
        """Explicitly initialize from a measurement without reading another time source."""

    def update(
        self,
        measurement: MeasurementT,
        time_s: float | None = None,
    ) -> ControllerObservation | None:
        """Consume one measurement after explicit initialization."""

    def reset(self) -> None:
        """Clear provider-owned state and return to the uninitialized lifecycle."""


_ALLOWED_PROVIDER_TRANSITIONS: dict[
    ObservationProviderStatus, frozenset[ObservationProviderStatus]
] = {
    ObservationProviderStatus.UNINITIALIZED: frozenset(
        {
            ObservationProviderStatus.UNINITIALIZED,
            ObservationProviderStatus.READY,
            ObservationProviderStatus.INVALID,
            ObservationProviderStatus.RESET,
        }
    ),
    ObservationProviderStatus.READY: frozenset(
        {
            ObservationProviderStatus.READY,
            ObservationProviderStatus.STALE,
            ObservationProviderStatus.INVALID,
            ObservationProviderStatus.RESET,
        }
    ),
    ObservationProviderStatus.STALE: frozenset(
        {
            ObservationProviderStatus.STALE,
            ObservationProviderStatus.READY,
            ObservationProviderStatus.INVALID,
            ObservationProviderStatus.RESET,
        }
    ),
    ObservationProviderStatus.INVALID: frozenset(
        {
            ObservationProviderStatus.INVALID,
            ObservationProviderStatus.RESET,
        }
    ),
    ObservationProviderStatus.RESET: frozenset(
        {
            ObservationProviderStatus.RESET,
            ObservationProviderStatus.UNINITIALIZED,
        }
    ),
}


@dataclass(frozen=True, slots=True)
class ControllerObservation:
    """Immutable controller observation with aggregate and field-level validity.

    ``state`` remains the unified ``QuadState`` numerical carrier. ``validity`` is kept as
    the aggregate boolean used by the existing V1 runner: it is ``True`` only when every
    field is valid. ``field_validity`` records which fields the provider actually
    estimates, allowing an attitude-only provider to mark position and velocity invalid
    without pretending that their carrier values are estimates. The object stores its
    simulation timestamp only. Observation age, stale thresholds, and stale decisions
    belong to a scheduler/control boundary and are deliberately absent.
    """

    state: QuadState
    timestamp: float
    validity: bool
    source: ObservationSource
    field_validity: ObservationValidity | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, QuadState):
            raise TypeError("state must be a QuadState-compatible observation payload")
        if isinstance(self.timestamp, bool) or not isinstance(self.timestamp, (int, float)):
            raise TypeError("timestamp must be a real simulation-time value")
        timestamp = float(self.timestamp)
        if not isfinite(timestamp) or timestamp < 0.0:
            raise ValueError("timestamp must be finite and non-negative")
        if type(self.validity) is not bool:
            raise TypeError(
                "validity must be a bool representing whole observation aggregate validity"
            )
        if not isinstance(self.source, ObservationSource):
            raise TypeError("source must be an explicit ObservationSource")
        if self.field_validity is None:
            field_validity = (
                ObservationValidity.all_valid()
                if self.validity
                else ObservationValidity.all_invalid()
            )
        elif not isinstance(self.field_validity, ObservationValidity):
            raise TypeError("field_validity must be an ObservationValidity")
        else:
            field_validity = self.field_validity
        if self.validity != field_validity.is_complete:
            raise ValueError(
                "validity must equal field_validity.is_complete; partial observations "
                "must use validity=False"
            )
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "field_validity", field_validity)
