"""Run the frozen three-factor 2x2x2 attribution audit."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parents[1] / "work/tools/robustness-python"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import joblib
import numpy as np
from threadpoolctl import threadpool_limits

from src.cross_generator import generate_paired_generator_bank
from src.robustness_diagnostics import classification_metrics
from src.robustness_generalization import fit_baseline, predict_baseline
from run_robustness_stage1 import summary, write_json


PROTOCOL = ROOT / "docs/validation/factorial_attribution_protocol.json"
FACTORS = ("C", "G", "P")
CORNERS = ("v000", "v100", "v010", "v110", "v001", "v101", "v011", "v111")
SOURCES = [
    Path(__file__), ROOT / "src/cross_generator.py",
    ROOT / "src/robustness_generalization.py",
    ROOT / "src/robustness_diagnostics.py", ROOT / "src/recognition.py",
    ROOT / "src/recognition_data.py", ROOT / "src/micro_doppler.py",
    ROOT / "src/experiment_protocol.py", ROOT / "src/fdtd_plasma.py",
    ROOT / "src/constants.py",
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compact_bank(bank):
    noise = bank.pop("noise")
    digest = hashlib.sha256(np.ascontiguousarray(noise).view(np.uint8)).hexdigest()
    return bank, digest


def corner_name(state):
    return "v" + "".join(str(int(value)) for value in state)


def derive(values, paths):
    """Compute paths, Shapley values and interactions from eight corners."""
    path_values = {}
    by_factor = {factor: [] for factor in FACTORS}
    for path in paths:
        state = [0, 0, 0]
        marginals = {}
        for factor in path:
            index = FACTORS.index(factor)
            before = values[corner_name(state)]
            state[index] = 1
            after = values[corner_name(state)]
            marginals[factor] = float(after - before)
            by_factor[factor].append(marginals[factor])
        path_values["".join(path)] = marginals
    shapley = {factor: float(np.mean(by_factor[factor])) for factor in FACTORS}
    explicit = {
        "C": ((values["v100"] - values["v000"]) / 3
              + ((values["v110"] - values["v010"])
                 + (values["v101"] - values["v001"])) / 6
              + (values["v111"] - values["v011"]) / 3),
        "G": ((values["v010"] - values["v000"]) / 3
              + ((values["v110"] - values["v100"])
                 + (values["v011"] - values["v001"])) / 6
              + (values["v111"] - values["v101"]) / 3),
        "P": ((values["v001"] - values["v000"]) / 3
              + ((values["v101"] - values["v100"])
                 + (values["v011"] - values["v010"])) / 6
              + (values["v111"] - values["v110"]) / 3),
    }
    interactions = {
        "CG_given_P0": values["v110"] - values["v100"] - values["v010"] + values["v000"],
        "CG_given_P1": values["v111"] - values["v101"] - values["v011"] + values["v001"],
        "CP_given_G0": values["v101"] - values["v100"] - values["v001"] + values["v000"],
        "CP_given_G1": values["v111"] - values["v110"] - values["v011"] + values["v010"],
        "GP_given_C0": values["v011"] - values["v010"] - values["v001"] + values["v000"],
        "GP_given_C1": values["v111"] - values["v110"] - values["v101"] + values["v100"],
        "three_way": (values["v111"] - values["v110"] - values["v101"]
                      - values["v011"] + values["v100"] + values["v010"]
                      + values["v001"] - values["v000"]),
    }
    total = float(values["v111"] - values["v000"])
    factor_paths = {}
    for factor in FACTORS:
        items = {name: path_values[name][factor] for name in path_values}
        factor_paths[factor] = dict(
            values=items, minimum=float(min(items.values())),
            maximum=float(max(items.values())),
            range=float(max(items.values()) - min(items.values())),
            legacy_cgp=float(path_values["CGP"][factor]),
            legacy_minus_shapley=float(path_values["CGP"][factor] - shapley[factor]),
        )
    return dict(paths=path_values, shapley=shapley, explicit_shapley=explicit,
                interactions={key: float(value) for key, value in interactions.items()},
                total_gain=total, factor_paths=factor_paths,
                efficiency_residual=float(sum(shapley.values()) - total))


def check_pairing(p0, p1, source_ranges, target_ranges, expected_noise_digest,
                  p1_noise_digest, tolerance):
    if not np.array_equal(p0["y"], p1["y"]):
        raise RuntimeError("paired labels differ")
    for column in (0, 4, 5, 6):
        if not np.array_equal(p0["latent"][:, column], p1["latent"][:, column]):
            raise RuntimeError("paired invariant latent differs")
    for key, column, factor in (("rotation_hz", 1, 1.0),
                                ("length_m", 2, 1.0),
                                ("beta_deg", 3, 180.0 / np.pi)):
        q0 = ((p0["latent"][:, column] * factor - source_ranges[key][0])
              / (source_ranges[key][1] - source_ranges[key][0]))
        q1 = ((p1["latent"][:, column] * factor - target_ranges[key][0])
              / (target_ranges[key][1] - target_ranges[key][0]))
        if not np.allclose(q0, q1, rtol=0.0, atol=tolerance):
            raise RuntimeError("paired parameter quantiles differ: " + key)
    for key in ("noise_raw", "scatter_positions_fraction", "scatter_weights",
                "scatter_active"):
        if not np.allclose(p0[key], p1[key], rtol=0.0, atol=tolerance):
            raise RuntimeError("paired audit array differs: " + key)
    if p1_noise_digest != expected_noise_digest:
        raise RuntimeError("paired full-noise digest differs")


def select_corners(p0, p1):
    return {
        "v000": p0["continuous_raw"][0], "v100": p0["continuous_raw"][1],
        "v010": p0["sparse_raw"][0], "v110": p0["sparse_raw"][1],
        "v001": p1["continuous_raw"][0], "v101": p1["continuous_raw"][1],
        "v011": p1["sparse_raw"][0], "v111": p1["sparse_raw"][1],
    }


def bank_arrays(bank, prefix):
    keep = ("continuous_raw", "sparse_raw", "y", "latent", "noise_raw",
            "continuous_clean_power", "sparse_clean_power",
            "scatter_positions_fraction", "scatter_weights", "scatter_active")
    return {prefix + key: bank[key] for key in keep}


def load_formal_banks(seed, config, source, protocol):
    source_run = next(run for run in source["runs"] if run["seed"] == seed)
    data_path, model_path = ROOT / source_run["data_file"], ROOT / source_run["model_file"]
    if sha(data_path) != source_run["data_sha256"] or sha(model_path) != source_run["model_sha256"]:
        raise RuntimeError("locked source artifact hash mismatch")
    with np.load(data_path, allow_pickle=False) as data:
        p0 = {key: data["continuous_source__" + key].copy() for key in (
            "continuous_raw", "sparse_raw", "y", "latent", "noise_raw",
            "continuous_clean_power", "sparse_clean_power",
            "scatter_positions_fraction", "scatter_weights", "scatter_active")}
        test_x = data["test__joint_holdout__sparse_raw"][1].copy()
        test_y = data["test__joint_holdout__y"].copy()
    target_ranges = dict(config["train_ranges"], **config["test_domains"]["joint_holdout"])
    p1, p1_digest = compact_bank(generate_paired_generator_bank(
        protocol["sample_budget"]["train_per_class"], 90000 + seed, 220000 + seed,
        config, target_ranges))
    expected = source_run["noise_sha256"]["continuous_source"]
    tolerance = protocol["acceptance"]["hard_integrity_gates"]["pairing_float_absolute_tolerance"]
    check_pairing(p0, p1, config["train_ranges"], target_ranges,
                  expected, p1_digest, tolerance)
    return p0, p1, test_x, test_y, dict(
        p0=expected, p1=p1_digest,
        test=source_run["noise_sha256"]["domains"]["joint_holdout"]["test"],
        source_data_file=source_run["data_file"], source_data_sha256=source_run["data_sha256"],
        source_model_file=source_run["model_file"], source_model_sha256=source_run["model_sha256"])


def load_smoke_banks(seed, config, protocol):
    n_train = protocol["smoke_test"]["train_per_class"]
    n_test = protocol["smoke_test"]["test_per_class"]
    target_ranges = dict(config["train_ranges"], **config["test_domains"]["joint_holdout"])
    p0, p0_digest = compact_bank(generate_paired_generator_bank(
        n_train, 90000 + seed, 220000 + seed, config, config["train_ranges"]))
    p1, p1_digest = compact_bank(generate_paired_generator_bank(
        n_train, 90000 + seed, 220000 + seed, config, target_ranges))
    test, test_digest = compact_bank(generate_paired_generator_bank(
        n_test, 110000 + seed, 240000 + seed, config, target_ranges))
    tolerance = protocol["acceptance"]["hard_integrity_gates"]["pairing_float_absolute_tolerance"]
    check_pairing(p0, p1, config["train_ranges"], target_ranges,
                  p0_digest, p1_digest, tolerance)
    return p0, p1, test["sparse_raw"][1], test["y"], dict(
        p0=p0_digest, p1=p1_digest, test=test_digest)


def run_seed(seed, config, protocol, source, directory, smoke):
    started = time.perf_counter()
    print("factorial seed", seed, "paired banks", flush=True)
    if smoke:
        p0, p1, test_x, test_y, digests = load_smoke_banks(seed, config, protocol)
    else:
        p0, p1, test_x, test_y, digests = load_formal_banks(
            seed, config, source, protocol)
    corners = select_corners(p0, p1)
    models, metrics, arrays = {}, {}, {}
    arrays.update(bank_arrays(p0, "p0__"))
    arrays.update(bank_arrays(p1, "p1__"))
    arrays.update(test_x=test_x, test_y=test_y)
    for name in CORNERS:
        fitted = fit_baseline(corners[name], p0["y"], "mlp", seed, config)
        models[name] = fitted
        prediction = predict_baseline(fitted, test_x)
        arrays["pred__" + name] = prediction
        metrics[name] = classification_metrics(test_y, prediction)
    values = {name: metrics[name]["macro_f1"] for name in CORNERS}
    attribution = derive(values, protocol["attribution"]["all_paths"])
    tol = protocol["acceptance"]["hard_integrity_gates"]["shapley_efficiency_absolute_tolerance"]
    if abs(attribution["efficiency_residual"]) > tol:
        raise RuntimeError("Shapley efficiency failed")
    for factor in FACTORS:
        if abs(attribution["shapley"][factor]
               - attribution["explicit_shapley"][factor]) > tol:
            raise RuntimeError("path and explicit Shapley differ")
    data_path = directory / f"seed_{seed}.npz"
    model_path = directory / f"seed_{seed}_models.joblib"
    np.savez_compressed(data_path, **arrays)
    joblib.dump(models, model_path, compress=3)
    return dict(
        seed=seed, metrics=metrics, attribution=attribution, noise_sha256=digests,
        data_file=str(data_path.relative_to(ROOT)).replace("\\", "/"),
        data_sha256=sha(data_path),
        model_file=str(model_path.relative_to(ROOT)).replace("\\", "/"),
        model_sha256=sha(model_path), elapsed_s=time.perf_counter() - started)


def bootstrap_ci(values, indices):
    values = np.asarray(values, dtype=float)
    means = values[indices].mean(axis=1)
    return [float(x) for x in np.percentile(means, [2.5, 97.5])]


def aggregate(runs, protocol, repetitions):
    result = dict(corners={}, shapley={}, interactions={}, paths={}, total_gain=None)
    indices = np.random.default_rng(protocol["seed_rules"]["bootstrap_seed"]).integers(
        0, len(runs), size=(repetitions, len(runs)))
    for name in CORNERS:
        values = [run["metrics"][name]["macro_f1"] for run in runs]
        result["corners"][name] = summary(values)
        result["corners"][name]["bootstrap_ci95"] = bootstrap_ci(values, indices)
    for factor in FACTORS:
        values = [run["attribution"]["shapley"][factor] for run in runs]
        result["shapley"][factor] = summary(values)
        result["shapley"][factor]["bootstrap_ci95"] = bootstrap_ci(values, indices)
    for key in runs[0]["attribution"]["interactions"]:
        values = [run["attribution"]["interactions"][key] for run in runs]
        result["interactions"][key] = summary(values)
        result["interactions"][key]["bootstrap_ci95"] = bootstrap_ci(values, indices)
    for path in runs[0]["attribution"]["paths"]:
        result["paths"][path] = {}
        for factor in FACTORS:
            values = [run["attribution"]["paths"][path][factor] for run in runs]
            result["paths"][path][factor] = summary(values)
            result["paths"][path][factor]["bootstrap_ci95"] = bootstrap_ci(values, indices)
    total = [run["attribution"]["total_gain"] for run in runs]
    result["total_gain"] = summary(total)
    result["total_gain"]["bootstrap_ci95"] = bootstrap_ci(total, indices)
    lines = protocol["acceptance"]["predeclared_interpretation_lines"]
    total_mean = result["total_gain"]["mean"]
    interpretation = {}
    for factor in FACTORS:
        path_means = {path: result["paths"][path][factor]["mean"]
                      for path in result["paths"]}
        order_range = max(path_means.values()) - min(path_means.values())
        relative = None if abs(total_mean) < lines["minimum_absolute_total_gain_for_relative_ratios"] else order_range / abs(total_mean)
        signs = np.sign(list(path_means.values()))
        seed_signs = np.sign(result["shapley"][factor]["raw_runs"])
        common = 1 if np.all(signs > 0) else (-1 if np.all(signs < 0) else 0)
        direction_stable = bool(common and np.sum(seed_signs == common) >= 5)
        ci = result["shapley"][factor]["bootstrap_ci95"]
        legacy_difference = path_means["CGP"] - result["shapley"][factor]["mean"]
        interpretation[factor] = dict(
            path_means=path_means, path_mean_min=float(min(path_means.values())),
            path_mean_max=float(max(path_means.values())), order_range=float(order_range),
            relative_order_range=None if relative is None else float(relative),
            order_stable=bool(relative is not None
                              and order_range <= lines["order_stable_absolute_path_range_macro_f1"]
                              and relative <= lines["order_stable_relative_path_range_fraction_of_total_gain"]),
            legacy_cgp_minus_shapley=float(legacy_difference),
            legacy_close_to_shapley=bool(abs(legacy_difference) <=
                                         lines["legacy_path_close_to_shapley_absolute_difference_macro_f1"]),
            direction_stable=direction_stable,
            resolved_direction=bool(ci[0] > 0 or ci[1] < 0))
    material = {key: bool(abs(value["mean"]) >
                          lines["material_absolute_interaction_macro_f1"])
                for key, value in result["interactions"].items()}
    result["interpretation"] = dict(factors=interpretation,
                                    material_interactions=material,
                                    claim_downgrade_required=any(
                                        not value["order_stable"] or not value["legacy_close_to_shapley"]
                                        for value in interpretation.values()))
    result["bootstrap"] = dict(repetitions=repetitions,
                               seed=protocol["seed_rules"]["bootstrap_seed"],
                               type="paired seed-block percentile")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    source_path = ROOT / protocol["source_lock"]["results"]
    source = json.loads(source_path.read_text(encoding="utf-8"))
    for key, field in ((source_path, "results_sha256"),
                       (ROOT / protocol["source_lock"]["protocol"], "protocol_sha256"),
                       (ROOT / protocol["source_lock"]["generator_source"], "generator_source_sha256"),
                       (ROOT / protocol["source_lock"]["model_source"], "model_source_sha256")):
        if sha(key) != protocol["source_lock"][field]:
            raise RuntimeError("frozen source lock differs: " + str(key))
    config = deepcopy(source["config"])
    config["models"] = {"mlp": deepcopy(protocol["model"]["configuration"])}
    seeds = protocol["smoke_test"]["seeds"] if args.smoke else protocol["seeds"]
    repetitions = (protocol["smoke_test"]["bootstrap_repetitions"] if args.smoke
                   else protocol["uncertainty"]["bootstrap_repetitions"])
    if args.smoke:
        config["models"]["mlp"]["epochs"] = protocol["smoke_test"]["mlp_epochs"]
    relative = args.output or Path(protocol["smoke_test"]["output"] if args.smoke
                                   else protocol["formal_output"])
    directory = (ROOT / relative).resolve()
    directory.relative_to(ROOT)
    if directory.exists() and any(directory.iterdir()):
        raise FileExistsError("refusing to overwrite existing experiment output")
    directory.mkdir(parents=True, exist_ok=True)
    document = dict(
        status="running", mode="smoke" if args.smoke else "frozen_full",
        protocol_version=protocol["version"], protocol_sha256=sha(PROTOCOL),
        source_results_sha256=sha(source_path), effective_config=config,
        seeds=seeds, bootstrap_repetitions=repetitions,
        started_utc=datetime.now(timezone.utc).isoformat(),
        environment=dict(python=platform.python_version(), numpy=np.__version__,
                         dependencies={name: importlib.metadata.version(name)
                                       for name in ("scikit-learn", "joblib", "threadpoolctl")},
                         thread_limit=1),
        source_sha256={str(path.relative_to(ROOT)).replace("\\", "/"): sha(path)
                       for path in SOURCES}, runs=[])
    try:
        with threadpool_limits(limits=1):
            for seed in seeds:
                document["runs"].append(run_seed(
                    seed, config, protocol, source, directory, args.smoke))
                write_json(directory / "progress.json", document)
        document["aggregate"] = aggregate(document["runs"], protocol, repetitions)
        document.update(status="complete",
                        completed_utc=datetime.now(timezone.utc).isoformat())
        write_json(directory / "results.json", document)
        print(json.dumps(document["aggregate"]["shapley"], indent=2), flush=True)
    except Exception as error:
        document.update(status="failed", error=f"{type(error).__name__}: {error}")
        write_json(directory / "failure.json", document)
        raise


if __name__ == "__main__":
    main()
