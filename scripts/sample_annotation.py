"""Sample 100 policies for manual annotation.

Produces two files:
- data/external/annotation_sample.csv   (company_id, url, policy_text_snippet, is_hard_case)
- data/external/annotation_groundtruth.csv  (empty template for annotators)

Hard cases: companies where MockPolicyEvaluator and OpenAI GPT-4 disagree on
mentions_third_party_trackers.
"""
import csv
import json
import random
from pathlib import Path

RANDOM_SEED = 42
N_RANDOM = 70
N_HARD = 30
SNIPPET_LEN = 400


def load_policies(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found — run extract-policies first")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_llm_eval(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found — run evaluate-llm first")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def find_hard_cases(llm_eval: dict, n: int, rng: random.Random) -> list[str]:
    """Companies where Mock (keyword) and GPT-4 disagree on tracker mention."""
    from src.llm.evaluator import MockPolicyEvaluator
    mock = MockPolicyEvaluator()
    disagreements = []
    for company_id, record in llm_eval.items():
        policy_text = record.get("policy_text", "")
        if not policy_text:
            continue
        mock_result = mock.evaluate(policy_text)
        llm_flag = record.get("mentions_third_party_trackers", None)
        if llm_flag is not None and mock_result.mentions_third_party_trackers != llm_flag:
            disagreements.append(company_id)
    rng.shuffle(disagreements)
    return disagreements[:n]


def main():
    rng = random.Random(RANDOM_SEED)
    policies = load_policies(Path("data/interim/policies.json"))
    llm_eval = load_llm_eval(Path("data/interim/llm_eval.json"))

    all_ids = list(policies.keys())
    hard_ids = set(find_hard_cases(llm_eval, N_HARD, rng))

    # fill random pool excluding hard cases
    pool = [cid for cid in all_ids if cid not in hard_ids]
    rng.shuffle(pool)
    random_ids = pool[:N_RANDOM]

    sample = [(cid, True) for cid in hard_ids] + [(cid, False) for cid in random_ids]
    rng.shuffle(sample)

    out_sample = Path("data/external/annotation_sample.csv")
    out_truth = Path("data/external/annotation_groundtruth.csv")

    fields_sample = ["company_id", "policy_snippet", "is_hard_case"]
    fields_truth = [
        "company_id",
        "annotator_1_tracker", "annotator_2_tracker", "annotator_3_tracker",
        "annotator_1_mandatory", "annotator_2_mandatory", "annotator_3_mandatory",
        "majority_tracker", "majority_mandatory",
        "notes",
    ]

    with open(out_sample, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields_sample)
        w.writeheader()
        for cid, hard in sample:
            text = policies.get(cid, {}).get("text", "")
            w.writerow({
                "company_id": cid,
                "policy_snippet": text[:SNIPPET_LEN].replace("\n", " "),
                "is_hard_case": hard,
            })

    with open(out_truth, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields_truth)
        w.writeheader()
        for cid, _ in sample:
            w.writerow({k: "" for k in fields_truth} | {"company_id": cid})

    print(f"Wrote {len(sample)} rows to {out_sample}")
    print(f"Wrote blank ground-truth template to {out_truth}")


if __name__ == "__main__":
    main()
