"""Exact parent-artifact comparison for the 20 frozen trajectory fields."""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from experiments.simulation.layer_b_identity import verify_parent_artifacts

ROW_FIELDS = frozenset({
    "actuator_command", "angular_velocity", "angular_velocity_truth", "attitude",
    "attitude_truth", "controller_input_validity", "controller_output", "failure_state",
    "mixer_output", "observation_age", "observation_source", "observation_timestamp",
    "sample_index", "source_mode", "state_index", "step_index", "time", "timestamp",
    "truth_state_index", "truth_timestamp",
})
FIXED_METADATA = frozenset({
    "configuration_hash", "plant_hash", "controller_hash", "actuator_hash",
    "initial_state_hash", "reference_hash", "quad_params_sha256",
    "simulation_params_sha256", "environment_fingerprint", "dirty_state",
})


@dataclass(frozen=True, slots=True)
class LayerBEquivalenceAudit:
    checked_row_count: int
    checked_field_count: int
    mismatch_count: int
    first_mismatch_location: str | None
    per_field_mismatch: tuple[tuple[str, int], ...]
    passed: bool

    def to_record(self) -> dict[str, Any]:
        return {
            "checked_row_count": self.checked_row_count,
            "checked_field_count": self.checked_field_count,
            "mismatch_count": self.mismatch_count,
            "first_mismatch_location": self.first_mismatch_location,
            "per_field_mismatch": dict(self.per_field_mismatch),
            "passed": self.passed,
        }


class LayerBReplayEquivalenceChecker:
    """Read the frozen ESTIMATED file; never regenerate a parent reference."""

    @staticmethod
    def compare(parent: dict[str, Any], replay: dict[str, Any]) -> LayerBEquivalenceAudit:
        counts: Counter[str] = Counter()
        first: str | None = None
        fields_checked = 0

        def mismatch(location: str) -> None:
            nonlocal first
            if location.startswith("trajectory[") and "]." in location:
                category = "trajectory." + location.split("].", 1)[1].split(".", 1)[0]
            else:
                category = location.split(".", 1)[0]
            counts[category] += 1
            if first is None:
                first = location

        def walk(left: Any, right: Any, location: str) -> None:
            nonlocal fields_checked
            fields_checked += 1
            if type(left) is not type(right):
                mismatch(location)
            elif isinstance(left, dict):
                if set(left) != set(right):
                    mismatch(location + ".keys")
                for key in sorted(set(left) & set(right)):
                    walk(left[key], right[key], location + "." + str(key))
            elif isinstance(left, list):
                if len(left) != len(right):
                    mismatch(location + ".length")
                for index, (a, b) in enumerate(zip(left, right, strict=False)):
                    walk(a, b, f"{location}[{index}]")
            elif isinstance(left, float):
                if (
                    not math.isfinite(left) or not math.isfinite(right)
                    or left != right
                    or (left == 0.0 and math.copysign(1.0, left) != math.copysign(1.0, right))
                ):
                    mismatch(location)
            elif left != right:
                mismatch(location)

        parent_rows = parent.get("trajectory", [])
        replay_rows = replay.get("trajectory", [])
        if not isinstance(parent_rows, list) or not isinstance(replay_rows, list):
            raise ValueError("trajectory must be a list")
        if len(parent_rows) != len(replay_rows):
            mismatch("trajectory.length")
        if len(parent_rows) != 10000:
            mismatch("trajectory.parent_length")
        checked_rows = min(len(parent_rows), len(replay_rows))
        for index, (left, right) in enumerate(zip(parent_rows, replay_rows, strict=False)):
            if not isinstance(left, dict) or not isinstance(right, dict):
                mismatch(f"trajectory[{index}].shape")
                continue
            if set(left) != ROW_FIELDS or set(right) != ROW_FIELDS:
                mismatch(f"trajectory[{index}].keys")
            for field in sorted(ROW_FIELDS & set(left) & set(right)):
                walk(left[field], right[field], f"trajectory[{index}].{field}")
        for name in ("initialization", "experiment", "audit"):
            walk(parent.get(name), replay.get(name), name)
        for name in sorted(FIXED_METADATA):
            walk(parent.get("metadata", {}).get(name), replay.get("metadata", {}).get(name),
                 "metadata." + name)
        for label, rows in (("parent", parent_rows), ("replay", replay_rows)):
            count = sum(isinstance(row, dict) and row.get("controller_output") is not None
                        for row in rows)
            if count != 1000:
                mismatch(label + ".control_count")
        return LayerBEquivalenceAudit(
            checked_row_count=checked_rows, checked_field_count=fields_checked,
            mismatch_count=sum(counts.values()), first_mismatch_location=first,
            per_field_mismatch=tuple(sorted(counts.items())), passed=not counts,
        )

    @classmethod
    def check_path(cls, parent_directory: Path, replay: dict[str, Any]) -> LayerBEquivalenceAudit:
        verify_parent_artifacts(parent_directory)
        parent = json.loads((Path(parent_directory) / "ESTIMATED_ATTITUDE_LOOP.json")
                            .read_text(encoding="utf-8"))
        return cls.compare(parent, replay)
