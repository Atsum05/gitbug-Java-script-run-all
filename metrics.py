#!/usr/bin/env python3
"""
Lee results.csv, extrae métricas de cada evosuite_fixed.log
y genera metrics.csv con una fila por (bug, algorithm, seed).

Columnas de salida:
    bug_id, class_under_test, algorithm, seed, java_version,
    line_coverage, branch_coverage, mutation_score_final,
    test_count, assertion_count, detected

Uso:
    python3 metrics.py
"""

import csv
import re
from pathlib import Path

RESULTS_CSV = Path("/home/vboxuser/tesis/gitbug-batch/results.csv")
OUT_CSV     = Path("/home/vboxuser/tesis/gitbug-batch/metrics.csv")

DETECTED_RESULTS = {"BUG-REVEALING"}

COVERAGE_RE = re.compile(r"Coverage of criterion (\w+): ([0-9]+)%")
FINAL_MS_RE = re.compile(r"Resulting test suite.s mutation score: ([0-9]+)%")
SUITE_RE    = re.compile(r"Generated\s+([0-9]+)\s+tests?,\s+total\s+([0-9]+)\s+assertions?")
STRATEGY_RE = re.compile(r"strategy:\s*(\w+)", re.IGNORECASE)

FIELDNAMES = [
    "bug_id",
    "class_under_test",
    "algorithm",
    "seed",
    "java_version",
    "line_coverage",
    "branch_coverage",
    "mutation_score_final",
    "test_count",
    "assertion_count",
    "detected",
]


def parse_log(log_path: Path, fallback_algorithm: str) -> dict:
    m = {
        "algorithm":            fallback_algorithm,
        "line_coverage":        "",
        "branch_coverage":      "",
        "mutation_score_final": "",
        "test_count":           "",
        "assertion_count":      "",
    }

    if not log_path.is_file():
        return m

    with open(log_path, encoding="utf-8", errors="replace") as f:
        for line in f:

            if not m["algorithm"]:
                s = STRATEGY_RE.search(line)
                if s:
                    m["algorithm"] = s.group(1).upper()

            c = COVERAGE_RE.search(line)
            if c:
                crit, val = c.group(1), c.group(2)
                if crit == "LINE":
                    m["line_coverage"] = val
                elif crit == "BRANCH":
                    m["branch_coverage"] = val
                continue

            ms = FINAL_MS_RE.search(line)
            if ms:
                m["mutation_score_final"] = ms.group(1)
                continue

            su = SUITE_RE.search(line)
            if su:
                m["test_count"]      = su.group(1)
                m["assertion_count"] = su.group(2)

    return m


def main() -> None:
    if not RESULTS_CSV.is_file():
        print(f"ERROR: No se encontró {RESULTS_CSV}")
        return

    out_rows = []

    with open(RESULTS_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            log  = Path(row["logs_dir"]) / "evosuite_fixed.log"
            info = parse_log(log, row.get("algorithm", ""))

            out_rows.append({
                "bug_id":               row["bug_id"],
                "class_under_test":     row["class_under_test"],
                "algorithm":            info["algorithm"],
                "seed":                 row["seed"],
                "java_version":         row["java_version"],
                "line_coverage":        info["line_coverage"],
                "branch_coverage":      info["branch_coverage"],
                "mutation_score_final": info["mutation_score_final"],
                "test_count":           info["test_count"],
                "assertion_count":      info["assertion_count"],
                "detected":             "YES" if row.get("result") in DETECTED_RESULTS else "NO",
            })

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"Escrito en: {OUT_CSV}  ({len(out_rows)} filas)")
    print()
    print(f"{'bug_id':<45} {'alg':<8} {'seed':<16} {'line%':<6} {'br%':<5} {'ms%':<5} {'det'}")
    print("-" * 95)
    for r in out_rows:
        print(
            f"{r['bug_id']:<45} "
            f"{r['algorithm']:<8} "
            f"{r['seed']:<16} "
            f"{r['line_coverage']:<6} "
            f"{r['branch_coverage']:<5} "
            f"{r['mutation_score_final']:<5} "
            f"{r['detected']}"
        )


if __name__ == "__main__":
    main()