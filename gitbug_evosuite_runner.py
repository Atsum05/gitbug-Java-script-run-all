import argparse
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

"""
For java 8
export JAVA_HOME=/usr/lib/jvm/java-8-openjdk-amd64
export PATH="$JAVA_HOME/bin:$PATH"
"""


def _docker_prefix(image: str, cwd: Path, *extra_mounts: Path) -> List[str]:
    """
    Build the 'docker run' prefix that wraps a Java/Maven command.

    All paths are mounted at the same absolute location inside the container so
    that classpath strings written by Maven (cp.txt) resolve without translation.

    --user {uid}:{gid} ensures files created inside Docker (Maven target/,
    EvoSuite tests, cp.txt) are owned by the host user, so shutil.rmtree
    on subsequent runs does not fail with PermissionError.

    --network=host lets EvoSuite's RMI communicate the same as on the host.
    """
    seen: set = set()
    vol_args: List[str] = []
    for p in (cwd, *extra_mounts):
        s = str(p)
        if s not in seen:
            seen.add(s)
            vol_args += ["-v", f"{s}:{s}"]

    import os
    uid = os.getuid()
    gid = os.getgid()
    home = os.path.expanduser("~")

    return [
        "docker", "run", "--rm",
        "--network=host",
        "--user", f"{uid}:{gid}",
        "-e", f"HOME={home}",
        *vol_args,
        "-w", str(cwd),
        image,
    ]


def run_cmd(cmd: List[str], cwd: Path | None = None, check: bool = True) -> Tuple[int, str, str]:
    """
    Run a terminal command and capture its stdout/stderr.

    If check=True, the function raises an error when the command fails.
    If check=False, the caller decides what to do with the return code.
    """
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    stdout, stderr = proc.communicate()

    if check and proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"[stdout]\n{stdout}\n[stderr]\n{stderr}"
        )

    return proc.returncode, stdout, stderr


def write_log(log_dir: Path, name: str, stdout: str, stderr: str) -> None:
    """
    Save command output to a log file.

    Each major step writes a separate log so it is easier to debug later.
    """
    log_dir.mkdir(parents=True, exist_ok=True)

    with open(log_dir / name, "w", encoding="utf-8") as f:
        f.write(stdout)
        f.write("\n--- STDERR ---\n")
        f.write(stderr)


def ensure_file(path: Path, label: str) -> None:
    """Fail early if an expected file is missing."""
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")


def ensure_dir(path: Path, label: str) -> None:
    """Fail early if an expected directory is missing."""
    if not path.is_dir():
        raise FileNotFoundError(f"{label} not found: {path}")


def checkout_version(gitbug_repo: Path, bug_id: str, out_dir: Path, fixed: bool) -> None:
    """
    Checkout either the buggy or fixed version of a GitBug-Java bug.

    The destination folder is deleted first so each run starts from a clean
    checkout.
    """
    if out_dir.exists():
        shutil.rmtree(out_dir)

    cmd = [sys.executable, "./gitbug-java", "checkout", bug_id, str(out_dir)]

    if fixed:
        cmd.append("--fixed")

    run_cmd(cmd, cwd=gitbug_repo)


def checkout_version_json(gitbug_repo: Path, bug_id: str, out_dir: Path, fixed: bool) -> None:
    """
    Checkout either the buggy or fixed version directly from the JSON data files.

    Unlike checkout_version(), this does not require poetry or the gitbug-java
    CLI. It reads the bug metadata from data/bugs/*.json, clones the repo via
    pygit2, and applies the relevant patches for the buggy version.
    """
    import json
    import pygit2
    import uuid

    if out_dir.exists():
        shutil.rmtree(out_dir)

    bugs_dir = gitbug_repo / "data" / "bugs"
    commit_short = bug_id.split("-")[-1]

    bug_data = None
    for json_file in bugs_dir.glob("*.json"):
        with open(json_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("commit_hash", "").startswith(commit_short):
                    bug_data = data
                    break
        if bug_data is not None:
            break

    if bug_data is None:
        raise RuntimeError(f"No JSON entry found for bug_id: {bug_id}")

    clone_url = bug_data["clone_url"]
    commit_hash = bug_data["commit_hash"]
    previous_commit_hash = bug_data["previous_commit_hash"]
    test_patch = bug_data.get("test_patch", "")
    non_code_patch = bug_data.get("non_code_patch", "")
    bug_patch = bug_data.get("bug_patch", "")

    logging.info(f"Cloning {clone_url} → {out_dir}")
    repo = pygit2.clone_repository(clone_url, str(out_dir))

    subprocess.run(["git", "config", "gc.auto", "0"], cwd=str(out_dir), capture_output=True)

    def set_commit(target_hash: str) -> None:
        commit = repo.revparse_single(target_hash)
        repo.checkout_tree(commit)
        repo.create_tag(
            str(uuid.uuid4()),
            commit.id,
            pygit2.GIT_OBJECT_COMMIT,
            commit.author,
            commit.message,
        )
        repo.set_head(commit.id)

    if fixed:
        set_commit(commit_hash)
    else:
        set_commit(previous_commit_hash)
        if non_code_patch and bug_patch:
            repo.apply(pygit2.Diff.parse_diff(non_code_patch))
        if test_patch:
            repo.apply(pygit2.Diff.parse_diff(test_patch))

    workflows_dir = out_dir / ".github" / "workflows"
    if workflows_dir.exists():
        for wf in list(workflows_dir.glob("*.yml")) + list(workflows_dir.glob("*.yaml")):
            wf.unlink()

    bug_data["fixed"] = fixed
    with open(out_dir / "gitbug.json", "w") as f:
        json.dump(bug_data, f)


def compile_maven_project(
    project_dir: Path,
    log_dir: Path,
    prefix: str,
    docker_image: str | None = None,
    m2_dir: Path | None = None,
) -> str:
    """
    Compile a Maven project and build its dependency classpath.

    EvoSuite needs compiled .class files, not just .java files. That is why
    Maven compile is required before running EvoSuite.

    Returns a classpath string that includes target/classes and Maven
    dependencies.
    """
    extra = [m2_dir] if m2_dir else []
    prefix_docker = _docker_prefix(docker_image, project_dir, *extra) if docker_image else []
    cwd = None if docker_image else project_dir

    # When running inside Docker as root, user.home=/root so Maven would write
    # /root/.m2 paths into cp.txt. We force the local repo to the mounted path
    # so all classpath entries are resolvable inside the container.
    repo_flag = [f"-Dmaven.repo.local={m2_dir}/repository"] if (docker_image and m2_dir) else []

    code, out, err = run_cmd(
        prefix_docker + ["mvn", "-q", "-DskipTests", "-Dmaven.javadoc.skip=true", "compile"] + repo_flag,
        cwd=cwd,
    )
    write_log(log_dir, f"{prefix}_mvn_compile.log", out, err)

    if code != 0:
        raise RuntimeError(f"Maven compile failed for {project_dir}")

    code, out, err = run_cmd(
        prefix_docker + ["mvn", "-q", "dependency:build-classpath", "-Dmdep.outputFile=cp.txt"] + repo_flag,
        cwd=cwd,
    )
    write_log(log_dir, f"{prefix}_mvn_classpath.log", out, err)

    if code != 0:
        raise RuntimeError(f"Maven classpath build failed for {project_dir}")

    cp_txt = project_dir / "cp.txt"
    ensure_file(cp_txt, "cp.txt")

    deps = cp_txt.read_text(encoding="utf-8").strip()
    cp = f"target/classes:{deps}" if deps else "target/classes"

    return cp


def compile_gradle_project(
    project_dir: Path,
    log_dir: Path,
    prefix: str,
    docker_image: str | None = None,
    m2_dir: Path | None = None,
) -> str:
    """
    Compile a Gradle project and build its dependency classpath.

    Uses ./gradlew to compile, then injects a temporary init script to
    collect the runtimeClasspath into cp.txt — the same format Maven uses.

    Returns a classpath string pointing to Gradle's build output + dependencies.
    """
    gradle_cache = Path.home() / ".gradle"
    extra = []
    if m2_dir:
        extra.append(m2_dir)
    if docker_image and gradle_cache.exists():
        extra.append(gradle_cache)

    prefix_docker = _docker_prefix(docker_image, project_dir, *extra) if docker_image else []
    cwd = None if docker_image else project_dir

    gradlew = project_dir / "gradlew"
    gradlew.chmod(0o755)
    gradle_cmd = [str(gradlew)]

    # Step 1: compile
    code, out, err = run_cmd(
        prefix_docker + gradle_cmd + ["--no-daemon", "-q", "classes"],
        cwd=cwd,
        check=False,
    )
    write_log(log_dir, f"{prefix}_gradle_compile.log", out, err)

    if code != 0:
        raise RuntimeError(f"Gradle compile failed for {project_dir}")

    # Step 2: collect classpath via a temporary init script.
    # The script defines a task that resolves runtimeClasspath/compileClasspath
    # and writes all jar paths to cp.txt in the project root.
    init_script = """\
allprojects {
    task evosuite_cp {
        doLast {
            def cp = [] as LinkedHashSet
            try { cp += configurations.runtimeClasspath.resolve() } catch(ignored) {}
            try { cp += configurations.compileClasspath.resolve() } catch(ignored) {}
            new File(rootProject.rootDir, 'cp.txt').text = cp.collect { it.absolutePath }.unique().join(':')
        }
    }
}
"""
    init_file = project_dir / "_evosuite_init.gradle"
    init_file.write_text(init_script, encoding="utf-8")

    try:
        code, out, err = run_cmd(
            prefix_docker + gradle_cmd + [
                "--no-daemon", "-q",
                "--no-configuration-cache",
                "--init-script", str(init_file),
                "evosuite_cp",
            ],
            cwd=cwd,
            check=False,
        )
        write_log(log_dir, f"{prefix}_gradle_classpath.log", out, err)

        if code != 0:
            raise RuntimeError(f"Gradle classpath collection failed for {project_dir}")
    finally:
        init_file.unlink(missing_ok=True)

    cp_txt = project_dir / "cp.txt"
    ensure_file(cp_txt, "cp.txt")

    deps = cp_txt.read_text(encoding="utf-8").strip()

    # Gradle puts compiled classes in build/classes/java/main/ by default.
    classes_dir = project_dir / "build" / "classes" / "java" / "main"
    if not classes_dir.is_dir():
        for alt in ["build/classes/groovy/main", "build/classes/kotlin/main"]:
            candidate = project_dir / alt
            if candidate.is_dir():
                classes_dir = candidate
                break

    cp = f"{classes_dir}:{deps}" if deps else str(classes_dir)
    return cp


def compile_project(
    project_dir: Path,
    log_dir: Path,
    prefix: str,
    docker_image: str | None = None,
    m2_dir: Path | None = None,
) -> str:
    """
    Compile a project using Maven or Gradle, auto-detected from the project root.

    Returns a classpath string including compiled classes and dependencies.
    """
    if (project_dir / "pom.xml").is_file():
        logging.info(f"Detected Maven project at {project_dir}")
        return compile_maven_project(project_dir, log_dir, prefix, docker_image, m2_dir)
    elif (project_dir / "build.gradle").is_file() or (project_dir / "build.gradle.kts").is_file():
        logging.info(f"Detected Gradle project at {project_dir}")
        return compile_gradle_project(project_dir, log_dir, prefix, docker_image, m2_dir)
    else:
        raise RuntimeError(f"No pom.xml or build.gradle found in {project_dir} — cannot compile.")


def run_evosuite(
    fixed_dir: Path,
    classpath: str,
    cut: str,
    evosuite_jar: Path,
    heap: str,
    log_dir: Path,
    seed: int | None = None,
    docker_image: str | None = None,
    m2_dir: Path | None = None,
    evosuite_port: int | None = None,
    no_junit_check: bool = False,
) -> None:
    """
    Run EvoSuite on the fixed version.

    EvoSuite receives:
    - the target class name,
    - the compiled project classpath,
    - and the EvoSuite jar.

    It should create an evosuite-tests/ folder with generated JUnit tests.
    """
    cmd = ["java", f"-Xmx{heap}"]

    # -Dstarting_port is a JVM system property read by EvoSuite's RMI layer,
    # not an EvoSuite CLI option — it must go before -jar.
    if evosuite_port is not None:
        cmd += [f"-Dstarting_port={evosuite_port}"]

    cmd += [
        "-jar",
        str(evosuite_jar),
        "-class",
        cut,
        "-projectCP",
        classpath,
    ]

    if seed is not None:
        cmd += ["-seed", str(seed)]

    # Skip assertion minimization (which uses RMI and crashes on Java 11).
    # Tests are written immediately after the search with all assertions included.
    if no_junit_check:
        cmd += ["-Djunit_check=false"]

    if docker_image:
        extra = [evosuite_jar]
        if m2_dir:
            extra.append(m2_dir)
        cmd = _docker_prefix(docker_image, fixed_dir, *extra) + cmd
        cwd = None
    else:
        cwd = fixed_dir

    code, out, err = run_cmd(cmd, cwd=cwd, check=False)
    write_log(log_dir, "evosuite_fixed.log", out, err)

    if code != 0:
        raise RuntimeError("EvoSuite generation failed on fixed version")


def find_generated_java_files(project_dir: Path) -> List[Path]:
    """
    Find the .java test files generated by EvoSuite.

    Typical files look like:
        SomeClass_ESTest.java
        SomeClass_ESTest_scaffolding.java
    """
    tests_dir = project_dir / "evosuite-tests"

    if not tests_dir.is_dir():
        return []

    return sorted(tests_dir.rglob("*.java"))


def compile_generated_tests(
    project_dir: Path,
    project_cp: str,
    evosuite_jar: Path,
    junit_jar: Path,
    hamcrest_jar: Path,
    log_dir: Path,
    docker_image: str | None = None,
    m2_dir: Path | None = None,
) -> None:
    """
    Compile the EvoSuite-generated tests.

    EvoSuite creates .java files. JUnit can only run them after javac compiles
    them into .class files.
    """
    java_files = find_generated_java_files(project_dir)

    if not java_files:
        raise RuntimeError("No generated Java test files found in evosuite-tests")

    cp = ":".join([
        project_cp,
        str(evosuite_jar),
        str(junit_jar),
        str(hamcrest_jar),
    ])

    cmd = ["javac", "-cp", cp] + [str(p) for p in java_files]

    if docker_image:
        extra = [evosuite_jar]
        if m2_dir:
            extra.append(m2_dir)
        cmd = _docker_prefix(docker_image, project_dir, *extra) + cmd
        cwd = None
    else:
        cwd = project_dir

    code, out, err = run_cmd(cmd, cwd=cwd, check=False)
    write_log(log_dir, "compile_generated_tests.log", out, err)

    if code != 0:
        raise RuntimeError("Compilation of generated tests failed")


def run_junit(
    project_dir: Path,
    project_cp: str,
    tests_dir: Path,
    test_class: str,
    evosuite_jar: Path,
    junit_jar: Path,
    hamcrest_jar: Path,
    log_dir: Path,
    log_name: str,
    docker_image: str | None = None,
    m2_dir: Path | None = None,
) -> bool:
    """
    Run the generated test suite with JUnit.

    Returns True if all tests pass and False otherwise.
    """
    cp = ":".join([
        project_cp,
        str(tests_dir),
        str(evosuite_jar),
        str(junit_jar),
        str(hamcrest_jar),
    ])

    cmd = ["java", "-cp", cp, "org.junit.runner.JUnitCore", test_class]

    if docker_image:
        extra = [tests_dir, evosuite_jar]
        if m2_dir:
            extra.append(m2_dir)
        cmd = _docker_prefix(docker_image, project_dir, *extra) + cmd
        cwd = None
    else:
        cwd = project_dir

    code, out, err = run_cmd(cmd, cwd=cwd, check=False)
    write_log(log_dir, log_name, out, err)

    return code == 0


def print_changed_java_files(buggy_dir: Path, fixed_dir: Path) -> None:
    """
    Print changed Java files between buggy and fixed versions.

    This is mainly a sanity check: the selected class under test should usually
    be one of these changed files.
    """
    buggy_src = buggy_dir / "src" / "main" / "java"
    fixed_src = fixed_dir / "src" / "main" / "java"

    if not buggy_src.is_dir() or not fixed_src.is_dir():
        return

    _, out, err = run_cmd(
        ["diff", "-rq", str(buggy_src), str(fixed_src)],
        check=False,
    )

    if out.strip():
        print("Changed Java files between buggy and fixed:")

        for line in out.splitlines():
            if ".java" in line:
                print(line)

    if err.strip():
        logging.warning(err.strip())


def summarize(fixed_pass: bool, buggy_pass: bool) -> str:
    """
    Convert fixed/buggy JUnit results into a human-readable conclusion.
    """
    if fixed_pass and not buggy_pass:
        return "BUG-REVEALING: tests pass on fixed and fail on buggy."

    if fixed_pass and buggy_pass:
        return "NO DIFFERENCE FOUND: tests pass on both fixed and buggy."

    if not fixed_pass and not buggy_pass:
        return "UNSTABLE/UNHELPFUL: tests fail on both fixed and buggy."

    return "MIXED RESULT: tests fail on fixed but pass on buggy; inspect logs carefully."


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate EvoSuite tests on a fixed GitBug-Java checkout, "
            "then run them on fixed and buggy versions."
        )
    )

    parser.add_argument("--gitbug-repo", required=True, help="Path to the gitbug-java repo")
    parser.add_argument("--bug-id", required=True, help="GitBug-Java bug ID")
    parser.add_argument("--class-under-test", required=True, help="Fully qualified Java class name")
    parser.add_argument("--evosuite-jar", required=True, help="Path to evosuite-1.2.0.jar")
    parser.add_argument("--junit-jar", required=True, help="Path to junit-4.13.2.jar")
    parser.add_argument("--hamcrest-jar", required=True, help="Path to hamcrest-core-1.3.jar")
    parser.add_argument("--work-root", required=True, help="Directory where checkouts and logs are stored")
    parser.add_argument("--heap", default="4g", help="Heap size for EvoSuite. Default: 4g")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for EvoSuite. Omit for EvoSuite default.")
    parser.add_argument("--docker-image", default=None, help="Docker image to use for Java steps (e.g. gitbug-java8). Omit to use system Java.")
    parser.add_argument("--evosuite-port", type=int, default=None,
                        help="Starting RMI port for EvoSuite (e.g. 40000). Use to avoid port conflicts in parallel runs.")
    parser.add_argument("--no-junit-check", action="store_true",
                        help="Pass -Djunit_check=false to EvoSuite. Skips assertion minimization "
                             "(which uses RMI and crashes on Java 11). Tests are noisier but will be written to disk.")
    parser.add_argument("--checkout-mode", choices=["cli", "json"], default="cli",
                        help="How to checkout bugs. 'cli' uses the gitbug-java CLI (requires poetry). "
                             "'json' reads data/bugs/*.json directly via pygit2 (no poetry needed). Default: cli")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    gitbug_repo = Path(args.gitbug_repo).expanduser().resolve()
    evosuite_jar = Path(args.evosuite_jar).expanduser().resolve()
    junit_jar = Path(args.junit_jar).expanduser().resolve()
    hamcrest_jar = Path(args.hamcrest_jar).expanduser().resolve()
    work_root = Path(args.work_root).expanduser().resolve()

    # Basic validation before doing expensive work.
    ensure_dir(gitbug_repo, "gitbug-java repo")
    ensure_file(gitbug_repo / "gitbug-java", "gitbug-java script")
    ensure_file(evosuite_jar, "EvoSuite jar")
    ensure_file(junit_jar, "JUnit jar")
    ensure_file(hamcrest_jar, "Hamcrest jar")

    docker_image = args.docker_image
    m2_dir = Path.home() / ".m2"

    buggy_dir = work_root / "buggy"
    fixed_dir = work_root / "fixed"
    log_dir = work_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    _checkout = checkout_version_json if args.checkout_mode == "json" else checkout_version

    logging.info(f"Checking out buggy version (mode={args.checkout_mode})")
    _checkout(gitbug_repo, args.bug_id, buggy_dir, fixed=False)

    logging.info(f"Checking out fixed version (mode={args.checkout_mode})")
    _checkout(gitbug_repo, args.bug_id, fixed_dir, fixed=True)

    print_changed_java_files(buggy_dir, fixed_dir)

    logging.info("Compiling fixed version")
    cp_fixed = compile_project(fixed_dir, log_dir, "fixed", docker_image, m2_dir)

    logging.info("Running EvoSuite on fixed version")
    run_evosuite(
        fixed_dir,
        cp_fixed,
        args.class_under_test,
        evosuite_jar,
        args.heap,
        log_dir,
        seed=args.seed,
        docker_image=docker_image,
        m2_dir=m2_dir,
        evosuite_port=args.evosuite_port,
        no_junit_check=args.no_junit_check,
    )

    logging.info("Compiling generated tests")
    compile_generated_tests(
        fixed_dir,
        cp_fixed,
        evosuite_jar,
        junit_jar,
        hamcrest_jar,
        log_dir,
        docker_image=docker_image,
        m2_dir=m2_dir,
    )

    test_class = f"{args.class_under_test}_ESTest"

    logging.info("Running generated tests on fixed version")
    fixed_pass = run_junit(
        fixed_dir,
        cp_fixed,
        fixed_dir / "evosuite-tests",
        test_class,
        evosuite_jar,
        junit_jar,
        hamcrest_jar,
        log_dir,
        "run_fixed.log",
        docker_image=docker_image,
        m2_dir=m2_dir,
    )

    logging.info("Compiling buggy version")
    cp_buggy = compile_project(buggy_dir, log_dir, "buggy", docker_image, m2_dir)

    logging.info("Running fixed-generated tests on buggy version")
    buggy_pass = run_junit(
        buggy_dir,
        cp_buggy,
        fixed_dir / "evosuite-tests",
        test_class,
        evosuite_jar,
        junit_jar,
        hamcrest_jar,
        log_dir,
        "run_buggy.log",
        docker_image=docker_image,
        m2_dir=m2_dir,
    )

    summary = summarize(fixed_pass, buggy_pass)

    print("\n========== SUMMARY ==========")
    print(f"Bug ID:           {args.bug_id}")
    print(f"Class under test: {args.class_under_test}")
    print(f"Seed:             {args.seed if args.seed is not None else 'default'}")
    print(f"Java image:       {docker_image if docker_image else 'system'}")
    print(f"Fixed result:     {'PASS' if fixed_pass else 'FAIL'}")
    print(f"Buggy result:     {'PASS' if buggy_pass else 'FAIL'}")
    print(f"Conclusion:       {summary}")
    print(f"Logs directory:   {log_dir}")
    print("=============================")


if __name__ == "__main__":
    main()

# to execute the file cd /home/atsum/Documents/gitbug-java
# poetry run python3 /home/atsum/Documents/investigation/run_screened_bugs.py

# cat /home/atsum/Documents/gitbug-batch/results.csv
# cd /home/atsum/Documents/gitbug-batch/semver4j-semver4j-10102b374298/logs
# cd ../fixed
# find evosuite-tests -name "*.java"
# find evosuite-tests -name "*.class"

# cat ../logs/run_fixed.log
# cat ../logs/run_buggy.log

"""cd /home/atsum/Documents/gitbug-java
poetry run python /home/atsum/Documents/investigation/run_screened_bugs.py \
  --bugs-csv /home/atsum/Documents/investigation/bugs_java_assigned.csv \
  --limit 1 \
  --seeds 3147383999447 9617663486099 5379124608314 4823761945872 9865472301598 \
          4098156729301 4378296150428 8912357690412 4082159706387 5017574018895 \
          3157718788187 8462097531802 8394051763254 7861948975955 5785033018534 \
          4687593021847 1369827450193 8912045678210 4195939566797 5919993929691 \
          8492013567021 4629875132408 7245638910523 4823167590241 7237611861410 \
          4872395016821 4967023185649 6048152937021 9516437924806 8609571234567 \
  --force"""

# For specific bug
"""poetry run python /home/atsum/Documents/investigation/run_screened_bugs.py \
  --bugs-csv /home/atsum/Documents/investigation/bugs_java_assigned.csv \
  --bug-id Bears-1 \
  --seeds 3147383999447 9617663486099 ... \
  --force"""