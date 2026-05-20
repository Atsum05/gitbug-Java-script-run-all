import argparse
import csv
import os
import signal
import subprocess
import sys
import shutil
from pathlib import Path
from typing import Dict, List, Set, Tuple


# Local paths for my machine. These are intentionally kept at the top so they
# are easy to adjust if the project is moved to another computer.
RUNNER = "/home/vboxuser/tesis/gitbug-Java-script-run-all/gitbug_evosuite_runner.py"
GITBUG_REPO = "/home/vboxuser/tesis/gitbug-java"
EVOSUITE_JAR = "/home/vboxuser/tesis/evosuite/shaded/target/evosuite-shaded-1.2.1-SNAPSHOT.jar"
JUNIT_JAR = "/home/vboxuser/.m2/repository/junit/junit/4.13.2/junit-4.13.2.jar"
HAMCREST_JAR = "/home/vboxuser/.m2/repository/org/hamcrest/hamcrest-core/1.3/hamcrest-core-1.3.jar"
BASE_WORK_ROOT = Path("/home/vboxuser/tesis/gitbug-batch")
DEFAULT_BUGS_CSV = Path("/home/vboxuser/tesis/gitbug-Java-script-run-all/bugs.csv")
RESULTS_CSV = BASE_WORK_ROOT / "results.csv"


# Docker image to use per Java version.
# Build them once with: bash docker/build.sh
JAVA_DOCKER_IMAGES = {
    8: "gitbug-java8",
    11: "gitbug-java11",
}

# First 5 seeds from the experiment seed list (K=5 for initial runs).
DEFAULT_SEEDS = [
    3147383999447,
    9617663486099,
    5379124608314,
    4823761945872,
    9865472301598,
]

# Full list of 30 seeds available for larger experiments.
ALL_SEEDS = [
    3147383999447, 9617663486099, 5379124608314, 4823761945872, 9865472301598,
    4098156729301, 4378296150428, 8912357690412, 4082159706387, 5017574018895,
    3157718788187, 8462097531802, 8394051763254, 7861948975955, 5785033018534,
    4687593021847, 1369827450193, 8912045678210, 4195939566797, 5919993929691,
    8492013567021, 4629875132408, 7245638910523, 4823167590241, 7237611861410,
    4872395016821, 4967023185649, 6048152937021, 9516437924806, 8609571234567,
]

# Column order for the final results file.
FIELDNAMES = [
    "bug_id",
    "class_under_test",
    "seed",
    "java_version",
    "exit_code",
    "fixed_result",
    "buggy_result",
    "result",
    "conclusion",
    "work_root",
    "logs_dir",
]


# Small fallback list used when bugs.csv is not available.
# For larger experiments, use bugs.csv instead of editing this list.
DEFAULT_BUGS = [
    {
        "bug_id": "davidmoten-word-wrap-e59eedf0bac7",
        "class_under_test": "org.davidmoten.text.utils.WordWrap",
    },
    {
        "bug_id": "semver4j-semver4j-10102b374298",
        "class_under_test": "org.semver4j.RangesList",
    },
    {
        "bug_id": "klausbrunner-solarpositioning-79c0044373b4",
        "class_under_test": "net.e175.klaus.solarpositioning.Grena3",
    },
    {
        "bug_id": "klausbrunner-solarpositioning-4d35aecb4840",
        "class_under_test": "net.e175.klaus.solarpositioning.DeltaT",
    },
    {
        "bug_id": "cdimascio-dotenv-java-bbfbcfa63e3a",
        "class_under_test": "io.github.cdimascio.dotenv.internal.DotenvParser",
    },
]


def setup_java_env() -> None:
    """
    Make sure Java 11 is used when running Maven/EvoSuite.

    This avoids having to export JAVA_HOME manually every time after restarting
    the terminal.
    """
    java_home = Path("/usr/lib/jvm/java-11-openjdk-amd64")
    if java_home.is_dir():
        os.environ["JAVA_HOME"] = str(java_home)
        os.environ["PATH"] = f"{java_home / 'bin'}:{os.environ.get('PATH', '')}"


def bug_key(bug_id: str, class_under_test: str, seed: int, java_version: int) -> str:
    """
    Unique key for one bug-class-seed-javaversion experiment.

    This matters because the same bug can touch more than one Java class,
    each seed is an independent run, and Java 8 vs 11 are separate experiments.
    """
    return f"{bug_id}::{class_under_test}::{seed}::java{java_version}"


def load_bugs(csv_path: Path) -> List[Dict[str, str]]:
    """
    Load bug/class pairs from bugs.csv.

    If the CSV is missing, use DEFAULT_BUGS so the script can still run.
    """
    if not csv_path.is_file():
        print(f"[INFO] No bugs CSV found at {csv_path}. Using DEFAULT_BUGS in script.")
        return DEFAULT_BUGS

    bugs: List[Dict[str, str]] = []

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            bug_id = row.get("bug_id", "").strip()
            class_under_test = row.get("class_under_test", "").strip()

            # Ignore incomplete rows instead of failing the whole batch.
            if not bug_id or not class_under_test:
                continue

            entry: Dict[str, str] = {
                "bug_id": bug_id,
                "class_under_test": class_under_test,
            }
            java_version = row.get("java_version", "").strip()
            if java_version:
                entry["java_version"] = java_version
            bugs.append(entry)

    return bugs


def load_completed_keys(results_csv: Path) -> Set[str]:
    """
    Read previous results so we can skip experiments that already finished.

    Failed runner errors and timeouts are not considered final, so those can
    be retried in later runs.
    """
    completed: Set[str] = set()

    if not results_csv.is_file():
        return completed

    with open(results_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            bug_id = row.get("bug_id", "").strip()
            class_under_test = row.get("class_under_test", "").strip()
            seed_str = row.get("seed", "").strip()
            java_version_str = row.get("java_version", "").strip()
            result = row.get("result", "").strip()

            if not bug_id or not class_under_test or not seed_str or not java_version_str:
                continue

            if result not in {"RUNNER ERROR", "TIMEOUT", ""}:
                completed.add(bug_key(bug_id, class_under_test, int(seed_str), int(java_version_str)))

    return completed


def classify_output(output: str, exit_code: int) -> str:
    """
    Convert the worker script output into a compact result label.

    The one-bug runner prints human-readable summaries. Here we search for
    those summaries and normalize them into values that are easier to count
    later in results.csv.
    """
    if exit_code == 124:
        return "TIMEOUT"

    if "BUG-REVEALING:" in output:
        return "BUG-REVEALING"

    if "NO DIFFERENCE FOUND:" in output:
        return "NO DIFFERENCE FOUND"

    if "UNSTABLE/UNHELPFUL:" in output:
        return "UNSTABLE/UNHELPFUL"

    if "MIXED RESULT:" in output:
        return "MIXED RESULT"

    if "No generated Java test files found" in output:
        return "EVOSUITE NO TESTS"

    if "EvoSuite generation failed" in output:
        return "EVOSUITE FAILED"

    if "Maven compile failed" in output:
        return "COMPILE FAILED"

    if "Compilation of generated tests failed" in output:
        return "TEST COMPILE FAILED"

    if exit_code == 0:
        return "UNKNOWN-SUCCESS"

    return "RUNNER ERROR"


def extract_summary_value(output: str, label: str) -> str:
    """
    Extract values from summary lines printed by gitbug_evosuite_runner.py.

    Example line:
        Fixed result:     PASS
    """
    prefix = f"{label}:"

    for line in output.splitlines():
        if line.strip().startswith(prefix):
            return line.split(":", 1)[1].strip()

    return ""


def run_process_with_timeout(
    cmd: List[str],
    cwd: Path,
    timeout_seconds: int,
) -> Tuple[int, str, str]:
    """
    Run one command and kill it if it takes too long.

    start_new_session=True lets us terminate the whole process group, which is
    useful because EvoSuite/Maven may start child Java processes.
    """
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )

    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
        return proc.returncode, stdout, stderr

    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            stdout, stderr = proc.communicate(timeout=10)
        except Exception:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            stdout, stderr = proc.communicate()

        stderr = (stderr or "") + f"\n[TIMEOUT] Killed after {timeout_seconds} seconds.\n"
        return 124, stdout or "", stderr


def append_result(results_csv: Path, row: Dict[str, str]) -> None:
    """
    Append one result immediately.

    This is safer than writing all results at the end because long experiments
    may be interrupted halfway through.
    """
    results_csv.parent.mkdir(parents=True, exist_ok=True)
    file_exists = results_csv.is_file()

    with open(results_csv, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)

        if not file_exists:
            writer.writeheader()

        writer.writerow(row)


def cleanup_targets(work_root: Path) -> None:
    """
    Remove Maven target/ folders to save disk space.

    Logs and generated EvoSuite tests are left intact.
    """
    for target_dir in work_root.rglob("target"):
        if target_dir.is_dir():
            shutil.rmtree(target_dir, ignore_errors=True)


def run_one_bug(bug: Dict[str, str], seed: int, java_version: int, timeout_seconds: int, heap: str) -> Dict[str, str]:
    """
    Run one bug-class-seed experiment.

    This calls gitbug_evosuite_runner.py, which handles the actual workflow:
    checkout buggy/fixed, compile, run EvoSuite, compile tests, and compare
    fixed vs buggy.
    """
    bug_id = bug["bug_id"]
    class_under_test = bug["class_under_test"]

    # Each seed+java_version combination gets its own folder.
    safe_class = class_under_test.replace(".", "_").replace("$", "_")
    work_root = BASE_WORK_ROOT / f"{bug_id}__{safe_class}__seed_{seed}__java{java_version}"

    logs_dir = work_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    docker_image = JAVA_DOCKER_IMAGES.get(java_version)

    cmd = [
        sys.executable,
        RUNNER,
        "--gitbug-repo",
        GITBUG_REPO,
        "--bug-id",
        bug_id,
        "--class-under-test",
        class_under_test,
        "--evosuite-jar",
        EVOSUITE_JAR,
        "--junit-jar",
        JUNIT_JAR,
        "--hamcrest-jar",
        HAMCREST_JAR,
        "--work-root",
        str(work_root),
        "--heap",
        heap,
        "--seed",
        str(seed),
    ]

    if docker_image:
        cmd += ["--docker-image", docker_image]

    # Save the exact command used. This makes reruns/debugging easier.
    with open(logs_dir / "batch_command.txt", "w", encoding="utf-8") as f:
        f.write(" ".join(cmd) + "\n")

    exit_code, stdout, stderr = run_process_with_timeout(
        cmd=cmd,
        cwd=Path(GITBUG_REPO),
        timeout_seconds=timeout_seconds,
    )

    combined_output = stdout + "\n--- STDERR ---\n" + stderr

    with open(logs_dir / "batch_driver_stdout_stderr.log", "w", encoding="utf-8") as f:
        f.write(combined_output)

    result = classify_output(combined_output, exit_code)

    return {
        "bug_id": bug_id,
        "class_under_test": class_under_test,
        "seed": str(seed),
        "java_version": str(java_version),
        "exit_code": str(exit_code),
        "fixed_result": extract_summary_value(stdout, "Fixed result"),
        "buggy_result": extract_summary_value(stdout, "Buggy result"),
        "result": result,
        "conclusion": extract_summary_value(stdout, "Conclusion"),
        "work_root": str(work_root),
        "logs_dir": str(logs_dir),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch runner for GitBug-Java + EvoSuite experiments."
    )
    parser.add_argument(
        "--bugs-csv",
        default=str(DEFAULT_BUGS_CSV),
        help="CSV with columns: bug_id,class_under_test",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="Timeout per bug in seconds. Default: 1800 = 3 min",
    )
    parser.add_argument(
        "--heap",
        default="4g",
        help="Heap for EvoSuite. Default: 4g",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Run only the first N bugs. 0 = no limit",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rerun bugs even if they already have successful results",
    )
    parser.add_argument(
        "--cleanup-targets",
        action="store_true",
        help="Delete Maven target/ folders after each bug",
    )
    parser.add_argument(
        "--seeds",
        nargs="+",
        type=int,
        default=DEFAULT_SEEDS,
        help="List of EvoSuite seeds to run per bug. Default: first 5 seeds.",
    )
    parser.add_argument(
        "--java-versions",
        nargs="+",
        type=int,
        default=[8, 11],
        help="Java versions to test per bug. Each version uses its Docker image. Default: 8 11",
    )

    args = parser.parse_args()

    setup_java_env()
    BASE_WORK_ROOT.mkdir(parents=True, exist_ok=True)

    bugs = load_bugs(Path(args.bugs_csv))

    if args.limit > 0:
        bugs = bugs[:args.limit]

    seeds = args.seeds
    global_java_versions = args.java_versions

    # If the bugs CSV has a java_version column, use it per-row (one version
    # per bug) instead of looping over --java-versions globally.
    per_bug_versions = any("java_version" in bug for bug in bugs)

    if per_bug_versions:
        total_runs = sum(len(seeds) for _ in bugs)
        version_note = "per-bug (from CSV)"
    else:
        total_runs = len(bugs) * len(seeds) * len(global_java_versions)
        version_note = str(global_java_versions)

    completed = set() if args.force else load_completed_keys(RESULTS_CSV)

    print(f"Loaded {len(bugs)} bug(s) x {len(seeds)} seed(s) = {total_runs} total runs.")
    print(f"Seeds:         {seeds}")
    print(f"Java versions: {version_note}")
    print(f"Results CSV:   {RESULTS_CSV}")
    print(f"Timeout per run: {args.timeout} seconds")
    print(f"Heap: {args.heap}")

    run_index = 0
    for bug in bugs:
        bug_id = bug["bug_id"]
        class_under_test = bug["class_under_test"]

        # Per-bug version from CSV takes priority over --java-versions.
        if per_bug_versions:
            versions_for_bug = [int(bug["java_version"])]
        else:
            versions_for_bug = global_java_versions

        for java_version in versions_for_bug:
            for seed in seeds:
                run_index += 1
                key = bug_key(bug_id, class_under_test, seed, java_version)

                print(f"\n[{run_index}/{total_runs}] {bug_id} :: {class_under_test} :: seed={seed} :: java={java_version}")

                if key in completed:
                    print("  SKIPPED: already completed. Use --force to rerun.")
                    continue

                try:
                    result = run_one_bug(
                        bug,
                        seed=seed,
                        java_version=java_version,
                        timeout_seconds=args.timeout,
                        heap=args.heap,
                    )

                except Exception as e:
                    safe_class = class_under_test.replace(".", "_").replace("$", "_")
                    work_root = BASE_WORK_ROOT / f"{bug_id}__{safe_class}__seed_{seed}__java{java_version}"
                    logs_dir = work_root / "logs"
                    logs_dir.mkdir(parents=True, exist_ok=True)

                    result = {
                        "bug_id": bug_id,
                        "class_under_test": class_under_test,
                        "seed": str(seed),
                        "java_version": str(java_version),
                        "exit_code": "-1",
                        "fixed_result": "",
                        "buggy_result": "",
                        "result": "RUNNER ERROR",
                        "conclusion": str(e),
                        "work_root": str(work_root),
                        "logs_dir": str(logs_dir),
                    }

                    with open(logs_dir / "batch_driver_exception.log", "w", encoding="utf-8") as f:
                        f.write(str(e) + "\n")

                append_result(RESULTS_CSV, result)

                print(
                    f"  fixed={result['fixed_result'] or '?'} | "
                    f"buggy={result['buggy_result'] or '?'} | "
                    f"result={result['result']}"
                )
                print(f"  logs={result['logs_dir']}")

                if args.cleanup_targets:
                    cleanup_targets(Path(result["work_root"]))

    print(f"\nDone. {run_index} runs attempted.")
    print(f"Saved incremental results to: {RESULTS_CSV}")


if __name__ == "__main__":
    main()

