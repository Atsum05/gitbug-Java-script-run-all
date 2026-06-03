#!/usr/bin/env python3
"""
Análisis estadístico JUAMPI vs MUTATION sobre metrics.csv.

Produce analysis.csv con una fila por bug con:

  Efectividad
    juampi_detected      — YES/NO (mayoría de seeds)
    mutation_detected    — YES/NO (mayoría de seeds)

  Coverage (line + branch promediados)
    juampi_avg_coverage
    mutation_avg_coverage
    coverage_wilcoxon_significant   — YES/NO
    coverage_pvalue
    coverage_vargha_delaney         — efect size (0..1)

  Mutation Score
    juampi_avg_score
    mutation_avg_score
    score_wilcoxon_significant      — YES/NO
    score_pvalue
    score_vargha_delaney

Uso:
    python3 analysis.py
    python3 analysis.py --metrics /ruta/a/metrics.csv
"""

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path

METRICS_CSV = Path("/home/vboxuser/tesis/gitbug-batch/metrics.csv")
OUT_CSV     = Path("/home/vboxuser/tesis/gitbug-batch/analysis.csv")

FIELDNAMES = [
    "bug_id",
    "class_under_test",
    # Efectividad
    "juampi_detected",
    "mutation_detected",
    # Coverage
    "juampi_avg_coverage",
    "mutation_avg_coverage",
    "coverage_wilcoxon_significant",
    "coverage_pvalue",
    "coverage_vargha_delaney",
    # Mutation score
    "juampi_avg_score",
    "mutation_avg_score",
    "score_wilcoxon_significant",
    "score_pvalue",
    "score_vargha_delaney",
]


# ── Estadísticos ─────────────────────────────────────────────────────────────

def mean(values: list) -> float:
    return sum(values) / len(values) if values else 0.0


def vargha_delaney(a: list, b: list) -> float:
    """
    A12 effect size (Vargha-Delaney).
    A12 > 0.5  → grupo A tiende a ser mayor
    A12 = 0.5  → sin diferencia
    A12 < 0.5  → grupo B tiende a ser mayor
    """
    n = len(a) * len(b)
    if n == 0:
        return 0.5
    wins = sum(1 for x in a for y in b if x > y)
    ties = sum(1 for x in a for y in b if x == y)
    return (wins + 0.5 * ties) / n


def wilcoxon_signed_rank(a: list, b: list, alpha: float = 0.05):
    """
    Wilcoxon signed-rank test (one-tailed: H1: A > B).
    Devuelve (p_value, significant).

    Implementación exacta para muestras pequeñas usando la distribución
    normal aproximada con corrección de continuidad.
    """
    diffs = [x - y for x, y in zip(a, b) if x != y]
    n = len(diffs)

    if n == 0:
        return 1.0, False

    ranks_with_sign = []
    abs_diffs = sorted(enumerate(diffs), key=lambda t: abs(t[1]))

    # Asignar rangos (promedio de empates)
    i = 0
    ranked = [0.0] * len(abs_diffs)
    while i < len(abs_diffs):
        j = i
        while j < len(abs_diffs) and abs(abs_diffs[j][1]) == abs(abs_diffs[i][1]):
            j += 1
        avg_rank = (i + 1 + j) / 2
        for k in range(i, j):
            ranked[k] = avg_rank
        i = j

    w_plus  = sum(ranked[k] for k, (_, d) in enumerate(abs_diffs) if d > 0)
    w_minus = sum(ranked[k] for k, (_, d) in enumerate(abs_diffs) if d < 0)

    # Aproximación normal
    mu    = n * (n + 1) / 4
    sigma = math.sqrt(n * (n + 1) * (2 * n + 1) / 24)

    if sigma == 0:
        return 1.0, False

    # One-tailed: H1 que A > B → usamos W+
    z = (w_plus - 0.5 - mu) / sigma
    p = 1 - _norm_cdf(z)

    return round(p, 4), p < alpha


def _norm_cdf(x: float) -> float:
    """CDF de la normal estándar (aproximación de Abramowitz & Stegun)."""
    t = 1 / (1 + 0.2316419 * abs(x))
    poly = t * (0.319381530
                + t * (-0.356563782
                       + t * (1.781477937
                              + t * (-1.821255978
                                     + t * 1.330274429))))
    approx = 1 - (1 / math.sqrt(2 * math.pi)) * math.exp(-0.5 * x ** 2) * poly
    return approx if x >= 0 else 1 - approx


# ── Pipeline principal ────────────────────────────────────────────────────────

def load_metrics(csv_path: Path) -> dict:
    """
    Carga metrics.csv y agrupa por (bug_id, class_under_test, algorithm).
    Devuelve:
        data[(bug_id, cut, algorithm)] = {
            "coverage": [...],   # promedio line+branch por seed
            "score":    [...],   # mutation_score_final por seed
            "detected": [...]    # 1 o 0 por seed
        }
    """
    data = defaultdict(lambda: {"coverage": [], "score": [], "detected": []})

    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            bug_id = row["bug_id"]
            cut    = row["class_under_test"]
            alg    = row["algorithm"].upper()
            key    = (bug_id, cut, alg)

            line   = row.get("line_coverage", "")
            branch = row.get("branch_coverage", "")
            score  = row.get("mutation_score_final", "")

            if line and branch:
                data[key]["coverage"].append((int(line) + int(branch)) / 2)
            if score:
                data[key]["score"].append(int(score))

            data[key]["detected"].append(1 if row.get("detected") == "YES" else 0)

    return data


def majority_detected(detected_list: list) -> str:
    if not detected_list:
        return ""
    return "YES" if sum(detected_list) > len(detected_list) / 2 else "NO"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics", default=str(METRICS_CSV))
    parser.add_argument("--out",     default=str(OUT_CSV))
    args = parser.parse_args()

    metrics_path = Path(args.metrics)
    out_path     = Path(args.out)

    if not metrics_path.is_file():
        print(f"ERROR: No se encontró {metrics_path}")
        return

    data = load_metrics(metrics_path)

    # Obtener lista de bugs únicos (en orden de aparición)
    bugs_seen = {}
    with open(metrics_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (row["bug_id"], row["class_under_test"])
            if key not in bugs_seen:
                bugs_seen[key] = True
    bugs = list(bugs_seen.keys())

    out_rows = []

    for bug_id, cut in bugs:
        j = data.get((bug_id, cut, "JUAMPI"),   {"coverage": [], "score": [], "detected": []})
        m = data.get((bug_id, cut, "MUTATION"),  {"coverage": [], "score": [], "detected": []})

        # Coverage stats
        cov_p, cov_sig = wilcoxon_signed_rank(j["coverage"], m["coverage"]) if (j["coverage"] and m["coverage"]) else (None, None)
        cov_vd         = vargha_delaney(j["coverage"], m["coverage"])        if (j["coverage"] and m["coverage"]) else None

        # Mutation score stats
        ms_p,  ms_sig  = wilcoxon_signed_rank(j["score"],    m["score"])    if (j["score"]    and m["score"])    else (None, None)
        ms_vd          = vargha_delaney(j["score"],    m["score"])           if (j["score"]    and m["score"])    else None

        out_rows.append({
            "bug_id":           bug_id,
            "class_under_test": cut,
            # Efectividad
            "juampi_detected":   majority_detected(j["detected"]),
            "mutation_detected": majority_detected(m["detected"]),
            # Coverage
            "juampi_avg_coverage":          round(mean(j["coverage"]), 1) if j["coverage"] else "",
            "mutation_avg_coverage":        round(mean(m["coverage"]), 1) if m["coverage"] else "",
            "coverage_wilcoxon_significant": ("YES" if cov_sig else "NO") if cov_sig is not None else "",
            "coverage_pvalue":               cov_p if cov_p is not None else "",
            "coverage_vargha_delaney":       round(cov_vd, 3) if cov_vd is not None else "",
            # Mutation score
            "juampi_avg_score":             round(mean(j["score"]), 1) if j["score"] else "",
            "mutation_avg_score":           round(mean(m["score"]), 1) if m["score"] else "",
            "score_wilcoxon_significant":   ("YES" if ms_sig  else "NO") if ms_sig  is not None else "",
            "score_pvalue":                  ms_p  if ms_p   is not None else "",
            "score_vargha_delaney":          round(ms_vd,  3) if ms_vd  is not None else "",
        })

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"Escrito en: {out_path}  ({len(out_rows)} bugs)")
    print()

    # Vista previa
    hdr = f"{'bug_id':<45} {'J_det':<6} {'M_det':<6} {'J_cov':<6} {'M_cov':<6} {'cov_sig':<8} {'J_ms':<6} {'M_ms':<6} {'ms_sig':<8} {'ms_A12'}"
    print(hdr)
    print("-" * len(hdr))
    for r in out_rows:
        print(
            f"{r['bug_id']:<45} "
            f"{r['juampi_detected']:<6} "
            f"{r['mutation_detected']:<6} "
            f"{str(r['juampi_avg_coverage']):<6} "
            f"{str(r['mutation_avg_coverage']):<6} "
            f"{str(r['coverage_wilcoxon_significant']):<8} "
            f"{str(r['juampi_avg_score']):<6} "
            f"{str(r['mutation_avg_score']):<6} "
            f"{str(r['score_wilcoxon_significant']):<8} "
            f"{r['score_vargha_delaney']}"
        )


if __name__ == "__main__":
    main()