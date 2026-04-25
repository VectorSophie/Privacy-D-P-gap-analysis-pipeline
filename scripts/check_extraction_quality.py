"""Scan policies.json and report low_quality extraction entries.

Prints a summary table and lists the first N low_quality companies for manual review.

Usage:
    uv run python scripts/check_extraction_quality.py [--show 20]
"""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--show", type=int, default=20, help="Number of low_quality entries to print")
    args = parser.parse_args()

    path = Path("data/interim/policies.json")
    if not path.exists():
        raise FileNotFoundError("policies.json not found — run extract-policies first")

    with open(path, encoding="utf-8") as f:
        policies = json.load(f)

    total = len(policies)
    low_quality = []
    old_format = 0  # list-only entries (pre-quality-flag format)

    for cid, data in policies.items():
        if isinstance(data, list):
            old_format += 1
            continue
        if data.get("quality_flag") == "low_quality":
            low_quality.append((cid, data.get("quality_reason", ""), len(data.get("text", ""))))

    ok_count = total - len(low_quality) - old_format
    print(f"\n=== Extraction Quality Summary ===")
    print(f"  Total entries  : {total}")
    print(f"  OK             : {ok_count}")
    print(f"  low_quality    : {len(low_quality)}  ({len(low_quality)/total:.1%})")
    if old_format:
        print(f"  old format (no flag): {old_format}  (re-run extract-policies to upgrade)")

    if low_quality:
        print(f"\nFirst {min(args.show, len(low_quality))} low_quality entries:")
        print(f"  {'Company ID':<30} {'Chars':>6}  Reason")
        print("  " + "-" * 70)
        for cid, reason, chars in low_quality[: args.show]:
            print(f"  {cid:<30} {chars:>6}  {reason}")


if __name__ == "__main__":
    main()
