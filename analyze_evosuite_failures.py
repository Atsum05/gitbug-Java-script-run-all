#!/usr/bin/env python3
"""
Reads results.csv and classifies why EvoSuite produced no tests.

For each row where result is EVOSUITE NO TESTS or EVOSUITE FAILED, it reads
the evosuite_fixed.log and matches known error patterns.

Usage:
    python3 analyze_evosuite_failures.py
    python3 analyze_evosuite_failures.py --results-csv /path/to/results.csv
    python3 analyze_evosuite_failures.py --out failures_analysis.csv
"""

import argparse
import csv
import re
from pathlib import Path
from collections import Counter


RESULTS_CSV = Path("/home/atsum/Documents/gitbug-batch/results.csv")
DEFAULT_OUT = Path("/home/atsum/Documents/investigation/evosuite_failures_analysis.csv")

FIELDNAMES_OUT = [
    "bug_id",
    "class_under_test",
    "seed",
    "result",
    "failure_category",
    "failure_detail",
    "logs_dir",
]

# Ordered list of (category, pattern) pairs.
# First match wins, so put more specific patterns first.
PATTERNS = [
    (
        "JAVA_VERSION_TOO_NEW",
        re.compile(r"Unsupported class file major version (\d+)"),
    ),
    (
        "WILDCARD_GENERICS",
        re.compile(r"not supported: class sun\.reflect\.generics"),
    ),
    (
        "CFG_ERROR",
        re.compile(r"expect to get instruction without BasicBlock already set"),
    ),
    (
        "FORBIDDEN_PACKAGE",
        re.compile(r"cannot currently handle|belongs to one of the packages EvoSuite"),
    ),
    (
        "CLASSPATH_ERROR",
        re.compile(
            r"(NoClassDefFoundError|ClassNotFoundException.*Class not found:)",
            re.DOTALL,
        ),
    ),
    (
        "RMI_CRASH",
        re.compile(
            r"(Broken pipe|ConnectException|Connection refused|UnmarshalException)"
        ),
    ),
    (
        "NO_GENERATORS",
        re.compile(r"no generators for|SUT has no testable methods|Cannot generate"),
    ),
]

# Java class file major version → Java release number.
_JAVA_MAJOR = {str(44 + n): str(n) for n in range(1, 30)}


def classify_log(log_text: str) -> tuple[str, str]:
    """Return (category, detail) for one evosuite_fixed.log."""
    for category, pattern in PATTERNS:
        m = pattern.search(log_text)
        if m:
            detail = ""
            if category == "JAVA_VERSION_TOO_NEW":
                major = m.group(1)
                java = _JAVA_MAJOR.get(major, "?")
                detail = f"class file version {major} (Java {java})"
            elif category == "CLASSPATH_ERROR":
                # Extract the missing class name from the log line if possible.
                missing = re.search(
                    r"Class not found:? ([^\s\n:]+)|NoClassDefFoundError: ([^\s\n]+)",
                    log_text,
                )
                if missing:
                    detail = (missing.group(1) or missing.group(2) or "").replace("/", ".")
            return category, detail
    return "UNKNOWN", ""


def load_results(results_csv: Path) -> list[dict]:
    rows = []
    with open(results_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def analyze(results_csv: Path, out_csv: Path) -> None:
    rows = load_results(results_csv)

    target_results = {"EVOSUITE NO TESTS", "EVOSUITE FAILED"}
    failures = [r for r in rows if r.get("result", "").strip() in target_results]

    print(f"Total rows in results.csv:        {len(rows)}")
    print(f"Rows with EVOSUITE NO TESTS/FAILED: {len(failures)}")
    print()

    out_rows = []
    category_counter: Counter = Counter()

    for row in failures:
        logs_dir = Path(row.get("logs_dir", "").strip())
        log_file = logs_dir / "evosuite_fixed.log"

        if not log_file.is_file():
            category, detail = "NO_LOG", ""
        else:
            log_text = log_file.read_text(encoding="utf-8", errors="replace")
            category, detail = classify_log(log_text)

        category_counter[category] += 1

        out_rows.append({
            "bug_id": row.get("bug_id", ""),
            "class_under_test": row.get("class_under_test", ""),
            "seed": row.get("seed", ""),
            "result": row.get("result", ""),
            "failure_category": category,
            "failure_detail": detail,
            "logs_dir": str(logs_dir),
        })

    # Write output CSV.
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES_OUT)
        writer.writeheader()
        writer.writerows(out_rows)

    # Print summary.
    print("=== Failure category breakdown ===")
    for cat, count in category_counter.most_common():
        pct = 100 * count / len(failures) if failures else 0
        print(f"  {cat:<25} {count:>4}  ({pct:.0f}%)")

    print()
    print("=== Per-category bug list ===")
    for cat, _ in category_counter.most_common():
        entries = [r for r in out_rows if r["failure_category"] == cat]
        print(f"\n[{cat}]  ({len(entries)} entries)")
        # Deduplicate by bug_id+class for readability when multiple seeds exist.
        seen = set()
        for r in entries:
            key = (r["bug_id"], r["class_under_test"])
            if key in seen:
                continue
            seen.add(key)
            detail = f"  ({r['failure_detail']})" if r["failure_detail"] else ""
            print(f"  {r['bug_id']} :: {r['class_under_test']}{detail}")

    print()
    print(f"Full analysis saved to: {out_csv}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify EvoSuite no-test failures.")
    parser.add_argument(
        "--results-csv",
        default=str(RESULTS_CSV),
        help=f"Path to results.csv. Default: {RESULTS_CSV}",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help=f"Output CSV path. Default: {DEFAULT_OUT}",
    )
    args = parser.parse_args()

    analyze(Path(args.results_csv), Path(args.out))


if __name__ == "__main__":
    main()
