"""Replay the frozen C/G/P factorial using the independently generated n=20 source bank."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import math
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import scripts.run_factorial_attribution as factorial

def summary20(values):
    values = np.asarray(values, dtype=float)
    mean = float(values.mean())
    sd = float(values.std(ddof=1))
    half = 2.093024054 * sd / math.sqrt(len(values))
    return {"raw_runs": values.tolist(), "mean": mean, "sample_sd": sd,
            "ci95": [mean - half, mean + half]}

factorial.summary = summary20

protocol = json.loads(factorial.PROTOCOL.read_text(encoding="utf-8"))
source_path = ROOT / "data/cross_generator_n20/results.json"
source = json.loads(source_path.read_text(encoding="utf-8"))
config = deepcopy(source["config"])
config["models"] = {"mlp": deepcopy(protocol["model"]["configuration"])}
seeds = list(range(20))
out = ROOT / "data/factorial_attribution_n20"
if out.exists() and any(out.iterdir()):
    raise FileExistsError("refusing to overwrite existing n20 factorial output")
out.mkdir(parents=True)
document = {"status": "running", "mode": "frozen_full_n20",
            "protocol_version": protocol["version"],
            "protocol_sha256": factorial.sha(factorial.PROTOCOL),
            "source_results_sha256": factorial.sha(source_path),
            "effective_config": config, "seeds": seeds,
            "bootstrap_repetitions": protocol["uncertainty"]["bootstrap_repetitions"],
            "runs": []}
for seed in seeds:
    document["runs"].append(factorial.run_seed(
        seed, config, protocol, source, out, smoke=False))
    factorial.write_json(out / "progress.json", document)
document["aggregate"] = factorial.aggregate(
    document["runs"], protocol, document["bootstrap_repetitions"])
document["status"] = "complete"
factorial.write_json(out / "results.json", document)
print(json.dumps(document["aggregate"], ensure_ascii=False, indent=2))
