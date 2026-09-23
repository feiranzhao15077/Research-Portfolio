"""Structural tests for the source-neutral observation adapter boundary."""

from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pytest

from core.contracts import ControllerObservation, ObservationSource
from core.contracts.observation_adapter import ObservationAdapter
from core.state import stationary_state


@pytest.mark.parametrize("source", list(ObservationSource))
def test_adapter_preserves_every_approved_source(source: ObservationSource) -> None:
    observation = ControllerObservation(
        state=stationary_state(altitude_m=2.0),
        timestamp=3.5,
        validity=True,
        source=source,
    )

    result = ObservationAdapter.adapt(observation)

    assert result.source is source
    assert result.timestamp == 3.5
    assert result.validity is True
    assert result.controller_state is observation.state


def test_adapter_does_not_change_observation_state_values() -> None:
    observation = ControllerObservation(
        state=stationary_state(altitude_m=2.0),
        timestamp=3.5,
        validity=True,
        source=ObservationSource.ESTIMATED,
    )
    before = observation.state.as_vector().copy()

    result = ObservationAdapter.adapt(observation)

    assert result.controller_state is not None
    np.testing.assert_array_equal(observation.state.as_vector(), before)
    np.testing.assert_array_equal(result.controller_state.as_vector(), before)


def test_adapter_has_no_truth_or_controller_dependency() -> None:
    adapter_path = Path("core/contracts/observation_adapter.py")
    tree = ast.parse(adapter_path.read_text(encoding="utf-8"))
    imported_roots: set[str] = set()
    called_names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_roots.add(node.module.split(".", maxsplit=1)[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called_names.add(node.func.id)

    assert "simulation" not in imported_roots
    assert "controllers" not in imported_roots
    assert "ControllerPipeline" not in called_names
