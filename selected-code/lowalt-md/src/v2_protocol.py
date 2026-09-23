"""Pure protocol helpers for the Physics Model v2 audit.

This module deliberately contains no renderer, feature extractor, or dataset
generation code.  It provides small deterministic helpers used by the v2
core-validation tests and by the later cache-only R2.1/R3 migrations.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

import numpy as np


PERMUTATION_COMBINATIONS: tuple[tuple[str, str], ...] = (
    ("A", "logistic"),
    ("A", "mlp"),
    ("B", "logistic"),
    ("B", "mlp"),
)


def permute_training_labels(labels: Sequence[int] | np.ndarray, seed: int) -> np.ndarray:
    """Return a deterministic permutation of *training* labels only.

    The input is copied; no feature matrix, test label, or split metadata is
    touched.  A permutation preserves the class-count multiset exactly.
    """
    y = np.asarray(labels).copy()
    if y.ndim != 1:
        raise ValueError("training labels must be one-dimensional")
    rng = np.random.default_rng(int(seed))
    return y[rng.permutation(y.size)]


def class_count_signature(labels: Sequence[int] | np.ndarray) -> tuple[tuple[int, int], ...]:
    """Return a stable ``(class, count)`` signature for balance checks."""
    y = np.asarray(labels)
    if y.ndim != 1:
        raise ValueError("labels must be one-dimensional")
    values, counts = np.unique(y, return_counts=True)
    return tuple((int(value), int(count)) for value, count in zip(values, counts))


def validate_disjoint_ids(train_ids: Iterable[str], test_ids: Iterable[str]) -> bool:
    """Check the leakage invariant for train/test latent-state IDs."""
    return set(train_ids).isdisjoint(set(test_ids))


@dataclass(frozen=True)
class R3Budget:
    """Expected per-class R3-v2 training budget for one experiment seed."""

    unique_latent_per_class: int = 30
    waveform_per_class: int = 60
    heldout_latent_per_class: int = 20

    @property
    def single_render_counts(self) -> tuple[int, int]:
        return (self.unique_latent_per_class, 2)

    @property
    def multi_render_counts(self) -> tuple[int, int]:
        return (self.unique_latent_per_class, 2)  # one render at each height

    def expected_waveform_count(self) -> int:
        return self.unique_latent_per_class * 2


def validate_r3_budget(
    condition_rows: Mapping[str, Sequence[Mapping[str, object]]],
    test_ids: Iterable[str],
    budget: R3Budget | None = None,
) -> dict[str, bool]:
    """Validate the matched unique-latent and waveform budgets.

    ``condition_rows`` is intentionally a light-weight manifest interface.  A
    row must contain ``sample_id`` and ``environment``; each condition is
    expected to have 60 rows for one class and no test ID may occur in train.
    The helper does not render data or inspect features.
    """
    b = budget or R3Budget()
    test_set = set(test_ids)
    unique_sets: dict[str, set[str]] = {}
    conditions_match = set(condition_rows) == {"Single-20", "Single-40", "Multi-20+40"}
    counts_ok = conditions_match
    test_disjoint = conditions_match
    for condition, rows in condition_rows.items():
        row_list = list(rows)
        ids = [str(row["sample_id"]) for row in row_list]
        envs = [str(row["environment"]) for row in row_list]
        unique_sets[condition] = set(ids)
        counts_ok &= len(row_list) == b.waveform_per_class
        counts_ok &= len(set(ids)) == b.unique_latent_per_class
        test_disjoint &= set(ids).isdisjoint(test_set)
        if condition.startswith("Single-"):
            counts_ok &= sorted({env for env in envs}) in (["20m"], ["40m"])
        elif condition == "Multi-20+40":
            counts_ok &= set(envs) == {"20m", "40m"}
    same_ids = conditions_match and len(unique_sets) == 3 and len({tuple(sorted(v)) for v in unique_sets.values()}) == 1
    return {
        "unique_latent_match": same_ids,
        "waveform_match": counts_ok,
        "test_disjoint": test_disjoint,
    }
