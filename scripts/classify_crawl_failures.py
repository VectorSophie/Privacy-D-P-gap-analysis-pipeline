"""Classify the 431 crawl failures by reason for the survivorship bias analysis.

Reads data/interim/crawl_results.json and produces:
- data/external/crawl_failure_summary.csv  (reason, count, share)

Reasons:
  robots_blocked  — robots.txt disallowed crawling
  timeout         — page load or selector wait exceeded limit
  policy_not_found — page loaded but no policy link found in BFS
  http_error      — non-2xx HTTP response
  unknown         — no specific reason recorded
"""
import csv
import json
from collections import Counter
from pathlib import Path


REASON_MAP = {
    "robots_blocked": "robots_blocked",
    "timeout": "timeout",
    "policy_not_found": "policy_not_found",
    "http_error": "http_error",
}


def classify(status_detail: str | None) -> str:
    if not status_detail:
        return "unknown"
    detail = status_detail.lower()
    for key in REASON_MAP:
        if key in detail:
            return REASON_MAP[key]
    return "unknown"


def main():
    crawl_path = Path("data/interim/crawl_results.json")
    if not crawl_path.exists():
        raise FileNotFoundError("crawl_results.json not found — run crawl-sites first")

    with open(crawl_path, encoding="utf-8") as f:
        crawl_map = json.load(f)

    failures = {
        cid: record
        for cid, record in crawl_map.items()
        if record.get("status") != "success"
    }

    reason_counter: Counter = Counter()
    for record in failures.values():
        reason = classify(record.get("status") or record.get("error"))
        reason_counter[reason] += 1

    total_failures = len(failures)
    total_attempted = len(crawl_map)
    total_success = total_attempted - total_failures

    print(f"\nCrawl summary: {total_success} success / {total_failures} failed / {total_attempted} total")
    print(f"Success rate: {total_success / total_attempted:.1%}\n")

    print("Failure breakdown:")
    out_path = Path("data/external/crawl_failure_summary.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["reason", "count", "pct_of_failures", "pct_of_total"])
        for reason, count in reason_counter.most_common():
            pct_fail = count / total_failures if total_failures else 0
            pct_total = count / total_attempted if total_attempted else 0
            writer.writerow([reason, count, f"{pct_fail:.1%}", f"{pct_total:.1%}"])
            print(f"  {reason:20s}  {count:5d}  ({pct_fail:.1%} of failures)")

    print(f"\nWrote {out_path}")

    # Sensitivity analysis: what if all failures = under_disclosure?
    try:
        import pandas as pd
        mismatch = pd.read_csv(Path("data/processed/mismatch.csv"))
        n_under = int(mismatch["under_disclosure"].sum())
        n_total_sample = len(mismatch)
        n_total_with_failures = n_total_sample + total_failures
        upper_bound_n = n_under + total_failures
        upper_bound_pct = upper_bound_n / n_total_with_failures
        print(
            f"\nSensitivity: if all {total_failures} failures = under_disclosure, "
            f"rate = ({n_under} + {total_failures}) / ({n_total_sample} + {total_failures}) "
            f"= {upper_bound_n}/{n_total_with_failures} = {upper_bound_pct:.1%}"
        )
    except FileNotFoundError:
        print("\n(mismatch.csv not found — skipping sensitivity analysis)")


if __name__ == "__main__":
    main()
