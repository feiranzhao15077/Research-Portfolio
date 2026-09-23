"""Consumer-scoped observation adaptation for D-068/D-6-6-7-b.

This module does not change the frozen ``ControllerPipeline`` API.  It proves that a
``ControllerObservation`` satisfies one explicit consumer requirement and, on success,
returns a state view that the existing pipeline can receive as ``state``.  The view
retains the observation provenance and field-validity metadata so a partial observation
is never reclassified as a complete state estimate.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from core.contracts.observation import (
    ATTITUDE_INNER_LOOP,
    FULL_STATE_OUTER_LOOP,
    ConsumerObservationProfile,
    ConsumerObservationRequirements,
    ControllerObservation,
    ObservationSource,
    ObservationValidity,
    observation_satisfies,
)
from core.state import QuadState

__all__ = [
    "ConsumerObservationAdapter",
    "ConsumerObservationAdapterResult",
    "ConsumerStateView",
]


@dataclass(frozen=True, slots=True)
class ConsumerStateView:
    """A ``QuadState`` carrier accepted for one explicit consumer profile.

    The numerical carrier is structurally compatible with the frozen
    ``ControllerPipeline.step(state=...)`` input.  ``validity`` and ``field_validity``
    retain the original observation semantics; they do not claim that every field in
    the carrier is estimated.
    """

    state: QuadState
    profile: ConsumerObservationProfile
    timestamp: float
    validity: bool
    source: ObservationSource
    field_validity: ObservationValidity

    def __post_init__(self) -> None:
        if not isinstance(self.state, QuadState):
            raise TypeError("state must be a QuadState")
        if not isinstance(self.profile, ConsumerObservationProfile):
            raise TypeError("profile must be a ConsumerObservationProfile")
        if isinstance(self.timestamp, bool) or not isinstance(self.timestamp, (int, float)):
            raise TypeError("timestamp must be a real simulation-time value")
        timestamp = float(self.timestamp)
        if not isfinite(timestamp) or timestamp < 0.0:
            raise ValueError("timestamp must be finite and non-negative")
        if type(self.validity) is not bool:
            raise TypeError("validity must be a bool")
        if not isinstance(self.source, ObservationSource):
            raise TypeError("source must be an ObservationSource")
        if not isinstance(self.field_validity, ObservationValidity):
            raise TypeError("field_validity must be an ObservationValidity")
        object.__setattr__(self, "timestamp", timestamp)


@dataclass(frozen=True, slots=True)
class ConsumerObservationAdapterResult:
    """Explicit success or rejection result for one consumer boundary."""

    accepted: bool
    state_view: ConsumerStateView | None
    profile: ConsumerObservationProfile
    timestamp: float
    validity: bool
    source: ObservationSource
    field_validity: ObservationValidity
    rejection_reason: str | None = None

    def __post_init__(self) -> None:
        if type(self.accepted) is not bool:
            raise TypeError("accepted must be a bool")
        if not isinstance(self.profile, ConsumerObservationProfile):
            raise TypeError("profile must be a ConsumerObservationProfile")
        if isinstance(self.timestamp, bool) or not isinstance(self.timestamp, (int, float)):
            raise TypeError("timestamp must be a real simulation-time value")
        timestamp = float(self.timestamp)
        if not isfinite(timestamp) or timestamp < 0.0:
            raise ValueError("timestamp must be finite and non-negative")
        if type(self.validity) is not bool:
            raise TypeError("validity must be a bool")
        if not isinstance(self.source, ObservationSource):
            raise TypeError("source must be an ObservationSource")
        if not isinstance(self.field_validity, ObservationValidity):
            raise TypeError("field_validity must be an ObservationValidity")
        if self.accepted != (self.state_view is not None):
            raise ValueError("accepted must agree with state_view presence")
        if self.accepted and self.rejection_reason is not None:
            raise ValueError("accepted result cannot carry a rejection_reason")
        if not self.accepted and (
            not isinstance(self.rejection_reason, str) or not self.rejection_reason
        ):
            raise ValueError("rejected result must carry a rejection_reason")
        if self.state_view is not None:
            if self.state_view.profile is not self.profile:
                raise ValueError("state_view profile must match result profile")
            if self.state_view.timestamp != timestamp:
                raise ValueError("state_view timestamp must match result timestamp")
            if self.state_view.validity is not self.validity:
                raise ValueError("state_view validity must match result validity")
            if self.state_view.source is not self.source:
                raise ValueError("state_view source must match result source")
            if self.state_view.field_validity != self.field_validity:
                raise ValueError("state_view field_validity must match result metadata")
        object.__setattr__(self, "timestamp", timestamp)


class ConsumerObservationAdapter:
    """Validate and scope an observation for a selected consumer profile."""

    @staticmethod
    def adapt(
        observation: ControllerObservation,
        profile: ConsumerObservationProfile,
    ) -> ConsumerObservationAdapterResult:
        """Return a consumer-scoped state view or an explicit rejection.

        The adapter reads only the supplied observation and profile.  It never reads
        plant truth, fills invalid fields, changes source, or falls back to another
        observation provider.
        """
        if not isinstance(observation, ControllerObservation):
            raise TypeError("observation must be a ControllerObservation")
        if not isinstance(profile, ConsumerObservationProfile):
            raise TypeError("profile must be a ConsumerObservationProfile")
        requirements = _requirements_for(profile)
        field_validity = observation.field_validity
        if field_validity is None:  # pragma: no cover - ControllerObservation normalizes it
            raise ValueError("observation must carry field_validity")

        accepted = observation_satisfies(observation, requirements)
        state_view = None
        rejection_reason = None
        if accepted:
            state_view = ConsumerStateView(
                state=observation.state,
                profile=profile,
                timestamp=observation.timestamp,
                validity=observation.validity,
                source=observation.source,
                field_validity=field_validity,
            )
        else:
            rejection_reason = "one or more required observation fields are INVALID"

        return ConsumerObservationAdapterResult(
            accepted=accepted,
            state_view=state_view,
            profile=profile,
            timestamp=observation.timestamp,
            validity=observation.validity,
            source=observation.source,
            field_validity=field_validity,
            rejection_reason=rejection_reason,
        )


def _requirements_for(
    profile: ConsumerObservationProfile,
) -> ConsumerObservationRequirements:
    if profile is ConsumerObservationProfile.ATTITUDE_INNER_LOOP:
        return ATTITUDE_INNER_LOOP
    if profile is ConsumerObservationProfile.FULL_STATE_OUTER_LOOP:
        return FULL_STATE_OUTER_LOOP
    raise ValueError(f"unsupported consumer profile: {profile!r}")
