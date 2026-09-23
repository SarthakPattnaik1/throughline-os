"""The sandbox executor — parent side.

 is non-negotiable: analysis code must not execute inside the main
application process. This module spawns the runtime as a separate process and
constrains it.

**What is genuinely enforced here**

* Separate OS process — a crash or a memory blow-up cannot take the API down.
* Scrubbed environment — no `THROUGHLINE_DATABASE_URL`, no `*_API_KEY`, no
  `*_TOKEN`, no OAuth material. The child is given the minimum needed to run
  Python.
* Isolated working directory, destroyed after the run.
* Read-only input — the source is copied in and chmod'd `0o444`, so an analysis
  cannot mutate the data it describes.
* Wall-clock timeout, killing the whole process tree so children die too.
* CPU-seconds and memory limits.
* No shell: the child is exec'd with an argument vector.

**How the limits are imposed differs by platform, and the report says which**

* POSIX — `setrlimit` in the child between fork and exec, plus `setsid` so a
  timeout can `killpg` the entire tree. The limits are in force before the
  analysis executes an instruction.
* Windows — a Job Object holding the same ceilings, created before the child and
  attached to it as soon as it exists. See `jobobject.py` for the mapping.
  Two differences are real and are reported as best-effort rather than glossed:
  the read-only input relies on a file attribute the analysis could clear, and
  the job is attached just after the process is created rather than just before
  it starts.

**What is best-effort and reported as such**

* Network egress. `setrlimit` cannot express it and this is not a container.
  The child disables Python-level sockets itself, which stops a library from
  quietly calling home but is not a kernel boundary.

`policy_report()` returns exactly which controls are enforced on this platform.
It is stored with every run and is what `require_full_isolation` checks
before it will run untrusted code — so the honest limits of a desktop sandbox
are recorded rather than assumed away.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from collections.abc import Sequence
from typing import Any

#: Which family of process controls this platform provides.
#:
#: `resource` is POSIX-only and was imported unconditionally at module scope,
#: which made this module — and therefore the whole API, which imports it for
#: `policy_report` — fail to import on Windows before any analysis was attempted.
#: The import is guarded so the platform that lacks rlimits gets the equivalent
#: it does have rather than an ImportError at startup.
WINDOWS = sys.platform == "win32"

if WINDOWS:  # pragma: no cover - selected by platform
    resource = None
    from . import jobobject
else:
    import resource

    jobobject = None

DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MEMORY_MB = 2048
DEFAULT_CPU_SECONDS = 120

#: Environment variables the child may keep. Everything else is dropped, so a
#: secret cannot leak into an analysis process by accident.
#:
#: `SYSTEMROOT`, `windir` and `PATHEXT` are here for Windows, where a scrubbed
#: environment missing them can fail to load a DLL — an analysis importing scipy
#: or touching SSL then dies for a reason that has nothing to do with its own
#: correctness. None of them names anything secret, and on POSIX they are simply
#: absent.
#:
#: `TEMP` and `TMP` are deliberately *not* allowlisted. Without them Windows
#: falls back through `tempfile.gettempdir()` to the working directory, which is
#: the sandbox's own — better isolation than handing the child the user's temp
#: directory, so the omission is the feature.
_ENV_ALLOWLIST = {"PATH", "LANG", "LC_ALL", "TZ", "HOME", "TMPDIR", "PYTHONPATH",
                  "SYSTEMROOT", "windir", "PATHEXT"}
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "DATABASE", "DSN")


class SandboxError(RuntimeError):
    pass


class SandboxTimeout(SandboxError):
    pass


class IsolationUnavailable(SandboxError):
    """Required isolation controls are not available on this platform."""


_MEMORY_LIMIT_WARNING = "The analysis is running WITHOUT a memory ceiling"


def _report_actual_limits(report: dict[str, Any], stderr: str) -> dict[str, Any]:
    """Downgrade claims when the child reports an unapplied POSIX memory limit.

    policy_report() describes the mechanism expected on this platform before the
    child starts. POSIX may still refuse both RLIMIT_AS and RLIMIT_DATA at exec
    time. In that case the child's stderr is the authoritative observation and
    the stored run must not continue to claim memory_limit=True.
    """
    if _MEMORY_LIMIT_WARNING not in stderr:
        return report

    actual = {
        **report,
        "enforced": dict(report["enforced"]),
        "best_effort": dict(report["best_effort"]),
    }
    actual["enforced"]["memory_limit"] = False
    actual["best_effort"]["memory_limit"] = "requested_but_not_applied"
    return actual


@dataclass(slots=True)
class SandboxPolicy:
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    memory_mb: int = DEFAULT_MEMORY_MB
    cpu_seconds: int = DEFAULT_CPU_SECONDS
    allow_network: bool = False


@dataclass(slots=True)
class SandboxResult:
    ok: bool
    payload: dict[str, Any] = field(default_factory=dict)
    stderr: str = ""
    exit_code: int | None = None
    duration_ms: int = 0
    policy: dict[str, Any] = field(default_factory=dict)


def scrub_environment() -> dict[str, str]:
    """Build the child's environment: allowlisted, and never anything secret."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key in _ENV_ALLOWLIST and not any(marker in key.upper() for marker in _SECRET_MARKERS)
    }
    env["PYTHONHASHSEED"] = "0"          # determinism
    env["PYTHONDONTWRITEBYTECODE"] = "1"  # keep the working directory clean
    env["MPLBACKEND"] = "Agg"             # no display access
    # Deterministic single-threaded BLAS: thread scheduling otherwise perturbs
    # floating-point reductions and a rerun stops being bit-identical.
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        env[var] = "1"
    return env


def policy_report(policy: SandboxPolicy | None = None) -> dict[str, Any]:
    """Which controls this platform actually enforces.

    Every value here was once a hardcoded `True` sitting next to a real
    `platform.system()`, which is the one shape of dishonesty this file exists to
    prevent: a report that names the platform correctly and then describes
    another platform's guarantees. It is stored with every analysis run, so a
    result recorded under it would have carried a claim nobody checked.

    The mechanism differs by platform and the report says which is in force, so
    two runs of the same analysis on different machines can be compared knowing
    what each was actually protected by.
    """
    policy = policy or SandboxPolicy()

    enforced = {
        # Platform-independent: these come from how the child is spawned and
        # where it is spawned, not from any OS limit facility.
        "separate_process": True,
        "scrubbed_environment": True,
        "no_application_secrets": True,
        "isolated_working_directory": True,
        "wall_clock_timeout": True,
        "environment_destroyed_after_run": True,
        "no_shell": True,
        # Platform-dependent, and the reason this is computed rather than written
        # out: the limits and the tree-kill come from rlimits and process groups
        # on POSIX and from a Job Object on Windows.
        "cpu_limit": True,
        "memory_limit": True,
        "process_tree_killed_together": True,
        "read_only_inputs": not WINDOWS,
    }

    best_effort = {
        # Honest: this is a process sandbox, not a container.
        "network_egress_disabled": "python_level_only",
    }
    not_enforced = {
        "kernel_level_filesystem_isolation": True,
        "gpu_quota": True,
    }

    if WINDOWS:  # pragma: no cover - selected by platform
        mechanism = "windows_job_object"
        # chmod(0o444) sets the read-only attribute, which an analysis running as
        # the same user can simply clear. On POSIX the mode bits actually deny the
        # write. Reported as best-effort rather than enforced, because the
        # difference is real and a run recorded as "inputs were read-only" would
        # be overstating it.
        best_effort["read_only_inputs"] = "read_only_attribute_only"
        # Assignment to the job happens immediately after the process is created
        # rather than before it starts, since Popen does not expose the suspended
        # thread needed to do it earlier. The window is microseconds and the child
        # spends it in interpreter startup, but it is a window and not a fiction.
        best_effort["limits_applied_before_first_instruction"] = "assigned_after_spawn"
    else:
        mechanism = "posix_rlimit_process_group"

    return {
        "platform": platform.system(),
        "mechanism": mechanism,
        "enforced": enforced,
        "best_effort": best_effort,
        "not_enforced": not_enforced,
        "limits": {
            "timeout_seconds": policy.timeout_seconds,
            "memory_mb": policy.memory_mb,
            "cpu_seconds": policy.cpu_seconds,
        },
        "note": ("Process-level isolation suitable for the platform's own declarative "
                 "analysis methods. Arbitrary or model-authored code requires "
                 "container isolation and is refused until that exists."),
    }


def require_full_isolation() -> None:
    """Gate for running code the researcher did not write.

    Phase 2 executes only whitelisted methods driven by a validated spec, so this
    is not needed yet. It exists so that whoever later adds model-authored code
    has to confront the missing container boundary rather than discover it in
    production.
    """
    raise IsolationUnavailable(
        "Executing arbitrary or model-authored code requires kernel-level isolation "
        "(container or VM), which this desktop deployment does not provide. "
        "Only whitelisted AnalysisSpec methods may run."
    )


def _destroy(workdir: Path) -> None:
    """Remove the sandbox directory, including the files it made read-only.

    Found by running the suite on Windows. `shutil.rmtree` will not delete a file
    carrying the Windows read-only attribute, and the sandbox sets exactly that on
    the input and the job description — so `ignore_errors=True` swallowed the
    failure and left the directory, with a copy of the researcher's data in it,
    in the temp folder after every single analysis. Meanwhile `policy_report()`
    went on claiming the environment was destroyed afterwards.

    POSIX never showed this: there, permission to unlink comes from the *parent*
    directory, so a read-only file inside a writable directory deletes fine.
    """
    def clear_readonly_and_retry(func, path, _exc):  # noqa: ANN001
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except OSError:
            # Nothing further to try. Raised out of rmtree below rather than
            # silently ignored, because a sandbox that quietly fails to clean up
            # is the thing this function exists to prevent.
            raise

    shutil.rmtree(workdir, onexc=clear_readonly_and_retry)


def _limit_child(policy: SandboxPolicy) -> None:
    """Runs in the forked child, before exec. POSIX only."""
    # Own process group so a timeout kills the whole tree, not just the parent stub.
    os.setsid()
    resource.setrlimit(resource.RLIMIT_CPU, (policy.cpu_seconds, policy.cpu_seconds))
    memory_bytes = policy.memory_mb * 1024 * 1024
    try:
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    except (ValueError, OSError):
        # macOS refuses RLIMIT_AS for large values; RLIMIT_DATA is the fallback.
        try:
            resource.setrlimit(resource.RLIMIT_DATA, (memory_bytes, memory_bytes))
        except (ValueError, OSError):
            # Both refused: this analysis runs with no memory ceiling at all.
            #
            # It used to pass silently, which made the sandbox claim a limit it
            # was not applying — the worst shape a safety guarantee can take,
            # because everything downstream keeps believing it. Running anyway
            # is the right call (refusing would make the platform unusable
            # wherever this happens), but it must be visible: the parent
            # captures this stream into the recorded result, so the run itself
            # carries the admission.
            #
            # Written with os.write rather than print: this is a forked child
            # before exec, where the interpreter is in a state that makes
            # buffered I/O unsafe.
            os.write(2, (
                f"[sandbox] WARNING: could not apply a {policy.memory_mb}MB "
                "memory limit — neither RLIMIT_AS nor RLIMIT_DATA was accepted "
                "on this platform. The analysis is running WITHOUT a memory "
                "ceiling and could exhaust this machine.\n").encode())
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))  # no core dumps of research data
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))


#: How much of the per-analysis budget each extra spec in a batch is worth.
#: A batched spec skips the startup and the file read that dominate a lone run,
#: so it needs far less than a whole one — but it needs more than nothing, or a
#: long sweep would be killed part-way through by a limit set for a single
#: correlation.
BATCH_SECONDS_PER_SPEC = 2

#: The ceiling on a batch's budget, however many specs it carries.
#:
#: Without one, scaling the timeout per spec means a 200-column sweep — 19,900
#: pairs — is granted eleven hours, which is not a limit. A sweep that has not
#: finished in this long is not slow, it is wrong, and the researcher is better
#: served by an error than by a process that never returns.
MAX_BATCH_TIMEOUT_SECONDS = 30 * 60


def run_analysis(
    *,
    spec: dict[str, Any],
    input_path: Path,
    input_suffix: str,
    policy: SandboxPolicy | None = None,
) -> SandboxResult:
    """Execute one analysis in an isolated process and return its JSON result."""
    return _run_sandbox(job_fields={"spec": spec}, input_path=input_path,
                        input_suffix=input_suffix, policy=policy)


def run_many(
    *,
    specs: Sequence[dict[str, Any]],
    input_path: Path,
    input_suffix: str,
    policy: SandboxPolicy | None = None,
) -> SandboxResult:
    """
    Execute many analyses in *one* isolated process.

    Discovery tests every usable pair of columns. Giving each pair its own
    sandbox meant a fresh Python, a fresh pandas import and a fresh copy of the
    whole dataset per pair — measured at 1.02s each, of which 0.8s is startup —
    so a forty-column file cost a quarter of an hour and a hundred-column file
    the better part of two, serially, while a researcher watched a spinner.

    The isolation is unchanged. These specs come from a closed list of methods
    and are generated by the system, never written by a person, so running them
    together is the same boundary as running them apart: no network, no
    environment, a read-only copy of the input, and the same memory and CPU
    ceilings. What each candidate keeps is its own result, its own seed and its
    own verdict — a failing pair reports beside the others rather than taking
    the sweep down with it.

    The payload is ``{"ok": true, "results": [...]}`` where each entry has the
    shape a lone run returns.
    """
    specs = list(specs)
    policy = policy or SandboxPolicy()
    # The whole sweep runs inside one budget, so the budget grows with it.
    # Without this the limit written for a single correlation would kill a long
    # sweep in the middle and report it as a timeout rather than a miscount.
    extra = BATCH_SECONDS_PER_SPEC * max(0, len(specs) - 1)
    policy = replace(
        policy,
        timeout_seconds=min(policy.timeout_seconds + extra,
                            MAX_BATCH_TIMEOUT_SECONDS),
        cpu_seconds=min(policy.cpu_seconds + extra,
                        MAX_BATCH_TIMEOUT_SECONDS),
    )
    return _run_sandbox(job_fields={"specs": specs}, input_path=input_path,
                        input_suffix=input_suffix, policy=policy)


def _run_sandbox(
    *,
    job_fields: dict[str, Any],
    input_path: Path,
    input_suffix: str,
    policy: SandboxPolicy | None = None,
) -> SandboxResult:
    """The isolated process itself, shared by one analysis and by a sweep."""
    policy = policy or SandboxPolicy()
    report = policy_report(policy)
    started = time.time()

    workdir = Path(tempfile.mkdtemp(prefix="throughline-sandbox-"))
    try:
        # Copy the input in and make it read-only: an analysis describes data, it
        # does not alter it.
        sandbox_input = workdir / f"input{input_suffix or '.csv'}"
        shutil.copy2(input_path, sandbox_input)
        sandbox_input.chmod(0o444)

        job_path = workdir / "job.json"
        job_path.write_text(json.dumps({
            **job_fields,
            "input_path": str(sandbox_input),
            "input_suffix": input_suffix,
        }), encoding="utf-8")
        job_path.chmod(0o444)

        command = [sys.executable, "-I", "-m",
                   "throughline_runtime.entrypoint", str(job_path)]
        common = {
            "cwd": workdir,
            "env": scrub_environment(),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "shell": False,
        }

        # The two platforms reach the same place by different routes. POSIX sets
        # the limits in the child between fork and exec, so they are in force
        # before the analysis has run an instruction. Windows has no fork and no
        # rlimits: the limits live in a Job Object created here, and the child is
        # attached to it the moment it exists.
        job = None
        if WINDOWS:  # pragma: no cover - selected by platform
            # CREATE_NEW_PROCESS_GROUP is the closest thing to setsid(): it stops
            # a Ctrl-C in this console from reaching the analysis, so the parent
            # decides when the child dies.
            job = jobobject.Job(memory_mb=policy.memory_mb,
                                cpu_seconds=policy.cpu_seconds)
            try:
                process = subprocess.Popen(
                    command, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                    **common)
            except BaseException:
                job.close()
                raise
            try:
                job.assign(int(process._handle))  # noqa: SLF001 — the only handle Popen exposes
            except jobobject.JobObjectError:
                # Refuse rather than continue unprotected. A run that silently lost
                # its memory and CPU ceilings would still be recorded against a
                # policy report claiming it had them.
                process.kill()
                process.communicate()
                job.close()
                raise
        else:
            process = subprocess.Popen(
                command,
                preexec_fn=lambda: _limit_child(policy),  # noqa: PLW1509 — intended
                **common)

        try:
            stdout, stderr = process.communicate(timeout=policy.timeout_seconds)
        except subprocess.TimeoutExpired:
            if job is not None:  # pragma: no cover - selected by platform
                # Terminates the process and every descendant, as killpg does.
                job.terminate()
            else:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            process.communicate()
            raise SandboxTimeout(
                f"Analysis exceeded its {policy.timeout_seconds}s limit and was terminated."
            ) from None
        finally:
            if job is not None:  # pragma: no cover - selected by platform
                # Closing the handle kills anything still inside the job, because
                # it was created with KILL_ON_JOB_CLOSE. A grandchild that outlived
                # the analysis does not outlive this line.
                job.close()

        duration = int((time.time() - started) * 1000)
        report = _report_actual_limits(report, stderr)
        if not stdout.strip():
            return SandboxResult(
                ok=False,
                payload={"error": "The analysis process produced no output. "
                                  "It was most likely killed by a resource limit."},
                stderr=stderr[-4000:], exit_code=process.returncode,
                duration_ms=duration, policy=report,
            )
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return SandboxResult(
                ok=False,
                payload={"error": "The analysis process returned malformed output."},
                stderr=(stdout[-2000:] + "\n" + stderr[-2000:]),
                exit_code=process.returncode, duration_ms=duration, policy=report,
            )

        return SandboxResult(
            ok=bool(payload.get("ok")), payload=payload, stderr=stderr[-4000:],
            exit_code=process.returncode, duration_ms=duration, policy=report,
        )
    finally:
        # Destroy the environment after execution. Not ignore_errors: research
        # data left in a temp directory is the failure, and hiding it is worse
        # than the exception.
        _destroy(workdir)
