#!/usr/bin/env python3
"""
Reads a baseline results.csv and assigns a Java version per bug-class pair.

  BUG-REVEALING or NO DIFFERENCE FOUND  →  java_version = 11  (already works)
  anything else                          →  java_version = 8   (try Java 8)

Writes bugs_java_assigned.csv with columns: bug_id, class_under_test, java_version.
That file is used by run_screened_bugs.py as the --bugs-csv input.

Usage:
    python3 assign_java_versions.py
    python3 assign_java_versions.py --results-csv /path/to/results.csv
    python3 assign_java_versions.py --out bugs_java_assigned.csv
"""

import argparse
import csv
from pathlib import Path

RESULTS_CSV = Path("/home/atsum/Documents/gitbug-batch/results.csv")
DEFAULT_OUT  = Path("/home/atsum/Documents/investigation/bugs_java_assigned.csv")

JAVA11_RESULTS = {"BUG-REVEALING", "NO DIFFERENCE FOUND"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-csv", default=str(RESULTS_CSV))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    results_csv = Path(args.results_csv)
    out_csv = Path(args.out)

    # Keep only the best result per (bug_id, class_under_test) across any
    # seeds/versions already in the CSV.  "Best" means: if ANY run was
    # BUG-REVEALING or NO DIFFERENCE FOUND, we consider the bug as working
    # with Java 11 and mark it java_version=11.
    best: dict[tuple, str] = {}

    with open(results_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bug_id = row.get("bug_id", "").strip()
            cut    = row.get("class_under_test", "").strip()
            result = row.get("result", "").strip()
            if not bug_id or not cut:
                continue
            key = (bug_id, cut)
            # Promote to java11 if any run produced a useful result.
            if result in JAVA11_RESULTS:
                best[key] = "11"
            elif key not in best:
                best[key] = "8"

    rows = [
        {"bug_id": k[0], "class_under_test": k[1], "java_version": v}
        for k, v in sorted(best.items())
    ]

    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["bug_id", "class_under_test", "java_version"])
        writer.writeheader()
        writer.writerows(rows)

    java11 = sum(1 for r in rows if r["java_version"] == "11")
    java8  = sum(1 for r in rows if r["java_version"] == "8")

    print(f"Total bugs: {len(rows)}")
    print(f"  java_version=11 (BUG-REVEALING / NO DIFFERENCE FOUND): {java11}")
    print(f"  java_version=8  (EVOSUITE NO TESTS / FAILED / ERROR):   {java8}")
    print(f"Written to: {out_csv}")


if __name__ == "__main__":
    main()
