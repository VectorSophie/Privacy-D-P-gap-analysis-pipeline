"""Compute inter-rater agreement and LLM classification metrics from annotation data.

Reads data/external/annotation_groundtruth.csv (filled by annotators) and
data/interim/llm_eval.json, then prints:
  - Cohen's κ (pairwise and average)
  - GPT-4 Precision / Recall / F1 vs majority-vote ground truth
  - Rule-based Baseline Precision / Recall / F1

Usage:
    uv run python scripts/compute_annotation_metrics.py
"""
import csv
import json
from pathlib import Path


def _cohen_kappa(a: list, b: list) -> float:
    from sklearn.metrics import cohen_kappa_score  # type: ignore
    return float(cohen_kappa_score(a, b))


def _prf(y_true: list, y_pred: list) -> tuple[float, float, float]:
    from sklearn.metrics import precision_recall_fscore_support  # type: ignore
    p, r, f, _ = precision_recall_fscore_support(y_true, y_pred, average="binary", zero_division=0)
    return float(p), float(r), float(f)


def main():
    gt_path = Path("data/external/annotation_groundtruth.csv")
    llm_path = Path("data/interim/llm_eval.json")

    if not gt_path.exists():
        raise FileNotFoundError("annotation_groundtruth.csv not found — fill it first")
    if not llm_path.exists():
        raise FileNotFoundError("llm_eval.json not found — run evaluate-llm first")

    with open(llm_path, encoding="utf-8") as f:
        llm_eval = json.load(f)

    rows = []
    with open(gt_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["majority_tracker"] not in ("True", "False"):
                continue  # skip unfilled rows
            rows.append(row)

    if not rows:
        print("No completed rows in annotation_groundtruth.csv yet.")
        return

    cids = [r["company_id"] for r in rows]
    majority = [r["majority_tracker"] == "True" for r in rows]

    a1 = [r["annotator_1_tracker"] == "True" for r in rows]
    a2 = [r["annotator_2_tracker"] == "True" for r in rows]
    a3 = [r["annotator_3_tracker"] == "True" for r in rows]

    k12 = _cohen_kappa(a1, a2)
    k13 = _cohen_kappa(a1, a3)
    k23 = _cohen_kappa(a2, a3)
    kappa_avg = (k12 + k13 + k23) / 3

    llm_pred = [llm_eval.get(cid, {}).get("mentions_third_party_trackers", False) for cid in cids]

    from src.llm.evaluator import MockPolicyEvaluator
    mock = MockPolicyEvaluator()
    # policies needed for mock eval — try loading
    try:
        with open(Path("data/interim/policies.json"), encoding="utf-8") as f:
            policies = json.load(f)
        mock_pred = [
            mock.evaluate(policies.get(cid, {}).get("text", "")).mentions_third_party_trackers
            for cid in cids
        ]
    except FileNotFoundError:
        mock_pred = [False] * len(cids)
        print("Warning: policies.json not found — rule-based baseline set to all-False")

    llm_p, llm_r, llm_f = _prf(majority, llm_pred)
    mock_p, mock_r, mock_f = _prf(majority, mock_pred)

    print(f"\n=== Inter-rater Agreement (κ) ===")
    print(f"  A1 vs A2: {k12:.3f}")
    print(f"  A1 vs A3: {k13:.3f}")
    print(f"  A2 vs A3: {k23:.3f}")
    print(f"  Average κ: {kappa_avg:.3f}")

    print(f"\n=== Classification Performance (mentions_third_party_trackers) ===")
    print(f"{'Metric':<12} {'Rule-based':>12} {'GPT-4 Turbo':>12}")
    print(f"{'Precision':<12} {mock_p:>12.3f} {llm_p:>12.3f}")
    print(f"{'Recall':<12} {mock_r:>12.3f} {llm_r:>12.3f}")
    print(f"{'F1':<12} {mock_f:>12.3f} {llm_f:>12.3f}")
    print(f"\nN = {len(rows)} annotated samples")


if __name__ == "__main__":
    main()
