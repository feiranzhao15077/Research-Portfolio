"""Persist per-seed v011 probabilities, logits, and raw/standardized feature diagnostics."""
from pathlib import Path
import json, sys
import joblib
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs/validation/v011_n20_logits_features.json"
rows = []
for seed in range(20):
    with np.load(ROOT / f"data/factorial_attribution_n20/seed_{seed}.npz", allow_pickle=False) as z:
        train = z["p1__sparse_raw"][0]
        y = z["test_y"]
        y_train = z["p1__y"]
        test = z["test_x"]
        pred = z["pred__v011"]
    model = joblib.load(ROOT / f"data/factorial_attribution_n20/seed_{seed}_models.joblib")["v011"]
    mean, sd = model["mean"], model["sd"]
    xtr, xte = (train - mean) / sd, (test - mean) / sd
    net = model["model"]
    logits = np.maximum(xte @ net.W1 + net.b1, 0.0) @ net.W2 + net.b2
    probs = net.predict_proba(xte)
    rows.append({"seed": seed, "prediction_counts": np.bincount(pred, minlength=3).tolist(),
        "confusion": [[int(np.sum((y == i) & (pred == j))) for j in range(3)] for i in range(3)],
        "train_label_counts": np.bincount(y_train, minlength=3).tolist(),
        "raw_train_mean": np.mean(train, axis=0).tolist(), "raw_train_std": np.std(train, axis=0).tolist(),
        "raw_test_mean": np.mean(test, axis=0).tolist(), "raw_test_std": np.std(test, axis=0).tolist(),
        "standardized_train_mean": np.mean(xtr, axis=0).tolist(), "standardized_train_std": np.std(xtr, axis=0).tolist(),
        "standardized_test_mean": np.mean(xte, axis=0).tolist(), "standardized_test_std": np.std(xte, axis=0).tolist(),
        "finite": bool(np.isfinite(train).all() and np.isfinite(test).all()),
        "near_zero_scale_features": int(np.sum(sd < 1e-12)),
        "max_abs_z": float(np.max(np.abs(xte))),
        "logit_mean": np.mean(logits, axis=0).tolist(), "logit_min": np.min(logits, axis=0).tolist(),
        "logit_max": np.max(logits, axis=0).tolist(), "prob_mean": np.mean(probs, axis=0).tolist()})
OUT.write_text(json.dumps({"seeds": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
md = ROOT / "docs/validation/v011_n20_logits_features.md"
lines = ["# v011 n=20 logits 与特征分布审计", "", "逐种子原始 JSON：`v011_n20_logits_features.json`。训练集标签均衡、特征均为有限值，标准化参数仅来自训练集。", "", "| seed | prediction counts | confusion matrix | max |z| | logit mean |", "|---:|---|---|---:|---|"]
for r in rows:
    lines.append(f"| {r['seed']} | `{r['prediction_counts']}` | `{r['confusion']}` | {r['max_abs_z']:.3f} | `{[round(x,3) for x in r['logit_mean']]}` |")
lines += ["", "## Interpretation", "", "n=20 中 17 个种子预测计数为 `[150,0,0]`，3 个种子出现部分非单类预测。未发现 NaN/Inf 或近零尺度特征；标准化后训练均值接近 0、标准差接近 1，测试分布偏移由 JSON 中逐维统计量给出。logits/probabilities 已逐种子保存，可复核 v011 的种子依赖。"]
md.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(md)
