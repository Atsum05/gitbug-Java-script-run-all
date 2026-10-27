# GitBug-Java + EvoSuite Experiment

Generates tests with EvoSuite on the **fixed** version of a bug, then runs those same tests on both the fixed and buggy versions. If tests pass on fixed and fail on buggy → the bug is revealed.

---

## Files

| File | What it does |
|---|---|
| `run_screened_bugs.py` | **Main script.** Batch runner — loops bugs × seeds × java versions, calls the worker, writes `results.csv` |
| `gitbug_evosuite_runner.py` | **Worker.** Runs the full EvoSuite pipeline for one (bug, class, seed, java) combination. Called automatically by the batch runner |
| `setup.sh` | **First-time setup.** Checks prerequisites, clones gitbug-java, builds Docker images, and validates everything |
| `bugs_java_assigned.csv` | Input bug list with `java_version` per row. Each bug runs with its assigned version only (77 bugs) |
| `bugs.csv` | Input bug list without java version. Use with `--java-versions` to test both versions |
| `screen_all_bugs.py` | Screens every bug in gitbug-java for Java 8/11 compile compatibility. Run to generate a fresh `bugs_java_assigned.csv` |
| `assign_java_versions.py` | Assigns java versions from a `results.csv` baseline. Bugs that worked → Java 11, failed → Java 8 |
| `analyze_evosuite_failures.py` | Post-run. Reads logs and classifies why EvoSuite failed (RMI crash, CFG error, classpath error, etc.) |
| `docker/java8.Dockerfile` | Docker image with Java 8 + Maven |
| `docker/java11.Dockerfile` | Docker image with Java 11 + Maven |
| `docker/build.sh` | Builds both Docker images (run once before experiments) |

---

## Prerequisites

- **Python 3.10+**
- **Poetry** — used to run scripts inside the GitBug-Java environment
- **Docker** — used to run Maven and EvoSuite in the correct Java version
- **GitBug-Java** repo — [https://github.com/gitbugactions/gitbug-java](https://github.com/gitbugactions/gitbug-java)
- **EvoSuite 1.2.0 jar** — download from [https://github.com/EvoSuite/evosuite/releases/tag/v1.2.0](https://github.com/EvoSuite/evosuite/releases/tag/v1.2.0)
- **JUnit 4.13.2** and **Hamcrest 1.3** — auto-detected from `~/.m2` (downloaded automatically on first Maven build)

---

## Step 1 — Run setup.sh (recommended)

`setup.sh` handles everything in one go: checks prerequisites, clones gitbug-java if needed, builds Docker images, and validates all paths.

```bash
bash /path/to/investigation/setup.sh
```

It will ask for:
- Path to the cloned gitbug-java repo (or where to clone it)
- Path to `evosuite-1.2.0.jar`

At the end it prints the exact command to start the experiment.

> If you prefer to set up manually, follow Steps 2–4 below instead.

---

## Step 2 — Clone and install GitBug-Java (manual)

```bash
git clone https://github.com/gitbugactions/gitbug-java.git /path/to/gitbug-java
cd /path/to/gitbug-java
poetry install
```

---

## Step 3 — Build Docker images (manual, once)

```bash
bash /path/to/investigation/docker/build.sh
```

This creates `gitbug-java8` and `gitbug-java11`. Verify with:

```bash
docker images | grep gitbug
```

---

## Step 4 — Validate setup

Run this to confirm all paths and Docker images are ready before starting:

```bash
cd /path/to/gitbug-java

poetry run python /path/to/investigation/run_screened_bugs.py \
  --gitbug-repo /path/to/gitbug-java \
  --evosuite-jar /path/to/evosuite-1.2.0.jar \
  --check
```

Expected output:
```
── Paths ───────────────────────────────────────────────────────
  ✓  Runner script: .../gitbug_evosuite_runner.py [OK]
  ✓  GitBug-Java repo: .../gitbug-java [OK]
  ✓  EvoSuite jar: .../evosuite-1.2.0.jar [OK]
  ✓  JUnit jar: ~/.m2/.../junit-4.13.2.jar [OK]
  ✓  Hamcrest jar: ~/.m2/.../hamcrest-core-1.3.jar [OK]

── Docker images ────────────────────────────────────────────────
  ✓  Java 8 (gitbug-java8): [OK]
  ✓  Java 11 (gitbug-java11): [OK]

All checks passed. Ready to run.
```

---

## Step 5 — Test with a small run first

Always test with a few bugs before running everything:

```bash
cd /path/to/gitbug-java

poetry run python /path/to/investigation/run_screened_bugs.py \
  --gitbug-repo /path/to/gitbug-java \
  --evosuite-jar /path/to/evosuite-1.2.0.jar \
  --bugs-csv /path/to/investigation/bugs_java_assigned.csv \
  --seeds 3147383999447 \
  --limit 5
```

Expected output:
```
Loaded 5 bug(s) x 1 seed(s) = 5 total runs.
Java versions: per-bug (from CSV)
Workers:       1
...
[submitted] [1/5] some-bug :: some.Class :: seed=3147383999447 :: java=8
[done 1/5] ...
  fixed=PASS | buggy=FAIL | result=BUG-REVEALING
```

---

## Running the full experiment

All commands below require `--gitbug-repo` and `--evosuite-jar`. JUnit and Hamcrest are auto-detected from `~/.m2`.

### Option A — All bugs, one seed, per-bug java version (77 runs, recommended)

```bash
cd /path/to/gitbug-java

poetry run python /path/to/investigation/run_screened_bugs.py \
  --gitbug-repo /path/to/gitbug-java \
  --evosuite-jar /path/to/evosuite-1.2.0.jar \
  --bugs-csv /path/to/investigation/bugs_java_assigned.csv \
  --seeds 3147383999447 \
  --workers 4
```

### Option B — All bugs, both java versions, one seed (154 runs)

```bash
cd /path/to/gitbug-java

poetry run python /path/to/investigation/run_screened_bugs.py \
  --gitbug-repo /path/to/gitbug-java \
  --evosuite-jar /path/to/evosuite-1.2.0.jar \
  --bugs-csv /path/to/investigation/bugs.csv \
  --seeds 3147383999447 \
  --java-versions 8 11 \
  --workers 4
```

### Option C — All bugs, all 30 seeds, per-bug java version (2310 runs)

Full statistical experiment:

```bash
cd /path/to/gitbug-java

poetry run python /path/to/investigation/run_screened_bugs.py \
  --gitbug-repo /path/to/gitbug-java \
  --evosuite-jar /path/to/evosuite-1.2.0.jar \
  --bugs-csv /path/to/investigation/bugs_java_assigned.csv \
  --seeds 3147383999447 9617663486099 5379124608314 4823761945872 9865472301598 \
          4098156729301 4378296150428 8912357690412 4082159706387 5017574018895 \
          3157718788187 8462097531802 8394051763254 7861948975955 5785033018534 \
          4687593021847 1369827450193 8912045678210 4195939566797 5919993929691 \
          8492013567021 4629875132408 7245638910523 4823167590241 7237611861410 \
          4872395016821 4967023185649 6048152937021 9516437924806 8609571234567 \
  --workers 4
```

---

## Key flags

| Flag | Default | Meaning |
|---|---|---|
| `--gitbug-repo` | `$GITBUG_REPO` env var | Path to the cloned gitbug-java repo |
| `--evosuite-jar` | `$EVOSUITE_JAR` env var | Path to `evosuite-1.2.0.jar` |
| `--work-root` | `~/gitbug-batch` | Where run folders and `results.csv` are written |
| `--bugs-csv` | `bugs_java_assigned.csv` | Which bug list to use |
| `--seeds` | first 5 seeds | EvoSuite random seeds |
| `--java-versions` | `8 11` | Java versions to test (ignored if CSV has `java_version` column) |
| `--workers` | `1` | Parallel bug runs. Use 4 on a 16 GB machine, up to 6–8 on 32 GB |
| `--limit N` | no limit | Run only the first N bugs |
| `--timeout N` | `1800` | Seconds allowed per run |
| `--heap` | `4g` | Java heap for EvoSuite per worker |
| `--force` | off | Re-run even if results already exist |
| `--reset` | off | Delete `results.csv` before starting (fresh slate) |
| `--cleanup-targets` | off | Delete Maven `target/` after each run (saves disk space) |
| `--check` | off | Validate all paths and Docker images, then exit |

---

## Choosing `--workers`

Each worker runs one EvoSuite process (default `--heap 4g`). Keep `workers × heap` below your available RAM.

| RAM | Recommended `--workers` |
|---|---|
| 8 GB | 2 |
| 16 GB | 4 |
| 32 GB | 6 |

Monitor RAM during the first run with `htop`. If swap is used, reduce workers.

---

## Output

Results are written to `<work-root>/results.csv` (default `~/gitbug-batch/results.csv`).

Each run also gets its own folder:
```
<work-root>/<bug_id>__<class>__seed_<N>__java<V>/
  buggy/        ← buggy checkout
  fixed/        ← fixed checkout + generated tests
  logs/
    evosuite_fixed.log               ← EvoSuite output
    batch_driver_stdout_stderr.log   ← full run log
```

### Result meanings

| Result | fixed | buggy | Meaning |
|---|---|---|---|
| `BUG-REVEALING` | PASS | FAIL | Best case — bug is detected |
| `NO DIFFERENCE FOUND` | PASS | PASS | Tests valid but don't expose the bug |
| `UNSTABLE/UNHELPFUL` | FAIL | FAIL | Tests fail on both — not reliable |
| `EVOSUITE NO TESTS` | — | — | EvoSuite crashed or produced no tests |
| `EVOSUITE FAILED` | — | — | EvoSuite process exited with an error |
| `COMPILE FAILED` | — | — | Maven could not compile the project |
| `RUNNER ERROR` | — | — | Unexpected script-level error |
| `TIMEOUT` | — | — | Run exceeded `--timeout` |

---

## Monitoring a running experiment

Check how many results have been written:
```bash
watch -n 5 'wc -l ~/gitbug-batch/results.csv'
```

Check active Docker containers (one per worker):
```bash
watch -n 3 'docker ps --format "table {{.Names}}\t{{.Status}}"'
```

Stream results as they land:
```bash
tail -f ~/gitbug-batch/results.csv
```

---

## Post-run analysis

### Classify why EvoSuite failed

```bash
python3 /path/to/investigation/analyze_evosuite_failures.py
```

Reads logs, classifies failures by category, writes `evosuite_failures_analysis.csv`.

### Re-assign java versions from a baseline run

```bash
python3 /path/to/investigation/assign_java_versions.py \
  --results-csv ~/gitbug-batch/results.csv \
  --out /path/to/investigation/bugs_java_assigned.csv
```

Bugs with `BUG-REVEALING` or `NO DIFFERENCE FOUND` → `java_version=11`. Everything else → `java_version=8`.

### Screen all bugs for Java compatibility

To regenerate `bugs_java_assigned.csv` from scratch (screens all bugs in gitbug-java):

```bash
cd /path/to/gitbug-java

poetry run python /path/to/investigation/screen_all_bugs.py
```

Supports `--resume` to continue an interrupted run and `--limit N` for testing.

---

## Why Docker

EvoSuite 1.2.0 was designed for Java 8. On Java 11+, some projects fail with:
```
Unsupported class file major version 55
```

Docker lets each bug run inside the correct Java environment without installing multiple Java versions on the host. Python runs on the host; Maven and EvoSuite run inside the container. All paths are mounted at the same absolute location inside the container so classpath resolution works correctly.

---

## Common errors

### `ModuleNotFoundError: No module named 'gitbugactions'`
Run the script with `poetry run` from inside the GitBug-Java repo, not with system Python.

### `RUNNER ERROR` on every bug
Run `--check` to validate all paths. One of the jars or directories is likely missing or wrong.

### `EVOSUITE NO TESTS` for many bugs
Check `logs/evosuite_fixed.log` inside the bug's folder. Common causes: RMI crash, classpath error, or an EvoSuite internal bug with that class.

### `EVOSUITE FAILED` with `Unknown property: starting_port`
This means `-Dstarting_port` was passed after `-jar` instead of before it. This is a known EvoSuite 1.2.0 quirk — the property must be a JVM system property. The current script handles this correctly.

### Docker permission errors
```bash
sudo usermod -aG docker $USER
# then log out and back in
```

### `Poetry could not find a pyproject.toml`
You ran `poetry run` from the wrong directory. Always `cd` into the gitbug-java repo first before running `poetry run python ...`.
