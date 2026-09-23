"""Independently replay and audit the frozen three-factor attribution experiment."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parents[1] / "work/tools/robustness-python"))
sys.path.insert(0, str(ROOT))

import joblib
import numpy as np
from threadpoolctl import threadpool_limits


PROTOCOL = ROOT / "docs/validation/factorial_attribution_protocol.json"
FACTORS = ("C", "G", "P")
CORNERS = ("v000", "v100", "v010", "v110", "v001", "v101", "v011", "v111")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metric(labels, prediction):
    labels = np.asarray(labels, dtype=int)
    prediction = np.asarray(prediction, dtype=int)
    matrix = np.bincount(3 * labels + prediction, minlength=9).reshape(3, 3)
    true_positive = np.diag(matrix).astype(float)
    support, predicted = matrix.sum(1), matrix.sum(0)
    recall = np.divide(true_positive, support, out=np.zeros(3), where=support != 0)
    f1 = np.divide(2 * true_positive, support + predicted,
                   out=np.zeros(3), where=(support + predicted) != 0)
    return dict(accuracy=float(true_positive.sum() / matrix.sum()),
                macro_f1=float(f1.mean()), recall=recall.tolist(),
                confusion_matrix=matrix.tolist())


def corner_name(state):
    return "v" + "".join(str(int(value)) for value in state)


def derive(values, paths):
    path_values = {}
    collected = {factor: [] for factor in FACTORS}
    for path in paths:
        state = [0, 0, 0]
        marginal = {}
        for factor in path:
            index = FACTORS.index(factor)
            before = values[corner_name(state)]
            state[index] = 1
            after = values[corner_name(state)]
            marginal[factor] = float(after - before)
            collected[factor].append(marginal[factor])
        path_values["".join(path)] = marginal
    shapley = {factor: float(np.mean(collected[factor])) for factor in FACTORS}
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
            legacy_minus_shapley=float(path_values["CGP"][factor] - shapley[factor]))
    return dict(paths=path_values, shapley=shapley, explicit_shapley=explicit,
                interactions={key: float(value) for key, value in interactions.items()},
                total_gain=total, factor_paths=factor_paths,
                efficiency_residual=float(sum(shapley.values()) - total))


def expected_summary(values):
    values = np.asarray(values, dtype=float)
    mean, sd = float(values.mean()), float(values.std(ddof=1))
    # Independently repeat the project's frozen, decimal t-critical convention.
    critical = {2: 12.7062047364, 6: 2.5705818356}[len(values)]
    return dict(raw_runs=values.tolist(), mean=mean, sample_sd=sd,
                ci95=[mean - critical * sd / np.sqrt(len(values)),
                      mean + critical * sd / np.sqrt(len(values))])


def bootstrap_ci(values, indices):
    values = np.asarray(values, dtype=float)
    return np.percentile(values[indices].mean(axis=1), [2.5, 97.5]).tolist()


def verify(result_path):
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    document = json.loads(result_path.read_text(encoding="utf-8"))
    checks, failures = [], []

    def check(condition, label):
        checks.append(label)
        if not bool(condition):
            failures.append(label)

    def close(actual, expected, label, tolerance=1e-12):
        check(np.shape(actual) == np.shape(expected)
              and np.allclose(actual, expected, rtol=0.0, atol=tolerance), label)

    def compare_metric(actual, expected, label):
        for key in ("accuracy", "macro_f1", "recall", "confusion_matrix"):
            close(actual[key], expected[key], label + " " + key)

    check(document["status"] == "complete", "result complete")
    check(document["mode"] in ("smoke", "frozen_full"), "known mode")
    check(document["protocol_version"] == protocol["version"], "protocol version")
    check(document["protocol_sha256"] == sha(PROTOCOL), "protocol hash")
    source_result = ROOT / protocol["source_lock"]["results"]
    check(document["source_results_sha256"] == sha(source_result)
          == protocol["source_lock"]["results_sha256"], "locked source result")
    for path_key, hash_key in (("protocol", "protocol_sha256"),
                               ("generator_source", "generator_source_sha256"),
                               ("model_source", "model_source_sha256")):
        check(sha(ROOT / protocol["source_lock"][path_key])
              == protocol["source_lock"][hash_key], "source lock " + path_key)
    for name, digest in document["source_sha256"].items():
        check(sha(ROOT / name) == digest, "runner source " + name)
    expected_seeds = (protocol["smoke_test"]["seeds"] if document["mode"] == "smoke"
                      else protocol["seeds"])
    check(document["seeds"] == expected_seeds, "frozen seed list")
    check([run["seed"] for run in document["runs"]] == expected_seeds,
          "completed seed order")
    source = json.loads(source_result.read_text(encoding="utf-8"))
    source_runs = {run["seed"]: run for run in source["runs"]}
    tolerance = protocol["acceptance"]["hard_integrity_gates"]["pairing_float_absolute_tolerance"]
    recomputed = []
    for run in document["runs"]:
        seed = run["seed"]
        data_path, model_path = ROOT / run["data_file"], ROOT / run["model_file"]
        check(data_path.resolve() == result_path.parent.resolve() / f"seed_{seed}.npz",
              "data location")
        check(model_path.resolve() == result_path.parent.resolve() / f"seed_{seed}_models.joblib",
              "model location")
        if sha(data_path) != run["data_sha256"] or sha(model_path) != run["model_sha256"]:
            raise RuntimeError("artifact hash mismatch; refusing deserialization")
        check(True, "artifact hashes before deserialization")
        models = joblib.load(model_path)
        check(set(models) == set(CORNERS), "eight saved models")
        with np.load(data_path, allow_pickle=False) as arrays:
            p0 = {key: arrays["p0__" + key] for key in (
                "continuous_raw", "sparse_raw", "y", "latent", "noise_raw",
                "scatter_positions_fraction", "scatter_weights", "scatter_active")}
            p1 = {key: arrays["p1__" + key] for key in p0}
            check(np.array_equal(p0["y"], p1["y"]), "paired labels")
            for column in (0, 4, 5, 6):
                close(p0["latent"][:, column], p1["latent"][:, column],
                      "paired invariant latent")
            source_ranges = source["config"]["train_ranges"]
            target_ranges = dict(source_ranges,
                                 **source["config"]["test_domains"]["joint_holdout"])
            for key, column, factor in (("rotation_hz", 1, 1.0),
                                        ("length_m", 2, 1.0),
                                        ("beta_deg", 3, 180.0 / np.pi)):
                q0 = ((p0["latent"][:, column] * factor - source_ranges[key][0])
                      / (source_ranges[key][1] - source_ranges[key][0]))
                q1 = ((p1["latent"][:, column] * factor - target_ranges[key][0])
                      / (target_ranges[key][1] - target_ranges[key][0]))
                close(q0, q1, "paired quantile " + key, tolerance)
            for key in ("noise_raw", "scatter_positions_fraction", "scatter_weights",
                        "scatter_active"):
                close(p0[key], p1[key], "paired " + key, tolerance)
            check(run["noise_sha256"]["p0"] == run["noise_sha256"]["p1"],
                  "paired full-noise digest")
            if document["mode"] == "frozen_full":
                locked = source_runs[seed]
                check(run["noise_sha256"]["p0"]
                      == locked["noise_sha256"]["continuous_source"],
                      "formal p0 noise lock")
                for role in ("data", "model"):
                    check(sha(ROOT / run["noise_sha256"][f"source_{role}_file"])
                          == run["noise_sha256"][f"source_{role}_sha256"]
                          == locked[f"{role}_sha256"], "formal source artifact " + role)
            test_x, test_y = arrays["test_x"], arrays["test_y"]
            corner_training = {
                "v000": p0["continuous_raw"][0], "v100": p0["continuous_raw"][1],
                "v010": p0["sparse_raw"][0], "v110": p0["sparse_raw"][1],
                "v001": p1["continuous_raw"][0], "v101": p1["continuous_raw"][1],
                "v011": p1["sparse_raw"][0], "v111": p1["sparse_raw"][1],
            }
            values = {}
            for name in CORNERS:
                fitted = models[name]
                columns = fitted["columns"]
                train = corner_training[name][:, columns]
                close(fitted["mean"], train.mean(0), name + " train mean")
                close(fitted["sd"], train.std(0) + 1e-12, name + " train sd")
                prediction = fitted["model"].predict(
                    (test_x[:, columns] - fitted["mean"]) / fitted["sd"])
                close(prediction, arrays["pred__" + name], name + " prediction")
                got = metric(test_y, prediction)
                compare_metric(got, run["metrics"][name], name + " metric")
                values[name] = got["macro_f1"]
        attribution = derive(values, protocol["attribution"]["all_paths"])
        close(attribution["efficiency_residual"], 0.0, "Shapley efficiency")
        for factor in FACTORS:
            close(attribution["shapley"][factor],
                  attribution["explicit_shapley"][factor],
                  "explicit Shapley " + factor)
            close(attribution["shapley"][factor],
                  run["attribution"]["shapley"][factor], "saved Shapley " + factor)
            for key in ("minimum", "maximum", "range", "legacy_cgp",
                        "legacy_minus_shapley"):
                close(attribution["factor_paths"][factor][key],
                      run["attribution"]["factor_paths"][factor][key],
                      f"saved {factor} path {key}")
        for path in attribution["paths"]:
            for factor in FACTORS:
                close(attribution["paths"][path][factor],
                      run["attribution"]["paths"][path][factor],
                      "saved path marginal")
        for key, value in attribution["interactions"].items():
            close(value, run["attribution"]["interactions"][key],
                  "saved interaction " + key)
        close(attribution["total_gain"], run["attribution"]["total_gain"],
              "saved total gain")
        recomputed.append(dict(values=values, attribution=attribution))

    repetitions = document["bootstrap_repetitions"]
    expected_repetitions = (protocol["smoke_test"]["bootstrap_repetitions"]
                            if document["mode"] == "smoke"
                            else protocol["uncertainty"]["bootstrap_repetitions"])
    check(repetitions == expected_repetitions, "bootstrap repetition count")
    indices = np.random.default_rng(protocol["seed_rules"]["bootstrap_seed"]).integers(
        0, len(recomputed), size=(repetitions, len(recomputed)))
    aggregate = document["aggregate"]

    def check_aggregate(saved, values, label):
        expected = expected_summary(values)
        for key in ("raw_runs", "mean", "sample_sd", "ci95"):
            close(saved[key], expected[key], label + " " + key)
        close(saved["bootstrap_ci95"], bootstrap_ci(values, indices),
              label + " bootstrap")

    for name in CORNERS:
        check_aggregate(aggregate["corners"][name],
                        [item["values"][name] for item in recomputed],
                        "corner " + name)
    for factor in FACTORS:
        check_aggregate(aggregate["shapley"][factor],
                        [item["attribution"]["shapley"][factor]
                         for item in recomputed], "Shapley " + factor)
    for key in recomputed[0]["attribution"]["interactions"]:
        check_aggregate(aggregate["interactions"][key],
                        [item["attribution"]["interactions"][key]
                         for item in recomputed], "interaction " + key)
    for path in recomputed[0]["attribution"]["paths"]:
        for factor in FACTORS:
            check_aggregate(aggregate["paths"][path][factor],
                            [item["attribution"]["paths"][path][factor]
                             for item in recomputed], "path " + path + " " + factor)
    check_aggregate(aggregate["total_gain"],
                    [item["attribution"]["total_gain"] for item in recomputed],
                    "total gain")
    lines = protocol["acceptance"]["predeclared_interpretation_lines"]
    total_mean = aggregate["total_gain"]["mean"]
    claim_downgrade = False
    for factor in FACTORS:
        saved = aggregate["interpretation"]["factors"][factor]
        path_means = {path: aggregate["paths"][path][factor]["mean"]
                      for path in aggregate["paths"]}
        order_range = max(path_means.values()) - min(path_means.values())
        relative = None if abs(total_mean) < lines["minimum_absolute_total_gain_for_relative_ratios"] else order_range / abs(total_mean)
        signs = np.sign(list(path_means.values()))
        seed_signs = np.sign(aggregate["shapley"][factor]["raw_runs"])
        common = 1 if np.all(signs > 0) else (-1 if np.all(signs < 0) else 0)
        expected_direction = bool(common and np.sum(seed_signs == common) >= 5)
        ci = aggregate["shapley"][factor]["bootstrap_ci95"]
        legacy_difference = path_means["CGP"] - aggregate["shapley"][factor]["mean"]
        expected_order = bool(relative is not None
                              and order_range <= lines["order_stable_absolute_path_range_macro_f1"]
                              and relative <= lines["order_stable_relative_path_range_fraction_of_total_gain"])
        expected_legacy = bool(abs(legacy_difference) <=
                               lines["legacy_path_close_to_shapley_absolute_difference_macro_f1"])
        close(saved["order_range"], order_range, "interpretation order range")
        if relative is None:
            check(saved["relative_order_range"] is None, "undefined relative range")
        else:
            close(saved["relative_order_range"], relative, "relative order range")
        check(saved["order_stable"] == expected_order, "order stability")
        close(saved["legacy_cgp_minus_shapley"], legacy_difference,
              "legacy path difference")
        check(saved["legacy_close_to_shapley"] == expected_legacy,
              "legacy closeness")
        check(saved["direction_stable"] == expected_direction,
              "direction stability")
        check(saved["resolved_direction"] == bool(ci[0] > 0 or ci[1] < 0),
              "resolved direction")
        claim_downgrade = claim_downgrade or not expected_order or not expected_legacy
    for key, saved in aggregate["interpretation"]["material_interactions"].items():
        check(saved == bool(abs(aggregate["interactions"][key]["mean"])
                            > lines["material_absolute_interaction_macro_f1"]),
              "material interaction " + key)
    check(aggregate["interpretation"]["claim_downgrade_required"] == claim_downgrade,
          "claim downgrade rule")
    return dict(status="passed" if not failures else "failed", checks=len(checks),
                failures=failures, results_sha256=sha(result_path),
                protocol_sha256=sha(PROTOCOL), verifier_sha256=sha(Path(__file__)),
                mode=document["mode"], scope=(
                    "Independent saved-model replay, strict paired-library audit, "
                    "eight-corner metrics, six-path Shapley attribution, interactions, "
                    "paired seed-block bootstrap and hashes; simulation only."))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    relative = args.input or Path(protocol["smoke_test"]["output"] if args.smoke
                                  else protocol["formal_output"])
    result_path = (ROOT / relative / "results.json").resolve()
    result_path.relative_to(ROOT)
    with threadpool_limits(limits=1):
        report = verify(result_path)
    output = ROOT / "docs/validation" / (
        "factorial_attribution_smoke_verification.json"
        if report["mode"] == "smoke" else "factorial_attribution_verification.json")
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return int(bool(report["failures"]))


if __name__ == "__main__":
    raise SystemExit(main())
